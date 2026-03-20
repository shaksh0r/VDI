from provisioning_service.message_queue.celery_app import celery
from provisioning_service.message_queue.tasks import beat_process,fetch_instances
from datetime import datetime

celery.conf.beat_schedule = {
    "run-every-5-seconds":{
        "task":"tasks.fetch_instances",
        "schedule":30.0,
        "args":()
    }
}