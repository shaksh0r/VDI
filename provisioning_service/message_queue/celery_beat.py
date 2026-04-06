from provisioning_service.message_queue.celery_app import celery
from provisioning_service.message_queue.tasks import beat_process,fetch_instances
from datetime import datetime
import uuid

import json 



celery.conf.beat_schedule = {
    "run-every-5-seconds":{
        "task":"tasks.pool_generator",
        "schedule":20.0,
        "args":()
    }
}

