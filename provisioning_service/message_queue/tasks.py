from provisioning_service.message_queue.celery_app import celery
from provisioning_service.logic.vm import get_instances
import time
from dotenv import load_dotenv
import os

COMPUTE = "http://topcsnova.cloudlab.buet.ac.bd/v2.1"

load_dotenv()
x_auth_token = str(os.getenv("openstack_token"))

@celery.task
def process_data(x):
    print(f"Processing {x}")
    time.sleep(2)
    return x*2

@celery.task
def beat_process(x):
    output = f"beat at:{x}"
    time.sleep(2)
    return output

@celery.task(name="tasks.fetch_instances")
def fetch_instances():
    output = get_instances(COMPUTE,x_auth_token)
    return (output,x_auth_token)