from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..message_queue.celery_app import app
from ..models.requests import VMReleaseRequest, VMRequestRequest
from ..models.responses import VMClaimResponse, VMStatusResponse
from ..services import pool_service, vm_service
from .deps import get_current_user, get_db, get_pool, require_roles

router = APIRouter(prefix="/provision", tags=["vms"])


class PoolExpandRequest(BaseModel):
    pool_id: str
    count: int = Field(..., ge=1)


@router.post("/connect", response_model=VMClaimResponse)
async def connect(
    request: VMRequestRequest,
    user: dict = Depends(get_current_user),
    pool=Depends(get_pool),
):
    try:
        return await vm_service.claim_vm(
            pool,
            user["user_id"],
            user["role"],
            pool_id=request.pool_id,
            pool_type=request.pool_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except vm_service.PoolExhaustionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/disconnect")
async def disconnect(
    request: VMReleaseRequest,
    user: dict = Depends(get_current_user),
    conn=Depends(get_db),
):
    await vm_service.release_vm(conn, user["user_id"], request.reason)
    return {"ok": True}


@router.get("/status", response_model=VMStatusResponse)
async def status(
    user: dict = Depends(get_current_user),
    conn=Depends(get_db),
):
    return await vm_service.get_vm_status(conn, user["user_id"])


@router.post("/pool/expand")
async def expand_pool(
    request: PoolExpandRequest,
    user: dict = Depends(require_roles("admin", "faculty")),
    conn=Depends(get_db),
):
    pool = await pool_service.get_pool(conn, request.pool_id)
    if pool is None:
        raise HTTPException(status_code=404, detail="pool not found")
    cap = pool.max_vms - pool.current_count
    if cap <= 0:
        raise HTTPException(status_code=400, detail="pool at max capacity")
    count = min(request.count, cap)
    job_ids = await pool_service.insert_create_jobs(
        conn, request.pool_id, count
    )
    for job_id in job_ids:
        app.send_task(
            "provisioning_service.services.job_worker.create_vm_task",
            args=[str(request.pool_id), str(job_id)],
        )
    return {
        "pool_id": str(pool.pool_id),
        "requested": request.count,
        "dispatched": count,
    }
