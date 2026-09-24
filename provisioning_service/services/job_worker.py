from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx

from .. import config
from ..db import create_database_pool
from ..message_queue.celery_app import app
from ..openstack import OpenStackClient
from ..openstack import cinder
from ..openstack import neutron
from ..openstack import nova
from ..openstack.errors import OpenStackError

logger = logging.getLogger(__name__)


class _PermanentFailure(Exception):
    pass


def _get_os_client() -> OpenStackClient:
    return OpenStackClient(
        auth_url=config.OPENSTACK_AUTH_URL,
        username=config.OPENSTACK_USERNAME,
        password=config.OPENSTACK_PASSWORD,
        project_name=config.OPENSTACK_PROJECT_NAME,
        user_domain=config.OPENSTACK_USER_DOMAIN_NAME,
        project_domain=config.OPENSTACK_PROJECT_DOMAIN_NAME,
        compute_url=config.OPENSTACK_COMPUTE_URL,
        network_url=config.OPENSTACK_NETWORK_URL,
        image_url=config.OPENSTACK_IMAGE_URL,
        volume_url=config.OPENSTACK_VOLUME_URL,
        timeout=config.OPENSTACK_REQUEST_TIMEOUT,
    )


async def _fail_job(conn, job_id: str, message: str, details=None) -> None:
    await conn.execute(
        """
        UPDATE provisioning_jobs
        SET status = 'failed',
            started_at = COALESCE(started_at, NOW()),
            error_message = $1,
            error_details = $2::jsonb,
            completed_at = NOW()
        WHERE job_id = $3
        """,
        message,
        json.dumps(details or {}),
        job_id,
    )
    # Fail the instance alongside the job. create_vm_task inserts the
    # desktop_instances row before calling Nova, so a rejection there (quota
    # exceeded, bad flavor, no valid host) leaves a row with no
    # openstack_vm_id that nothing will ever advance: the job is 'failed'
    # but the instance reads 'provisioning' forever, looks like a VM that is
    # merely slow, and keeps counting against the pool's max_vms.
    #
    # Scoped to instances still mid-build so a job failing after the VM is
    # live (e.g. a delete_vm that could not reach Nova) does not relabel a
    # working desktop, and an already-deleted row is left alone.
    await conn.execute(
        """
        UPDATE desktop_instances
        SET status = 'error', updated_at = NOW()
        WHERE instance_id = (
                  SELECT instance_id FROM provisioning_jobs WHERE job_id = $1
              )
          AND status = 'provisioning'
        """,
        job_id,
    )


async def _handle_task_error(task, conn, job_id: str, exc: Exception) -> bool:
    job = await conn.fetchrow(
        "SELECT retry_count, max_retries FROM provisioning_jobs WHERE job_id = $1",
        job_id,
    )
    permanent = isinstance(exc, _PermanentFailure) or (
        isinstance(exc, OpenStackError) and exc.status_code < 500
    )
    if job is not None and not permanent and job["retry_count"] < job["max_retries"]:
        new_count = job["retry_count"] + 1
        await conn.execute(
            """
            UPDATE provisioning_jobs
            SET retry_count = $1,
                error_message = $2,
                error_details = $3::jsonb
            WHERE job_id = $4
            """,
            new_count,
            str(exc),
            json.dumps({"type": type(exc).__name__}),
            job_id,
        )
        raise task.retry(countdown=2 ** new_count, exc=exc)
    await _fail_job(conn, job_id, str(exc), {"type": type(exc).__name__})
    return True


@app.task(bind=True)
def create_vm_task(self, pool_id: str, job_id: str):
    return asyncio.run(_create_vm_async(self, pool_id, job_id))


