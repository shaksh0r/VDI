from __future__ import annotations

import asyncio
import logging

from ..db import create_database_pool
from ..message_queue.celery_app import app
from ..services.pool_service import insert_create_jobs
from ..services.vm_service import release_vm

logger = logging.getLogger(__name__)


@app.task
def expire_sessions_task():
    asyncio.run(_expire_sessions_async())


async def _expire_sessions_async() -> None:
    pool = None
    try:
        pool = await create_database_pool(min_size=1, max_size=2)
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT ua.user_id
                FROM user_assignments ua
                JOIN desktop_pools p ON p.pool_id = ua.pool_id
                WHERE ua.released_at IS NULL
                  AND (ua.assigned_at
                       + p.max_session_duration_minutes * interval '1 minute')
                      < NOW()
                """
            )
            for row in rows:
                await release_vm(conn, row["user_id"], "session_expired")

            # ── Heal orphaned 'in_use' instances ──────────────────────────
            # An instance can remain 'in_use' with no owner when its
            # assignment row disappears without a release (e.g. the owning
            # user row was hard-deleted and user_assignments cascaded away).
            # Such an instance is unclaimable and occupies a pool slot
            # forever (replenish is capped by current_count). Treat it like
            # an abandoned session: destroy non-persistent VMs (normal
            # release semantics), return persistent ones to ready.
            orphans = await conn.fetch(
                """
                SELECT i.instance_id, i.pool_id, p.desktop_type
                FROM desktop_instances i
                JOIN desktop_pools p ON p.pool_id = i.pool_id
                WHERE i.status = 'in_use'
                  AND i.assigned_user_id IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM user_assignments ua
                      WHERE ua.instance_id = i.instance_id
                        AND ua.released_at IS NULL
                  )
                """
            )
            for orphan in orphans:
                if orphan["desktop_type"] == "non_persistent":
                    job_id = await conn.fetchval(
                        """
                        INSERT INTO provisioning_jobs (pool_id, instance_id, job_type)
                        VALUES ($1, $2, 'delete_vm')
                        RETURNING job_id
                        """,
                        orphan["pool_id"], orphan["instance_id"],
                    )
                    app.send_task(
                        "provisioning_service.services.job_worker.delete_vm_task",
                        args=[str(orphan["instance_id"]), str(job_id)],
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE desktop_instances
                        SET status = 'ready', assigned_user_id = NULL,
                            assigned_at = NULL, updated_at = NOW()
                        WHERE instance_id = $1
                        """,
                        orphan["instance_id"],
                    )
                logger.info(
                    "healed orphaned in_use instance %s (type=%s)",
                    orphan["instance_id"], orphan["desktop_type"],
                )
    finally:
        if pool is not None:
            await pool.close()


@app.task
def replenish_pools_task():
    asyncio.run(_replenish_pools_async())


async def _replenish_pools_async() -> None:
    pool = None
    try:
        pool = await create_database_pool(min_size=1, max_size=2)
        async with pool.acquire() as conn:
            pools = await conn.fetch(
                """
                SELECT * FROM desktop_pools
                WHERE deleted_at IS NULL AND status = 'active'
                """
            )
        for pool_row in pools:
            try:
                async with pool.acquire() as conn:
                    await _replenish_one(conn, pool_row)
            except Exception:
                logger.exception(
                    "replenish failed for pool %s", pool_row["pool_id"]
                )
    finally:
        if pool is not None:
            await pool.close()


async def _replenish_one(conn, pool_row) -> None:
    pool_id = pool_row["pool_id"]
    counts = await conn.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE status = 'ready') AS ready_count,
            COUNT(*) FILTER (WHERE status = 'provisioning')
                AS provisioning_count
        FROM desktop_instances
        WHERE pool_id = $1
        """,
        pool_id,
    )
    ready_count = counts["ready_count"]
    provisioning_count = counts["provisioning_count"]
    current_count = pool_row["current_count"]
    min_vms = pool_row["min_vms"]
    max_vms = pool_row["max_vms"]

    deficit = min_vms - (ready_count + provisioning_count)
    if deficit > 0:
        to_create = min(deficit, max_vms - current_count)
        if to_create > 0:
            job_ids = await insert_create_jobs(conn, pool_id, to_create)
            for job_id in job_ids:
                app.send_task(
                    "provisioning_service.services.job_worker.create_vm_task",
                    args=[str(pool_id), str(job_id)],
                )

    if pool_row["desktop_type"] == "non_persistent" and ready_count > min_vms:
        excess = ready_count - min_vms
        idle = await conn.fetch(
            """
            SELECT instance_id FROM desktop_instances
            WHERE pool_id = $1
              AND status = 'ready'
              AND assigned_user_id IS NULL
            ORDER BY last_accessed_at ASC NULLS FIRST
            LIMIT $2
            """,
            pool_id,
            excess,
        )
        for instance in idle:
            job_id = await conn.fetchval(
                """
                INSERT INTO provisioning_jobs (pool_id, instance_id, job_type)
                VALUES ($1, $2, 'delete_vm')
                RETURNING job_id
                """,
                pool_id,
                instance["instance_id"],
            )
            app.send_task(
                "provisioning_service.services.job_worker.delete_vm_task",
                args=[str(instance["instance_id"]), str(job_id)],
            )
