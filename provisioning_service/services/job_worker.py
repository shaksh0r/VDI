from __future__ import annotations

import asyncio
import json

from .. import config
from ..db import create_database_pool
from ..message_queue.celery_app import app
from ..openstack import OpenStackClient
from ..openstack import nova
from ..openstack.errors import OpenStackError


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
            error_message = $1,
            error_details = $2::jsonb,
            completed_at = NOW()
        WHERE job_id = $3
        """,
        message,
        json.dumps(details or {}),
        job_id,
    )


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
                        args=[str(instance_id), instance_row["openstack_vm_id"]],
                    )
                    return (str(instance_id), instance_row["openstack_vm_id"])

        payload = {
            "server": {
                "name": f"vdi-{str(instance_id)[:8]}",
                "imageRef": pool_row["base_image_id"],
                "flavorRef": pool_row["flavor_id"],
                "key_name": config.DEFAULT_KEY_NAME,
                "networks": [{"uuid": pool_row["network_id"]}],
                "security_groups": [{"name": "default"}],
            }
        }
        if config.EXTRA_SEC_GROUP_1:
            payload["server"]["security_groups"].append(
                {"name": config.EXTRA_SEC_GROUP_1}
            )
        if config.EXTRA_SEC_GROUP_2:
            payload["server"]["security_groups"].append(
                {"name": config.EXTRA_SEC_GROUP_2}
            )

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
            args=[str(instance_id), openstack_vm_id],
        )

        return (str(instance_id), openstack_vm_id)

    except Exception as exc:
        if pool is not None:
            async with pool.acquire() as conn:
                job = await conn.fetchrow(
                    "SELECT retry_count, max_retries FROM provisioning_jobs WHERE job_id = $1",
                    job_id,
                )
                permanent = isinstance(exc, OpenStackError) and exc.status_code < 500
                if (
                    job is not None
                    and not permanent
                    and job["retry_count"] < job["max_retries"]
                ):
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
                await _fail_job(
                    conn, job_id, str(exc), {"type": type(exc).__name__}
                )
        raise

    finally:
        if client is not None:
            await client.close()
        if pool is not None:
            await pool.close()
