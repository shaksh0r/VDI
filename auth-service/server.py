import hashlib
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

import httpx
import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel
from passlib.context import CryptContext

from database_connection import create_database_pool, get_db


def _load_env_file() -> None:
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if value and len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]

        os.environ.setdefault(key, value)


_load_env_file()


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class LoginRequest(BaseModel):
    username: str
    password: str


class AssignedVM(BaseModel):
    instance_id: str
    pool_id: str
    hostname: Optional[str] = None
    private_ip: Optional[str] = None
    floating_ip: Optional[str] = None
    connection_details: Optional[dict[str, Any]] = None
    assignment_type: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: str
    role: str
    user_id: str
    openstack_token: str
    openstack_expires_at: str
    vm: AssignedVM


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db_pool = await create_database_pool("DB")
    try:
        yield
    finally:
        await app.state.db_pool.close()


app = FastAPI(lifespan=lifespan)


def _build_keystone_payload(
    username: str,
    password: str,
    project_id: Optional[str],
    project_name: Optional[str],
):
    user_domain = os.getenv("OS_USER_DOMAIN_NAME", "Default")
    project_domain_name = os.getenv("OS_PROJECT_DOMAIN_NAME")
    project_domain_id = os.getenv("OS_PROJECT_DOMAIN_ID", "default")

    identity = {
        "methods": ["password"],
        "password": {
            "user": {
                "domain": {"name": user_domain},
                "name": username,
                "password": password,
            }
        },
    }

    scope = None
    if project_id:
        scope = {"project": {"id": project_id}}
    elif project_name:
        project_domain = (
            {"name": project_domain_name} if project_domain_name else {"id": project_domain_id}
        )
        scope = {"project": {"domain": project_domain, "name": project_name}}

    payload = {"auth": {"identity": identity}}
    if scope:
        payload["auth"]["scope"] = scope

    return payload


async def _request_keystone_token(username: str, password: str) -> tuple[str, str]:
    auth_url = os.getenv("OS_AUTH_URL")
    if not auth_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OS_AUTH_URL is not configured",
        )

    project_id = os.getenv("OS_PROJECT_ID")
    project_name = os.getenv("OS_PROJECT_NAME")
    payload = _build_keystone_payload(username, password, project_id, project_name)

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"{auth_url.rstrip('/')}/auth/tokens?nocatalog",
            json=payload,
            headers={"Content-Type": "application/json"},
        )

    if response.status_code != status.HTTP_201_CREATED:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OpenStack authentication failed",
        )

    token = response.headers.get("X-Subject-Token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Missing OpenStack token in response",
        )

    body = response.json()
    expires_at = body.get("token", {}).get("expires_at")
    if not expires_at:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Missing token expiry in response",
        )

    return token, expires_at


def _normalize_vm_row(row) -> AssignedVM:
    return AssignedVM(
        instance_id=str(row["instance_id"]),
        pool_id=str(row["pool_id"]),
        hostname=row["hostname"],
        private_ip=str(row["private_ip"]) if row["private_ip"] else None,
        floating_ip=str(row["floating_ip"]) if row["floating_ip"] else None,
        connection_details=row["connection_details"],
        assignment_type=row["assignment_type"],
    )


def _jwt_settings() -> tuple[str, str, int]:
    secret = os.getenv("JWT_SECRET")
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT_SECRET is not configured",
        )

    algorithm = os.getenv("JWT_ALGORITHM", "HS256")
    expiry_minutes = int(os.getenv("JWT_EXPIRE_MINUTES", "30"))
    return secret, algorithm, expiry_minutes


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _create_access_token(user_id: str, username: str, role: str) -> tuple[str, datetime]:
    secret, algorithm, expiry_minutes = _jwt_settings()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes)
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "exp": expires_at,
        "iat": datetime.now(timezone.utc),
    }
    token = jwt.encode(payload, secret, algorithm=algorithm)
    return token, expires_at


