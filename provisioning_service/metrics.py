"""Domain metrics for the provisioning service.

These describe the VDI system itself — how many VMs exist and in what state,
how full each pool is, how many students are connected — as opposed to the
HTTP/process metrics that prometheus-fastapi-instrumentator already exports.

Why a refresh loop rather than a custom Collector: prometheus_client calls
collect() synchronously, and every value here comes from asyncpg. Instead of
blocking a scrape on the database (and hammering it once per scraper), a
single background task refreshes the gauges on a fixed interval and /metrics
serves whatever was last read. The staleness is bounded by REFRESH_SECONDS
and published as vdi_metrics_last_refresh_timestamp_seconds so a dashboard
can tell "zero VMs" apart from "the refresher died".

Every gauge is zero-filled across the full label space, so a pool with no
failed VMs reports vdi_instances{status="error"} 0 rather than no series at
all — panels and alerts then show 0 instead of NO DATA.
"""

from __future__ import annotations

import asyncio
import logging
import time

from prometheus_client import Counter, Gauge

logger = logging.getLogger(__name__)

REFRESH_SECONDS = 15

# Mirrors the instance_status enum in database/init.sql. Listed explicitly so
# the zero-fill covers states that have never occurred.
INSTANCE_STATUSES = (
    "provisioning", "ready", "assigned", "in_use",
    "stopping", "stopped", "error", "deleting", "deleted",
)
JOB_STATUSES = ("queued", "processing", "completed", "failed", "cancelled")
CODE_STATES = ("available", "redeemed", "revoked")


vdi_instances = Gauge(
    "vdi_instances",
    "Desktop instances by pool and lifecycle status",
    ["pool", "status"],
)
vdi_pool_capacity = Gauge(
    "vdi_pool_capacity",
    "Configured pool sizing (bound = min_vms or max_vms)",
    ["pool", "bound"],
)
vdi_pool_usable = Gauge(
    "vdi_pool_usable",
    "Instances in a pool that are not deleted/errored — what counts against max_vms",
    ["pool"],
)
vdi_active_assignments = Gauge(
    "vdi_active_assignments",
    "Assignments with no released_at — students currently holding a VM",
    ["pool"],
)
vdi_jobs = Gauge(
    "vdi_jobs",
    "Provisioning jobs by status",
    ["status"],
)
vdi_access_codes = Gauge(
    "vdi_access_codes",
    "Class-pool access codes by pool and state",
    ["pool", "state"],
)
vdi_pools_total = Gauge(
    "vdi_pools_total",
    "Pools that are active and not soft-deleted",
)

vdi_metrics_last_refresh = Gauge(
    "vdi_metrics_last_refresh_timestamp_seconds",
    "Unix time of the last successful domain-metrics refresh",
)
vdi_metrics_refresh_failures = Counter(
    "vdi_metrics_refresh_failures_total",
    "Refresh cycles that raised before completing",
)


async def _refresh_once(pool) -> None:
    async with pool.acquire() as conn:
        pools = await conn.fetch(
            """
            SELECT pool_id, name, min_vms, max_vms
            FROM desktop_pools
            WHERE deleted_at IS NULL AND status = 'active'
            """
        )
        instances = await conn.fetch(
            """
            SELECT p.name AS pool, i.status::text AS status, COUNT(*) AS n
            FROM desktop_instances i
            JOIN desktop_pools p ON p.pool_id = i.pool_id
            WHERE p.deleted_at IS NULL
            GROUP BY p.name, i.status
            """
        )
        assignments = await conn.fetch(
            """
            SELECT p.name AS pool, COUNT(*) AS n
            FROM user_assignments a
            JOIN desktop_pools p ON p.pool_id = a.pool_id
            WHERE a.released_at IS NULL
            GROUP BY p.name
            """
        )
        jobs = await conn.fetch(
            "SELECT status::text AS status, COUNT(*) AS n FROM provisioning_jobs GROUP BY status"
        )
        codes = await conn.fetch(
            """
            SELECT p.name AS pool,
                   CASE
                       WHEN c.revoked_at  IS NOT NULL THEN 'revoked'
                       WHEN c.redeemed_at IS NOT NULL THEN 'redeemed'
                       ELSE 'available'
                   END AS state,
                   COUNT(*) AS n
            FROM pool_access_codes c
            JOIN desktop_pools p ON p.pool_id = c.pool_id
            WHERE p.deleted_at IS NULL
            GROUP BY p.name, state
            """
        )

    pool_names = [r["name"] for r in pools]
    vdi_pools_total.set(len(pool_names))

    # Zero-fill first so states that disappeared since the last cycle drop
    # back to 0 instead of holding their old value forever.
    for name in pool_names:
        for status in INSTANCE_STATUSES:
            vdi_instances.labels(pool=name, status=status).set(0)
        for state in CODE_STATES:
            vdi_access_codes.labels(pool=name, state=state).set(0)
        vdi_active_assignments.labels(pool=name).set(0)
        vdi_pool_usable.labels(pool=name).set(0)
    for status in JOB_STATUSES:
        vdi_jobs.labels(status=status).set(0)

    for r in pools:
        vdi_pool_capacity.labels(pool=r["name"], bound="min").set(r["min_vms"])
        vdi_pool_capacity.labels(pool=r["name"], bound="max").set(r["max_vms"])

    usable: dict[str, int] = {}
    for r in instances:
        vdi_instances.labels(pool=r["pool"], status=r["status"]).set(r["n"])
        if r["status"] not in ("deleted", "error"):
            usable[r["pool"]] = usable.get(r["pool"], 0) + r["n"]
    for name, n in usable.items():
        vdi_pool_usable.labels(pool=name).set(n)

    for r in assignments:
        vdi_active_assignments.labels(pool=r["pool"]).set(r["n"])
    for r in jobs:
        vdi_jobs.labels(status=r["status"]).set(r["n"])
    for r in codes:
        vdi_access_codes.labels(pool=r["pool"], state=r["state"]).set(r["n"])

    vdi_metrics_last_refresh.set(time.time())


async def refresh_loop(pool) -> None:
    """Refresh every REFRESH_SECONDS until cancelled.

    A failing cycle is logged and retried rather than killing the task: a
    transient database blip should not silently freeze every gauge for the
    lifetime of the process.
    """
    logger.info("domain metrics refresher started (every %ds)", REFRESH_SECONDS)
    while True:
        try:
            await _refresh_once(pool)
        except asyncio.CancelledError:
            logger.info("domain metrics refresher stopped")
            raise
        except Exception:
            vdi_metrics_refresh_failures.inc()
            logger.exception("domain metrics refresh failed")
        await asyncio.sleep(REFRESH_SECONDS)
