from .. import config
from .celery_app import app

app.conf.beat_schedule = {
    "expire-sessions": {
        "task": "provisioning_service.services.reconciler.expire_sessions_task",
        "schedule": 30.0,
    },
    "replenish-pools": {
        "task": "provisioning_service.services.reconciler.replenish_pools_task",
        "schedule": 30.0,
    },
    # Sweep OpenStack for VMs this service created that the database no longer
    # tracks (e.g. after `docker compose down -v`). Without this, every DB
    # reset leaks a full pool of running VMs and then provisions min_vms more.
    # Interval is configurable — a shorter window means leaked VMs are
    # reclaimed sooner, at the cost of more OpenStack API calls.
    "reconcile-instances": {
        "task": "provisioning_service.services.reconciler.reconcile_instances_task",
        "schedule": float(config.RECONCILE_INTERVAL_SECONDS),
    },
}
