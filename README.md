# BUET Cloud VDI

Browser-based Virtual Desktop Infrastructure on [BUET Cloud Lab](https://cloudlab.buet.ac.bd) (OpenStack). Students and teachers get a remote desktop in the browser — VMs are provisioned on OpenStack and streamed over RDP via Apache Guacamole.

## How it works

```
Browser ── login ──────────────► auth-service (:8003)
         ── claim / release VM ► provisioning (:8001) ──► OpenStack (Nova / Neutron / Cinder)
         ── RDP in the page ──► mirroring (:8000) ──► guacd ──► VM:3389
```

Two ways to get a desktop:

| Flow | Who | What happens |
| --- | --- | --- |
| Walk-in lab | Student | Claims a ready VM from an open pool. On disconnect the VM is destroyed and the pool is refilled. |
| Class lab | Teacher → students | Teacher creates a pool and issues access codes (one per seat). Students enter a code and keep the same VM for the class. |

## Stack

| Service | Port | Role |
| --- | --- | --- |
| mirroring-service | 8000 | Web UI + Guacamole WebSocket relay |
| provisioning-server | 8001 | Pool / VM lifecycle API |
| auth-service | 8003 | Signup, login, sessions |
| PostgreSQL | 5432 | Shared database (`VDI`) |
| RabbitMQ | 5672 / 15672 | Celery broker + management UI |
| guacd | 4822 | RDP → Guacamole protocol |
| provisioning-worker / beat | — | Create/delete VMs; expire sessions and refill pools every 30s |

## Prerequisites

- Docker and Docker Compose
- Network reachability to BUET OpenStack (`*.cloudlab.buet.ac.bd`) — typically via the campus VPN
- An OpenStack project with a Glance image that has **xrdp** (and cloud-init), a flavor, a Neutron network, and an external network for floating IPs

## Quick start

1. Clone the repo and create the two env files Compose expects (they are gitignored):

```bash
cp provisioning_service/.env.example provisioning_service/.env   # or create it by hand
cp mirroring-service/.env.example mirroring-service/.env
```

2. Fill in OpenStack credentials and the RDP account baked into the image. Minimum:

**`provisioning_service/.env`**
```env
# OpenStack (BUET Cloud Lab)
OPENSTACK_AUTH_URL=http://topcskeystone.cloudlab.buet.ac.bd
OPENSTACK_USERNAME=
OPENSTACK_PASSWORD=
OPENSTACK_PROJECT_NAME=
OPENSTACK_USER_DOMAIN_NAME=Default
OPENSTACK_PROJECT_DOMAIN_NAME=Default
EXTERNAL_NETWORK_ID=

# Match database/Dockerfile
DB_USER=shakshor
DB_PASSWORD=shakshor
DB_NAME=VDI
DB_HOST=database
DB_PORT=5432

AUTH_SERVICE_URL=http://auth-service:8003
CELERY_BROKER_URL=pyamqp://guest:guest@rabbitmq:5672//

VM_KEY_NAME=rdp
VM_SECURITY_GROUP=allow-ping-ssh
```

**`mirroring-service/.env`**
```env
GUACD_HOST=guacd
GUACD_PORT=4822
AUTH_SERVICE_URL=http://auth-service:8003
PROVISIONING_SERVICE_URL=http://provisioning-server:8001
VM_PORT=3389
VM_PROTOCOL=rdp
VM_USERNAME=
VM_PASSWORD=
```

3. Start everything from the repo root:

```bash
docker compose up -d --build
```

4. Open [http://localhost:8000](http://localhost:8000).

| Account | Username | Password |
| --- | --- | --- |
| Seeded admin | `admin` | `admin123` |

Change that password before any real use. Students can sign up from the UI; faculty accounts are created by an admin.

5. As admin, create an **open, non-persistent** student pool (image / flavor / network UUIDs from OpenStack). Until at least one VM is `ready`, Connect will wait or fail — first boot plus xrdp can take several minutes.

```bash
docker compose logs -f provisioning-worker   # watch VM create / RDP probe
docker compose down                           # stop
```

## Using it

- **Student** — sign in, optionally enter a class code, click **Connect**. The desktop appears in the page. **Disconnect** returns the seat (and destroys walk-in VMs).
- **Teacher** — create a class pool, generate codes, hand them to students, expand or tear down the class from the dashboard.
- **Admin** — manage users, pools, and VMs; force-release or destroy instances.

Fresh VMs need a few minutes for the guest to boot and start xrdp. The first session may drop once (xrdp / LightDM race); the UI retries automatically.

## Project layout

```
auth-service/            Login, signup, admin user APIs
provisioning_service/    OpenStack client, pool/VM APIs, Celery jobs
mirroring-service/       Frontend + /ws/guacd relay
database/init.sql        Schema (users, pools, instances, jobs, access codes)
plan.md                  Provisioning design notes
```

## Notes

- Tokens from Keystone expire in a few hours; the provisioning client refreshes them automatically.
- Walk-in VMs are ephemeral. Class VMs persist until the teacher tears the pool down.
- RDP credentials are currently **image-level** (`VM_USERNAME` / `VM_PASSWORD`), not per-VM.
- For TLS / a reverse proxy, set `AUTH_PUBLIC_URL`, `PROVISION_PUBLIC_URL`, `ALLOWED_ORIGINS`, and `WS_ALLOWED_ORIGINS` on the mirroring service.

Health checks: `GET /health` on auth (`:8003`) and provisioning (`:8001`), `GET /api/health` on mirroring (`:8000`).
