from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..message_queue.celery_app import app
from .deps import get_db, require_roles

router = APIRouter(prefix="/admin", tags=["admin"])

admin = require_roles("admin")


def _job_dict(row) -> dict:
    data = dict(row)
    if data.get("job_params"):
        try:
            data["job_params"] = json.loads(data["job_params"])
        except Exception:
            pass
    return data


@router.get("/jobs")
async def list_jobs(
    status: Optional[str] = Query(default=None),
    job_type: Optional[str] = Query(default=None),
    user: dict = Depends(admin),
    conn=Depends(get_db),
):
    query = "SELECT * FROM provisioning_jobs j"
    where = []
    params = []
    if status:
        params.append(status)
        where.append(f"j.status = ${len(params)}")
    if job_type:
        params.append(job_type)
        where.append(f"j.job_type = ${len(params)}")
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY j.queued_at DESC"
    rows = await conn.fetch(query, *params)
    return {"jobs": [_job_dict(row) for row in rows], "total": len(rows)}


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: str,
    user: dict = Depends(admin),
    conn=Depends(get_db),
):
    row = await conn.fetchrow(
        "SELECT * FROM provisioning_jobs WHERE job_id = $1", job_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _job_dict(row)


@router.post("/jobs/{job_id}/retry")
async def retry_job(
    job_id: str,
    user: dict = Depends(admin),
    conn=Depends(get_db),
):
    row = await conn.fetchrow(
        """
        SELECT job_type, status, pool_id, instance_id
        FROM provisioning_jobs WHERE job_id = $1
        """,
        job_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    if row["status"] != "failed":
        raise HTTPException(
            status_code=400,
            detail=f"job is {row['status']}, only failed jobs can be retried",
        )

    await conn.execute(
        """
        UPDATE provisioning_jobs
        SET status = 'queued', retry_count = 0, error_message = NULL,
            error_details = NULL, started_at = NULL, completed_at = NULL
        WHERE job_id = $1
        """,
        job_id,
    )

    if row["job_type"] == "create_vm":
        app.send_task(
            "provisioning_service.services.job_worker.create_vm_task",
            args=[str(row["pool_id"]), job_id],
        )
    elif row["job_type"] == "delete_vm":
        app.send_task(
            "provisioning_service.services.job_worker.delete_vm_task",
            args=[str(row["instance_id"]), job_id],
        )
    else:
        raise HTTPException(
            status_code=400,
            detail=f"retry not supported for job type {row['job_type']}",
        )
    return {"ok": True, "job_id": job_id}
