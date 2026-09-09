"""Teacher (faculty) class-pool endpoints.

Part 1 of the teacher flow:
  * GET  /teacher/pool-template — the FIXED compute spec (image, flavor,
    network, vcpu/ram/disk) shown read-only to teachers. Everything is
    decided server-side; the browser can neither see nor send it.
  * POST /teacher/pools        — create a class pool for N VMs. The pool is
    desktop_type 'persistent' + access_mode 'code': instances live until
    the teacher tears the class down and are claimable only via access
    codes (redemption lands in Part 3).
  * GET  /teacher/pools        — pools owned by the calling user (never
    other teachers' pools), with live instance counts.

Part 2 of the teacher flow (access codes, one per VM seat):
  * GET  /teacher/pools/{id}/codes  — codes + redemption state
  * POST /teacher/pools/{id}/codes  — generate the codes still missing to
    reach capacity (max_vms); never duplicates or clobbers existing ones.
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request

from .. import config
from ..message_queue.celery_app import app
from ..models.requests import PoolCreateRequest, TeacherPoolCreateRequest
from ..models.responses import (
    PoolResponse,
    TeacherCodeEntry,
    TeacherCodeGenerateResponse,
    TeacherCodeListResponse,
    TeacherPoolListResponse,
    TeacherPoolSummary,
    TeacherTemplateResponse,
)
from ..openstack import nova
from ..services import pool_service
from .deps import get_db, require_roles

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/teacher", tags=["teachers"])

staff = require_roles("admin", "faculty")

# Codes: 8 chars from an unambiguous alphabet (no 0/O/1/I/L) →
# 32^8 ≈ 1.1e12 combinations; DB uniqueness guarantees no duplicates.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 8
_CODE_MAX_ATTEMPTS = 25

# Template resolution cache (flavor spec fetched from Nova): key -> (flavor_id, ts, spec)
_TEMPLATE_CACHE_TTL_SECONDS = 120
_template_cache: dict[str, Any] = {}


async def _resolve_template(conn) -> dict:
    """Image / flavor / network for class pools.

    Explicit env overrides (TEACHER_IMAGE_ID/FLAVOR_ID/NETWORK_ID) win;
    otherwise mirror the reference pool (student-pool by default) so class
    VMs use the same stack configuration as walk-in lab VMs.
    """
    base = {
        "image_id": config.TEACHER_IMAGE_ID.strip(),
        "flavor_id": config.TEACHER_FLAVOR_ID.strip(),
        "network_id": config.TEACHER_NETWORK_ID.strip(),
    }
    missing = [key for key, value in base.items() if not value]
    if missing:
        row = await conn.fetchrow(
            """
            SELECT base_image_id, flavor_id, network_id
            FROM desktop_pools
            WHERE name = $1 AND deleted_at IS NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            config.TEACHER_REFERENCE_POOL,
        )
        if row is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "class-pool template not configured: reference pool "
                    f"'{config.TEACHER_REFERENCE_POOL}' not found and no "
                    "TEACHER_* env overrides are set"
                ),
            )
        column_for = {
            "image_id": "base_image_id",
            "flavor_id": "flavor_id",
            "network_id": "network_id",
        }
        for key in missing:
            base[key] = str(row[column_for[key]])
    return base


async def _flavor_spec(request: Request, flavor_id: str) -> dict:
    """vcpus/ram/disk for the template flavor, cached briefly.

    Falls back to config constants if Nova is unreachable — the displayed
    values are informational only (the flavor id itself is the source of
    truth for creation).
    """
    cache = _template_cache.get("flavor")
    now = time.time()
    if (
        cache
        and cache["flavor_id"] == flavor_id
        and now - cache["ts"] < _TEMPLATE_CACHE_TTL_SECONDS
    ):
        return cache["spec"]

    spec = {
        "vcpus": config.TEACHER_FALLBACK_VCPUS,
        "ram_mb": config.TEACHER_FALLBACK_RAM_MB,
        "disk_gb": config.TEACHER_FALLBACK_DISK_GB,
    }
    try:
        response = await nova.get_flavor(request.app.state.openstack, flavor_id)
        body = response.get("flavor", response)
        if isinstance(body, dict):
            if body.get("vcpus") is not None:
                spec["vcpus"] = int(body["vcpus"])
            if body.get("ram") is not None:
                spec["ram_mb"] = int(body["ram"])
            if body.get("disk") is not None:
                spec["disk_gb"] = int(body["disk"])
    except Exception as exc:  # noqa: BLE001 — OpenStack flake must not break the UI
        logger.warning("flavor %s lookup failed, using fallback spec: %s", flavor_id, exc)

    _template_cache["flavor"] = {"flavor_id": flavor_id, "ts": now, "spec": spec}
    return spec


