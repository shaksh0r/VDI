# VDI Provisioning Service — Implementation Plan

## Overview

This document lays out the implementation plan for the **provisioning service**, the core backend responsible for orchestrating VM lifecycle on OpenStack. The service must support two user-facing workflows:

1. **Student self-service** — a student visits the web app, clicks "Connect", and gets a non-persistent VM streamed to their browser. The system maintains a warm pool of ready VMs, assigns them atomically on demand, and recycles them after the session ends.

2. **Teacher virtual lab** — a teacher defines a lab (image, flavor, count), the system provisions N VMs and generates per-VM credentials. The teacher distributes those credentials to students, who use them to claim their assigned VM.

The mirroring service (guacd WebSocket relay) and the auth service are already built and are **out of scope** for this plan. We only touch provisioning.

---

## Current State

### What exists (new `provisioning_service/`)

| Layer             | Files                                                                                                                              | Status                                                                                                                                                                               |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| OpenStack client  | `openstack/client.py`, `nova.py`, `neutron.py`, `cinder.py`, `glance.py`                                                           | **Needs modification** — async HTTP client exists but uses a static `auth_token`. Must be changed to authenticate via Keystone credentials and auto-refresh the token before expiry. |
| Configuration     | `config.py`                                                                                                                        | **Needs modification** — has `OPENSTACK_AUTH_TOKEN` (static). Must be replaced with Keystone credential vars (auth URL, username, password, project, domains).                       |
| Database pool     | `db.py`                                                                                                                            | **Done** — asyncpg pool factory                                                                                                                                                      |
| Pydantic models   | `models/requests.py`, `models/responses.py`                                                                                        | **Done** — request/response schemas                                                                                                                                                  |
| FastAPI app shell | `main.py`                                                                                                                          | **Partial** — lifespan creates DB pool + OpenStack client, `/health` endpoint exists                                                                                                 |
| API routes        | `api/admin.py`, `api/pools.py`, `api/vms.py`, `api/deps.py`                                                                        | **Empty stubs**                                                                                                                                                                      |
| Business logic    | `services/assignment.py`, `services/pool_service.py`, `services/vm_service.py`, `services/reconciler.py`, `services/job_worker.py` | **Empty stubs**                                                                                                                                                                      |
| Dockerfile        | `Dockerfile`                                                                                                                       | **Done**                                                                                                                                                                             |
| Celery infra      | None in new service                                                                                                                | **Needs creation**                                                                                                                                                                   |

### What exists (legacy reference code, now merged into `provisioning_service/`)

A working reference implementation that:

- Has a hardcoded pool name (`pool_1`)
- Uses Celery for async VM creation with a poll-then-finalize pattern
- Handles `/provision/connect`, `/provision/disconnect`, `/provision/pool/expand`
- Has in-memory session expiry (lost on restart — **needs fixing**)
- Does NOT use the new DB schema's `provisioning_jobs` table for tracking

### Database schema (`database/init.sql`)

Fully defined with all needed tables: `users`, `user_sessions`, `desktop_pools`, `desktop_instances`, `provisioning_jobs`, `user_assignments`, `scaling_policies`, `vm_health_checks`, `audit_logs`, `system_config`. Views, triggers, and functions are in place.

---

## Architecture Decisions

### 1. Celery for async VM operations

**Decision:** Keep Celery + RabbitMQ for long-running VM operations.

VM creation on OpenStack is inherently async (2–3 minutes from POST to ACTIVE). We need a reliable worker that can:

- Dispatch a create request to Nova
- Poll until ACTIVE
- Attach floating IP
- Update DB

Celery with RabbitMQ gives us persistent task queuing, retry semantics, and the ability to run multiple workers. The old service already proves this pattern works.

**What we will NOT do:** Do NOT build a custom polling loop inside the FastAPI process. FastAPI should remain responsive for API calls only.

### 2. Persistent job tracking via `provisioning_jobs` table

**Decision:** Every async operation (create VM, delete VM) creates a row in `provisioning_jobs`. The Celery worker updates status as it progresses.

This gives us:

- Visibility into what's happening (API can return job status)
- Crash recovery (on restart, worker picks up queued/processing jobs)
- Audit trail

### 3. DB-backed session expiry (not in-memory)

**Decision:** Store session expiry in `user_assignments` table. A periodic reconciler task checks for expired sessions and releases VMs.

The old service uses `asyncio.create_task` with `asyncio.sleep` — lost on restart. We need:

- `user_assignments.assigned_at` + `desktop_pools.max_session_duration_minutes` → compute expiry
- A periodic Celery Beat task (every 30s) scans for expired assignments and releases VMs

### 4. Pool-based VM lifecycle (not manual VM management)

**Decision:** All VMs belong to a pool. Pools define the template (image, flavor, network), sizing (min/max), and assignment policy. Individual VMs are never created outside a pool context.

### 5. Atomic VM claiming with `FOR UPDATE SKIP LOCKED`

**Decision:** Keep the proven pattern from the old service. When assigning a VM to a user, use PostgreSQL row-level locking to prevent double-assignment under concurrency.

