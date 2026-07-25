from __future__ import annotations
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class PoolResponse(BaseModel):
    pool_id: str
    name: str
    desktop_type: str
    image_id: str
    flavor_id: str
    network_id: str
    min_vms: int
    max_vms: int
    current_count: int
    auto_scaling_enabled: bool
    status: str
    allowed_roles: list[str]
    max_session_minutes: int
    created_by: str
    created_at: datetime
    updated_at: datetime


class PoolListResponse(BaseModel):
    pools: list[PoolResponse]
    total: int


class VMClaimResponse(BaseModel):

    ok: bool = True
    instance_id: str
    floating_ip: str
    private_ip: Optional[str] = None
    session_expires_in_minutes: int
    pool_name: str


class VMStatusResponse(BaseModel):
    has_assignment: bool
    instance_id: Optional[str] = None
    floating_ip: Optional[str] = None
    pool_name: Optional[str] = None
    desktop_type: Optional[str] = None
    status: Optional[str] = None
    assigned_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None


class HealthResponse(BaseModel):
    status: str = "ok"
    db_connected: bool
    openstack_reachable: bool
    active_pools: int
    total_ready_vms: int


class ErrorResponse(BaseModel):
    detail: str
    error_code: Optional[str] = None
    extra: Optional[dict[str, Any]] = None
