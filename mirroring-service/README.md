# VDI Mirroring Service

This service proxies guacd to a web client and pulls connection details from
Postgres (the same schema used across the VDI stack).

## Run locally

```bash
pip install -r requirements.txt
docker compose up
uvicorn main:app --reload --port 3000
```

Open http://localhost:3000 and provide a user UUID with an active assignment.

## Environment

- `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`
- `GUACD_HOST` (default `127.0.0.1`)
- `GUACD_PORT` (default `4822`)