### 6. Role-based pool access

**Decision:** Each pool has an `allowed_roles` column (array of user_role). When a student requests a VM, the system finds pools that allow their role. When a teacher creates a lab pool, they set `allowed_roles` to control which students can access it.

### 7. Keystone authentication with automatic token refresh

**Decision:** The OpenStack client must NOT use a static, long-lived token. Instead:

- Accept **Keystone credentials** (auth URL, username, password, project name, user domain, project domain) from configuration
- On first use (lazy init), authenticate with Keystone's `POST /v3/auth/tokens` to obtain a scoped token
- The token is returned in the `X-Subject-Token` **response header** (not the body) — the client must capture it
  - **⚠️ The old `provisioning_service/logic/identity.py` has a bug:** it only does `return response.json()` and discards the `X-Subject-Token` header entirely. The new implementation must read `response.headers["X-Subject-Token"]`.
- The response body contains `token.expires_at` (ISO 8601 UTC timestamp, e.g. `"2025-01-20T19:38:34.123456Z"`) — the client tracks this
- The response body ALSO contains a **service catalog** (`token.catalog[]`) listing endpoints for compute, network, image, volume, etc. **Decision:** V1 uses hardcoded service URLs from config (confirmed correct). Service catalog parsing for dynamic endpoint discovery is deferred to V2.
- Before every API request, check if the token is within a safety margin of expiry (e.g., 5 minutes before `expires_at`). If so, transparently re-authenticate before sending the request.
- If any request receives a `401 Unauthorized` (token expired earlier than expected), immediately re-authenticate and retry the request once.
- Use an `asyncio.Lock` around the authentication call to prevent concurrent token refresh storms when multiple requests all notice the token is about to expire.
- **Credentials source for V1:** A config file (e.g., JSON or TOML) read at startup. **Production:** Environment variables. The client should support both.

**Why this matters:** The university's OpenStack deployment does not issue permanent API tokens. Tokens expire after 2–3 hours. A long-running provisioning service that uses a static token will break mid-operation after token expiry. The auto-refresh mechanism ensures uninterrupted operation.

**What this replaces:** The current `OpenStackClient.__init__` takes a bare `auth_token: str`. This will be replaced with Keystone credential parameters. The existing `_request` method that blindly attaches `X-Auth-Token: self.auth_token` will instead call an `async def _get_token() -> str` helper that lazily authenticates and refreshes as needed.

---

## Workflow Design

### Workflow 1 — Student Self-Service Non-Persistent VM

```
Student                 Frontend               Provisioning API         Celery Worker          OpenStack
  |                        |                         |                      |                     |
  |--click Connect-------->|                         |                      |                     |
  |                        |--POST /provision/connect|                      |                     |
  |                        |  (Bearer token)         |                      |                     |
  |                        |                         |--validate token----->|                     |
  |                        |                         |  (call auth-service) |                     |
  |                        |                         |                      |                     |
  |                        |                         |--find pool by role   |                     |
  |                        |                         |--SELECT ... FOR UPDATE SKIP LOCKED              |
  |                        |                         |  (claim ready VM)    |                     |
  |                        |                         |                      |                     |
  |                        |                         |--INSERT user_assignments                    |
  |                        |                         |--UPDATE instance→in_use                     |
  |                        |                         |                      |                     |
  |                        |<--{floating_ip, expires}--|                      |                     |
  |                        |                         |                      |                     |
  |                        |--open WebSocket to      |                      |                     |
  |                        |  mirroring-service      |                      |                     |
  |                        |  (/ws/guacd)            |                      |                     |
  |<==== RDP stream =======|                         |                      |                     |
  |                        |                         |                      |                     |
  |  ... session ...       |                         |                      |                     |
  |                        |                         |                      |                     |
  |--click Disconnect----->|                         |                      |                     |
  |                        |--POST /provision/disconnect                    |                     |
  |                        |                         |--validate token      |                     |
  |                        |                         |--UPDATE instance→ready                      |
  |                        |                         |--UPDATE assignment (released_at)            |
  |                        |<--{ok}------------------|                      |                     |
  |                        |                         |                      |                     |
  |                        |                         |  (VM returns to pool, ready for next student)
```

**Session expiry (no explicit disconnect):**

```
Periodic Beat (every 30s)  →  Celery Worker scans user_assignments
  WHERE released_at IS NULL
    AND (assigned_at + pool.max_session_duration_minutes * interval '1 minute') < NOW()
  → UPDATE instance status = 'ready', SET assignment.released_at = NOW()
```

**Frontend session-expiry contract:** while a session is active, the frontend polls `GET /provision/status` every 15–30s. When the assignment is gone (expired or released), the frontend shows a "session expired" message and closes the WebSocket. **Decision:** frontend polling — no push or teardown call to the mirroring service (out of scope).

**Pool replenishment:**

