from __future__ import annotations

import asyncio
import time
from datetime import timedelta

from .. import config
from ..message_queue.celery_app import app
from ..models.responses import VMClaimResponse, VMStatusResponse


class PoolExhaustionError(Exception):
    pass


async def _find_active_assignment(conn, user_id):
    return await conn.fetchrow(
        """
        SELECT ua.assignment_id, ua.instance_id, ua.pool_id, ua.assigned_at,
               i.floating_ip, i.private_ip, i.status AS instance_status,
               p.name AS pool_name, p.desktop_type,
               p.max_session_duration_minutes
        FROM user_assignments ua
        JOIN desktop_instances i ON i.instance_id = ua.instance_id
        JOIN desktop_pools p ON p.pool_id = ua.pool_id
        WHERE ua.user_id = $1 AND ua.released_at IS NULL
        LIMIT 1
        """,
        user_id,
    )


def _claim_response(row) -> VMClaimResponse:
    return VMClaimResponse(
        instance_id=str(row["instance_id"]),
        floating_ip=str(row["floating_ip"]),
        private_ip=str(row["private_ip"]) if row["private_ip"] else None,
        session_expires_in_minutes=row["max_session_duration_minutes"],
        pool_name=row["pool_name"],
    )


async def _candidate_pools(conn, role: str, pool_id=None, pool_type=None):
    # Only 'open' pools are reachable via the plain claim path. 'code'
    # pools (class pools) are claimable exclusively through the access-code
    # flow, which resolves the pool server-side from a validated code.
    query = """
        SELECT * FROM desktop_pools
        WHERE deleted_at IS NULL
          AND status = 'active'
          AND access_mode = 'open'
          AND $1::user_role = ANY(allowed_roles)
    """
    params = [role]
    if pool_id:
        params.append(pool_id)
        query += f" AND pool_id = ${len(params)}"
    if pool_type:
        params.append(pool_type)
        query += f" AND desktop_type = ${len(params)}"
    query += " ORDER BY created_at ASC"
    return await conn.fetch(query, *params)


async def _try_claim(conn, user_id, pool_ids):
    async with conn.transaction():
        instance = await conn.fetchrow(
            """
            SELECT i.instance_id, i.pool_id, i.floating_ip, i.private_ip,
                   p.name AS pool_name, p.desktop_type,
                   p.max_session_duration_minutes
            FROM desktop_instances i
            JOIN desktop_pools p ON p.pool_id = i.pool_id
            WHERE i.pool_id = ANY($1::uuid[])
              AND i.status = 'ready'
              AND i.assigned_user_id IS NULL
            ORDER BY i.last_accessed_at ASC NULLS FIRST
            LIMIT 1
            FOR UPDATE SKIP LOCKED
            """,
            pool_ids,
        )
        if instance is None:
            return None
        await conn.execute(
            """
            INSERT INTO user_assignments
                (user_id, instance_id, pool_id, assignment_type)
            VALUES ($1, $2, $3, $4)
            """,
            user_id,
            instance["instance_id"],
            instance["pool_id"],
            "persistent"
            if instance["desktop_type"] == "persistent"
            else "temporary",
        )
        await conn.execute(
            """
            UPDATE desktop_instances
            SET status = 'in_use', assigned_user_id = $1,
                assigned_at = NOW(), last_accessed_at = NOW(),
                updated_at = NOW()
            WHERE instance_id = $2
            """,
            user_id,
            instance["instance_id"],
        )
        return instance


