"""
Teacher lab deployment: roster → Nova VM per student → lab_access_codes → queue Resend email.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import uuid
from typing import Any

import asyncpg

from provisioning_service.config_env import (
    db_settings,
    nova_compute_url,
    openstack_key_name,
    openstack_token,
)
from provisioning_service.logic.vm import create_instance_local_storage

logger = logging.getLogger(__name__)


async def _generate_unique_code(conn: asyncpg.Connection) -> str:
    for _ in range(200):
        code = f"{secrets.randbelow(1_000_000):06d}"
        exists = await conn.fetchval(
            "SELECT 1 FROM lab_access_codes WHERE access_code = $1",
            code,
        )
        if not exists:
            return code
    raise RuntimeError("could not allocate a unique 6-digit code")


def _queue_seat_email(seat_id: str) -> None:
    from provisioning_service.message_queue.tasks import send_lab_seat_email_task

    send_lab_seat_email_task.delay(seat_id)


async def process_lab_deployment(deployment_id: uuid.UUID) -> None:
    cfg = db_settings()
    pool = await asyncpg.create_pool(
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        host=cfg["host"],
        port=cfg["port"],
        min_size=1,
        max_size=5,
    )
    token = openstack_token()
    compute = nova_compute_url()
    key_name = openstack_key_name()

    try:
        async with pool.acquire() as conn:
            dep = await conn.fetchrow(
                """
                SELECT d.deployment_id, d.pool_id, d.lab_title, d.portal_url, d.roster_json,
                       p.base_image_id, p.flavor_id, p.network_id
                FROM lab_deployments d
                JOIN desktop_pools p ON p.pool_id = d.pool_id
                WHERE d.deployment_id = $1 AND p.deleted_at IS NULL
                """,
                deployment_id,
            )
            if not dep:
                logger.error("lab deployment %s not found or pool deleted", deployment_id)
                return

            await conn.execute(
                """
                UPDATE lab_deployments
                SET status = 'processing', error_message = NULL
                WHERE deployment_id = $1
                """,
                deployment_id,
            )

            roster = dep["roster_json"]
            if isinstance(roster, str):
                roster = json.loads(roster)
            if not isinstance(roster, list):
                await conn.execute(
                    """
                    UPDATE lab_deployments
                    SET status = 'failed',
                        error_message = 'roster_json must be a JSON array',
                        completed_at = CURRENT_TIMESTAMP
                    WHERE deployment_id = $1
                    """,
                    deployment_id,
                )
                return

            any_vm_fail = False
            any_roster_skip = False

            for entry in roster:
                if not isinstance(entry, dict):
                    any_roster_skip = True
                    continue
                email = (entry.get("email") or "").strip().lower()
                if not email or "@" not in email:
                    any_roster_skip = True
                    continue

                code = await _generate_unique_code(conn)
                ext_id = entry.get("student_id") or entry.get("student_external_id")
                full_name = entry.get("full_name")

                try:
                    seat_id = await conn.fetchval(
                        """
                        INSERT INTO lab_access_codes (
                            deployment_id, access_code, email,
                            student_external_id, full_name
                        )
                        VALUES ($1, $2, $3, $4, $5)
                        RETURNING seat_id::text
                        """,
                        deployment_id,
                        code,
                        email,
                        str(ext_id)[:100] if ext_id else None,
                        (str(full_name)[:255] if full_name else None),
                    )
                except asyncpg.exceptions.UniqueViolationError:
                    logger.warning("duplicate email in roster for deployment %s", deployment_id)
                    any_roster_skip = True
                    continue

                if not token:
                    await conn.execute(
                        """
                        UPDATE lab_access_codes
                        SET vm_error = 'OpenStack token not configured (openstack_token / OPENSTACK_TOKEN)'
                        WHERE seat_id = $1::uuid
                        """,
                        seat_id,
                    )
                    any_vm_fail = True
                    continue

                vm_name = f"lab-{str(deployment_id)[:8]}-{code}"[:240]
                payload: dict[str, Any] = {
                    "server": {
                        "name": vm_name,
                        "imageRef": dep["base_image_id"],
                        "flavorRef": dep["flavor_id"],
                        "key_name": key_name,
                        "networks": [{"uuid": dep["network_id"]}],
                        "security_groups": [{"name": "default"}],
                    }
                }

                try:
                    out = create_instance_local_storage(compute, token, payload)
                except Exception as exc:
                    logger.exception("Nova create failed for %s", email)
                    await conn.execute(
                        """
                        UPDATE lab_access_codes SET vm_error = $2 WHERE seat_id = $1::uuid
                        """,
                        seat_id,
                        str(exc)[:2000],
                    )
                    any_vm_fail = True
                    continue

                nova_id = None
                if isinstance(out, dict):
                    nova_id = (out.get("server") or {}).get("id")

                if not nova_id:
                    err = json.dumps(out)[:2000] if out else "empty response"
                    await conn.execute(
                        """
                        UPDATE lab_access_codes SET vm_error = $2 WHERE seat_id = $1::uuid
                        """,
                        seat_id,
                        f"Nova error: {err}",
                    )
                    any_vm_fail = True
                    continue

                instance_row_id = await conn.fetchval(
                    """
                    INSERT INTO desktop_instances (pool_id, openstack_vm_id, status)
                    VALUES ($1, $2, 'provisioning')
                    RETURNING instance_id
                    """,
                    dep["pool_id"],
                    nova_id,
                )
                await conn.execute(
                    """
                    UPDATE lab_access_codes
                    SET openstack_server_id = $2, instance_id = $3, vm_error = NULL
                    WHERE seat_id = $1::uuid
                    """,
                    seat_id,
                    nova_id,
                    instance_row_id,
                )
                _queue_seat_email(seat_id)

            if any_vm_fail or any_roster_skip:
                final = "partial"
                msg_parts = []
                if any_vm_fail:
                    msg_parts.append("one or more VMs failed; check lab_access_codes.vm_error")
                if any_roster_skip:
                    msg_parts.append("one or more roster rows were skipped")
                err_msg = "; ".join(msg_parts)
            else:
                final = "completed"
                err_msg = None

            await conn.execute(
                """
                UPDATE lab_deployments
                SET status = $2::lab_deployment_status,
                    completed_at = CURRENT_TIMESTAMP,
                    error_message = $3
                WHERE deployment_id = $1
                """,
                deployment_id,
                final,
                err_msg,
            )
    finally:
        await pool.close()


def run_lab_deployment_sync(deployment_id: str) -> None:
    asyncio.run(process_lab_deployment(uuid.UUID(deployment_id)))