```
Periodic Beat (every 30s)  →  Celery Worker scans desktop_pools
  WHERE status = 'active' AND current_count < min_vms
  → For each pool, calculate deficit = min_vms - (ready_count + provisioning_count)
  → Dispatch create_vm tasks for the deficit
```

---

### Workflow 2 — Teacher Virtual Lab

```
Teacher                Frontend              Provisioning API         Celery Worker          OpenStack
  |                       |                       |                      |                     |
  |--Create Lab Pool----->|                       |                      |                     |
  |  (image, flavor,      |                       |                      |                     |
  |   network, VM count)  |                       |                      |                     |
  |                       |--POST /admin/pools    |                      |                     |
  |                       |                       |--validate (is teacher)|                     |
  |                       |                       |--INSERT desktop_pools|                     |
  |                       |                       |--INSERT scaling_policy (auto-trigger)         |
  |                       |                       |--for i in 1..count:  |                     |
  |                       |                       |   dispatch create_vm |                     |
  |                       |                       |                      |--POST /servers (Nova)|
  |                       |                       |                      |  (per VM)            |
  |                       |                       |                      |                      |
  |                       |                       |                      |--poll until ACTIVE   |
  |                       |                       |                      |--create floating IP  |
  |                       |                       |                      |--attach FIP to port  |
  |                       |                       |                      |--UPDATE instance→ready|
  |                       |                       |                      |                      |
  |                       |<--{pool_id, status}---|                       |                      |
  |                       |                       |                       |                      |
  |--View Lab Details---->|                       |                       |                      |
  |                       |--GET /admin/pools/{id}/credentials           |                      |
  |                       |                       |--query desktop_instances WHERE pool_id        |
  |                       |                       |   + their credentials |                      |
  |                       |<--[{vm_ip, username, password}, ...]---------|                      |
  |                       |                       |                       |                      |
  |  (distributes credentials to students)        |                       |                      |
  |                       |                       |                       |                      |
  Student                 |                       |                       |                      |
  |--Visit website,       |                       |                       |                      |
  |  enter lab credentials|                       |                       |                      |
  |                       |--POST /provision/claim-with-credential       |                      |
  |                       |   {pool_id, credential} |                      |                      |
  |                       |                       |--validate credential  |                      |
  |                       |                       |--assign specific VM   |                      |
  |                       |<--{floating_ip}-------|                       |                      |
  |<==== RDP stream ======|                       |                       |                      |
```

**Credential generation strategy:**

When a teacher creates a lab pool, each VM gets:

- A unique username (e.g., `labuser-<short-uuid>`)
- A randomly generated password

These credentials are set on the VM at provision time (via cloud-init / user-data passed to Nova). The provisioning service stores them in `desktop_instances.connection_details` (JSONB column). The teacher fetches them via a dedicated endpoint.

---

## Implementation Phases

### Phase 0 — OpenStack Keystone Authentication (Prerequisite)

**Goal:** Modify the OpenStack client to authenticate via Keystone credentials with automatic token refresh. This is required before any VM operations can work.

#### 0.1 Update Configuration

**File to modify:** `provisioning_service/config.py`

Replace the static `OPENSTACK_AUTH_TOKEN` with Keystone credential variables:

```
OPENSTACK_AUTH_URL       — Keystone endpoint (confirmed: "http://topcskeystone.cloudlab.buet.ac.bd")
OPENSTACK_USERNAME       — OpenStack username
OPENSTACK_PASSWORD       — OpenStack password
OPENSTACK_PROJECT_NAME   — OpenStack project/tenant name
OPENSTACK_USER_DOMAIN    — User domain (default: "Default")
OPENSTACK_PROJECT_DOMAIN — Project domain (default: "Default")
```

Service endpoints (confirmed correct, hardcoded for V1 — service catalog parsing deferred to V2):

```
OPENSTACK_COMPUTE_URL = "http://topcsnova.cloudlab.buet.ac.bd/v2.1"
OPENSTACK_NETWORK_URL = "http://topcsneutron.cloudlab.buet.ac.bd/v2.0"
OPENSTACK_IMAGE_URL   = "http://topcsglance.cloudlab.buet.ac.bd/v2"
OPENSTACK_VOLUME_URL  = "http://topcscinder.cloudlab.buet.ac.bd/v3"
```

For V1, these are read from environment variables at startup. The `docker-compose.yml` already has `environment:` blocks on each service container — we inject them there.

#### 0.2 Add Keystone Auth Module

**File to create:** `provisioning_service/openstack/keystone.py`

A new module for Keystone identity operations:

- `authenticate(auth_url, username, password, project_name, user_domain, project_domain) → tuple[str, float]`
  - POST to `{auth_url}/v3/auth/tokens` with scoped auth payload (password method)
  - **Read `X-Subject-Token` from the response header** — this is the token
  - Parse `token.expires_at` from response body (ISO 8601 string like `"2025-01-20T19:38:34.123456Z"`), convert to Unix epoch float with `datetime.fromisoformat(...).timestamp()`
  - Return `(token_string, expires_at_epoch_float)`
  - The request body uses the existing `models/keystone_models.py` Pydantic models (`ScopedAuthRequest`)
  - **Domain note:** The Pydantic `Domain` model uses `name` field. OpenStack accepts both `"name"` and `"id"` for domain identification — using `name: "Default"` is correct for the BUET deployment.