async def _create_vm_async(task, pool_id: str, job_id: str):
    pool = None
    client = None
    try:
        pool = await create_database_pool(min_size=1, max_size=2)

        async with pool.acquire() as conn:
            pool_row = await conn.fetchrow(
                "SELECT * FROM desktop_pools WHERE pool_id = $1 AND deleted_at IS NULL",
                pool_id,
            )
            if pool_row is None:
                await _fail_job(conn, job_id, "pool not found")
                return None

            job = await conn.fetchrow(
                "SELECT retry_count, max_retries FROM provisioning_jobs WHERE job_id = $1",
                job_id,
            )
            if job is None:
                return None

            await conn.execute(
                """
                UPDATE provisioning_jobs
                SET status = 'processing', started_at = NOW()
                WHERE job_id = $1
                """,
                job_id,
            )

            instance_row = await conn.fetchrow(
                """
                SELECT i.instance_id, i.openstack_vm_id
                FROM desktop_instances i
                JOIN provisioning_jobs j ON j.instance_id = i.instance_id
                WHERE j.job_id = $1
                """,
                job_id,
            )
            if instance_row is None:
                instance_id = await conn.fetchval(
                    """
                    INSERT INTO desktop_instances (pool_id, status)
                    VALUES ($1, 'provisioning')
                    RETURNING instance_id
                    """,
                    pool_id,
                )
                await conn.execute(
                    """
                    UPDATE provisioning_jobs
                    SET instance_id = $1
                    WHERE job_id = $2
                    """,
                    instance_id,
                    job_id,
                )
            else:
                instance_id = instance_row["instance_id"]
                if instance_row["openstack_vm_id"]:
                    app.send_task(
                        "provisioning_service.services.job_worker.finalize_vm_task",
                        args=[
                            str(instance_id),
                            instance_row["openstack_vm_id"],
                            job_id,
                        ],
                    )
                    return (str(instance_id), instance_row["openstack_vm_id"])

        payload = {
            "server": {
                "name": f"vdi-{str(instance_id)[:8]}",
                "flavorRef": pool_row["flavor_id"],
                "key_name": config.VM_KEY_NAME,
                "networks": [{"uuid": pool_row["network_id"]}],
                "security_groups": [{"name": config.VM_SECURITY_GROUP}],
                "block_device_mapping_v2": [
                    {
                        "boot_index": 0,
                        "uuid": pool_row["base_image_id"],
                        "source_type": "image",
                        "destination_type": "volume",
                        "volume_size": config.VM_BOOT_VOLUME_SIZE_GB,
                        "delete_on_termination": True,
                    }
                ],
            }
        }

        client = _get_os_client()
        response = await nova.create_server(client, payload)
        openstack_vm_id = response.get("server", {}).get("id")
        if not openstack_vm_id:
            raise RuntimeError(f"Nova create response missing server id: {response}")

        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE desktop_instances
                SET openstack_vm_id = $1
                WHERE instance_id = $2
                """,
                openstack_vm_id,
                instance_id,
            )
            await conn.execute(
                """
                UPDATE provisioning_jobs
                SET instance_id = $1,
                    job_params = $2::jsonb
                WHERE job_id = $3
                """,
                instance_id,
                json.dumps({"openstack_vm_id": openstack_vm_id}),
                job_id,
            )

        app.send_task(
            "provisioning_service.services.job_worker.finalize_vm_task",
            args=[str(instance_id), openstack_vm_id, job_id],
        )

        return (str(instance_id), openstack_vm_id)

    except Exception as exc:
        if pool is not None:
            async with pool.acquire() as conn:
                await _handle_task_error(task, conn, job_id, exc)
        raise

    finally:
        if client is not None:
            await client.close()
        if pool is not None:
            await pool.close()


@app.task(bind=True)
def finalize_vm_task(self, instance_id: str, openstack_vm_id: str, job_id: str):
    return asyncio.run(
        _finalize_vm_async(self, instance_id, openstack_vm_id, job_id)
    )


async def _finalize_vm_async(task, instance_id: str, openstack_vm_id: str, job_id: str):
    pool = None
    client = None
    try:
        pool = await create_database_pool(min_size=1, max_size=2)

        async with pool.acquire() as conn:
            instance = await conn.fetchrow(
                "SELECT * FROM desktop_instances WHERE instance_id = $1",
                instance_id,
            )
            if instance is None:
                return None
            if instance["status"] != "provisioning":
                return None

        client = _get_os_client()

        deadline = time.time() + config.VM_CREATION_TIMEOUT_SECONDS
        server = None
        while True:
            response = await nova.get_server(client, openstack_vm_id)
            if "server" not in response:
                raise _PermanentFailure(
                    f"server {openstack_vm_id} not found: {response}"
                )
            server = response["server"]
            if server["status"] == "ACTIVE":
                break
            if server["status"] == "ERROR":
                raise _PermanentFailure(
                    f"nova server in ERROR state: {server.get('fault', {})}"
                )
            if time.time() >= deadline:
                raise _PermanentFailure(
                    f"VM did not reach ACTIVE within "
                    f"{config.VM_CREATION_TIMEOUT_SECONDS}s"
                )
            await asyncio.sleep(config.VM_CREATION_POLL_INTERVAL)

        private_ip = None
        for addr_list in server.get("addresses", {}).values():
            for addr in addr_list:
                if addr.get("OS-EXT-IPS:type") == "fixed":
                    private_ip = addr["addr"]
                    break
            if private_ip:
                break

        ports = (await neutron.list_ports(client, device_id=openstack_vm_id)).get(
            "ports", []
        )
        if not ports:
            raise _PermanentFailure(
                f"no neutron port found for server {openstack_vm_id}"
            )
        port_id = ports[0]["id"]

        # Idempotent FIP attach. A retried finalize (transient DB error,
        # worker restart, etc.) may already have associated a FIP with this
        # port; attaching a second one fails with
        # FloatingIPPortAlreadyAssociated and turns a recoverable retry
        # into a permanent error — leaving zombie 'error' instances.
        fips = (await neutron.list_floating_ips(client)).get("floatingips", [])
        port_fip = next((f for f in fips if f.get("port_id") == port_id), None)
        if port_fip is not None:
            fip_id = port_fip["id"]
            floating_ip_addr = port_fip["floating_ip_address"]
            logger.info(
                "reusing existing FIP %s (%s) on port %s",
                fip_id, floating_ip_addr, port_id,
            )
        else:
            created = await neutron.create_floating_ip(
                client,
                floating_network_id=config.EXTERNAL_NETWORK_ID,
                port_id=port_id,
                description=f"vdi-{openstack_vm_id[:8]}",
            )
            fip = created.get("floatingip", {})
            fip_id = fip.get("id")
            if not fip_id:
                raise _PermanentFailure(f"failed to allocate floating ip: {created}")
            floating_ip_addr = fip.get("floating_ip_address")

        # ── RDP readiness probe ────────────────────────────────────────────
        # Do NOT mark the instance ready until the guest has booted and the
        # RDP server actually accepts connections. Freshly provisioned VMs
        # take minutes to start xrdp; users connecting during that window
        # hit session failures, and repeated failed connections can wedge
        # the VM's xrdp (a self-reinforcing failure loop). The probe waits
        # for a TCP accept on the RDP port, then lets the session layer
        # settle for a grace period before the VM becomes claimable.
        rdp_ready = False
        probe_deadline = time.time() + config.RDP_READY_TIMEOUT_SECONDS
        while time.time() < probe_deadline:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(floating_ip_addr, 3389), timeout=5
                )
                writer.close()
                await writer.wait_closed()
                rdp_ready = True
                break
            except Exception:
                await asyncio.sleep(config.VM_CREATION_POLL_INTERVAL)
        if not rdp_ready:
            raise _PermanentFailure(
                f"RDP port 3389 never opened on {floating_ip_addr} within "
                f"{config.VM_CREATION_TIMEOUT_SECONDS}s"
            )
        # Grace period: xrdp can accept TCP before LightDM/PAM is fully
        # ready — a session started in that window drops within seconds.
        await asyncio.sleep(30)
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(floating_ip_addr, 3389), timeout=5
            )
            writer.close()
            await writer.wait_closed()
        except Exception:
            raise _PermanentFailure(
                f"RDP port on {floating_ip_addr} closed during settle window"
            )
        logger.info("RDP ready on %s — marking instance ready", floating_ip_addr)

        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE desktop_instances
                SET status = 'ready',
                    floating_ip = $1,
                    private_ip = $2,
                    connection_details = $3::jsonb,
                    provisioned_at = NOW()
                WHERE instance_id = $4
                """,
                floating_ip_addr,
                private_ip,
                json.dumps({"fip_id": fip_id, "fip_address": floating_ip_addr}),
                instance_id,
            )
            await conn.execute(
                """
                UPDATE provisioning_jobs
                SET status = 'completed',
                    started_at = COALESCE(started_at, NOW()),
                    completed_at = NOW()
                WHERE job_id = $1
                """,
                job_id,
            )

        return (floating_ip_addr, private_ip)

    except Exception as exc:
        if pool is not None:
            async with pool.acquire() as conn:
                failed = await _handle_task_error(task, conn, job_id, exc)
                if failed:
                    await conn.execute(
                        """
                        UPDATE desktop_instances
                        SET status = 'error', updated_at = NOW()
                        WHERE instance_id = $1 AND status = 'provisioning'
                        """,
                        instance_id,
                    )
        raise

    finally:
        if client is not None:
            await client.close()
        if pool is not None:
            await pool.close()


