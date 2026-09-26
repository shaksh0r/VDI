from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class PoolCreateRequest(BaseModel):

    name: str = Field(..., min_length=1, max_length=100,description="Unique pool name",)
    desktop_type: str = Field(
        ..., pattern="^(persistent|non_persistent)$",
        description="Pool type: 'persistent' or 'non_persistent'",
    )
    image_id: str = Field(
        ..., description="OpenStack Glance image UUID",
    )
    flavor_id: str = Field(
        ..., description="OpenStack Nova flavor UUID",
    )
    network_id: str = Field(
        ..., description="OpenStack Neutron network UUID",
    )
    volume_size_gb: Optional[int] = Field(
        None, ge=1, le=500,
        description="Root volume size per VM in GB. NULL uses the "
                    "VM_BOOT_VOLUME_SIZE_GB config default.",
    )
    min_vms: int = Field(
        default=1, ge=0, le=100,
        description="Minimum number of ready VMs to maintain",
    )
    max_vms: int = Field(
        default=10, ge=1, le=500,
        description="Maximum VMs allowed in this pool",
    )
    max_session_minutes: int = Field(
        default=240, ge=5, le=1440,
        description="Max session duration before auto-release",
    )
    access_mode: str = Field(
        default="open", pattern="^(open|code)$",
        description="'open' = claimable by eligible roles; "
                    "'code' = class pool, claimable only via an access code",
    )
    auto_scaling_enabled: bool = Field(
        default=False,
        description="Whether to auto-scale beyond min/max based on demand",
    )
    allowed_roles: list[str] = Field(
        default=["student"],
        description="User roles allowed to claim VMs from this pool",
    )


class PoolUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    min_vms: Optional[int] = Field(None, ge=0, le=100)
    max_vms: Optional[int] = Field(None, ge=1, le=500)
    max_session_minutes: Optional[int] = Field(None, ge=5, le=1440)
    auto_scaling_enabled: Optional[bool] = None
    allowed_roles: Optional[list[str]] = None
    status: Optional[str] = Field(
        None, pattern="^(active|inactive)$",
        description="Set pool to 'active' or 'inactive'",
    )


class VMRequestRequest(BaseModel):
    pool_type: Optional[str] = Field(
        None, pattern="^(persistent|non_persistent)$",
        description="Preferred pool type. If omitted, uses role default.",
    )
    pool_id: Optional[str] = Field(
        None,
        description="Request a specific pool (must be allowed for your role).",
    )
    code: Optional[str] = Field(
        None, min_length=6, max_length=16, pattern="^[A-Za-z0-9]+$",
        description="Class access code for a teacher (code-gated) pool. "
                    "Without a code, only 'open' pools are claimable.",
    )


class VMReleaseRequest(BaseModel):
    reason: str = Field(
        default="user_logout",
        pattern="^(user_logout|timeout|admin_action|vm_error|session_expired)$",
    )


class TeacherPoolCreateRequest(BaseModel):
    """Body for the teacher (faculty) class-pool creation endpoint.

    The teacher chooses the pool name, VM count, compute flavor and root
    volume size. Image / network / security group stay fixed server-side.

    `flavor_id` and `volume_gb` are optional so existing clients keep
    working: when omitted they fall back to the reference-pool template
    and the configured default volume size.
    """

    name: str = Field(..., min_length=3, max_length=100, description="Class pool name (unique)")
    vm_count: int = Field(
        ..., ge=1, le=100,
        description="Number of VMs (and later, access codes) for the class",
    )
    flavor_id: Optional[str] = Field(
        None,
        description="Nova flavor for the class VMs. Omit to use the "
                    "platform default (reference-pool flavor). The flavor "
                    "also determines the root disk size.",
    )


class TeacherExpandRequest(BaseModel):
    """Grow a class pool: raise its capacity, dispatch create_vm jobs and
    issue matching access codes (one per added VM seat)."""

    add_vms: int = Field(
        ..., ge=1, le=40,
        description="How many extra VM seats to add to the class pool",
    )