#### 0.3 Refactor OpenStackClient for Auto-Refresh

**File to modify:** `provisioning_service/openstack/client.py`

Changes to `OpenStackClient`:

- **`__init__`** now takes: `auth_url`, `username`, `password`, `project_name`, `user_domain`, `project_domain`, plus the existing service URLs and timeout. No more `auth_token` parameter.

- **New private state:**
  - `self._token: str | None` — current cached token
  - `self._token_expires_at: float | None` — epoch timestamp of expiry
  - `self._token_lock: asyncio.Lock` — prevents concurrent refresh
  - `self._token_safety_margin: float` — seconds before expiry to trigger refresh (default: 300 = 5 min)

- **New method `_get_token() → str`:**
  1. If token exists and `(now + safety_margin) < expires_at`: return cached token (fast path)
  2. Acquire `_token_lock`
  3. Double-check: another coroutine may have refreshed while we waited for the lock
  4. If still expired/expiring: call `keystone.authenticate(...)`, store new token + expiry
  5. Release lock, return token

- **Modified `_request` method:**
  - Call `await self._get_token()` instead of reading `self.auth_token`
  - If response is `401 Unauthorized`: force a token refresh (set `_token = None`, call `_get_token()` again), retry the request once

- The convenience methods (`compute_request`, `network_request`, etc.) remain unchanged — they only call `_request`.

- **`main.py` lifespan changes:** The `OpenStackClient(...)` construction now passes Keystone credentials instead of a static token.

- **Celery worker note:** Celery tasks run in a **separate process** from FastAPI. Each Celery task that needs OpenStack access must instantiate its own `OpenStackClient` with the same Keystone credentials (read from config/env). The auto-refresh mechanism works identically regardless of whether the client lives in a FastAPI request or a Celery task — it's a self-contained object. This means the config must be accessible from both the FastAPI process and the Celery worker process (i.e., use environment variables, not a file that only exists in one container).

#### 0.4 Test Authentication Standalone

Before building anything else, verify the client can:

1. Authenticate with Keystone and get a token
2. Make a simple API call (e.g., `GET /v2.1/servers` via Nova) with the token
3. Artificially shorten the safety margin and confirm auto-refresh triggers

---

### Phase 1 — Core Infrastructure (Foundation)

**Goal:** Get the skeleton working end-to-end with a single hardcoded pool.

#### 1.1 Celery App & Worker Setup

Create the Celery application module and wire it to RabbitMQ.

**Files to create/modify:**

- `provisioning_service/message_queue/__init__.py`
- `provisioning_service/message_queue/celery_app.py` — Celery app instance, broker config, task discovery
- `provisioning_service/message_queue/celery_beat.py` — beat schedule definition (periodic tasks)

**Key decisions:**

- Broker: `pyamqp://guest:guest@rabbitmq:5672//` (RabbitMQ service in docker-compose)
- Task modules: auto-discover or explicit include list
- Serializer: JSON

#### 1.2 Job Worker — VM Creation Task

Implement the `create_vm` and `finalize_vm` Celery tasks in the new codebase, adapted to use the new OpenStack client and the `provisioning_jobs` table.

**Files to create/modify:**

- `provisioning_service/services/job_worker.py` — Celery task definitions

**Important — Celery task OpenStack client:** Celery tasks run in separate worker processes. Each task must instantiate its own `OpenStackClient` using the same config (environment variables). Since the Keystone token auto-refresh is self-contained in the client object, no global state sharing is needed. A helper function `_get_os_client() → OpenStackClient` reads config and returns a fresh client. Tasks should call `await client.close()` when done to clean up the httpx session.

**Tasks:**

1. `create_vm_task(pool_id, job_id)` — the main entry point
   - Read pool config from `desktop_pools`
   - Build Nova server payload
   - POST to Nova via `openstack.nova.create_server`
   - Insert row into `desktop_instances` (status = `provisioning`)
   - Insert/update row in `provisioning_jobs` (status = `processing`)
   - Chain to `finalize_vm_task`

2. `finalize_vm_task(instance_id, openstack_vm_id)` — poll and configure
   - Poll Nova `get_server` every 10s until `status == "ACTIVE"` (timeout: 5 min)
     - **Poll on the `status` field (uppercase `"ACTIVE"`), NOT `OS-EXT-STS:vm_state` (lowercase `"active"`).** The `status` field is the user-facing server status. The old code polled on `OS-EXT-STS:vm_state` which works but is non-standard.
     - If `status == "ERROR"`: read the `fault` object from server detail, mark instance as `error` in DB, job as `failed` with fault message
   - On timeout: mark instance as `error` in DB, job as `failed`
   - On success: get Neutron port by `device_id` (the `openstack_vm_id`) → create floating IP on external network → attach FIP to port
     - Store BOTH the floating IP address (`floating_ip_address`) AND the Neutron resource ID (`id`) — the ID is needed for DELETE later
   - Update `desktop_instances` with `floating_ip`, `private_ip`, `status = 'ready'`, `provisioned_at`
   - Mark job as `completed`

