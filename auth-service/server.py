import hashlib
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field

from database_connection import create_database_pool, get_db



def _hash_password(password: str) -> str:
    pre_hashed = hashlib.sha256(password.encode()).hexdigest().encode()
    return bcrypt.hashpw(pre_hashed, bcrypt.gensalt()).decode()


def _verify_password(password: str, hashed: str) -> bool:
    pre_hashed = hashlib.sha256(password.encode()).hexdigest().encode()
    return bcrypt.checkpw(pre_hashed, hashed.encode())


class SignupRequest(BaseModel):
    username: str
    email: EmailStr
    password: str
    full_name: str
    student_id: str
    department: str


class SignupResponse(BaseModel):
    user_id: str
    username: str
    email: str
    role: str


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: str
    role: str
    user_id: str


class AdminUserCreateRequest(BaseModel):
    username: str = Field(..., min_length=4, max_length=255)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=255)
    full_name: str = Field(..., min_length=1, max_length=255)
    role: str = Field(
        "faculty",
        pattern="^(faculty|student)$",
        description="Accounts created by an admin. Teacher accounts use role 'faculty'.",
    )


class AdminUserResponse(BaseModel):
    user_id: str
    username: str
    email: str
    full_name: Optional[str] = None
    role: str
    is_active: bool
    created_at: Optional[datetime] = None


class AdminUserListResponse(BaseModel):
    users: list[AdminUserResponse]
    total: int


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db_pool = await create_database_pool()
    yield
    await app.state.db_pool.close()


app = FastAPI(title="Auth Service", version="1.0.0", lifespan=lifespan)

# Browser frontends (served by the mirroring service) call /auth/* directly
# with Bearer tokens. No credentials/cookies are used, so a wildcard origin
# is safe; set ALLOWED_ORIGINS (comma-separated) in production.
ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)



def _now() -> datetime:
    """Always returns a naive UTC datetime to match TIMESTAMP columns in DB."""
    return datetime.utcnow()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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


def _make_token(ttl_minutes: int) -> tuple[str, str, datetime]:
    token      = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)
    expires_at = _now() + timedelta(minutes=ttl_minutes)
    return token, token_hash, expires_at


async def _session_user(db, authorization, x_auth_token) -> dict:
    """Resolve a bearer token to its active user row (role included)."""
    token      = _extract_bearer_token(authorization, x_auth_token)
    token_hash = _hash_token(token)

    row = await db.fetchrow(
        """
        SELECT u.user_id, u.username, u.role, s.expires_at, s.revoked_at
        FROM user_sessions s
        JOIN users u ON u.user_id = s.user_id
        WHERE s.token_hash = $1
          AND u.is_active  = TRUE
          AND u.deleted_at IS NULL
        """,
        token_hash,
    )

    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if row["revoked_at"] is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token revoked")
    if row["expires_at"] <= _now():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    return row


async def _require_admin(db, authorization, x_auth_token) -> dict:
    """Like _session_user but only for admins (403 otherwise)."""
    row = await _session_user(db, authorization, x_auth_token)
    if str(row["role"]) != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return row



@app.get("/")
def healthcheck():
    return {"status": "ok"}


@app.get("/health")
def health():
    """Container healthcheck target (compose probes this path)."""
    return {"status": "ok"}


