# OpenStack Resources (BUET Cloudlab deployment)

Reference sheet for the external resources this project depends on. These are
**live cloud IDs**, not placeholders — if any of them changes (image rebuilt,
network recreated, project moved), update this file *and* `database/init.sql`.

Last confirmed against the live cloud: **2025-09-25**.

## Values used by the default pool

| Resource | ID | Name | Notes |
|---|---|---|---|
| Image | `fac30512-31d8-49e1-98b2-f6fda9e74ecd` | `ubuntu-xfce` | XRDP desktop image — required, since `finalize_vm_task` probes TCP 3389 |
| Flavor | `2` | `m1.medium` | 2 vCPU / 2048 MB RAM / 10 GB disk |
| Network | `58877b05-70db-4c49-8413-8cbd1aa6ad51` | `prod-net` | Private tenant network; VMs attach here, floating IPs are added on top |
| External network | `c2e7fc3d-ef57-459b-b251-205291635588` | `Buet_Provider_Network` | Floating-IP pool — set as `EXTERNAL_NETWORK_ID` in `.env` |

### Why this combination

- `ubuntu-xfce` is a **desktop** image with the XRDP stack. A plain server
  image (e.g. `Ubuntu_24_04`) would boot fine but never open port 3389, so
  `finalize_vm_task` would fail with
  `RDP port 3389 never opened ... within Ns` and mark the instance `error`.
- `m1.medium`'s 10 GB root disk matches both the image's `min_disk` (10) and
  `VM_BOOT_VOLUME_SIZE_GB=10` in `.env`. `job_worker.py` provisions a
  **volume-backed** boot disk via `block_device_mapping_v2`, so the flavor's
  disk must be >= that value.
- `prod-net` is the project's private network (the `.env` security groups
  `prod-sec` / `allow-ping-ssh` belong to it). Only `prod-net` and
  `private-net` are non-external networks in this project.

## Where these values live

| Place | What it holds |
|---|---|
| `database/init.sql` | Seeds the `student-pool` row with image / flavor / network |
| `provisioning_service/.env` | `EXTERNAL_NETWORK_ID`, security groups, key name, volume size |
| `.env` (repo root) | Legacy merged copy — see the `.env` handling notes |

## Optional: pin the teacher template via env

The teacher class-pool flow (`api/teachers.py`) copies image / flavor / network
from the reference pool named by `TEACHER_REFERENCE_POOL` (default
`student-pool`). Once `student-pool` exists this works with no extra config.

To decouple it from that pool, set these in `provisioning_service/.env`:

```dotenv
TEACHER_IMAGE_ID=fac30512-31d8-49e1-98b2-f6fda9e74ecd
TEACHER_FLAVOR_ID=2
TEACHER_NETWORK_ID=58877b05-70db-4c49-8413-8cbd1aa6ad51
```

## Other resources present in the project (not used by default)

Images: `4824e692-…` `ubuntu_xfce_another`, `fbafeba6-…` `Ubuntu_24_04`,
`fa3ec78c-…` `ws2022`, `f6712ed1-…` `ws2025`, `cb69ac62-…` `cirros`, and others.

Non-external networks: `58877b05-…` `prod-net` (used), `aedf2226-…`
`private-net`, `f88a5173-…` `Tenant_Trove_Network_Provider_28` (shared).

External networks: `c2e7fc3d-…` `Buet_Provider_Network` (used),
`18fe08ea-…` `Provider_Trove_Network_28_402`, `c905dbd5-…` `provider_29_502`,
`f3e378b6-…` `provider_27_302`.

## How to re-verify

Query the cloud through the app's own client (uses the Keystone credentials
from `.env`, so no separate auth needed):

```bash
docker compose exec -T provisioning-server python -c "
import asyncio
from provisioning_service import config
from provisioning_service.openstack import OpenStackClient
from provisioning_service.openstack import nova, glance, neutron

async def main():
    c = OpenStackClient(
        auth_url=config.OPENSTACK_AUTH_URL, username=config.OPENSTACK_USERNAME,
        password=config.OPENSTACK_PASSWORD, project_name=config.OPENSTACK_PROJECT_NAME,
        user_domain=config.OPENSTACK_USER_DOMAIN_NAME,
        project_domain=config.OPENSTACK_PROJECT_DOMAIN_NAME,
        compute_url=config.OPENSTACK_COMPUTE_URL,
        network_url=config.OPENSTACK_NETWORK_URL,
        image_url=config.OPENSTACK_IMAGE_URL,
        volume_url=config.OPENSTACK_VOLUME_URL,
        timeout=config.OPENSTACK_REQUEST_TIMEOUT)
    print([f['name'] for f in (await nova.list_flavors(c))['flavors']])
    print([f['name'] for f in (await glance.list_images(c))['images']])
    print([n['name'] for n in (await neutron.list_networks(c))['networks']])
    await c.close()

asyncio.run(main())
"
```

## Caveats

- **The Keystone token auto-refreshes** (`openstack/client.py`), so these
  queries keep working as long as `OPENSTACK_PASSWORD` is valid. If auth
  starts failing, the service-account password was probably rotated.
- **The cloud is remote and project-scoped.** These IDs are only valid for the
  `Capstone_VDI` project on `topcskeystone.cloudlab.buet.ac.bd`. A different
  project will have entirely different UUIDs.
- **Floating IPs are per-VM** (`finalize_vm_task`) and deleted on teardown
  (`delete_vm_task`). Quota can still leak if a job dies mid-flight — the
  reconciler's orphan sweep is the safety net, not a guarantee.