3. `delete_vm_task(instance_id, openstack_vm_id)` — cleanup
   - Delete floating IP by its Neutron resource ID: `DELETE /v2.0/floatingips/{fip_id}` (response 204, no body)
   - Delete Nova server: `DELETE /servers/{server_id}`
   - Update `desktop_instances` status → `deleted`
   - Decrement `desktop_pools.current_count`
   - Mark job as `completed`

**Key improvements over old implementation:**

- Use `provisioning_jobs` table for tracking (the old code does not)
- Proper error handling with retry (max_retries from job config)
- Use the new `openstack/` client module instead of raw httpx calls
- Proper async/await in Celery tasks (use `celery.contrib.async_task` or run async in sync context)

#### 1.3 Pool Service

Business logic for pool lifecycle, decoupled from HTTP.

**Files to modify:**

- `provisioning_service/services/pool_service.py`

**Functions:**

- `create_pool(db, creator_user_id, pool_data: PoolCreateRequest) → PoolResponse`
  - Validate image/flavor/network exist in OpenStack (optional — can defer to VM creation)
  - Insert `desktop_pools` row
  - Insert default `scaling_policies` row (already auto-created by DB trigger)
  - Dispatch initial VM creation tasks to reach `min_vms`
  - Insert audit log entry

- `get_pool(db, pool_id) → PoolResponse`
- `list_pools(db, filters) → PoolListResponse`
- `update_pool(db, pool_id, updates: PoolUpdateRequest) → PoolResponse`
- `delete_pool(db, pool_id)` — soft delete (set `deleted_at`), cascade cleanup of instances

- `get_pools_for_role(db, role: str) → list[PoolResponse]` — for student self-service

#### 1.4 VM Service

Business logic for VM assignment and release.

**Files to modify:**

- `provisioning_service/services/vm_service.py`

**Functions:**

- `claim_vm(db, user_id, pool_id=None, pool_type=None) → VMClaimResponse`
  - If pool_id given: claim from specific pool
  - If pool_type given: find best pool matching type and user's role
  - If neither: find any appropriate pool for user's role
  - Atomic SELECT ... FOR UPDATE SKIP LOCKED
  - Insert `user_assignments` row
  - Return floating_ip, instance_id, session expiry

- `release_vm(db, user_id, reason='user_logout') → None`
  - Find active assignment for user
  - Update `desktop_instances` → status = 'ready', clear assigned_user_id
  - Update `user_assignments` → released_at, release_reason
  - Cancel any pending session expiry for this assignment

- `get_vm_status(db, user_id) → VMStatusResponse`
  - Check if user has an active assignment
  - Return instance details if yes

- `get_pool_credentials(db, pool_id, requesting_user_id) → list[CredentialResponse]`
  - Verify requesting user is the pool creator or admin
  - Return list of (vm_ip, username, password) for all instances in the pool

#### 1.5 Session Expiry — Reconciler

**Files to modify:**

- `provisioning_service/services/reconciler.py`

**Celery Beat task (every 30s):**

- `expire_sessions_task()`
  - Find all `user_assignments` where `released_at IS NULL` and expiry time has passed
  - For each: release the VM (call release_vm logic)
  - Mark assignment with `release_reason = 'session_expired'`

- `replenish_pools_task()`
  - For each active pool where `(ready_count + provisioning_count) < min_vms`:
    - Calculate how many VMs to create
    - Respect `max_vms` cap
    - Dispatch `create_vm_task` for each
  - For non-persistent pools with excess idle VMs (above scale-down threshold):
    - Delete oldest idle VMs (not assigned, last_accessed_at oldest)

#### 1.6 API Routes

Wire the services to FastAPI endpoints.

**Files to modify:**

- `provisioning_service/api/deps.py` — dependency injection
  - `get_current_user` — validates token with auth-service, returns user dict
  - `get_db` — already exists in db.py

- `provisioning_service/api/pools.py` — pool CRUD (admin/teacher)
  - `POST /admin/pools` — create pool (teacher/admin only)
  - `GET /admin/pools` — list all pools
  - `GET /admin/pools/{pool_id}` — pool detail
  - `PATCH /admin/pools/{pool_id}` — update pool
  - `DELETE /admin/pools/{pool_id}` — soft delete pool
  - `GET /admin/pools/{pool_id}/credentials` — get VM credentials for lab

- `provisioning_service/api/vms.py` — VM operations (student-facing)
  - `POST /provision/connect` — claim a VM (student)
  - `POST /provision/disconnect` — release VM (student)
  - `GET /provision/status` — check current assignment
  - `POST /provision/pool/expand` — trigger pool expansion (admin/teacher)

