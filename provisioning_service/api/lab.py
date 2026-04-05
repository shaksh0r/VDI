"""
Teacher lab launch: JSON roster → background VMs + Resend emails with 6-digit codes.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, EmailStr, Field

from provisioning_service.database_connection import get_db

router = APIRouter(prefix="/lab", tags=["lab"])


def _verify_provisioning_secret(
    x_provisioning_secret: Annotated[
        Optional[str], Header(alias="X-Provisioning-Secret")
    ] = None,
) -> None:
    expected = os.getenv("PROVISIONING_INTERNAL_SECRET", "").strip()
    if not expected:
        return
    if not x_provisioning_secret or x_provisioning_secret != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing X-Provisioning-Secret")


class StudentRosterEntry(BaseModel):
    email: EmailStr
    student_id: Optional[str] = Field(None, max_length=100)
    full_name: Optional[str] = Field(None, max_length=255)


class LabDeployRequest(BaseModel):
    pool_name: str = Field(..., min_length=1, max_length=255)
    lab_title: str = Field(..., min_length=1, max_length=255)
    portal_url: Optional[str] = None
    teacher_username: Optional[str] = Field(
        None,
        description="Optional: link deployment to users.user_id for auditing",
    )
    students: list[StudentRosterEntry] = Field(..., min_length=1)


class LabDeployResponse(BaseModel):
    deployment_id: str
    status: str
    message: str


@router.post("/deploy", response_model=LabDeployResponse)
async def deploy_lab(
    body: LabDeployRequest,
    _: None = Depends(_verify_provisioning_secret),
    db=Depends(get_db),
):
    """
    Queue a lab deployment: one VM + unique 6-digit code per student; emails sent via Celery + Resend.

    Requires a running Celery worker and RabbitMQ (or your CELERY_BROKER_URL).
    Set RESEND_API_KEY and RESEND_FROM for email delivery.
    """
    pool = await db.fetchrow(
        """
        SELECT pool_id FROM desktop_pools
        WHERE name = $1 AND deleted_at IS NULL
        """,
        body.pool_name.strip(),
    )
    if not pool:
        raise HTTPException(status_code=404, detail=f"Pool not found: {body.pool_name}")

    teacher_id = None
    if body.teacher_username:
        row = await db.fetchrow(
            """
            SELECT user_id FROM users
            WHERE username = $1 AND deleted_at IS NULL
            """,
            body.teacher_username.strip(),
        )
        if row:
            teacher_id = row["user_id"]

    roster = [s.model_dump(mode="json") for s in body.students]

    dep_id = await db.fetchval(
        """
        INSERT INTO lab_deployments (
            pool_id, teacher_user_id, lab_title, portal_url, roster_json, status
        )
        VALUES ($1, $2, $3, $4, $5::jsonb, 'pending')
        RETURNING deployment_id::text
        """,
        pool["pool_id"],
        teacher_id,
        body.lab_title.strip(),
        body.portal_url.strip() if body.portal_url else None,
        json.dumps(roster),
    )

    from provisioning_service.message_queue.tasks import process_lab_deployment

    process_lab_deployment.delay(dep_id)

    return LabDeployResponse(
        deployment_id=dep_id,
        status="queued",
        message="Deployment queued; ensure Celery worker is running.",
    )


@router.get("/deployments/{deployment_id}")
async def get_deployment(
    deployment_id: uuid.UUID,
    _: None = Depends(_verify_provisioning_secret),
    db=Depends(get_db),
):
    dep = await db.fetchrow(
        """
        SELECT deployment_id, pool_id, lab_title, portal_url, status, error_message,
               created_at, completed_at, teacher_user_id
        FROM lab_deployments WHERE deployment_id = $1
        """,
        deployment_id,
    )
    if not dep:
        raise HTTPException(status_code=404, detail="Deployment not found")
    seats = await db.fetch(
        """
        SELECT seat_id, access_code, email, full_name, vm_error,
               email_sent_at, email_last_error, email_attempts, openstack_server_id
        FROM lab_access_codes
        WHERE deployment_id = $1
        ORDER BY created_at
        """,
        deployment_id,
    )
    return {
        "deployment": dict(dep),
        "seats": [dict(s) for s in seats],
    }
