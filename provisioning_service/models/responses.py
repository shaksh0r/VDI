from __future__ import annotations
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class PoolResponse(BaseModel):
    pool_id: str
    name: str
    desktop_type: str
    access_mode: str
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


class TeacherTemplateResponse(BaseModel):
    """The fixed, read-only compute spec shown to teachers when they
    create a class pool. Values are decided server-side."""

    desktop_type: str
    access_mode: str
    image_id: str
    flavor_id: str
    network_id: str
    security_group: str
    vcpus: Optional[int] = None
    ram_mb: Optional[int] = None
    disk_gb: Optional[int] = None
    max_session_minutes: int
    fixed: bool = True
    note: str = "VM specification is fixed by the platform administrator"


class TeacherPoolSummary(BaseModel):
    pool_id: str
    name: str
    status: str
    desktop_type: str
    access_mode: str
    min_vms: int
    max_vms: int
    current_count: int
    max_session_minutes: int
    created_at: datetime
    ready_count: int = 0
    in_use_count: int = 0
    provisioning_count: int = 0
    total_instances: int = 0


class TeacherPoolListResponse(BaseModel):
    pools: list[TeacherPoolSummary]
    total: int


class TeacherCodeEntry(BaseModel):
    code: str
    redeemed_by: Optional[str] = None   # username of the redeeming student
    redeemed_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class TeacherCodeListResponse(BaseModel):
    pool_id: str
    pool_name: str
    capacity: int                    # max_vms — one code per VM seat
    active_codes: int                # un-revoked codes issued so far
    redeemed_codes: int              # of the active ones, claimed by students
    codes: list[TeacherCodeEntry]


class TeacherCodeGenerateResponse(BaseModel):
    pool_id: str
    pool_name: str
    capacity: int
    generated: int                   # codes created by this call
    active_codes: int                # un-revoked total after this call
    codes: list[str]                 # only the newly generated ones


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