- `provisioning_service/api/admin.py` — admin-only endpoints
  - `GET /admin/jobs` — list provisioning jobs with status
  - `GET /admin/jobs/{job_id}` — job detail
  - `POST /admin/jobs/{job_id}/retry` — retry failed job
  - `GET /admin/health` — detailed health (already partially in main.py)

#### 1.7 Wire Everything in main.py

- Register API routers
- Ensure lifespan starts correctly
- The Celery worker runs as a separate container (already in docker-compose)

---

### Phase 2 — Teacher Virtual Lab Workflow

**Goal:** Implement the teacher lab creation and credential-based student access.

#### 2.1 Credential Generation

When creating VMs for a teacher's lab pool:

- Generate per-VM credentials (username + random password)
- Pass them via Nova's `user_data` (cloud-init script) to create the user account on the VM
- Store credentials in `desktop_instances.connection_details` JSONB

**Design consideration for cloud-init:**

The `user_data` passed to Nova must be **base64-encoded** (max 65535 bytes per Nova API). The decoded content is a cloud-init YAML (`#cloud-config`) that:

- Creates the specified user with `users:` directive
- Sets the password with `chpasswd:` directive
- Ensures RDP/xrdp is running via `packages:` and `runcmd:`

Example cloud-init before base64 encoding:

```yaml
#cloud-config
users:
  - name: labuser-a1b2
    sudo: false
    lock_passwd: false
chpasswd:
  list: |
    labuser-a1b2:GeneratedPassword123
  expire: false
packages:
  - xrdp
runcmd:
  - systemctl enable xrdp
  - systemctl start xrdp
```

**Pre-requisite:** The base Glance image must have `cloud-init` installed. Most Ubuntu cloud images do. If the image lacks cloud-init, `user_data` is silently ignored and credentials won't be set.

> **TODO (V2):** The credential injection mechanism (cloud-init user_data) is treated as a solved problem for V1. In a future iteration, we will implement the actual cloud-init generation and test it against the target images. For now, the code should structure credential generation as a pluggable function (`generate_vm_credentials(pool_config) → (username, password, user_data_b64)`) so it can be easily implemented later without restructuring.

The provisioning service calls this pluggable function per VM. For V1, the function returns a stub (empty user_data, placeholder credentials stored in DB).

#### 2.2 Credential Retrieval Endpoint

`GET /admin/pools/{pool_id}/credentials` returns:

```json
{
  "pool_id": "...",
  "pool_name": "...",
  "credentials": [
    {
      "instance_id": "...",
      "floating_ip": "192.168.x.x",
      "username": "labuser-a1b2",
      "password": "generated-password"
    }
  ]
}
```

Only the pool creator (or admin) can access this.

#### 2.3 Claim-by-Credential Endpoint

`POST /provision/claim-with-credential`

```json
{
  "pool_id": "...",
  "username": "labuser-a1b2",
  "password": "generated-password"
}
```

The system:

1. Verifies the credentials match an instance in the pool
2. Checks the instance is not already assigned (or allows reassignment if the same student)
3. Atomically assigns the instance
4. Returns connection details + session expiry

This endpoint does NOT require a Bearer token — the lab credential IS the auth for this flow.

---

### Phase 3 — Robustness & Production Hardening

#### 3.1 Idempotency & Crash Recovery

- **Idempotent VM creation:** Check if an `openstack_vm_id` already exists in `desktop_instances` before creating. If the Celery task was restarted after the Nova POST but before the DB insert, we should not create a duplicate VM.

- **Orphan detection:** Periodic reconciler compares `desktop_instances` rows against actual OpenStack servers. If a DB row says `provisioning` but Nova shows the server as `ERROR` or not found, mark it accordingly.

- **Stuck job recovery:** On worker startup, scan `provisioning_jobs` for rows stuck in `processing` (started_at is old, no heartbeat). Reset them to `queued` or mark as `failed` based on retry count.

#### 3.2 Floating IP Management

- **FIP strategy:** Create a new FIP per VM at provision time, delete it at VM teardown. The old service creates a new FIP per VM and **never cleans it up on delete** — this will exhaust the OpenStack quota over time.

- **FIP cleanup on VM delete:** `delete_vm_task` must call `DELETE /v2.0/floatingips/{fip_id}` (response 204, no body). The new `neutron.py` already has a `delete_floating_ip()` function ready.

- **FIP ID tracking:** Store the Neutron floating IP **resource `id`** (not just the IP address string) so we can DELETE it later. The old code only stored `floating_ip` (the address). Solution: add a column or store `{"fip_id": "...", "fip_address": "..."}` in `desktop_instances.connection_details` JSONB.

- **FIP reuse:** For non-persistent pools, consider keeping the FIP and reassigning it to a new VM (reduces OpenStack API calls). This is an optimization, not V1.

- **Orphan FIP cleanup:** A periodic Beat task (every 30 min) queries OpenStack for all floating IPs, cross-references with `desktop_instances`, and deletes any FIP not associated with a known active VM. This is a safety net for leaked IPs.

#### 3.3 Rate Limiting & Quotas

