import os
import sys
from pathlib import Path

# Repo root so `import notifications` works for Resend (monorepo).
_VDI_ROOT = Path(__file__).resolve().parents[2]
if str(_VDI_ROOT) not in sys.path:
    sys.path.insert(0, str(_VDI_ROOT))

from celery import Celery

celery = Celery(
    "worker",
    broker=os.getenv("CELERY_BROKER_URL", "amqp://guest:guest@localhost:5672//"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "rpc://"),
)

celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)

# Register tasks module with this app.
import provisioning_service.message_queue.tasks  # noqa: E402, F401
