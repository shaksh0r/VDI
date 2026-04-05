import asyncio
import logging
import os
import time
import uuid

from dotenv import load_dotenv

from provisioning_service.config_env import nova_compute_url, openstack_token
from provisioning_service.logic.lab_deploy import run_lab_deployment_sync
from provisioning_service.logic.vm import create_instance_local_storage, get_instances
from provisioning_service.message_queue.celery_app import celery
from provisioning_service.message_queue.database import create_database_pool

load_dotenv()

COMPUTE = nova_compute_url()
x_auth_token = str(openstack_token())

logger = logging.getLogger(__name__)

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


@celery.task(name="tasks.process_lab_deployment")
def process_lab_deployment(deployment_id: str) -> None:
    """Background: create VMs + seats + queue Resend per student."""
    run_lab_deployment_sync(deployment_id)


@celery.task(
    bind=True,
    name="tasks.send_lab_seat_email",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    max_retries=5,
)
def send_lab_seat_email_task(self, seat_id: str) -> str | None:
    """Send one access-code email via Resend; retries on transient failures."""
    import asyncpg

    from notifications import ResendMailError, send_access_code_email

    from provisioning_service.config_env import db_settings

    async def _run() -> str | None:
        cfg = db_settings()
        pool = await asyncpg.create_pool(
            user=cfg["user"],
            password=cfg["password"],
            database=cfg["database"],
            host=cfg["host"],
            port=cfg["port"],
            min_size=1,
            max_size=3,
        )
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT s.access_code, s.email, s.full_name, s.vm_error,
                           d.lab_title, d.portal_url
                    FROM lab_access_codes s
                    JOIN lab_deployments d ON d.deployment_id = s.deployment_id
                    WHERE s.seat_id = $1::uuid
                    """,
                    seat_id,
                )
                if not row:
                    logger.warning("send_lab_seat_email: unknown seat_id=%s", seat_id)
                    return None
                if row["vm_error"]:
                    logger.info(
                        "skip email for seat %s: VM not provisioned (%s)",
                        seat_id,
                        row["vm_error"][:80],
                    )
                    return None

                code = row["access_code"]
                to_email = row["email"]
                lab_title = row["lab_title"]
                portal = row["portal_url"] or None
                full_name = row["full_name"]

                try:
                    mid = send_access_code_email(
                        to_email=to_email,
                        code=str(code),
                        full_name=full_name,
                        lab_title=lab_title,
                        portal_url=portal,
                    )
                except ResendMailError:
                    await conn.execute(
                        """
                        UPDATE lab_access_codes
                        SET email_attempts = email_attempts + 1,
                            email_last_error = $2
                        WHERE seat_id = $1::uuid
                        """,
                        seat_id,
                        "resend configuration or API error (see logs)",
                    )
                    raise

                await conn.execute(
                    """
                    UPDATE lab_access_codes
                    SET email_sent_at = CURRENT_TIMESTAMP,
                        email_last_error = NULL,
                        resend_message_id = $2,
                        email_attempts = email_attempts + 1
                    WHERE seat_id = $1::uuid
                    """,
                    seat_id,
                    mid,
                )
                return mid
        finally:
            await pool.close()

    return asyncio.run(_run())