- Per-pool `max_vms` is enforced at the DB level (CHECK constraint) and at the API level.
- Global rate limiting: don't allow more than N concurrent VM creations across all pools. This prevents overwhelming OpenStack.

#### 3.4 Error Handling Patterns

- **OpenStack API failures:** All OpenStack calls go through the client's retry logic (already implemented — 3 retries with exponential backoff for 5xx, timeouts, network errors). Non-retryable errors (4xx) should fail the job immediately.

- **Partial failures during pool creation:** If a teacher requests 20 VMs and 3 fail, the pool shows 17 ready + 3 in error state. The teacher can retry failed ones individually.

- **DB constraint violations:** Caught and translated to proper HTTP 409/422 responses.

#### 3.5 Monitoring & Observability

- **Structured logging:** All Celery tasks log with `instance_id`, `job_id`, `pool_id` context.
- **Health endpoint** (already partial): DB connectivity, OpenStack reachability, queue depth, error counts.
- **Pool metrics view** (DB view `active_pool_status` already exists): utilization %, available/error/stopped counts.

---

## File Structure (Target State)

```
provisioning_service/
├── __init__.py
├── main.py                      # FastAPI app, lifespan, router registration
├── config.py                    # Environment configuration (needs Keystone vars)
├── db.py                        # Database pool factory (DONE)
├── Dockerfile                   # Container build (DONE)
├── requirements.txt             # Python dependencies (DONE)
│
├── api/
│   ├── __init__.py
│   ├── deps.py                  # Dependency injection (get_current_user, get_db)
│   ├── pools.py                 # Pool CRUD endpoints (admin/teacher)
│   ├── vms.py                   # VM claim/release/status endpoints (student)
│   └── admin.py                 # Admin-only endpoints (jobs, system)
│
├── models/
│   ├── __init__.py              # Re-exports (DONE)
│   ├── requests.py              # Pydantic request models (DONE)
│   └── responses.py             # Pydantic response models (DONE)
│
├── openstack/
│   ├── __init__.py              # Exports OpenStackClient, OpenStackError
│   ├── client.py                # HTTP client with retry/backoff + auto token refresh
│   ├── keystone.py              # Keystone authentication (token acquisition)
│   ├── nova.py                  # Nova API wrappers
│   ├── neutron.py               # Neutron API wrappers
│   ├── cinder.py                # Cinder API wrappers
│   └── glance.py                # Glance API wrappers
│
├── services/
│   ├── __init__.py
│   ├── pool_service.py          # Pool lifecycle business logic
│   ├── vm_service.py            # VM assignment/release/credential logic
│   ├── assignment.py            # User assignment tracking
│   ├── reconciler.py            # Periodic reconciliation & session expiry
│   └── job_worker.py            # Celery task definitions
│
└── message_queue/
    ├── __init__.py
    ├── celery_app.py            # Celery application instance
    └── celery_beat.py           # Beat schedule
```

---

## Database Usage (Existing Schema)

We will use the following tables (already defined in `database/init.sql`):

| Table               | Used For                                                                              |
| ------------------- | ------------------------------------------------------------------------------------- |
| `desktop_pools`     | Pool configuration: image, flavor, network, min/max VMs, type, allowed roles          |
| `desktop_instances` | Individual VM tracking: OpenStack IDs, IPs, status, assigned user, connection details |
| `provisioning_jobs` | Async job tracking: type, status, retries, timing, error details                      |
| `user_assignments`  | Assignment history: who had which VM, when, for how long, release reason              |
| `scaling_policies`  | Auto-scaling thresholds per pool (auto-created by DB trigger)                         |
| `audit_logs`        | Change tracking on pools (auto-populated by DB trigger)                               |

We will NOT modify `users` or `user_sessions` — those belong to the auth service.

---

## API Contract Summary

### Student Endpoints

| Method | Path                               | Auth           | Description                               |
| ------ | ---------------------------------- | -------------- | ----------------------------------------- |
| POST   | `/provision/connect`               | Bearer token   | Claim an available VM from a pool         |
| POST   | `/provision/disconnect`            | Bearer token   | Release currently assigned VM             |
| GET    | `/provision/status`                | Bearer token   | Check current assignment status           |
| POST   | `/provision/claim-with-credential` | Lab credential | Claim a specific VM using lab credentials |

### Admin/Teacher Endpoints

| Method | Path                                 | Auth                         | Description                       |
| ------ | ------------------------------------ | ---------------------------- | --------------------------------- |
| POST   | `/admin/pools`                       | Bearer token (teacher/admin) | Create a new desktop pool         |
| GET    | `/admin/pools`                       | Bearer token (teacher/admin) | List pools (optionally filtered)  |
| GET    | `/admin/pools/{pool_id}`             | Bearer token (teacher/admin) | Pool detail                       |
| PATCH  | `/admin/pools/{pool_id}`             | Bearer token (teacher/admin) | Update pool configuration         |
| DELETE | `/admin/pools/{pool_id}`             | Bearer token (teacher/admin) | Soft-delete a pool                |
| GET    | `/admin/pools/{pool_id}/credentials` | Bearer token (pool creator)  | Get VM credentials for a lab pool |
| POST   | `/provision/pool/expand`             | Bearer token (teacher/admin) | Manually trigger pool expansion   |
| GET    | `/admin/jobs`                        | Bearer token (admin)         | List provisioning jobs            |
| GET    | `/admin/jobs/{job_id}`               | Bearer token (admin)         | Job detail                        |
| POST   | `/admin/jobs/{job_id}/retry`         | Bearer token (admin)         | Retry a failed job                |

