import asyncio
import logging
import os
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, status

from database_connection import create_database_pool, get_db

# ─────────────────────────────────────────────────────────────────────────────
#  Logging
# ─────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  Environment
# ─────────────────────────────────────────────────────────────────────────────

POOL_NAME          = "pool_1"
AUTH_SERVICE_URL   = os.getenv("AUTH_SERVICE_URL",      "http://localhost:8001")
MIRRORING_BASE_URL = os.getenv("MIRRORING_SERVICE_URL", "http://localhost:8000")

router = APIRouter(prefix="/provision", tags=["provisioning"])

# ─────────────────────────────────────────────────────────────────────────────
#  Auth helpers
# ─────────────────────────────────────────────────────────────────────────────

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
    """
    Calls GET /auth/me on the auth service to verify the token.
    Returns { user_id, username, role, expires_at } on success.
    Raises 401 if the token is invalid/expired, 502 if auth service is down.
    """
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


# ─────────────────────────────────────────────────────────────────────────────
#  VM release helper
# ─────────────────────────────────────────────────────────────────────────────

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
        instance_id,
    )
    logger.info("VM %s released back to pool", instance_id)


# ─────────────────────────────────────────────────────────────────────────────
#  Session expiry background task
# ─────────────────────────────────────────────────────────────────────────────

async def _session_expiry_watcher(
    instance_id: str,
    user_id: str,
    timeout_seconds: int,
) -> None:
    try:
        await asyncio.sleep(timeout_seconds)
        logger.info(
            "Session expired for user %s on VM %s — releasing",
            user_id, instance_id,
        )
        pool = await create_database_pool()
        async with pool.acquire() as conn:
            await _release_vm(instance_id, conn)
        await pool.close()
    except asyncio.CancelledError:
        logger.info("Expiry watcher cancelled for VM %s", instance_id)


# Active expiry tasks: instance_id → asyncio.Task
_expiry_tasks: dict[str, asyncio.Task] = {}


# ─────────────────────────────────────────────────────────────────────────────
#  Mirroring service notification
# ─────────────────────────────────────────────────────────────────────────────

async def _notify_mirroring_service(user_id: str, floating_ip: str) -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                f"{MIRRORING_BASE_URL}/internal/assign",
                json={"user_id": user_id, "vm_ip": floating_ip},
            )
            resp.raise_for_status()
            logger.info(
                "Mirroring service notified: user=%s ip=%s",
                user_id, floating_ip,
            )
        except httpx.HTTPError as exc:
            logger.error("Failed to notify mirroring service: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Could not reach mirroring service",
            )


# ─────────────────────────────────────────────────────────────────────────────
#  Routes
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/connect")
async def connect(
    authorization: Optional[str] = Header(default=None),
    x_auth_token:  Optional[str] = Header(default=None),
    db=Depends(get_db),
):
    """
    Called when the user clicks Connect on the frontend.

    1. Validates token via auth service
    2. Checks pool_1 is active
    3. Atomically claims a ready VM (FOR UPDATE SKIP LOCKED)
    4. Notifies mirroring service of the VM IP
    5. Starts session expiry watcher
    6. Returns floating IP to frontend
    """

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

    # Atomically claim a ready VM
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
        user_id,
    )

    if not instance:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No VMs available at this time. Please try again later.",
        )

    instance_id = str(instance["instance_id"])
    floating_ip = str(instance["floating_ip"])

    logger.info("VM %s (%s) assigned to user %s", instance_id, floating_ip, user_id)

    # Notify mirroring service
    await _notify_mirroring_service(user_id, floating_ip)

    # Start expiry watcher (cancel any stale one first)
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


@router.post("/disconnect")
async def disconnect(
    authorization: Optional[str] = Header(default=None),
    x_auth_token:  Optional[str] = Header(default=None),
    db=Depends(get_db),
):
    """
    Called when the user clicks Disconnect or closes the session.
    Releases the VM back to the pool and cancels the expiry watcher.
    """
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
        user_id,
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
    """
    Returns the user's current VM session status.
    Useful for the frontend to check on page reload if a session is still active.
    """
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
        user_id,
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