@router.get("/pool-template", response_model=TeacherTemplateResponse)
async def pool_template(
    request: Request,
    user: dict = Depends(staff),
    conn=Depends(get_db),
):
    tpl = await _resolve_template(conn)
    spec = await _flavor_spec(request, tpl["flavor_id"])
    return TeacherTemplateResponse(
        desktop_type="persistent",
        access_mode="code",
        image_id=tpl["image_id"],
        flavor_id=tpl["flavor_id"],
        network_id=tpl["network_id"],
        security_group=config.VM_SECURITY_GROUP,
        vcpus=spec["vcpus"],
        ram_mb=spec["ram_mb"],
        disk_gb=spec["disk_gb"],
        max_session_minutes=config.TEACHER_MAX_SESSION_MINUTES,
    )


@router.post("/pools", response_model=PoolResponse, status_code=201)
async def create_class_pool(
    data: TeacherPoolCreateRequest,
    user: dict = Depends(staff),
    conn=Depends(get_db),
):
    if data.vm_count > config.TEACHER_VM_COUNT_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=f"vm_count cannot exceed {config.TEACHER_VM_COUNT_LIMIT}",
        )

    tpl = await _resolve_template(conn)
    pool_request = PoolCreateRequest(
        name=data.name,
        desktop_type="persistent",
        access_mode="code",
        image_id=tpl["image_id"],
        flavor_id=tpl["flavor_id"],
        network_id=tpl["network_id"],
        min_vms=0,                 # no auto-replenish — teacher-managed lifecycle
        max_vms=data.vm_count,
        max_session_minutes=config.TEACHER_MAX_SESSION_MINUTES,
        auto_scaling_enabled=False,
        allowed_roles=["student"],
    )
    try:
        pool = await pool_service.create_pool(conn, user["user_id"], pool_request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # create_pool() enqueues min_vms (0) jobs; dispatch exactly vm_count.
    job_ids = await pool_service.insert_create_jobs(conn, pool.pool_id, data.vm_count)
    for job_id in job_ids:
        app.send_task(
            "provisioning_service.services.job_worker.create_vm_task",
            args=[str(pool.pool_id), str(job_id)],
        )

    logger.info(
        "teacher %s created class pool '%s' (%d VMs, %d jobs dispatched)",
        user.get("username"), data.name, data.vm_count, len(job_ids),
    )
    return pool


@router.get("/pools", response_model=TeacherPoolListResponse)
async def list_class_pools(
    user: dict = Depends(staff),
    conn=Depends(get_db),
):
    rows = await conn.fetch(
        """
        SELECT p.pool_id,
               p.name,
               p.status,
               p.desktop_type,
               p.access_mode,
               p.min_vms,
               p.max_vms,
               p.current_count,
               p.max_session_duration_minutes,
               p.created_at,
               (SELECT COUNT(*) FROM desktop_instances i
                 WHERE i.pool_id = p.pool_id AND i.status = 'ready')
                 AS ready_count,
               (SELECT COUNT(*) FROM desktop_instances i
                 WHERE i.pool_id = p.pool_id AND i.status = 'in_use')
                 AS in_use_count,
               (SELECT COUNT(*) FROM desktop_instances i
                 WHERE i.pool_id = p.pool_id AND i.status = 'provisioning')
                 AS provisioning_count,
               (SELECT COUNT(*) FROM desktop_instances i
                 WHERE i.pool_id = p.pool_id
                   AND i.status NOT IN ('deleted', 'error'))
                 AS total_instances
        FROM desktop_pools p
        WHERE p.deleted_at IS NULL
          AND p.access_mode = 'code'
          AND p.created_by = $1::uuid
        ORDER BY p.created_at DESC
        """,
        user["user_id"],
    )
    pools = [
        TeacherPoolSummary(
            pool_id=str(row["pool_id"]),
            name=row["name"],
            status=row["status"],
            desktop_type=row["desktop_type"],
            access_mode=row["access_mode"],
            min_vms=row["min_vms"],
            max_vms=row["max_vms"],
            current_count=row["current_count"],
            max_session_minutes=row["max_session_duration_minutes"],
            created_at=row["created_at"],
            ready_count=row["ready_count"],
            in_use_count=row["in_use_count"],
            provisioning_count=row["provisioning_count"],
            total_instances=row["total_instances"],
        )
        for row in rows
    ]
    return TeacherPoolListResponse(pools=pools, total=len(pools))


# ── Access codes (Part 2) ──────────────────────────────────────────────────────

async def _own_code_pool(conn, user: dict, pool_id: str) -> Any:
    """Load a code-gated pool the caller is allowed to manage.

    Owner (created_by == caller) or admin. Anything else — including an
    open pool or another teacher's pool — answers 404 so pool ids never
    leak to non-owners.
    """
    row = await conn.fetchrow(
        """
        SELECT pool_id, name, max_vms, access_mode, created_by, deleted_at
        FROM desktop_pools
        WHERE pool_id = $1
        """,
        pool_id,
    )
    if (
        row is None
        or row["deleted_at"] is not None
        or row["access_mode"] != "code"
    ):
        raise HTTPException(status_code=404, detail="pool not found")
    if str(row["created_by"]) != user["user_id"] and user["role"] != "admin":
        raise HTTPException(status_code=404, detail="pool not found")
    return row


def _new_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))


