import logging

from celery import Celery
from celery.signals import worker_ready

from .. import config

logger = logging.getLogger(__name__)

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
)


@worker_ready.connect
def _sweep_orphans_on_startup(sender=None, **_kwargs):
    """Run the orphan sweep as soon as a worker boots.

    The beat schedule alone is not enough for the `docker compose down -v`
    case: Celery beat aligns to a fixed wall-clock interval, so after `up`
    the first reconcile can be up to RECONCILE_INTERVAL_SECONDS away, and
    leaked VMs keep consuming quota in the meantime. Dispatching here means
    untracked VMs from a previous run are destroyed within seconds of the
    stack coming up, with the periodic beat remaining the ongoing safety net
    for leaks that appear later.

    Sent as a task rather than run inline so the worker never blocks during
    startup.

    NOTE: `sender` is a Celery Consumer, which has no `.logger` attribute —
    logging there raises AttributeError inside the signal handler and the
    traceback is swallowed by celery's dispatch machinery. Use this module's
    own logger instead.
    """
    try:
        app.send_task(
            "provisioning_service.services.reconciler.reconcile_instances_task"
        )
        logger.info(
            "startup orphan sweep dispatched (periodic interval=%ss)",
            config.RECONCILE_INTERVAL_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 — never block worker startup
        logger.warning("startup orphan sweep dispatch failed: %s", exc)
