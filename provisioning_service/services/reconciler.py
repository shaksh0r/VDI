from __future__ import annotations

import asyncio
import logging

from .. import config
from ..db import create_database_pool
from ..message_queue.celery_app import app
from ..openstack import OpenStackClient, cinder, neutron, nova
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


# ── Orphan VM reconciliation ──────────────────────────────────────────────────
# `docker compose down -v` (or any DB reset) discards the only record of the
# VMs this service created: desktop_instances.openstack_vm_id. OpenStack has no
# idea the database was wiped, so those servers keep running and burning quota.
# Worse, the fresh DB reports zero ready VMs to replenish_pools_task, which
# provisions min_vms *more* — so every reset leaks a full pool.
#
# This task closes that loop: list every Nova server this service owns and
# destroy any that the database no longer tracks.
#
# A server is considered OURS when either:
#   * it carries metadata managed_by=provisioning_service (set by create_vm_task
#     at provisioning time — authoritative, and how new VMs are flagged), or
#   * its name matches the `vdi-<uuid8>` scheme used before metadata existed.
# Anything not owned by this service (other tenants' servers, manually created
# VMs, unrelated instances sharing the project) is NEVER touched.
#
# Deletion order matters: floating IP first, then server, then any boot volume
# left behind — mirroring delete_vm_task, because some clouds keep the volume
# even when delete_on_termination=True.
MANAGED_BY_TAG = "provisioning_service"
VM_NAME_PREFIX = "vdi-"


def _is_managed_server(server: dict) -> bool:
    """True when this Nova server belongs to the provisioning service."""
    metadata = server.get("metadata") or {}
    if metadata.get("managed_by") == MANAGED_BY_TAG:
        return True
    # Legacy fallback: VMs created before metadata tagging used this scheme.
    return str(server.get("name", "")).startswith(VM_NAME_PREFIX)


@app.task
def reconcile_instances_task():
    asyncio.run(_reconcile_instances_async())


async def _reconcile_instances_async() -> None:
    db_pool = None
    client = None
    try:
        db_pool = await create_database_pool(min_size=1, max_size=2)
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT openstack_vm_id FROM desktop_instances
                WHERE openstack_vm_id IS NOT NULL AND status <> 'deleted'
                """
            )
        tracked = {r["openstack_vm_id"] for r in rows}

        client = _reconciler_os_client()
        servers = (await nova.list_servers_detail(client)).get("servers", [])
        orphans = [
            s for s in servers
            if _is_managed_server(s) and s.get("id") not in tracked
        ]
        if not orphans:
            logger.debug(
                "reconcile: %d managed server(s), 0 orphans", len(tracked)
            )
            return

        # Map port -> attached floating IPs so orphans' FIPs are freed too.
        ports = (await neutron.list_ports(client)).get("ports", [])
        ports_by_device: dict[str, list[str]] = {}
        for port in ports:
            device = port.get("device_id")
            if device:
                ports_by_device.setdefault(device, []).append(port["id"])
        fips = (await neutron.list_floating_ips(client)).get("floatingips", [])

        for server in orphans:
            vm_id = server["id"]
            name = server.get("name", vm_id)
            port_ids = ports_by_device.get(vm_id, [])

            for fip in fips:
                if fip.get("port_id") not in port_ids:
                    continue
                try:
                    await neutron.delete_floating_ip(client, fip["id"])
                    logger.info(
                        "reconcile: released orphan FIP %s (%s)",
                        fip.get("floating_ip_address"), fip["id"],
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "reconcile: FIP %s delete failed: %s", fip["id"], exc
                    )

            try:
                await nova.delete_server(client, vm_id)
                logger.info(
                    "reconcile: destroyed orphan VM %s (%s) — no DB row",
                    name, vm_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "reconcile: server %s delete failed: %s", vm_id, exc
                )
                continue

            try:
                freed = await cinder.delete_volumes_for_server(client, vm_id)
                if freed:
                    logger.info(
                        "reconcile: freed %d orphan volume(s) for %s",
                        freed, name,
                    )
            except Exception as exc:  # noqa: BLE001 — never abort the sweep
                logger.warning(
                    "reconcile: volume cleanup for %s failed: %s", vm_id, exc
                )
    except Exception:
        logger.exception("reconcile_instances failed")
    finally:
        if client is not None:
            await client.close()
        if db_pool is not None:
            await db_pool.close()


def _reconciler_os_client() -> OpenStackClient:
    """A Celery-side OpenStack client (same config as job_worker's)."""
    return OpenStackClient(
        auth_url=config.OPENSTACK_AUTH_URL,
        username=config.OPENSTACK_USERNAME,
        password=config.OPENSTACK_PASSWORD,
        project_name=config.OPENSTACK_PROJECT_NAME,
        user_domain=config.OPENSTACK_USER_DOMAIN_NAME,
        project_domain=config.OPENSTACK_PROJECT_DOMAIN_NAME,
        compute_url=config.OPENSTACK_COMPUTE_URL,
        network_url=config.OPENSTACK_NETWORK_URL,
        image_url=config.OPENSTACK_IMAGE_URL,
        volume_url=config.OPENSTACK_VOLUME_URL,
        timeout=config.OPENSTACK_REQUEST_TIMEOUT,
    )
