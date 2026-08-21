from celery import Celery

from .. import config

app = Celery(
    "provisioning_service",
    broker=config.CELERY_BROKER_URL,
    include=[
        "provisioning_service.services.job_worker",
        "provisioning_service.services.reconciler",
    ],
)

app.conf.update(
    timezone="UTC",
    enable_utc=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
)
