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
    # Hardening: with the default acks_late=False a task that is mid-flight
    # when a worker dies (crash, container restart, deploy) is ACKed and lost
    # forever — its provisioning_jobs row stays 'processing' and the pool
    # slot it occupies never heals (the plan's "stuck job recovery" gap).
    # Acking only after completion lets another worker redeliver the task.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    # Task events are off by default. celery-exporter builds every task
    # metric from this event stream, so without these two the exporter
    # connects happily and reports nothing at all.
    worker_send_task_events=True,   # worker emits started/succeeded/failed
    task_send_sent_event=True,      # producer emits task-sent
)