async def _insert_codes(conn, pool_id: str, count: int) -> list[str]:
    """Insert `count` unique codes; retries on the astronomically rare
    collision. Raises 409 if uniqueness cannot be satisfied."""
    created: list[str] = []
    async with conn.transaction():
        for _ in range(count):
            inserted = False
            for _attempt in range(_CODE_MAX_ATTEMPTS):
                candidate = _new_code()
                try:
                    await conn.execute(
                        """
                        INSERT INTO pool_access_codes (pool_id, code)
                        VALUES ($1, $2)
                        """,
                        pool_id,
                        candidate,
                    )
                except asyncpg.UniqueViolationError:
                    continue
                created.append(candidate)
                inserted = True
                break
            if not inserted:
                raise HTTPException(
                    status_code=409,
                    detail="could not generate a unique code after "
                    f"{_CODE_MAX_ATTEMPTS} attempts",
                )
    return created


@router.get("/pools/{pool_id}/codes", response_model=TeacherCodeListResponse)
async def list_codes(
    pool_id: str,
    user: dict = Depends(staff),
    conn=Depends(get_db),
):
    pool = await _own_code_pool(conn, user, pool_id)
    rows = await conn.fetch(
        """
        SELECT c.code, c.redeemed_at, c.revoked_at, c.created_at,
               u.username AS redeemed_username
        FROM pool_access_codes c
        LEFT JOIN users u ON u.user_id = c.redeemed_by
        WHERE c.pool_id = $1
        ORDER BY c.created_at ASC
        """,
        pool_id,
    )
    codes = [
        TeacherCodeEntry(
            code=row["code"],
            redeemed_by=row["redeemed_username"],
            redeemed_at=row["redeemed_at"],
            revoked_at=row["revoked_at"],
            created_at=row["created_at"],
        )
        for row in rows
    ]
    active = [c for c in codes if c.revoked_at is None]
    redeemed = [c for c in active if c.redeemed_at is not None]
    return TeacherCodeListResponse(
        pool_id=str(pool["pool_id"]),
        pool_name=pool["name"],
        capacity=int(pool["max_vms"]),
        active_codes=len(active),
        redeemed_codes=len(redeemed),
        codes=codes,
    )


@router.post(
    "/pools/{pool_id}/codes",
    response_model=TeacherCodeGenerateResponse,
    status_code=201,
)
async def generate_codes(
    pool_id: str,
    user: dict = Depends(staff),
    conn=Depends(get_db),
):
    pool = await _own_code_pool(conn, user, pool_id)
    capacity = int(pool["max_vms"])

    active_count = await conn.fetchval(
        """
        SELECT COUNT(*) FROM pool_access_codes
        WHERE pool_id = $1 AND revoked_at IS NULL
        """,
        pool_id,
    )
    missing = capacity - int(active_count)
    if missing <= 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"pool already has {active_count} active access code(s) "
                f"(capacity {capacity})"
            ),
        )

    new_codes = await _insert_codes(conn, pool_id, missing)
    logger.info(
        "teacher %s generated %d access code(s) for pool '%s'",
        user.get("username"), len(new_codes), pool["name"],
    )
    return TeacherCodeGenerateResponse(
        pool_id=str(pool["pool_id"]),
        pool_name=pool["name"],
        capacity=capacity,
        generated=len(new_codes),
        active_codes=active_count + len(new_codes),
        codes=new_codes,
    )
