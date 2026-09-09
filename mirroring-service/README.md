# VDI Mirroring Service

A FastAPI service that mirrors a provisioned VM into a browser window via
Apache Guacamole. It is one component of the VDI stack:

```
browser ── POST /auth/login ──────────────────────► auth-service (token)
browser ── POST /provision/connect (Bearer) ──────► provisioning (floating_ip)
browser ── WS  /ws/guacd?token=… (Guacamole) ─────► this service  (RDP stream)
browser ── GET /provision/status (poll) ──────────► provisioning (expiry)
browser ── POST /provision/disconnect ────────────► provisioning (release)
```

The service serves the frontend (`static/`), authenticates the WebSocket
against auth-service, resolves the target VM **server-side** from the user's
active provisioning assignment (never from client-supplied parameters), and
relays the Guacamole protocol between the browser WebSocket and guacd over TCP.

## Quick Start (with the root docker-compose.yml)

From the repository root:

```bash
docker compose up -d --build
```

The frontend is served at `http://localhost:8000`. Sign in with an
auth-service account, press **Connect** to claim a VM from your pool, and the
remote desktop appears in the browser.

## Environment (.env — not tracked in git, injected via compose `env_file`)

### Service endpoints (defaults match the compose service names)
| Variable | Default | Purpose |
|---|---|---|
| `GUACD_HOST` / `GUACD_PORT` | `guacd` / `4822` | Guacamole daemon location |
| `AUTH_SERVICE_URL` | `http://auth-service:8003` | Token validation (`/auth/me`) |
| `PROVISIONING_SERVICE_URL` | `http://provisioning-server:8001` | Session resolution (`/provision/status`) |
| `AUTH_PUBLIC_URL` / `PROVISION_PUBLIC_URL` | *(empty)* | Browser-reachable URLs; set when a reverse proxy routes `/auth` and `/provision` on this origin (TLS deployment). Served via `/api/config`; empty = frontend derives ports from `window.location`. |

### RDP credentials (pool-level; per-VM credentials would come from `desktop_instances.connection_details` via provisioning)
`VM_PORT` (3389), `VM_USERNAME`, `VM_PASSWORD`, `VM_PROTOCOL` (rdp),
`VM_DOMAIN`, `VM_SECURITY`, plus the display defaults (`VM_WIDTH`,
`VM_HEIGHT`, `VM_DPI`) and RDP feature flags (`VM_COLOR_DEPTH`, …).

### Hardening knobs
| Variable | Default | Purpose |
|---|---|---|
| `GUACD_CONNECT_TIMEOUT` | `10` | TCP connect timeout to guacd (s) |
| `GUACD_HANDSHAKE_TIMEOUT` | `20` | Whole guacd handshake timeout (s) |
| `GUACD_READ_TIMEOUT` | `120` | Idle read timeout on the guacd stream (s) |
| `MAX_INSTRUCTION_BYTES` | `33554432` | Per-instruction buffer cap (bytes) |
| `MAX_CONCURRENT_SESSIONS` | `20` | Global concurrent WS sessions |
| `MAX_SESSIONS_PER_IP` | `3` | Concurrent WS sessions per client IP |
| `ALLOWED_ORIGINS` | *(empty = `*`)* | CORS allowlist (comma-separated) — set in production |
| `WS_ALLOWED_ORIGINS` | *(empty = any)* | Enforced on the WebSocket handshake (comma-separated) — set in production |

## Security model & behaviors

- **Auth:** every `/ws/guacd` connection requires `?token=<auth token>`,
  validated against auth-service `/auth/me` (close 1008 on failure).
- **Per-session target:** the VM IP comes from `GET /provision/status` for the
  authenticated user — clients cannot point the tunnel at arbitrary hosts.
- **Session expiry:** the WebSocket closes with 1012 at the assignment's
  `expires_at`; the authoritative release is provisioning's
  `expire_sessions` beat. The mirroring service does **not** release on
  teardown, so transient drops don't destroy non-persistent VMs (and the
  frontend auto-reconnect can resume the same VM).
- **Client input whitelist:** only `mouse`/`key`/`size`/`clipboard`/`sync`/
  `disconnect`/`nop` instructions reach guacd; handshake instructions and
  malformed frames are dropped. `nop` keepalives are **forwarded** — guacd
  treats any instruction as user activity and aborts sessions idle for ~15 s
  ("User is not responding"); the browser's 5 s nop is what keeps idle
  sessions alive.
- **Limits:** viewport params are clamped (640–7680 × 480–4320, dpi 48–288);
  instruction buffers are capped; concurrent sessions and per-IP sessions are
  capped (close 1013).

## Deployment notes

- **Secrets:** `.env` is untracked and excluded from the Docker image
  (`.dockerignore`); compose injects it via `env_file`. **Rotate any VM
  credentials that were previously committed to git** — history retains them.
- **TLS/WSS:** terminate TLS on a reverse proxy in front of this service (the
  frontend auto-selects `wss://` when served over https). For same-origin
  proxying of `/auth` and `/provision`, set `AUTH_PUBLIC_URL` /
  `PROVISION_PUBLIC_URL` and `ALLOWED_ORIGINS` / `WS_ALLOWED_ORIGINS` to the
  public origin.
- **Healthcheck:** compose probes `/api/health` with a `python` one-liner
  (the slim image has no `curl`).
- **Known limitation:** freshly provisioned pool VMs need a few minutes for
  the guest to boot and start xrdp; the first session may also drop once due
  to the xrdp/LightDM startup race — the frontend auto-reconnect (2→32 s
  backoff, 5 attempts) recovers from this.
