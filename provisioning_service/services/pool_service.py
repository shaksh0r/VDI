from __future__ import annotations

import asyncpg

from ..message_queue.celery_app import app
from ..models.requests import PoolCreateRequest, PoolUpdateRequest
from ..models.responses import PoolListResponse, PoolResponse


def _row_to_pool_response(row) -> PoolResponse:
    return PoolResponse(
        pool_id=str(row["pool_id"]),
        name=row["name"],
        desktop_type=row["desktop_type"],
        access_mode=row["access_mode"],
        image_id=row["base_image_id"],
        flavor_id=row["flavor_id"],
        network_id=row["network_id"],
        min_vms=row["min_vms"],
        max_vms=row["max_vms"],
        current_count=row["current_count"],
        auto_scaling_enabled=row["auto_scaling_enabled"],
        status=row["status"],
        allowed_roles=list(row["allowed_roles"]),
        max_session_minutes=row["max_session_duration_minutes"],
        created_by=str(row["created_by"]) if row["created_by"] else "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def insert_create_jobs(conn, pool_id, count: int) -> list:
    job_ids = []
    for _ in range(count):
        job_ids.append(
            await conn.fetchval(
                """
                INSERT INTO provisioning_jobs (pool_id, job_type)
                VALUES ($1, 'create_vm')
                RETURNING job_id
                """,
                pool_id,
            )
        )
    return job_ids


async def create_pool(conn, creator_user_id, data: PoolCreateRequest) -> PoolResponse:
    if data.max_vms < data.min_vms:
        raise ValueError("max_vms must be >= min_vms")
    try:
        async with conn.transaction():
            pool_id = await conn.fetchval(
                """
                INSERT INTO desktop_pools (
                    name, base_image_id, flavor_id, network_id,
                    min_vms, max_vms, desktop_type, access_mode,
                    auto_scaling_enabled, allowed_roles,
                    max_session_duration_minutes, created_by
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::user_role[], $11, $12)
                RETURNING pool_id
                """,
                data.name,
                data.image_id,
                data.flavor_id,
                data.network_id,
                data.min_vms,
                data.max_vms,
                data.desktop_type,
                data.access_mode,
                data.auto_scaling_enabled,
                data.allowed_roles,
                data.max_session_minutes,
                creator_user_id,
            )
            job_ids = await insert_create_jobs(conn, pool_id, data.min_vms)
    except asyncpg.exceptions.UniqueViolationError:
        raise ValueError(f"pool name '{data.name}' already exists") from None

    for job_id in job_ids:
        app.send_task(
            "provisioning_service.services.job_worker.create_vm_task",
            args=[str(pool_id), str(job_id)],
        )

    return await get_pool(conn, pool_id)


async def get_pool(conn, pool_id) -> PoolResponse | None:
    row = await conn.fetchrow(
        "SELECT * FROM desktop_pools WHERE pool_id = $1 AND deleted_at IS NULL",
        pool_id,
    )
    return _row_to_pool_response(row) if row else None


async def list_pools(
    conn,
    status: str | None = None,
    desktop_type: str | None = None,
) -> PoolListResponse:
    query = "SELECT * FROM desktop_pools WHERE deleted_at IS NULL"
    params = []
    if status:
        params.append(status)
        query += f" AND status = ${len(params)}"
    if desktop_type:
        params.append(desktop_type)
        query += f" AND desktop_type = ${len(params)}"
    query += " ORDER BY created_at DESC"
    rows = await conn.fetch(query, *params)
    pools = [_row_to_pool_response(row) for row in rows]
    return PoolListResponse(pools=pools, total=len(pools))


async def update_pool(conn, pool_id, updates: PoolUpdateRequest) -> PoolResponse | None:
    row = await conn.fetchrow(
        "SELECT * FROM desktop_pools WHERE pool_id = $1 AND deleted_at IS NULL",
        pool_id,
    )
    if row is None:
        return None

    fields = {}
    if updates.name is not None:
        fields["name"] = updates.name
    if updates.min_vms is not None:
        fields["min_vms"] = updates.min_vms
    if updates.max_vms is not None:
        fields["max_vms"] = updates.max_vms
    if updates.max_session_minutes is not None:
        fields["max_session_duration_minutes"] = updates.max_session_minutes
    if updates.auto_scaling_enabled is not None:
        fields["auto_scaling_enabled"] = updates.auto_scaling_enabled
    if updates.allowed_roles is not None:
        fields["allowed_roles"] = updates.allowed_roles
    if updates.status is not None:
        fields["status"] = updates.status

    if not fields:
        return _row_to_pool_response(row)

    new_min = fields.get("min_vms", row["min_vms"])
    new_max = fields.get("max_vms", row["max_vms"])
    if new_max < new_min:
        raise ValueError("max_vms must be >= min_vms")

    set_parts = []
    params = []
    for col, value in fields.items():
        params.append(value)
        cast = "::user_role[]" if col == "allowed_roles" else ""
        set_parts.append(f"{col} = ${len(params)}{cast}")
    query = (
        f"UPDATE desktop_pools SET {', '.join(set_parts)} "
        f"WHERE pool_id = ${len(params) + 1} RETURNING *"
    )
    try:
        updated = await conn.fetchrow(query, *params, pool_id)
    except asyncpg.exceptions.UniqueViolationError:
        raise ValueError(f"pool name '{fields['name']}' already exists") from None
    return _row_to_pool_response(updated)


async def delete_pool(conn, pool_id) -> bool:
    """Delete a pool (and everything attached to it, cascadingly).

    Soft-deletes the pool row, force-ends every open student session
    (release_reason 'admin_action'), cancels not-yet-started create_vm
    jobs, and enqueues a delete_vm job for every remaining instance —
    the worker destroys the OpenStack servers / floating IPs. Instances
    already being deleted keep their in-flight job.
    """
    row = await conn.fetchrow(
        "SELECT pool_id FROM desktop_pools WHERE pool_id = $1 AND deleted_at IS NULL",
        pool_id,
    )
    if row is None:
        return False

    pairs = []
    async with conn.transaction():
        await conn.execute(
            """
            UPDATE desktop_pools
            SET deleted_at = NOW(), status = 'deleted', updated_at = NOW()
            WHERE pool_id = $1
            """,
            pool_id,
        )
        # 1) Force-end sessions — the delete worker refuses instances
        #    that still hold an open assignment.
        await conn.execute(
            """
            UPDATE user_assignments
            SET released_at = NOW(), release_reason = 'admin_action',
                session_duration_seconds =
                    EXTRACT(EPOCH FROM (NOW() - assigned_at))::int
            WHERE pool_id = $1 AND released_at IS NULL
            """,
            pool_id,
        )
        # 2) Cancel create jobs that never started.
        await conn.execute(
            """
            UPDATE provisioning_jobs
            SET status = 'cancelled',
                error_message = 'pool deleted',
                completed_at = NOW()
            WHERE pool_id = $1 AND job_type = 'create_vm' AND status = 'queued'
            """,
            pool_id,
        )
        # 3) Destroy every remaining instance (worker skips instances
        #    without an OpenStack id gracefully).
        instances = await conn.fetch(
            """
            SELECT instance_id FROM desktop_instances
            WHERE pool_id = $1 AND status NOT IN ('deleted', 'deleting')
            """,
            pool_id,
        )
        for instance in instances:
            job_id = await conn.fetchval(
                """
                INSERT INTO provisioning_jobs (pool_id, instance_id, job_type)
                VALUES ($1, $2, 'delete_vm')
                RETURNING job_id
                """,
                pool_id,
                instance["instance_id"],
            )
            pairs.append((instance["instance_id"], job_id))

    for instance_id, job_id in pairs:
        app.send_task(
            "provisioning_service.services.job_worker.delete_vm_task",
            args=[str(instance_id), str(job_id)],
        )
    return True


async def get_pools_for_role(conn, role: str) -> list[PoolResponse]:
    rows = await conn.fetch(
        """
        SELECT * FROM desktop_pools
        WHERE deleted_at IS NULL
          AND status = 'active'
          AND $1::user_role = ANY(allowed_roles)
        ORDER BY created_at ASC
        """,
        role,
    )
    return [_row_to_pool_response(row) for row in rows]
