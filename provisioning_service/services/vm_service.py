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
    query = """
        SELECT * FROM desktop_pools
        WHERE deleted_at IS NULL
          AND status = 'active'
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
    conn,
    user_id,
    role: str,
    pool_id=None,
    pool_type=None,
) -> VMClaimResponse:
    active = await _find_active_assignment(conn, user_id)
    if active is not None:
        return _claim_response(active)

    pools = await _candidate_pools(conn, role, pool_id, pool_type)
    if not pools:
        if pool_id:
            raise ValueError("pool not found or not allowed for your role")
        raise ValueError("no pool available for your role")
    pool_ids = [pool["pool_id"] for pool in pools]

    deadline = time.time() + config.CLAIM_QUEUE_TIMEOUT_SECONDS
    while True:
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
