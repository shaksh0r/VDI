from __future__ import annotations

from datetime import datetime

import httpx

from models.common import Domain
from models.keystone_models import (
    Identity,
    Password,
    Project,
    ScopedAuth,
    ScopedAuthRequest,
    Scope,
    User,
)

from .errors import OpenStackError


async def authenticate(
    auth_url: str,
    username: str,
    password: str,
    project_name: str,
    user_domain: str,
    project_domain: str,
    timeout: float = 30.0,
) -> tuple[str, float]:
    base = auth_url.rstrip("/")
    if base.endswith("/v3"):
        base = base[:-3]
    url = f"{base}/v3/auth/tokens"

    payload = ScopedAuthRequest(
        auth=ScopedAuth(
            identity=Identity(
                methods=["password"],
                password=Password(
                    user=User(
                        name=username,
                        domain=Domain(name=user_domain),
                        password=password,
                    )
                ),
            ),
            scope=Scope(
                project=Project(
                    name=project_name,
                    domain=Domain(name=project_domain),
                )
            ),
        )
    )

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, json=payload.model_dump())

    if response.status_code < 200 or response.status_code >= 300:
        body = None
        try:
            body = response.json()
        except Exception:
            body = response.text
        raise OpenStackError(
            response.status_code, "Keystone authentication failed", body
        )

    token = response.headers.get("X-Subject-Token")
    if not token:
        raise OpenStackError(
            response.status_code,
            "Keystone response missing X-Subject-Token header",
        )

    expires_at = response.json()["token"]["expires_at"]
    expires_epoch = datetime.fromisoformat(expires_at).timestamp()

    return token, expires_epoch
