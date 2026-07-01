import asyncio
import logging
import os
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel

from ..database_connection import create_database_pool, get_db
from ..message_queue.tasks import (
    create_vm,
    DEFAULT_KEY_NAME,
    EXTRA_SEC_GROUP_1,
    EXTRA_SEC_GROUP_2,
)


logger = logging.getLogger(__name__)

POOL_NAME        = "pool_1"
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://auth-service:8003")

router = APIRouter()


class PoolExpandRequest(BaseModel):
    count: int


def _extract_bearer_token(
    authorization: Optional[str],
    x_auth_token: Optional[str],
) -> str:
    token = x_auth_token
    if not token and authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1]
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing token",
        )
    return token


async def _validate_token(token: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                f"{AUTH_SERVICE_URL}/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            logger.error("Auth service unreachable: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Auth service unreachable",
            )

    if resp.status_code == status.HTTP_401_UNAUTHORIZED:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    if resp.status_code != status.HTTP_200_OK:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unexpected response from auth service",
        )

    return resp.json()


async def _release_vm(instance_id: str, db) -> None:
    await db.execute(
        """
        UPDATE desktop_instances
        SET status           = 'ready',
            assigned_user_id = NULL,
            assigned_at      = NULL,
            updated_at       = CURRENT_TIMESTAMP
        WHERE instance_id = $1
        """,
        uuid.UUID(instance_id),
    )
    logger.info("VM %s released back to pool", instance_id)



async def _session_expiry_watcher(
    instance_id: str,
    user_id: str,
    timeout_seconds: int,
) -> None:
    try:
        await asyncio.sleep(timeout_seconds)
        logger.info("Session expired for user %s on VM %s — releasing", user_id, instance_id)
        pool = await create_database_pool()
        async with pool.acquire() as conn:
            await _release_vm(instance_id, conn)
        await pool.close()
    except asyncio.CancelledError:
        logger.info("Expiry watcher cancelled for VM %s", instance_id)


_expiry_tasks: dict[str, asyncio.Task] = {}


@router.post("/connect")
async def connect(
    authorization: Optional[str] = Header(default=None),
    x_auth_token:  Optional[str] = Header(default=None),
    db=Depends(get_db),
):
    token   = _extract_bearer_token(authorization, x_auth_token)
    user    = await _validate_token(token)
    user_id = user["user_id"]

    # Check pool
    pool = await db.fetchrow(
        """
        SELECT pool_id, max_session_duration_minutes
        FROM desktop_pools
        WHERE name         = $1
          AND status       = 'active'
          AND deleted_at   IS NULL
          AND desktop_type = 'non_persistent'
        """,
        POOL_NAME,
    )
    if not pool:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No active pool available",
        )

    pool_id             = pool["pool_id"]
    max_session_minutes = pool["max_session_duration_minutes"] or 240

    # Atomically claim a ready VM — FOR UPDATE SKIP LOCKED prevents
    # two concurrent requests from grabbing the same VM
    instance = await db.fetchrow(
        """
        WITH selected AS (
            SELECT instance_id
            FROM desktop_instances
            WHERE pool_id          = $1
              AND status           = 'ready'
              AND assigned_user_id IS NULL
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        UPDATE desktop_instances
        SET status           = 'in_use',
            assigned_user_id = $2,
            assigned_at      = CURRENT_TIMESTAMP,
            last_accessed_at = CURRENT_TIMESTAMP,
            updated_at       = CURRENT_TIMESTAMP
        WHERE instance_id = (SELECT instance_id FROM selected)
        RETURNING instance_id, floating_ip
        """,
        pool_id,
        uuid.UUID(user_id),  # cast: auth returns str, DB column is UUID
    )

    if not instance:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No VMs available at this time. Please try again later.",
        )

    instance_id = str(instance["instance_id"])
    floating_ip = str(instance["floating_ip"])

    logger.info("VM %s (%s) assigned to user %s", instance_id, floating_ip, user_id)

    # Start expiry watcher
    existing = _expiry_tasks.pop(instance_id, None)
    if existing and not existing.done():
        existing.cancel()

    task = asyncio.create_task(
        _session_expiry_watcher(instance_id, user_id, max_session_minutes * 60),
        name=f"expiry-{instance_id}",
    )
    _expiry_tasks[instance_id] = task

    return {
        "ok":                         True,
        "floating_ip":                floating_ip,
        "instance_id":                instance_id,
        "session_expires_in_minutes": max_session_minutes,
    }


