from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..models.requests import PoolCreateRequest, PoolUpdateRequest
from ..models.responses import PoolListResponse, PoolResponse
from ..services import pool_service
from .deps import get_db, require_roles

router = APIRouter(prefix="/admin/pools", tags=["pools"])

teacher = require_roles("admin", "faculty")


@router.post("", response_model=PoolResponse, status_code=201)
async def create_pool(
    data: PoolCreateRequest,
    user: dict = Depends(teacher),
    conn=Depends(get_db),
):
    try:
        return await pool_service.create_pool(conn, user["user_id"], data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("", response_model=PoolListResponse)
async def list_pools(
    status: Optional[str] = Query(default=None),
    desktop_type: Optional[str] = Query(default=None),
    user: dict = Depends(teacher),
    conn=Depends(get_db),
):
    return await pool_service.list_pools(
        conn, status=status, desktop_type=desktop_type
    )


@router.get("/{pool_id}", response_model=PoolResponse)
async def get_pool(
    pool_id: str,
    user: dict = Depends(teacher),
    conn=Depends(get_db),
):
    pool = await pool_service.get_pool(conn, pool_id)
    if pool is None:
        raise HTTPException(status_code=404, detail="pool not found")
    return pool


@router.patch("/{pool_id}", response_model=PoolResponse)
async def update_pool(
    pool_id: str,
    updates: PoolUpdateRequest,
    user: dict = Depends(teacher),
    conn=Depends(get_db),
):
    try:
        pool = await pool_service.update_pool(conn, pool_id, updates)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if pool is None:
        raise HTTPException(status_code=404, detail="pool not found")
    return pool


@router.delete("/{pool_id}", status_code=204)
async def delete_pool(
    pool_id: str,
    user: dict = Depends(teacher),
    conn=Depends(get_db),
):
    ok = await pool_service.delete_pool(conn, pool_id)
    if not ok:
        raise HTTPException(status_code=404, detail="pool not found")
