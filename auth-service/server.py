import hashlib
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from passlib.context import CryptContext
from pydantic import BaseModel

from database_connection import create_database_pool, get_db


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: str
    role: str
    user_id: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db_pool = await create_database_pool()
    yield
    await app.state.db_pool.close()


app = FastAPI(lifespan=lifespan)


@app.get("/")
def healthcheck():
    return {"status": "ok"}


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _extract_bearer_token(authorization: Optional[str], x_auth_token: Optional[str]) -> str:
    token = x_auth_token
    if not token and authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1]

    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")

    return token


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

    if not pwd_context.verify(payload.password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if str(user["role"]).lower() != "student":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only students can request provisioning access",
        )

    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)
    ttl_minutes = int(os.getenv("AUTH_TOKEN_TTL_MINUTES", "60"))
    expires_at_dt = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)

    await db.execute(
        """
        INSERT INTO user_sessions (user_id, token_hash, ip_address, user_agent, expires_at)
        VALUES ($1, $2, $3, $4, $5)
        """,
        user["user_id"],
        token_hash,
        request.client.host if request.client else None,
        request.headers.get("user-agent"),
        expires_at_dt,
    )

    await db.execute(
        """
        UPDATE users
        SET last_login_at = $1, updated_at = CURRENT_TIMESTAMP
        WHERE user_id = $2
        """,
        datetime.now(timezone.utc),
        user["user_id"],
    )

    return LoginResponse(
        access_token=token,
        expires_at=expires_at_dt.isoformat(),
        role=str(user["role"]),
        user_id=str(user["user_id"]),
    )


@app.get("/auth/me")
async def me(
    authorization: Optional[str] = Header(default=None),
    x_auth_token: Optional[str] = Header(default=None),
    db=Depends(get_db),
):
    token = _extract_bearer_token(authorization, x_auth_token)
    token_hash = _hash_token(token)

    row = await db.fetchrow(
        """
        SELECT u.user_id, u.username, u.role, s.expires_at, s.revoked_at
        FROM user_sessions s
        JOIN users u ON u.user_id = s.user_id
        WHERE s.token_hash = $1
        """,
        token_hash,
    )

    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    if row["revoked_at"] is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token revoked")

    if row["expires_at"] <= datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")

    return {
        "user_id": str(row["user_id"]),
        "username": row["username"],
        "role": str(row["role"]),
        "expires_at": row["expires_at"].isoformat(),
    }


@app.post("/auth/logout")
async def logout(
    authorization: Optional[str] = Header(default=None),
    x_auth_token: Optional[str] = Header(default=None),
    db=Depends(get_db),
):
    token = _extract_bearer_token(authorization, x_auth_token)
    token_hash = _hash_token(token)

    await db.execute(
        """
        UPDATE user_sessions
        SET revoked_at = $1
        WHERE token_hash = $2
        """,
        datetime.now(timezone.utc),
        token_hash,
    )

    return {"status": "logged_out"}