@router.post("/pool/expand")
async def expand_pool(
    payload: PoolExpandRequest,
    authorization: Optional[str] = Header(default=None),
    x_auth_token:  Optional[str] = Header(default=None),
    db=Depends(get_db),
):
    token = _extract_bearer_token(authorization, x_auth_token)
    await _validate_token(token)

    count = int(payload.count or 0)
    if count < 1 or count > 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="count must be between 1 and 2",
        )

    pool = await db.fetchrow(
        """
        SELECT pool_id, base_image_id, flavor_id, network_id
        FROM desktop_pools
        WHERE name         = $1
          AND status       = 'active'
          AND deleted_at   IS NULL
          AND desktop_type = 'non_persistent'
        """,
        POOL_NAME,
    )
    if not pool:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No active pool available",
        )

    queued = 0
    for _ in range(count):
        name = "vm-" + str(uuid.uuid4())

        security_groups = [{"name": "default"}]
        if EXTRA_SEC_GROUP_1:
            security_groups.append({"name": EXTRA_SEC_GROUP_1})
        if EXTRA_SEC_GROUP_2:
            security_groups.append({"name": EXTRA_SEC_GROUP_2})

        vm_payload = {
            "server": {
                "name":            name,
                "imageRef":        pool["base_image_id"],
                "flavorRef":       pool["flavor_id"],
                "key_name":        DEFAULT_KEY_NAME,
                "networks": [
                    {"uuid": pool["network_id"]}
                ],
                "security_groups": security_groups,
            }
        }

        create_vm.delay(vm_payload)
        queued += 1

    return {
        "ok": True,
        "requested": count,
        "queued": queued,
        "pool": POOL_NAME,
    }


@router.post("/disconnect")
async def disconnect(
    authorization: Optional[str] = Header(default=None),
    x_auth_token:  Optional[str] = Header(default=None),
    db=Depends(get_db),
):
    token   = _extract_bearer_token(authorization, x_auth_token)
    user    = await _validate_token(token)
    user_id = user["user_id"]

    instance = await db.fetchrow(
        """
        SELECT instance_id
        FROM desktop_instances
        WHERE assigned_user_id = $1
          AND status           = 'in_use'
        """,
        uuid.UUID(user_id),
    )

    if not instance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active VM session found for this user",
        )

    instance_id = str(instance["instance_id"])

    task = _expiry_tasks.pop(instance_id, None)
    if task and not task.done():
        task.cancel()

    await _release_vm(instance_id, db)

    return {"ok": True, "message": "VM released successfully"}


@router.get("/status")
async def session_status(
    authorization: Optional[str] = Header(default=None),
    x_auth_token:  Optional[str] = Header(default=None),
    db=Depends(get_db),
):
    token   = _extract_bearer_token(authorization, x_auth_token)
    user    = await _validate_token(token)
    user_id = user["user_id"]

    instance = await db.fetchrow(
        """
        SELECT instance_id, floating_ip, assigned_at, status
        FROM desktop_instances
        WHERE assigned_user_id = $1
          AND status           = 'in_use'
        """,
        uuid.UUID(user_id),
    )

    if not instance:
        return {"ok": False, "active": False}

    return {
        "ok":          True,
        "active":      True,
        "instance_id": str(instance["instance_id"]),
        "floating_ip": str(instance["floating_ip"]),
        "assigned_at": instance["assigned_at"].isoformat(),
        "status":      str(instance["status"]),
    }