def _check_deleted(response: httpx.Response, resource: str) -> None:
    if response.status_code in (200, 202, 204, 404):
        return
    body = None
    try:
        body = response.json()
    except Exception:
        body = response.text
    raise OpenStackError(
        response.status_code, f"failed to delete {resource}", body
    )


@app.task(bind=True)
def delete_vm_task(self, instance_id: str, job_id: str):
    return asyncio.run(_delete_vm_async(self, instance_id, job_id))


async def _delete_vm_async(task, instance_id: str, job_id: str):
    pool = None
    client = None
    try:
        pool = await create_database_pool(min_size=1, max_size=2)

        async with pool.acquire() as conn:
            instance = await conn.fetchrow(
                "SELECT * FROM desktop_instances WHERE instance_id = $1",
                instance_id,
            )
            if instance is None or instance["status"] == "deleted":
                return None

            active = await conn.fetchval(
                """
                SELECT 1 FROM user_assignments
                WHERE instance_id = $1 AND released_at IS NULL
                LIMIT 1
                """,
                instance_id,
            )
            if active:
                await _fail_job(conn, job_id, "instance has an active assignment")
                return None

            await conn.execute(
                """
                UPDATE desktop_instances
                SET status = 'deleting', updated_at = NOW()
                WHERE instance_id = $1
                """,
                instance_id,
            )
            await conn.execute(
                """
                UPDATE provisioning_jobs
                SET status = 'processing', started_at = NOW()
                WHERE job_id = $1
                """,
                job_id,
            )

        client = _get_os_client()

        details = {}
        if instance["connection_details"]:
            details = json.loads(instance["connection_details"])
        fip_id = details.get("fip_id")
        if fip_id:
            response = await neutron.delete_floating_ip(client, fip_id)
            _check_deleted(response, f"floating ip {fip_id}")

        openstack_vm_id = instance["openstack_vm_id"]
        if openstack_vm_id:
            response = await nova.delete_server(client, openstack_vm_id)
            _check_deleted(response, f"server {openstack_vm_id}")

        # Some clouds keep the boot volume after server deletion even with
        # delete_on_termination=True — clean the attached volumes up so the
        # project quota never leaks.
        if openstack_vm_id:
            try:
                freed = await cinder.delete_volumes_for_server(
                    client, openstack_vm_id
                )
                if freed:
                    logger.info(
                        "deleted %d volume(s) orphaned by server %s",
                        freed, openstack_vm_id,
                    )
            except Exception as exc:  # noqa: BLE001 — never fail the job on this
                logger.warning(
                    "volume cleanup for server %s failed: %s",
                    openstack_vm_id, exc,
                )

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE desktop_instances
                    SET status = 'deleted', updated_at = NOW()
                    WHERE instance_id = $1
                    """,
                    instance_id,
                )
                await conn.execute(
                    """
                    UPDATE desktop_pools
                    SET current_count = GREATEST(current_count - 1, 0),
                        updated_at = NOW()
                    WHERE pool_id = $1
                    """,
                    instance["pool_id"],
                )
                await conn.execute(
                    """
                    UPDATE provisioning_jobs
                    SET status = 'completed',
                        started_at = COALESCE(started_at, NOW()),
                        completed_at = NOW()
                    WHERE job_id = $1
                    """,
                    job_id,
                )

        return None

    except Exception as exc:
        if pool is not None:
            async with pool.acquire() as conn:
                await _handle_task_error(task, conn, job_id, exc)
        raise

    finally:
        if client is not None:
            await client.close()
        if pool is not None:
            await pool.close()