async def claim_vm(
    pool,
    user_id,
    role: str,
    pool_id=None,
    pool_type=None,
) -> VMClaimResponse:
    """Claim a VM for a user.

    `pool` is the asyncpg pool, NOT a checked-out connection: the claim
    wait loop can poll for CLAIM_QUEUE_TIMEOUT_SECONDS, and holding a
    pooled connection for the whole duration would exhaust the pool once
    several students wait concurrently. Each attempt acquires and releases
    a connection of its own.
    """
    async with pool.acquire() as conn:
        active = await _find_active_assignment(conn, user_id)
        if active is not None:
            return _claim_response(active)

        pools = await _candidate_pools(conn, role, pool_id, pool_type)
        if not pools:
            if pool_id:
                raise ValueError("pool not found or not allowed for your role")
            raise ValueError("no pool available for your role")
    pool_ids = [pool_row["pool_id"] for pool_row in pools]

    deadline = time.time() + config.CLAIM_QUEUE_TIMEOUT_SECONDS
    while True:
        async with pool.acquire() as conn:
            claimed = await _try_claim(conn, user_id, pool_ids)
        if claimed is not None:
            return _claim_response(claimed)
        if time.time() >= deadline:
            raise PoolExhaustionError(
                "no VM available within "
                f"{config.CLAIM_QUEUE_TIMEOUT_SECONDS}s"
            )
        await asyncio.sleep(config.CLAIM_QUEUE_POLL_INTERVAL_SECONDS)


async def release_vm(conn, user_id, reason: str = "user_logout") -> None:
    active = await _find_active_assignment(conn, user_id)
    if active is None:
        return

    delete_job_id = None
    async with conn.transaction():
        await conn.execute(
            """
            UPDATE user_assignments
            SET released_at = NOW(), release_reason = $1
            WHERE assignment_id = $2
            """,
            reason,
            active["assignment_id"],
        )
        if active["desktop_type"] == "non_persistent":
            await conn.execute(
                """
                UPDATE desktop_instances
                SET status = 'deleting', assigned_user_id = NULL,
                    assigned_at = NULL, updated_at = NOW()
                WHERE instance_id = $1
                """,
                active["instance_id"],
            )
            delete_job_id = await conn.fetchval(
                """
                INSERT INTO provisioning_jobs (pool_id, instance_id, job_type)
                VALUES ($1, $2, 'delete_vm')
                RETURNING job_id
                """,
                active["pool_id"],
                active["instance_id"],
            )
        else:
            await conn.execute(
                """
                UPDATE desktop_instances
                SET status = 'ready', assigned_user_id = NULL,
                    assigned_at = NULL, updated_at = NOW()
                WHERE instance_id = $1
                """,
                active["instance_id"],
            )

    if delete_job_id is not None:
        app.send_task(
            "provisioning_service.services.job_worker.delete_vm_task",
            args=[str(active["instance_id"]), str(delete_job_id)],
        )


async def release_instance(conn, instance_id: str, reason: str = "admin_action") -> dict:
    """Admin force-release of a VM session, keyed by instance.

    Closes any open assignment for the instance and follows the pool's
    lifecycle semantics: non-persistent instances are destroyed (a
    delete_vm job is dispatched), persistent ones return to 'ready'.
    Used by the admin console (Release) — an open assignment is a
    precondition for deleting an instance in the worker.
    """
    row = await conn.fetchrow(
        """
        SELECT i.instance_id, i.pool_id, i.status AS instance_status,
               i.assigned_user_id, p.desktop_type,
               ua.assignment_id
        FROM desktop_instances i
        JOIN desktop_pools p ON p.pool_id = i.pool_id
        LEFT JOIN user_assignments ua
               ON ua.instance_id = i.instance_id AND ua.released_at IS NULL
        WHERE i.instance_id = $1
        """,
        instance_id,
    )
    if row is None or row["instance_status"] == "deleted":
        return {"found": False}

    assignment_id = row["assignment_id"]
    has_session   = assignment_id is not None
    is_held       = has_session or row["assigned_user_id"] is not None
    destroy       = (
        row["desktop_type"] == "non_persistent" and is_held
    ) or (
        # Ownerless in-use non-persistent orphan — clean it up proactively.
        row["desktop_type"] == "non_persistent"
        and row["instance_status"] == "in_use"
    )

    delete_job_id = None
    async with conn.transaction():
        if has_session:
            await conn.execute(
                """
                UPDATE user_assignments
                SET released_at = NOW(), release_reason = $1
                WHERE assignment_id = $2 AND released_at IS NULL
                """,
                reason,
                assignment_id,
            )
        if destroy:
            await conn.execute(
                """
                UPDATE desktop_instances
                SET status = 'deleting', assigned_user_id = NULL,
                    assigned_at = NULL, updated_at = NOW()
                WHERE instance_id = $1
                """,
                instance_id,
            )
            delete_job_id = await conn.fetchval(
                """
                INSERT INTO provisioning_jobs (pool_id, instance_id, job_type)
                VALUES ($1, $2, 'delete_vm')
                RETURNING job_id
                """,
                row["pool_id"],
                instance_id,
            )
        elif is_held:
            await conn.execute(
                """
                UPDATE desktop_instances
                SET status = 'ready', assigned_user_id = NULL,
                    assigned_at = NULL, updated_at = NOW()
                WHERE instance_id = $1
                """,
                instance_id,
            )

    if delete_job_id is not None:
        app.send_task(
            "provisioning_service.services.job_worker.delete_vm_task",
            args=[str(instance_id), str(delete_job_id)],
        )

    return {
        "found": True,
        "assignment_closed": has_session,
        "destroyed": destroy,
        "job_id": str(delete_job_id) if delete_job_id else None,
    }