def _decode_access_token(token: str) -> dict[str, Any]:
    secret, algorithm, _ = _jwt_settings()
    try:
        payload = jwt.decode(token, secret, algorithms=[algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        ) from exc

    return payload


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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")

    return token


async def _get_existing_assignment(db, user_uuid: str) -> Optional[AssignedVM]:
    row = await db.fetchrow(
        """
        SELECT
            di.instance_id,
            di.pool_id,
            di.hostname,
            di.private_ip,
            di.floating_ip,
            di.connection_details,
            ua.assignment_type
        FROM user_assignments ua
        JOIN desktop_instances di ON di.instance_id = ua.instance_id
        WHERE ua.user_id = $1::uuid
          AND ua.released_at IS NULL
          AND di.status IN ('assigned', 'in_use', 'ready')
        ORDER BY ua.assigned_at DESC
        LIMIT 1
        """,
        user_uuid,
    )

    if not row:
        return None

    return _normalize_vm_row(row)


async def _assign_vm_to_student(db, user_uuid: str) -> AssignedVM:
    existing_assignment = await _get_existing_assignment(db, user_uuid)
    if existing_assignment:
        return existing_assignment

    async with db.transaction():
        vm_row = await db.fetchrow(
            """
            SELECT
                instance_id,
                pool_id,
                hostname,
                private_ip,
                floating_ip,
                connection_details
            FROM desktop_instances
            WHERE status = 'ready'
              AND assigned_user_id IS NULL
            ORDER BY created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        )

        if not vm_row:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No VM is currently available",
            )

        await db.execute(
            """
            UPDATE desktop_instances
            SET assigned_user_id = $1::uuid,
                status = 'assigned',
                assigned_at = CURRENT_TIMESTAMP,
                last_accessed_at = CURRENT_TIMESTAMP
            WHERE instance_id = $2::uuid
            """,
            user_uuid,
            str(vm_row["instance_id"]),
        )

        await db.execute(
            """
            INSERT INTO user_assignments (user_id, instance_id, pool_id, assignment_type)
            VALUES ($1::uuid, $2::uuid, $3::uuid, 'temporary')
            """,
            user_uuid,
            str(vm_row["instance_id"]),
            str(vm_row["pool_id"]),
        )

    return AssignedVM(
        instance_id=str(vm_row["instance_id"]),
        pool_id=str(vm_row["pool_id"]),
        hostname=vm_row["hostname"],
        private_ip=str(vm_row["private_ip"]) if vm_row["private_ip"] else None,
        floating_ip=str(vm_row["floating_ip"]) if vm_row["floating_ip"] else None,
        connection_details=vm_row["connection_details"],
        assignment_type="temporary",
    )


async def _store_user_session(
    db,
    user_id: str,
    access_token: str,
    expires_at: datetime,
    request: Request,
) -> None:
    await db.execute(
        """
        INSERT INTO user_sessions (user_id, token_hash, ip_address, user_agent, expires_at)
        VALUES ($1::uuid, $2, $3::inet, $4, $5)
        """,
        user_id,
        _hash_token(access_token),
        request.client.host if request.client else None,
        request.headers.get("user-agent"),
        expires_at.replace(tzinfo=None),
    )


@app.get("/")
def healthcheck():
    return {"status": "ok"}


@app.post("/auth/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    db=Depends(get_db),
):
    user = await db.fetchrow(
        """
        SELECT user_id, username, password_hash, role, is_active
        FROM users
        WHERE username = $1
          AND deleted_at IS NULL
        """,
        payload.username,
    )

    if not user or not user["is_active"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not pwd_context.verify(payload.password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    openstack_token, openstack_expires_at = await _request_keystone_token(
        user["username"],
        payload.password,
    )

    user_id = str(user["user_id"])
    try:
        str(UUID(user_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="User record does not contain a valid UUID",
        ) from exc

    try:
        assigned_vm = await _assign_vm_to_student(db, user_id)
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to assign a VM for this user",
        ) from exc

    access_token, session_expires_at = _create_access_token(
        user_id=user_id,
        username=user["username"],
        role=user["role"],
    )

    await db.execute(
        """
        UPDATE users
        SET last_login_at = CURRENT_TIMESTAMP
        WHERE user_id = $1::uuid
        """,
        user_id,
    )

    await db.execute(
        """
        UPDATE user_sessions
        SET revoked_at = CURRENT_TIMESTAMP
        WHERE user_id = $1::uuid
          AND revoked_at IS NULL
        """,
        user_id,
    )

    await _store_user_session(db, user_id, access_token, session_expires_at, request)

    return LoginResponse(
        access_token=access_token,
        expires_at=session_expires_at.isoformat(),
        role=user["role"],
        user_id=user_id,
        openstack_token=openstack_token,
        openstack_expires_at=openstack_expires_at,
        vm=assigned_vm,
    )


@app.get("/auth/me")
async def me(
    authorization: Optional[str] = Header(default=None),
    x_auth_token: Optional[str] = Header(default=None),
    db=Depends(get_db),
):
    token = _extract_bearer_token(authorization, x_auth_token)
    payload = _decode_access_token(token)
    token_hash = _hash_token(token)

    row = await db.fetchrow(
        """
        SELECT
            u.user_id,
            u.username,
            u.role,
            s.expires_at
        FROM user_sessions s
        JOIN users u ON u.user_id = s.user_id
        WHERE s.token_hash = $1
          AND s.revoked_at IS NULL
          AND s.expires_at > CURRENT_TIMESTAMP
          AND u.deleted_at IS NULL
        """,
        token_hash,
    )

    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    if str(row["user_id"]) != payload.get("sub"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    return {
        "user_id": str(row["user_id"]),
        "username": row["username"],
        "role": row["role"],
        "expires_at": row["expires_at"].isoformat(),
    }


@app.post("/auth/logout")
async def logout(
    authorization: Optional[str] = Header(default=None),
    x_auth_token: Optional[str] = Header(default=None),
    db=Depends(get_db),
):
    token = _extract_bearer_token(authorization, x_auth_token)
    payload = _decode_access_token(token)

    session_status = await db.execute(
        """
        UPDATE user_sessions
        SET revoked_at = CURRENT_TIMESTAMP
        WHERE user_id = $1::uuid
          AND token_hash = $2
          AND revoked_at IS NULL
        """,
        payload["sub"],
        _hash_token(token),
    )

    if session_status.endswith("0"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    return {"status": "logged_out"}
