from provisioning_service.message_queue.celery_app import celery
from provisioning_service.logic.vm import get_instances,create_instance_local_storage
from provisioning_service.message_queue.database import create_database_pool
import time
from dotenv import load_dotenv
import os
import asyncio
import uuid

from ..services.pooling.pool_manager import Pool_Manager

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
    return output


@celery.task(name = "tasks.create_vm")
def create_vm(payload):
    output = create_instance_local_storage(COMPUTE,x_auth_token,payload)

    if not output:
        return
    
    async def update_db():
        pool = await create_database_pool()
        pool_name = "pool_1"
        async with pool.acquire() as conn:
            config = await conn.fetchrow("SELECT * FROM desktop_pools WHERE name = $1",pool_name)
            if not config:
                return
            
            config = dict(config)

            current_count = config["current_count"]
            
            current_count += 1

            await conn.execute("UPDATE desktop_pools SET current_count = $1 WHERE name = $2",current_count,pool_name)

    asyncio.run(update_db())
    return output
    

@celery.task(name="tasks.pool_generator")
def generate_pool():
    pool_name = "pool_1"
    async def get_conf():
        pool = await create_database_pool()
        async with pool.acquire() as conn:
            config = await conn.fetchrow("SELECT * FROM desktop_pools WHERE name = $1",pool_name)

            if not config:
                return
            config = dict(config)

            if config["current_count"] < config["min_vms"]:
                vm_need = config["min_vms"] - config["current_count"]
                for i in range(vm_need):
                    name = "vm-" + str(uuid.uuid4())
                    imageRef = config['base_image_id']
                    flavorRef = config['flavor_id']
                    key_name = "default-key"
                    networkID = config['network_id']
                    payload = {
                        "server": {
                        "name": name,
                        "imageRef": imageRef,
                        "flavorRef": flavorRef,
                        "key_name": key_name,
                        "networks": [
                            {
                                "uuid": networkID
                            }
                        ],
                        "security_groups": [
                            {
                                "name": "default"
                            }
                        ]
                        }
                    }

                    create_vm.delay(payload)
    asyncio.run(get_conf())