### System Endpoints

| Method | Path      | Auth | Description                                  |
| ------ | --------- | ---- | -------------------------------------------- |
| GET    | `/health` | None | Service health (DB + OpenStack reachability) |

---

## Celery Task Inventory

| Task Name             | Trigger                     | Description                                                       |
| --------------------- | --------------------------- | ----------------------------------------------------------------- |
| `create_vm`           | API call / pool replenisher | Create VM on OpenStack, poll until ACTIVE, attach FIP, mark ready |
| `delete_vm`           | API call / scale-down       | Delete VM from OpenStack, release FIP, update DB                  |
| `expire_sessions`     | Beat (every 30s)            | Find and release expired VM assignments                           |
| `replenish_pools`     | Beat (every 30s)            | Check pool levels, create VMs to meet min_vms                     |
| `reconcile_instances` | Beat (every 5 min)          | Sync DB state with actual OpenStack state (orphan detection)      |
| `cleanup_orphan_fips` | Beat (every 30 min)         | Delete leaked floating IPs not associated with any known VM       |

---

## Open Questions / Decisions Needed

1. **FIP strategy for non-persistent pools:** ~~Should we create a new FIP per VM (and release on delete), or maintain a pre-allocated FIP pool that gets reassigned?~~ **Resolved:** Create-per-VM with proper cleanup on delete. A periodic `cleanup_orphan_fips` Beat task catches any leaked FIPs as a safety net.

2. **VM recycling for non-persistent pools:** When a student disconnects, should the VM be:
   - (a) Kept running, returned to `ready` state for immediate reuse by next student — simpler, faster, but students might see artifacts from the previous session
   - (b) Destroyed and a fresh one created — clean but slow (2-3 min provisioning delay)  
     **Recommendation:** (a) for V1 — reset the VM to a clean state via a cloud-init cleanup script or snapshot revert. If cleanup is unreliable, fall back to (b) with pre-warming.

3. **Credential-based auth for lab VMs:** Should lab VMs use a separate auth mechanism (credentials embedded in the VM image) or should we map lab credentials to the auth service? **Recommendation:** V1 embeds credentials via cloud-init. The `/provision/claim-with-credential` endpoint validates against stored credentials in `desktop_instances.connection_details`.

4. **VM image preparation:** Who creates the base images (Glance images) that pools reference? Is there a standard image that already has RDP/xrdp configured? This is a prerequisite — provisioning can't work without a properly configured base image. **Assumption:** A suitable image exists in Glance and its UUID is known.

5. **Persistent vs non-persistent pool cleanup:** For persistent pools, VMs are not recycled — they persist until explicitly deleted. For non-persistent, how do we handle the VM after release? (See question 2).

---

## What This Plan Explicitly Excludes (V1)

- **Auto-scaling beyond min/max:** The `scaling_policies` table exists but V1 only maintains `min_vms`. Dynamic scaling based on utilization is V2.
- **Volume-backed instances:** Only local-storage (ephemeral) VMs in V1. The Cinder client exists but won't be wired yet.
- **VM health checks:** The `vm_health_checks` table and health check infrastructure exist but won't be actively used in V1.
- **Multi-tenancy / OpenStack project isolation:** V1 assumes a single OpenStack project. Per-pool project isolation is V2.
- **Snapshot / revert for non-persistent pool cleanup:** V1 reuses the running VM as-is after student disconnect.
- **Web UI for pool management:** Teacher/Admin operations are API-only in V1. A management UI is separate work.

---

## Implementation Order (Recommended)

0. **Keystone auth + client refactor** (Phase 0) — must be first; everything else depends on it
1. Celery app + worker setup (Phase 1.1)
2. `job_worker.py` — `create_vm_task` + `finalize_vm_task` (Phase 1.2)
3. `pool_service.py` — CRUD functions (Phase 1.3)
4. `vm_service.py` — claim/release functions (Phase 1.4)
5. `api/deps.py` + `api/pools.py` — pool API endpoints (Phase 1.6)
6. `api/vms.py` — VM claim/release/status endpoints (Phase 1.6)
7. `reconciler.py` — session expiry + pool replenishment Beat tasks (Phase 1.5)
8. Wire `main.py` — register routers, verify end-to-end (Phase 1.7)
9. `api/admin.py` — admin endpoints for job management (Phase 1.6)
10. Teacher lab workflow — credential generation + claim-by-credential (Phase 2)
11. Robustness — idempotency, orphan detection, FIP cleanup (Phase 3)
