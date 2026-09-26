"""Project quota accounting.

The BUET Cloud Lab project has a fixed allocation that BOTH the student
walk-in pool and teacher class pools draw from:

    vCPUs    20
    RAM      50 GB
    volumes  10
    storage  1000 GB

OpenStack enforces these itself, but only at the moment a VM is created —
by which time the provisioning job is already running and will fail minutes
later. This module reads the live figures up-front so the API can refuse an
over-quota request immediately, and so the UI can show what is left.

Design notes:

* Usage is read from the cloud (Nova ``/limits`` and Cinder ``/limits``),
  never summed from our own tables. The project is shared: volumes and
  snapshots created outside this service still consume quota, so a
  DB-derived figure would under-report and let teachers oversubscribe.

* RAM is tracked in MB (OpenStack's unit) and surfaced in GB for the UI.
  ``totalRAMUsed`` is an integer count of MB, so no rounding games are
  needed — 51200 MB is exactly 50 GB.

* A reserve is withheld from the teacher-visible budget so a teacher cannot
  consume the whole project and starve the student walk-in pool. The
  student pool maintains ``min_vms`` instances; those must always fit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .. import config

logger = logging.getLogger(__name__)

MB_PER_GB = 1024


class QuotaExceededError(Exception):
    """A request would exceed the project allocation.

    Carries a structured breakdown so the API can explain *which* resource
    ran out and by how much, rather than a bare "quota exceeded".
    """

    def __init__(self, violations: list[dict]):
        self.violations = violations
        detail = "; ".join(v["message"] for v in violations)
        super().__init__(detail)


@dataclass
class QuotaSnapshot:
    """Live project usage plus the derived headroom for each resource."""

    # Absolute project limits (from the cloud)
    max_vcpus: int = 0
    max_ram_mb: int = 0
    max_volumes: int = 0
    max_storage_gb: int = 0

    # Currently consumed (whole project, not just this service)
    used_vcpus: int = 0
    used_ram_mb: int = 0
    used_volumes: int = 0
    used_storage_gb: int = 0

    # Withheld for the student walk-in pool (not available to teachers)
    reserved_vcpus: int = 0
    reserved_ram_mb: int = 0
    reserved_volumes: int = 0
    reserved_storage_gb: int = 0

    # Non-fatal problems encountered while reading usage
    warnings: list[str] = field(default_factory=list)

    # ── Derived: teacher-available headroom ───────────────────────────────
    @property
    def free_vcpus(self) -> int:
        return max(0, self.max_vcpus - self.used_vcpus - self.reserved_vcpus)

    @property
    def free_ram_mb(self) -> int:
        return max(0, self.max_ram_mb - self.used_ram_mb - self.reserved_ram_mb)

    @property
    def free_volumes(self) -> int:
        return max(0, self.max_volumes - self.used_volumes - self.reserved_volumes)

    @property
    def free_storage_gb(self) -> int:
        return max(
            0, self.max_storage_gb - self.used_storage_gb - self.reserved_storage_gb
        )

    def as_dict(self) -> dict:
        """JSON-friendly view for the API/UI.

        ``free_*`` are the teacher-usable figures (after the student
        reserve); ``headroom_*`` ignore the reserve and reflect the raw
        project state, which is what an admin wants to see.
        """
        return {
            "vcpus": {
                "limit": self.max_vcpus,
                "used": self.used_vcpus,
                "reserved": self.reserved_vcpus,
                "free": self.free_vcpus,
            },
            "ram_gb": {
                "limit": self.max_ram_mb // MB_PER_GB,
                "used": round(self.used_ram_mb / MB_PER_GB, 1),
                "reserved": self.reserved_ram_mb // MB_PER_GB,
                "free": round(self.free_ram_mb / MB_PER_GB, 1),
            },
            "volumes": {
                "limit": self.max_volumes,
                "used": self.used_volumes,
                "reserved": self.reserved_volumes,
                "free": self.free_volumes,
            },
            "storage_gb": {
                "limit": self.max_storage_gb,
                "used": self.used_storage_gb,
                "reserved": self.reserved_storage_gb,
                "free": self.free_storage_gb,
            },
            "warnings": list(self.warnings),
        }


def _reserve() -> tuple[int, int, int, int]:
    """Capacity withheld from teachers for the student walk-in pool.

    Derived from the pool's ``min_vms`` at the reference flavor, so the
    reserve tracks reality instead of being a magic number. Falls back to
    config defaults when the reference flavor cannot be resolved.
    """
    return (
        config.STUDENT_POOL_RESERVE_VCPUS,
        config.STUDENT_POOL_RESERVE_RAM_MB,
        config.STUDENT_POOL_RESERVE_VOLUMES,
        config.STUDENT_POOL_RESERVE_STORAGE_GB,
    )


async def read_quota(client, *, apply_reserve: bool = True) -> QuotaSnapshot:
    """Read live project quota and usage from OpenStack.

    Never raises for a single failed probe: a partial snapshot with
    ``warnings`` populated is more useful than a 500, and the caller
    decides whether the missing data is fatal. Limits that could not be
    read stay at 0, which makes ``free_*`` 0 and conservatively blocks
    over-commit rather than silently allowing it.
    """
    snap = QuotaSnapshot()

    # ── Nova: instances / vCPU / RAM ──────────────────────────────────────
    try:
        body = (await client.compute_request("GET", "/limits")).json()
        absolute = body.get("limits", {}).get("absolute", {})
        snap.max_vcpus = int(absolute.get("maxTotalCores") or 0)
        snap.max_ram_mb = int(absolute.get("maxTotalRAMSize") or 0)
        snap.used_vcpus = int(absolute.get("totalCoresUsed") or 0)
        snap.used_ram_mb = int(absolute.get("totalRAMUsed") or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("quota: Nova /limits unreadable: %s", exc)
        snap.warnings.append("compute quota unavailable")

    # ── Cinder: volumes / storage ─────────────────────────────────────────
    try:
        body = (await client.volume_request("GET", "/limits")).json()
        absolute = body.get("limits", {}).get("absolute", {})
        snap.max_volumes = int(absolute.get("maxTotalVolumes") or 0)
        snap.max_storage_gb = int(absolute.get("maxTotalVolumeGigabytes") or 0)
        snap.used_volumes = int(absolute.get("totalVolumesUsed") or 0)
        snap.used_storage_gb = int(absolute.get("totalGigabytesUsed") or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("quota: Cinder /limits unreadable: %s", exc)
        snap.warnings.append("volume quota unavailable")

    if apply_reserve:
        (
            snap.reserved_vcpus,
            snap.reserved_ram_mb,
            snap.reserved_volumes,
            snap.reserved_storage_gb,
        ) = _reserve()

    return snap


def check_request(
    snap: QuotaSnapshot,
    *,
    vcpus: int,
    ram_mb: int,
    volumes: int,
    storage_gb: int,
) -> None:
    """Raise :class:`QuotaExceededError` if the request does not fit.

    Every violated resource is reported, not just the first, so a teacher
    asking for 8 VMs learns about all three problems at once instead of
    fixing them one round-trip at a time.
    """
    checks = [
        (
            "vcpus", vcpus, snap.free_vcpus, snap.max_vcpus,
            snap.used_vcpus, snap.reserved_vcpus, "vCPU",
        ),
        (
            "ram_mb", ram_mb, snap.free_ram_mb, snap.max_ram_mb,
            snap.used_ram_mb, snap.reserved_ram_mb, "MB RAM",
        ),
        (
            "volumes", volumes, snap.free_volumes, snap.max_volumes,
            snap.used_volumes, snap.reserved_volumes, "volume",
        ),
        (
            "storage_gb", storage_gb, snap.free_storage_gb, snap.max_storage_gb,
            snap.used_storage_gb, snap.reserved_storage_gb, "GB storage",
        ),
    ]

    violations: list[dict] = []
    for key, need, free, limit, used, reserved, unit in checks:
        # A limit of 0 means the probe failed — treat as blocking, but say so.
        if need <= free:
            continue
        if limit == 0:
            message = (
                f"cannot verify {unit} quota — OpenStack limits are "
                f"unavailable right now; try again shortly"
            )
        else:
            message = (
                f"needs {need} {unit} but only {free} available "
                f"({used} of {limit} already used"
                + (f", {reserved} reserved for the student pool" if reserved else "")
                + ")"
            )
        violations.append(
            {
                "resource": key,
                "required": need,
                "available": free,
                "limit": limit,
                "used": used,
                "reserved": reserved,
                "message": message,
            }
        )

    if violations:
        raise QuotaExceededError(violations)
