# VDI Monitoring Microservice

Part of the Virtual Desktop Infrastructure (VDI) on OpenStack. This service exposes **Prometheus** metrics for VM, host, pool (course), and session-level monitoring. **Grafana** is used for dashboards.

## Architecture

```
Frontend Dashboards (Grafana)
        ↓
Monitoring Microservice (this service)
        ↓
OpenStack APIs (Nova) + Monitoring Database (PostgreSQL)
        ↓
Prometheus (scrapes /metrics)
```

## Metrics (aligned with VDI-Monitoring-Goal)

| Category | Metrics |
|----------|---------|
| **VM resource** | `vdi_vm_status`, `vdi_vm_count_by_status`, `vdi_vm_total` (from OpenStack Nova) |
| **Host-level (admin)** | `vdi_host_vm_count`, `vdi_host_total` (hypervisors) |
| **Pool / course-level** | `vdi_pool_vm_total`, `vdi_pool_vm_active`, `vdi_pool_utilization_percent`, `vdi_pool_active_users`, `vdi_pool_vm_by_status` |
| **Session-level** | `vdi_active_sessions_total` (from DB; Guacamole session details can be added later) |
| **DB instance status** | `vdi_db_instance_count_by_status`, `vdi_db_instance_total` |

VM-level CPU/RAM/disk usage can be added later via Ceilometer or in-guest agents and exported here or via a separate OpenStack exporter.

## Run with Docker Compose

From the project root:

```bash
# Copy env and set OpenStack + DB credentials
cp .env.example .env
# Edit .env: POSTGRES_*, OS_*, GRAFANA_*

docker compose up -d database monitoring-service prometheus grafana
```

- **Monitoring service**: http://localhost:9091/health, http://localhost:9091/metrics  
- **Prometheus**: http://localhost:9090  
- **Grafana**: http://localhost:3000 (default login `admin` / `admin`; change in `.env`)

## Run locally (development)

```bash
cd monitoring-service
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
# Set env (see .env.example) or use a .env file
uvicorn main:app --reload --port 9091
```

Prometheus must be configured to scrape `http://localhost:9091/metrics` when running the service locally.

## Configuration

- **OpenStack**: `OS_AUTH_URL`, `OS_USERNAME`, `OS_PASSWORD`, `OS_PROJECT_NAME`, and optionally `OS_*_DOMAIN_NAME`. If not set, VM/host metrics from Nova are skipped; DB metrics still work.
- **Database**: `MONITORING_DB_*` (same PostgreSQL as the rest of VDI).

## Prometheus & Grafana

- **Prometheus** config: `monitoring-service/prometheus/prometheus.yml` (scrapes this service every 30s).
- **Grafana** is provisioned with a Prometheus datasource and a **VDI Overview** dashboard under folder **VDI**.

To add more dashboards, place JSON in `grafana/provisioning/dashboards/json/` or create them in the Grafana UI.
