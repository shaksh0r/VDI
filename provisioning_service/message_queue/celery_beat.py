from provisioning_service.message_queue.celery_app import celery
from provisioning_service.message_queue.tasks import beat_process,fetch_instances
from datetime import datetime
import uuid

import json 

with open("test.json","r") as file:
    data = json.load(file)

data["server"]["name"] = "vm-" + str(uuid.uuid4()) 


celery.conf.beat_schedule = {
    "run-every-5-seconds":{
        "task":"tasks.pool_generator",
        "schedule":30.0,
        "args":()
    }
}

