from provisioning_service_old_old.message_queue.celery_app import celery
from provisioning_service_old_old.logic.vm import (
    get_instances,
    get_detailed_instances,
    create_instance_local_storage,
)

from provisioning_service_old_old.logic.network import (
    create_floating_ip,
    get_port_by_device,
    attach_floating_ip,
)
from provisioning_service_old_old.message_queue.database import create_database_pool
import time
from dotenv import load_dotenv
import os
import asyncio
import uuid
from datetime import datetime

from ..services.pooling.pool_manager import Pool_Manager


load_dotenv()

COMPUTE             = "http://topcsnova.cloudlab.buet.ac.bd/v2.1"
NETWORK             = "http://topcsneutron.cloudlab.buet.ac.bd/v2.0"      # Neutron base URL
EXTERNAL_NETWORK_ID = os.getenv("EXTERNAL_NETWORK_ID")    # External network for floating IPs
x_auth_token        = str(os.getenv("openstack_token"))


EXTRA_SEC_GROUP_1 = os.getenv("PROD_SEC")
EXTRA_SEC_GROUP_2 = os.getenv("ALLOW_PING_SSH")


DEFAULT_KEY_NAME = "default-key"

POLL_INTERVAL_SECONDS = 5
POLL_MAX_ATTEMPTS     = 24   

POOL_NAME = "pool_1"



@celery.task
def process_data(x):
    print(f"Processing {x}")
    time.sleep(2)
    return x * 2


@celery.task
def beat_process(x):
    output = f"beat at:{x}"
    time.sleep(2)
    return output


@celery.task(name="tasks.fetch_instances")
def fetch_instances():
    output = get_instances(COMPUTE, x_auth_token)
    return output



@celery.task(name="tasks.create_vm")
def create_vm(payload):
    server = payload.get("server") or {}
    if not server.get("key_name"):
        server["key_name"] = DEFAULT_KEY_NAME
        payload["server"] = server

    nova_response = create_instance_local_storage(COMPUTE, x_auth_token, payload)

    if not nova_response:
        return

    openstack_vm_id = nova_response.get("server", {}).get("id")
    if not openstack_vm_id:
        return

    async def insert_and_bump():
        pool = await create_database_pool()
        async with pool.acquire() as conn:
            config = await conn.fetchrow(
                "SELECT pool_id, current_count FROM desktop_pools WHERE name = $1",
                POOL_NAME,
            )
            if not config:
                return

            pool_id       = config["pool_id"]
            current_count = config["current_count"]

            # Insert placeholder row — IPs are filled in by finalize_vm
            await conn.execute(
                """
                INSERT INTO desktop_instances (
                    pool_id,
                    openstack_vm_id,
                    status,
                    created_at,
                    updated_at
                )
                VALUES ($1, $2, 'provisioning', $3, $3)
                ON CONFLICT (openstack_vm_id) DO NOTHING
                """,
                pool_id,
                openstack_vm_id,
                datetime.utcnow(),
            )

            await conn.execute(
                "UPDATE desktop_pools SET current_count = $1 WHERE name = $2",
                current_count + 1,
                POOL_NAME,
            )

        await pool.close()

    asyncio.run(insert_and_bump())

    # Fire finalize_vm to poll, attach IP, and mark ready
    finalize_vm.delay(openstack_vm_id)

    return nova_response


# -----------------------------------------------------------------------------
#  finalize_vm  (new)
#  Polls Nova until ACTIVE, allocates/reuses a floating IP, attaches it,
#  then marks the desktop_instances row as 'ready'.
# -----------------------------------------------------------------------------

