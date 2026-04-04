"""
Database-backed metrics for VDI monitoring.

Covers pool/course-level aggregation and session-related counts from
desktop_instances, desktop_pools, user_assignments, pool_metrics.
"""
import logging
from contextlib import contextmanager
from typing import Generator

from prometheus_client import Gauge

logger = logging.getLogger(__name__)

# Pool / course-level aggregation
pool_vm_total = Gauge(
    "vdi_pool_vm_total",
    "Total VMs in pool",
    ["pool_id", "pool_name"],
)
pool_vm_active = Gauge(
    "vdi_pool_vm_active",
    "Active (ready/in_use) VMs in pool",
    ["pool_id", "pool_name"],
)
pool_vm_by_status = Gauge(
    "vdi_pool_vm_by_status",
    "VMs in pool by instance status",
    ["pool_id", "pool_name", "status"],
)
pool_utilization_percent = Gauge(
    "vdi_pool_utilization_percent",
    "Pool utilization percentage",
    ["pool_id", "pool_name"],
)
pool_active_users = Gauge(
    "vdi_pool_active_users",
    "Active users in pool",
    ["pool_id", "pool_name"],
)

# Instance status counts (global from DB)
db_instance_count_by_status = Gauge(
    "vdi_db_instance_count_by_status",
    "Desktop instance count by status from DB",
    ["status"],
)
db_instance_total = Gauge("vdi_db_instance_total", "Total desktop instances in DB")

# Active sessions (session-level placeholder; Guacamole can augment later)
active_sessions_total = Gauge(
    "vdi_active_sessions_total",
    "Total active desktop sessions (released_at IS NULL)",
)

# Collector health
db_collect_errors = Gauge(
    "vdi_db_collect_errors_total",
    "Total errors while collecting from DB",
)


@contextmanager
def get_db_connection():
    import psycopg2
    from config import get_db_connection_string
    conn = None
    try:
        conn = psycopg2.connect(get_db_connection_string())
        yield conn
    finally:
        if conn:
            conn.close()


def collect_db_metrics() -> None:
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Pool-level (course-level) aggregation
                cur.execute("""
                    SELECT
                        p.pool_id::text,
                        p.name,
                        COUNT(di.instance_id) AS total,
                        COUNT(CASE WHEN di.status IN ('ready', 'in_use') THEN 1 END) AS active,
                        COUNT(CASE WHEN di.status = 'provisioning' THEN 1 END) AS provisioning,
                        COUNT(CASE WHEN di.status = 'ready' THEN 1 END) AS ready,
                        COUNT(CASE WHEN di.status = 'in_use' THEN 1 END) AS in_use,
                        COUNT(CASE WHEN di.status = 'stopped' THEN 1 END) AS stopped,
                        COUNT(CASE WHEN di.status = 'error' THEN 1 END) AS error,
                        COUNT(CASE WHEN di.status = 'deleted' THEN 1 END) AS deleted
                    FROM desktop_pools p
                    LEFT JOIN desktop_instances di ON p.pool_id = di.pool_id
                    WHERE p.deleted_at IS NULL
                    GROUP BY p.pool_id, p.name
                """)
                for row in cur.fetchall():
                    pool_id, name = row[0][:64], (row[1] or "unknown")[:64]
                    total, active = row[2] or 0, row[3] or 0
                    pool_vm_total.labels(pool_id=pool_id, pool_name=name).set(total)
                    pool_vm_active.labels(pool_id=pool_id, pool_name=name).set(active)
                    for idx, status in enumerate(
                        ["provisioning", "ready", "in_use", "stopped", "error", "deleted"]
                    ):
                        val = row[4 + idx] if 4 + idx < len(row) else 0
                        pool_vm_by_status.labels(
                            pool_id=pool_id, pool_name=name, status=status
                        ).set(val or 0)
                    util = (100.0 * active / total) if total else 0
                    pool_utilization_percent.labels(
                        pool_id=pool_id, pool_name=name
                    ).set(round(util, 2))

                # Active users per pool
                cur.execute("""
                    SELECT p.pool_id::text, p.name,
                           COUNT(DISTINCT ua.user_id) AS active_users
                    FROM desktop_pools p
                    LEFT JOIN user_assignments ua
                        ON ua.pool_id = p.pool_id AND ua.released_at IS NULL
                    WHERE p.deleted_at IS NULL
                    GROUP BY p.pool_id, p.name
                """)
                for row in cur.fetchall():
                    pool_id, name = row[0][:64], (row[1] or "unknown")[:64]
                    pool_active_users.labels(pool_id=pool_id, pool_name=name).set(
                        row[2] or 0
                    )

                # Global instance counts by status
                cur.execute("""
                    SELECT status, COUNT(*) FROM desktop_instances
                    GROUP BY status
                """)
                total_instances = 0
                for row in cur.fetchall():
                    status = (row[0] or "unknown").lower()
                    count = row[1] or 0
                    total_instances += count
                    db_instance_count_by_status.labels(status=status).set(count)
                db_instance_total.set(total_instances)

                # Active sessions total
                cur.execute(
                    "SELECT COUNT(*) FROM user_assignments WHERE released_at IS NULL"
                )
                active_sessions_total.set(cur.fetchone()[0] or 0)

    except Exception as e:
        logger.exception("DB collect failed: %s", e)
        db_collect_errors.inc()