async def destroy_instance(conn, instance_id: str, reason: str = "admin_action") -> dict:
    """Admin force-destroy of a VM regardless of pool lifecycle.

    Closes any open assignment (the worker refuses open ones), marks the
    instance 'deleting' and dispatches the delete_vm job that destroys
    the OpenStack server + floating IP.
    """
    row = await conn.fetchrow(
        """
        SELECT i.pool_id, i.status AS instance_status,
               ua.assignment_id
        FROM desktop_instances i
        LEFT JOIN user_assignments ua
               ON ua.instance_id = i.instance_id AND ua.released_at IS NULL
        WHERE i.instance_id = $1
        """,
        instance_id,
    )
    if row is None or row["instance_status"] == "deleted":
        return {"found": False}
    if row["instance_status"] == "deleting":
        return {"found": True, "already_deleting": True, "job_id": None}

    assignment_id = row["assignment_id"]
    async with conn.transaction():
        if assignment_id is not None:
            await conn.execute(
                """
                UPDATE user_assignments
                SET released_at = NOW(), release_reason = $1
                WHERE assignment_id = $2 AND released_at IS NULL
                """,
                reason,
                assignment_id,
            )
        await conn.execute(
            """
            UPDATE desktop_instances
            SET status = 'deleting', assigned_user_id = NULL,
                assigned_at = NULL, updated_at = NOW()
            WHERE instance_id = $1
            """,
            instance_id,
        )
        job_id = await conn.fetchval(
            """
            INSERT INTO provisioning_jobs (pool_id, instance_id, job_type)
            VALUES ($1, $2, 'delete_vm')
            RETURNING job_id
            """,
            row["pool_id"],
            instance_id,
        )

    app.send_task(
        "provisioning_service.services.job_worker.delete_vm_task",
        args=[str(instance_id), str(job_id)],
    )
    return {"found": True, "already_deleting": False, "job_id": str(job_id)}


async def get_vm_status(conn, user_id) -> VMStatusResponse:
    active = await _find_active_assignment(conn, user_id)
    if active is None:
        return VMStatusResponse(has_assignment=False)
    return VMStatusResponse(
        has_assignment=True,
        instance_id=str(active["instance_id"]),
        floating_ip=str(active["floating_ip"])
        if active["floating_ip"]
        else None,
        pool_name=active["pool_name"],
        desktop_type=active["desktop_type"],
        status=active["instance_status"],
        assigned_at=active["assigned_at"],
        expires_at=active["assigned_at"]
        + timedelta(minutes=active["max_session_duration_minutes"]),
    )
