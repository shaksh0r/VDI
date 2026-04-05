# Run
```sudo docker compose up -d database monitoring-service prometheus grafana```

# Confirm
```sudo docker compose ps```

# Test Endpoints
```
curl http://localhost:9091/health
curl http://localhost:9091/metrics | head
curl http://localhost:9090      # Prometheus UI HTML
curl http://localhost:3000      # Grafana login HTML
```

# Stop
```sudo docker compose down```

# Email (Resend)
Set `RESEND_API_KEY` and `RESEND_FROM` in `.env` (see `.env.example`). From the repo root, with a venv that has `notifications/requirements.txt` installed:

```bash
pip install -r notifications/requirements.txt
export RESEND_API_KEY=re_... RESEND_FROM="VDI <onboarding@resend.dev>"
python -m notifications.resend_mail --to teammate@example.com --code 123456
```

Use `from notifications import send_access_code_email` in your provisioning or API code after VMs and codes are created.

# Lab deploy (provisioning + Resend + Celery)

1. Apply DB schema: new installs get `lab_*` tables from `database/init.sql`. Existing DBs: run `database/migrations/002_lab_deployments.sql`.
2. Set `OPENSTACK_TOKEN`, `RESEND_API_KEY`, `RESEND_FROM`, and (recommended) `PROVISIONING_INTERNAL_SECRET` in `.env`.
3. Start stack with provisioning profile:

```bash
sudo docker compose --profile provisioning up -d database rabbitmq provisioning-api celery-worker
```

4. Create a lab (example):

```bash
curl -s -X POST http://localhost:8010/lab/deploy \
  -H "Content-Type: application/json" \
  -H "X-Provisioning-Secret: $PROVISIONING_INTERNAL_SECRET" \
  -d '{
    "pool_name": "pool_1",
    "lab_title": "Demo lab",
    "portal_url": "https://vdi.example.edu",
    "students": [
      {"email": "teammate@example.com", "student_id": "2024001", "full_name": "Teammate"}
    ]
  }'
```

5. Check status: `GET http://localhost:8010/lab/deployments/{deployment_id}` with the same secret header.

Local dev (no Docker): `PYTHONPATH=.` from repo root, RabbitMQ running, then `uvicorn provisioning_service.server:app --port 8010` and a Celery worker as in `docker-compose` `celery-worker` command.
