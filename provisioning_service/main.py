from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import config
from .db import create_database_pool
from .openstack import OpenStackClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting provisioning service…")

    app.state.db_pool = await create_database_pool()
    logger.info("Database pool created (host=%s:%s, db=%s)",
                config.DB_HOST, config.DB_PORT, config.DB_NAME)

    app.state.openstack = OpenStackClient(
        auth_token=config.OPENSTACK_AUTH_TOKEN,
        compute_url=config.OPENSTACK_COMPUTE_URL,
        network_url=config.OPENSTACK_NETWORK_URL,
        image_url=config.OPENSTACK_IMAGE_URL,
        volume_url=config.OPENSTACK_VOLUME_URL,
        timeout=config.OPENSTACK_REQUEST_TIMEOUT,
    )
    logger.info("OpenStack client created (compute=%s, network=%s)",
                config.OPENSTACK_COMPUTE_URL, config.OPENSTACK_NETWORK_URL)

    #TODO: Background tasks will be started here in later:
    # app.state.reconciler_task = asyncio.create_task(reconciler_loop(app))
    # app.state.worker_task = asyncio.create_task(job_worker_loop(app))

    yield

    logger.info("Shutting down provisioning service…")

    #TODO: Cancel background tasks
    # ...

    await app.state.openstack.close()
    logger.info("OpenStack client closed")

    await app.state.db_pool.close()
    logger.info("Database pool closed")


app = FastAPI(
    title="VDI Provisioning Service",
    version="2.0.0",
    lifespan=lifespan,
)



@app.get("/health")
async def health():
    db_ok = False
    os_ok = False
    pool_count = 0
    ready_count = 0

    try:
        async with app.state.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS cnt FROM desktop_pools WHERE status = 'active'"
            )
            pool_count = row["cnt"]
            row2 = await conn.fetchrow(
                "SELECT COUNT(*) AS cnt FROM desktop_instances WHERE status = 'ready'"
            )
            ready_count = row2["cnt"]
        db_ok = True
    except Exception as exc:
        logger.warning("DB health check failed: %s", exc)

    try:
        from .openstack import nova
        await nova.list_servers(app.state.openstack)
        os_ok = True
    except Exception as exc:
        logger.warning("OpenStack health check failed: %s", exc)

    return {
        "status": "ok" if (db_ok and os_ok) else "degraded",
        "db_connected": db_ok,
        "openstack_reachable": os_ok,
        "active_pools": pool_count,
        "total_ready_vms": ready_count,
    }
