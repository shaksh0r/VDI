"""Pydantic request/response models for the provisioning service API."""

from .requests import (
    PoolCreateRequest,
    PoolUpdateRequest,
    VMRequestRequest,
    VMReleaseRequest,
)
from .responses import (
    PoolResponse,
    PoolListResponse,
    VMClaimResponse,
    VMStatusResponse,
    HealthResponse,
    ErrorResponse,
)

__all__ = [
    # Requests
    "PoolCreateRequest",
    "PoolUpdateRequest",
    "VMRequestRequest",
    "VMReleaseRequest",
    # Responses
    "PoolResponse",
    "PoolListResponse",
    "VMClaimResponse",
    "VMStatusResponse",
    "HealthResponse",
    "ErrorResponse",
]
