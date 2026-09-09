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
}
