"""
VDI Monitoring Microservice.

Exposes Prometheus metrics for:
- VM resource metrics (from OpenStack Nova)
- Host-level metrics (compute nodes)
- Pool/course-level aggregation (from DB)
- Session counts (from DB; Guacamole session details can be added later)

Prometheus scrapes /metrics. Grafana visualizes via Prometheus datasource.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_client import REGISTRY, generate_latest

from config import openstack_configured

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: optional one-time collect or health check
    logger.info("VDI Monitoring service starting; OpenStack configured=%s", openstack_configured())
    yield
    # Shutdown
    logger.info("VDI Monitoring service shutting down")


app = FastAPI(
    title="VDI Monitoring Microservice",
    description="Prometheus metrics for Virtual Desktop Infrastructure (OpenStack + DB)",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "vdi-monitoring"}


@app.get("/ready")
async def ready():
    """Readiness: optional checks (DB, OpenStack) can be added."""
    return {"status": "ready"}


from fastapi.responses import Response


@app.get("/metrics", response_class=Response)
async def metrics():
    """Prometheus scrape endpoint."""
    from collectors.openstack_metrics import collect_openstack_metrics
    from collectors.db_metrics import collect_db_metrics

    if openstack_configured():
        collect_openstack_metrics()
    collect_db_metrics()

    return Response(
        content=generate_latest(REGISTRY),
        media_type="text/plain; charset=utf-8; version=0.0.4",
    )