@app.post("/auth/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
async def signup(payload: SignupRequest, db=Depends(get_db)):
    existing = await db.fetchrow(
        "SELECT user_id FROM users WHERE username = $1 AND deleted_at IS NULL",
        payload.username,
    )
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already taken")

    existing = await db.fetchrow(
        "SELECT user_id FROM users WHERE email = $1 AND deleted_at IS NULL",
        payload.email,
    )
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    password_hash = _hash_password(payload.password)

    user = await db.fetchrow(
        """
        INSERT INTO users (
            username, email, password_hash,
            full_name, student_id, department,
            role, is_active, email_verified
        )
        VALUES ($1, $2, $3, $4, $5, $6, 'student', TRUE, FALSE)
        RETURNING user_id, username, email, role
        """,
        payload.username,
        payload.email,
        password_hash,
        payload.full_name,
        payload.student_id,
        payload.department,
    )

    return SignupResponse(
        user_id=str(user["user_id"]),
        username=user["username"],
        email=user["email"],
        role=str(user["role"]),
    )


@app.post("/auth/login", response_model=LoginResponse)
async def login(payload: LoginRequest, request: Request, db=Depends(get_db)):
    user = await db.fetchrow(
        """
        SELECT user_id, username, password_hash, role, is_active, deleted_at
        FROM users
        WHERE username = $1
        """,
        payload.username,
    )

    if not user or user["deleted_at"] is not None or not user["is_active"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not _verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    ttl_minutes = int(os.getenv("AUTH_TOKEN_TTL_MINUTES", "60"))
    token, token_hash, expires_at = _make_token(ttl_minutes)

    await db.execute(
        """
        INSERT INTO user_sessions (user_id, token_hash, ip_address, user_agent, expires_at)
        VALUES ($1, $2, $3, $4, $5)
        """,
        user["user_id"],
        token_hash,
        request.client.host if request.client else None,
        request.headers.get("user-agent"),
        expires_at,
    )

    await db.execute(
        "UPDATE users SET last_login_at = $1, updated_at = $1 WHERE user_id = $2",
        _now(),
        user["user_id"],
    )

    return LoginResponse(
        access_token=token,
        expires_at=expires_at.isoformat(),
        role=str(user["role"]),
        user_id=str(user["user_id"]),
    )


@app.get("/auth/me")
async def me(
    authorization: Optional[str] = Header(default=None),
    x_auth_token:  Optional[str] = Header(default=None),
    db=Depends(get_db),
):
    token      = _extract_bearer_token(authorization, x_auth_token)
    token_hash = _hash_token(token)

    row = await db.fetchrow(
        """
        SELECT u.user_id, u.username, u.role, s.expires_at, s.revoked_at
        FROM user_sessions s
        JOIN users u ON u.user_id = s.user_id
        WHERE s.token_hash = $1
          AND u.is_active  = TRUE
          AND u.deleted_at IS NULL
        """,
        token_hash,
    )

    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if row["revoked_at"] is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token revoked")
    if row["expires_at"] <= _now():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")

    return {
        "user_id":    str(row["user_id"]),
        "username":   row["username"],
        "role":       str(row["role"]),
        "expires_at": row["expires_at"].isoformat(),
    }


@app.post("/auth/logout")
async def logout(
    authorization: Optional[str] = Header(default=None),
    x_auth_token:  Optional[str] = Header(default=None),
    db=Depends(get_db),
):
    token      = _extract_bearer_token(authorization, x_auth_token)
    token_hash = _hash_token(token)

    result = await db.execute(
        """
        UPDATE user_sessions
        SET revoked_at = $1
        WHERE token_hash = $2
          AND revoked_at IS NULL
        """,
        _now(),
        token_hash,
    )

    if result == "UPDATE 0":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or already revoked token",
        )

    return {"status": "logged_out"}


# ── Admin-only account management ─────────────────────────────────────────────
# Teacher (faculty) accounts are created ONLY by an authenticated admin from
# the admin dashboard — never by public self-registration (the /auth/signup
# endpoint stays hard-coded to role 'student').


@app.post(
    "/auth/admin/users",
    response_model=AdminUserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def admin_create_user(
    payload: AdminUserCreateRequest,
    db=Depends(get_db),
    authorization: Optional[str] = Header(default=None),
    x_auth_token: Optional[str] = Header(default=None),
):
    await _require_admin(db, authorization, x_auth_token)

    existing = await db.fetchrow(
        "SELECT user_id FROM users WHERE username = $1 AND deleted_at IS NULL",
        payload.username,
    )
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already taken")

    existing = await db.fetchrow(
        "SELECT user_id FROM users WHERE email = $1 AND deleted_at IS NULL",
        payload.email,
    )
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    password_hash = _hash_password(payload.password)

    user = await db.fetchrow(
        """
        INSERT INTO users (
            username, email, password_hash, full_name,
            role, is_active, email_verified
        )
        VALUES ($1, $2, $3, $4, $5::user_role, TRUE, TRUE)
        RETURNING user_id, username, email, full_name, role, is_active, created_at
        """,
        payload.username,
        payload.email,
        password_hash,
        payload.full_name,
        payload.role,
    )

    return AdminUserResponse(
        user_id=str(user["user_id"]),
        username=user["username"],
        email=user["email"],
        full_name=user["full_name"],
        role=str(user["role"]),
        is_active=user["is_active"],
        created_at=user["created_at"],
    )


@app.get("/auth/admin/users", response_model=AdminUserListResponse)
async def admin_list_users(
    role: Optional[str] = Query(default=None, pattern="^(student|faculty|admin|guest)$"),
    db=Depends(get_db),
    authorization: Optional[str] = Header(default=None),
    x_auth_token: Optional[str] = Header(default=None),
):
    await _require_admin(db, authorization, x_auth_token)

    query = """
        SELECT user_id, username, email, full_name, role, is_active, created_at
        FROM users
        WHERE deleted_at IS NULL
    """
    params: list = []
    if role:
        params.append(role)
        query += f" AND role = ${len(params)}::user_role"
    query += " ORDER BY role ASC, created_at DESC"
    rows = await db.fetch(query, *params)

    return AdminUserListResponse(
        users=[
            AdminUserResponse(
                user_id=str(row["user_id"]),
                username=row["username"],
                email=row["email"],
                full_name=row["full_name"],
                role=str(row["role"]),
                is_active=row["is_active"],
                created_at=row["created_at"],
            )
            for row in rows
        ],
        total=len(rows),
    )