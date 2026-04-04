"""
OpenStack metrics collector for VDI.

Uses openstacksdk to gather:
- VM-level: per-instance status, counts by status (ACTIVE, SHUTOFF, ERROR).
- Host-level: compute node stats (VMs per host, capacity).
"""
import logging
from typing import Any

from prometheus_client import Gauge

logger = logging.getLogger(__name__)

# VM resource metrics (per VM)
vm_status = Gauge(
    "vdi_vm_status",
    "VM status: 1=ACTIVE, 0=SHUTOFF, -1=ERROR/other",
    ["vm_id", "vm_name", "host", "tenant_id"],
)
vm_count_by_status = Gauge(
    "vdi_vm_count_by_status",
    "Number of VMs in each Nova status",
    ["status"],
)
vm_total = Gauge("vdi_vm_total", "Total number of VMs reported by Nova")

# Host-level (admin view)
host_vm_count = Gauge(
    "vdi_host_vm_count",
    "Number of active VMs per compute host",
    ["host_id", "host_name"],
)
host_total = Gauge("vdi_host_total", "Total number of compute hosts")

# OpenStack collector health
openstack_collect_errors = Gauge(
    "vdi_openstack_collect_errors_total",
    "Total errors while collecting from OpenStack",
)


def _get_conn():
    from openstack import connection
    from config import (
        OS_AUTH_URL,
        OS_PROJECT_NAME,
        OS_USERNAME,
        OS_PASSWORD,
        OS_USER_DOMAIN_NAME,
        OS_PROJECT_DOMAIN_NAME,
    )
    return connection.Connection(
        auth_url=OS_AUTH_URL,
        project_name=OS_PROJECT_NAME,
        username=OS_USERNAME,
        password=OS_PASSWORD,
        user_domain_name=OS_USER_DOMAIN_NAME,
        project_domain_name=OS_PROJECT_DOMAIN_NAME,
    )


def _status_value(state: str) -> float:
    if state and state.upper() == "ACTIVE":
        return 1.0
    if state and state.upper() == "SHUTOFF":
        return 0.0
    return -1.0  # ERROR or other


def collect_openstack_metrics() -> None:
    try:
        conn = _get_conn()
    except Exception as e:
        logger.exception("OpenStack connection failed: %s", e)
        openstack_collect_errors.inc()
        return

    status_counts: dict[str, int] = {}
    seen_vms: set[tuple[str, str, str, str]] = set()

    try:
        # Servers (all projects if admin)
        for server in conn.compute.servers(details=True):
            state = (server.get("status") or "UNKNOWN").upper()
            status_counts[state] = status_counts.get(state, 0) + 1
            vm_id = server.get("id") or ""
            name = (server.get("name") or "unknown").replace('"', "'")
            host = (server.get("OS-EXT-SRV-ATTR:host") or "unknown").replace('"', "'")
            tenant = (server.get("tenant_id") or server.get("project_id") or "unknown")
            vm_status.labels(
                vm_id=vm_id,
                vm_name=name[:64],
                host=host[:64],
                tenant_id=tenant[:64],
            ).set(_status_value(server.get("status")))
            seen_vms.add((vm_id, name, host, tenant))

        vm_total.set(len(seen_vms))
        for st, count in status_counts.items():
            vm_count_by_status.labels(status=st).set(count)

        # Hypervisors (host-level)
        host_ids: set[str] = set()
        for hyp in conn.compute.hypervisors(details=True):
            hid = hyp.get("id") or ""
            name = (hyp.get("hypervisor_hostname") or "unknown").replace('"', "'")
            host_ids.add(str(hid))
            running = hyp.get("running_vms") or 0
            host_vm_count.labels(host_id=str(hid), host_name=name[:64]).set(running)

        host_total.set(len(host_ids))

    except Exception as e:
        logger.exception("OpenStack collect failed: %s", e)
        openstack_collect_errors.inc()