@celery.task(name="tasks.finalize_vm", bind=True, max_retries=0)
def finalize_vm(self, openstack_vm_id: str):

    async def run():
        # Step 1: Poll Nova until the VM is ACTIVE
        vm_data = None
        for attempt in range(1, POLL_MAX_ATTEMPTS + 1):
            detailed = await get_detailed_instances(COMPUTE, x_auth_token)
            servers  = detailed.get("servers", [])

            for server in servers:
                if server["id"] == openstack_vm_id:
                    if server.get("OS-EXT-STS:vm_state") == "active":
                        vm_data = server
                    break

            if vm_data:
                break

            await asyncio.sleep(POLL_INTERVAL_SECONDS)

        if not vm_data:
            await _mark_error(openstack_vm_id, "VM did not reach ACTIVE state within timeout")
            return

        # Step 2: Extract the private (fixed) IP from addresses
        private_ip = None
        for network_name, addr_list in vm_data.get("addresses", {}).items():
            for addr in addr_list:
                if addr.get("OS-EXT-IPS:type") == "fixed":
                    private_ip = addr["addr"]
                    break
            if private_ip:
                break

        # Step 3: Get the port for this VM using the device_id filter
        # Neutron filters server-side so the response only contains this VM's ports
        ports_response = await get_port_by_device(NETWORK, x_auth_token, openstack_vm_id)
        ports          = ports_response.get("ports", [])

        if not ports:
            await _mark_error(openstack_vm_id, "Could not find Neutron port for VM")
            return

        port_id = ports[0]["id"]
        print(f"[finalize_vm] port_id={port_id}")

        # Step 4: Always create a new floating IP
        fip_payload = {
            "floatingip": {
                "floating_network_id": EXTERNAL_NETWORK_ID,
                "description":        f"vdi-pool-{openstack_vm_id[:8]}",
            }
        }
        new_fip = await create_floating_ip(NETWORK, x_auth_token, fip_payload)
        fip_obj = new_fip.get("floatingip", {})

        floating_ip_id   = fip_obj.get("id")
        floating_ip_addr = fip_obj.get("floating_ip_address")

        if not floating_ip_id:
            await _mark_error(openstack_vm_id, "Failed to allocate floating IP")
            return

        # Step 5: Attach the floating IP to the VM's port
        attach_payload  = {"floatingip": {"port_id": port_id}}
        attach_response = await attach_floating_ip(
            NETWORK, x_auth_token, floating_ip_id, attach_payload
        )

        if "floatingip" not in attach_response:
            await _mark_error(openstack_vm_id, f"Failed to attach floating IP: {attach_response}")
            return

        # Step 6: Mark the instance as ready in the database
        pool = await create_database_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE desktop_instances
                SET floating_ip    = $1,
                    private_ip     = $2,
                    status         = 'ready',
                    provisioned_at = $3,
                    updated_at     = $3
                WHERE openstack_vm_id = $4
                """,
                floating_ip_addr,
                private_ip,
                datetime.utcnow(),
                openstack_vm_id,
            )
        await pool.close()

    asyncio.run(run())


# -----------------------------------------------------------------------------
#  _mark_error  (helper)
#  Sets a desktop_instances row to 'error' for any failure in finalize_vm
# -----------------------------------------------------------------------------

async def _mark_error(openstack_vm_id: str, reason: str) -> None:
    print(f"[finalize_vm] ERROR for VM {openstack_vm_id}: {reason}")
    try:
        pool = await create_database_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE desktop_instances
                SET status     = 'error',
                    updated_at = $1
                WHERE openstack_vm_id = $2
                """,
                datetime.utcnow(),
                openstack_vm_id,
            )
        await pool.close()
    except Exception as e:
        print(f"[finalize_vm] Failed to mark error in DB: {e}")


# -----------------------------------------------------------------------------
#  generate_pool  (unchanged logic)
# -----------------------------------------------------------------------------

@celery.task(name="tasks.pool_generator")
def generate_pool():
    async def get_conf():
        pool = await create_database_pool()
        async with pool.acquire() as conn:
            config = await conn.fetchrow(
                "SELECT * FROM desktop_pools WHERE name = $1", POOL_NAME
            )
            if not config:
                return

            config = dict(config)

            if config["current_count"] < config["min_vms"]:
                vm_need = config["min_vms"] - config["current_count"]
                for i in range(vm_need):
                    name      = "vm-" + str(uuid.uuid4())

                    # Build security_groups list: always "default" plus any extra
                    security_groups = [{"name": "default"}]
                    if EXTRA_SEC_GROUP_1:
                        security_groups.append({"name": EXTRA_SEC_GROUP_1})
                    if EXTRA_SEC_GROUP_2:
                        security_groups.append({"name": EXTRA_SEC_GROUP_2})

                    payload   = {
                        "server": {
                            "name":            name,
                            "imageRef":        config["base_image_id"],
                            "flavorRef":       config["flavor_id"],
                            "key_name":        DEFAULT_KEY_NAME,
                            "networks": [
                                {"uuid": config["network_id"]}
                            ],
                            "security_groups": security_groups,
                        }
                    }
                    create_vm.delay(payload)

    asyncio.run(get_conf())