# Environment files

| File | Purpose |
|------|---------|
| **`.env`** (repo root) | `POSTGRES_*` — read by Compose for substitution |
| **`monitoring-service/.env`** | Grafana (`GF_*`) + optional OpenStack for the metrics service (`OS_*`) |
| **`notifications/.env`** | Resend (`RESEND_*`) for local CLI / any service that sends mail |

Copy examples on first clone: `cp .env.example .env`, `cp monitoring-service/.env.example monitoring-service/.env`, `cp notifications/.env.example notifications/.env`.

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
Set `RESEND_*` in **`notifications/.env`** (see `notifications/.env.example`). From the repo root:

```bash
pip install -r notifications/requirements.txt
python -m notifications.resend_mail --to teammate@example.com --code 123456
```

Use `from notifications import send_access_code_email` in your provisioning or API code after VMs and codes are created.
