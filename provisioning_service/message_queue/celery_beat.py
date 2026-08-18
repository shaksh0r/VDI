from provisioning_service_old_old.message_queue.celery_app import celery
from provisioning_service_old_old.message_queue.tasks import beat_process,fetch_instances
from datetime import datetime
import uuid

import json 



celery.conf.beat_schedule = {
    "run-every-5-seconds":{
        "task":"beat_task",
        "schedule":10.0,
        "args":()
    }
}

