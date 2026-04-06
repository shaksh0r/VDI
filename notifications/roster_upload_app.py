"""
Dev-only API: upload a lab roster JSON → unique 6-digit codes → Resend per student.

No authentication (for local testing only).

Run from repo root::

  pip install -r notifications/requirements.txt
  uvicorn notifications.roster_upload_app:app --host 127.0.0.1 --port 8020

Upload::

  curl -s -X POST http://127.0.0.1:8020/send-codes \\
    -F "file=@notifications/sample_access_codes_batch.json"
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from notifications.resend_mail import ResendMailError, send_access_codes_from_lab_data

logger = logging.getLogger(__name__)

app = FastAPI(title="VDI roster mail (dev)", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/send-codes")
async def send_codes(file: UploadFile = File(..., description="Lab JSON with students[].email")) -> dict[str, Any]:
    """
    Accept a JSON file matching ``sample_access_codes_batch.json``:
    ``lab_title``, ``portal_url`` (optional), ``students`` array with ``email``, optional ``full_name``.
    """
    raw_bytes = await file.read()
    if not raw_bytes.strip():
        raise HTTPException(status_code=400, detail="Empty file")
    try:
        text = raw_bytes.decode("utf-8")
        data = json.loads(text)
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=400, detail=f"File must be UTF-8: {e}") from e
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}") from e

    try:
        sent = send_access_codes_from_lab_data(data)
    except ResendMailError as e:
        logger.warning("Send failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e)) from e

    return {"ok": True, "count": len(sent), "sent": sent}
