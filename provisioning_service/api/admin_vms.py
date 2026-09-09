"""Admin console resource endpoints (admin role only).

Complete admin access to the pool/VM layer:
  * GET  /admin/vms                     — every instance w/ pool + assignee
  * GET  /admin/pools/{id}/detail       — one pool + its instances + jobs
  * POST /admin/vms/{id}/release        — force-end a student's session
  * POST /admin/vms/{id}/delete         — destroy a VM (session ends first)

Pool create/update/delete already live under /admin/pools (update_pool /
delete_pool) — delete_pool now force-ends sessions and destroys every VM.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from asyncpg import Record

from ..message_queue.celery_app import app
from ..services import vm_service
from .deps import get_db, require_roles

router = APIRouter(prefix="/admin", tags=["admin-resources"])

admin = require_roles("admin")

_INSTANCE_SELECT = """
    SELECT i.instance_id, i.status, i.floating_ip, i.private_ip,
           i.openstack_vm_id, i.created_at, i.assigned_at,
           p.pool_id, p.name AS pool_name, p.desktop_type, p.access_mode,
           i.assigned_user_id, u.username AS assigned_username,
           ua.assigned_at AS session_started_at
    FROM desktop_instances i
    JOIN desktop_pools p ON p.pool_id = i.pool_id
    LEFT JOIN users u ON u.user_id = i.assigned_user_id
    LEFT JOIN user_assignments ua
           ON ua.instance_id = i.instance_id AND ua.released_at IS NULL
"""


def _str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _instance_dict(row: Record) -> dict:
    return {
        "instance_id": str(row["instance_id"]),
        "status": row["status"],
        "floating_ip": _str(row["floating_ip"]),
        "private_ip": _str(row["private_ip"]),
        "openstack_vm_id": _str(row["openstack_vm_id"]),
        "created_at": row["created_at"],
        "assigned_at": row["assigned_at"],
        "pool_id": str(row["pool_id"]),
        "pool_name": row["pool_name"],
        "pool_type": row["desktop_type"],
        "access_mode": row["access_mode"],
        "assigned_user_id": _str(row["assigned_user_id"]),
        "assigned_username": row["assigned_username"],
        "session_started_at": row["session_started_at"],
    }


async def _load_instance(conn, instance_id: str) -> Record:
    row = await conn.fetchrow(
        _INSTANCE_SELECT + " WHERE i.instance_id = $1",
        instance_id,
    )
    if row is None or row["status"] == "deleted":
        raise HTTPException(status_code=404, detail="instance not found")
    return row


@router.get("/vms")
async def list_vms(
    pool_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    user: dict = Depends(admin),
    conn=Depends(get_db),
):
    query = _INSTANCE_SELECT + " WHERE i.status <> 'deleted'"
    params: list[Any] = []
    if pool_id:
        params.append(pool_id)
        query += f" AND i.pool_id = ${len(params)}::uuid"
    if status:
        params.append(status)
        query += f" AND i.status = ${len(params)}"
    query += " ORDER BY i.created_at DESC"
    rows = await conn.fetch(query, *params)
    return {"vms": [_instance_dict(row) for row in rows], "total": len(rows)}


@router.get("/pools/{pool_id}/detail")
async def pool_detail(
    pool_id: str,
    user: dict = Depends(admin),
    conn=Depends(get_db),
):
    pool = await conn.fetchrow(
        """
        SELECT pool_id, name, description, desktop_type, access_mode,
               base_image_id AS image_id, flavor_id, network_id,
               min_vms, max_vms, current_count, auto_scaling_enabled,
               status, allowed_roles, max_session_duration_minutes,
               created_by, created_at, updated_at
        FROM desktop_pools
        WHERE pool_id = $1 AND deleted_at IS NULL
        """,
        pool_id,
    )
    if pool is None:
        raise HTTPException(status_code=404, detail="pool not found")

    instances = await conn.fetch(
        _INSTANCE_SELECT
        + " WHERE i.pool_id = $1 AND i.status <> 'deleted' ORDER BY i.created_at",
        pool_id,
    )
    jobs = await conn.fetch(
        """
        SELECT status, job_type, COUNT(*) AS count
        FROM provisioning_jobs
        WHERE pool_id = $1
        GROUP BY status, job_type
        ORDER BY job_type, status
        """,
        pool_id,
    )

    def _role_list(value) -> list[str]:
        return list(value) if value is not None else []

    return {
        "pool": {
            "pool_id": str(pool["pool_id"]),
            "name": pool["name"],
            "description": pool["description"],
            "desktop_type": pool["desktop_type"],
            "access_mode": pool["access_mode"],
            "image_id": _str(pool["image_id"]),
            "flavor_id": _str(pool["flavor_id"]),
            "network_id": _str(pool["network_id"]),
            "min_vms": pool["min_vms"],
            "max_vms": pool["max_vms"],
            "current_count": pool["current_count"],
            "auto_scaling_enabled": pool["auto_scaling_enabled"],
            "status": pool["status"],
            "allowed_roles": _role_list(pool["allowed_roles"]),
            "max_session_minutes": pool["max_session_duration_minutes"],
            "created_by": _str(pool["created_by"]),
            "created_at": pool["created_at"],
            "updated_at": pool["updated_at"],
        },
        "instances": [_instance_dict(row) for row in instances],
        "job_summary": [
            {"job_type": row["job_type"], "status": row["status"], "count": row["count"]}
            for row in jobs
        ],
    }


@router.post("/vms/{instance_id}/release")
async def release_vm(
    instance_id: str,
    user: dict = Depends(admin),
    conn=Depends(get_db),
):
    await _load_instance(conn, instance_id)
    outcome = await vm_service.release_instance(
        conn, instance_id, reason="admin_action"
    )
    return {
        "ok": True,
        "instance_id": instance_id,
        "assignment_closed": outcome["assignment_closed"],
        "destroyed": outcome["destroyed"],
        "job_id": outcome["job_id"],
    }


@router.post("/vms/{instance_id}/delete")
async def delete_vm(
    instance_id: str,
    user: dict = Depends(admin),
    conn=Depends(get_db),
):
    await _load_instance(conn, instance_id)  # 404 guard
    outcome = await vm_service.destroy_instance(
        conn, instance_id, reason="admin_action"
    )
    if not outcome["found"]:
        raise HTTPException(status_code=404, detail="instance not found")
    return {
        "ok": True,
        "instance_id": instance_id,
        "job_id": outcome["job_id"],
        "already_deleting": outcome["already_deleting"],
    }
