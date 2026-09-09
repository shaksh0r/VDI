#!/usr/bin/env python3
"""
Wave A seam verification — mirroring service ↔ auth-service ↔ provisioning-service.

Cases:
  1. WS with valid token + active assignment → RDP stream flows (per-session VM)
  2. WS with no token                      → close 1008
  3. WS with garbage token                 → close 1008
  4. WS with valid token, no assignment    → close 1011
  5. WS close does NOT release the assignment (teardown has no release call)
  6. /api/health and /api/session no longer expose a static VM host

Requires: stack up, student1 assignment active (see Wave 0), faculty1 exists.
"""
import asyncio
import json
import time

import httpx
import websockets

BASE_AUTH = "http://localhost:8003"
BASE_PROV = "http://localhost:8001"
WS_URL    = "ws://localhost:8000/ws/guacd"

results = []


def record(name: str, ok: bool, detail: str = ""):
    results.append((name, ok, detail))
    print(f"{'✅' if ok else '❌'} {name:55s} {detail}")


async def login(username: str, password: str) -> str:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{BASE_AUTH}/auth/login",
                         json={"username": username, "password": password})
        return r.json()["access_token"]


async def get_status(token: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{BASE_PROV}/provision/status",
                        headers={"Authorization": f"Bearer {token}"})
        return r.json()


async def ws_probe(ws_url: str, seconds: float = 12.0):
    """Connect and collect instruction opcodes for `seconds`. Returns
    (ok, opcode_counts) — ok=False if the connection was rejected/closed."""
    try:
        async with websockets.connect(ws_url, subprotocols=["guacamole"],
                                      open_timeout=10) as ws:
            counts: dict[str, int] = {}
            start = time.time()
            while time.time() - start < seconds:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=3)
                except asyncio.TimeoutError:
                    continue
                head = msg.split(",", 1)[0]
                op = head.split(".", 1)[-1] if "." in head else head
                counts[op] = counts.get(op, 0) + 1
            return True, counts
    except websockets.exceptions.ConnectionClosed as exc:
        return False, {"close_code": exc.rcvd.code if exc.rcvd else exc.code}
    except Exception as exc:
        return False, {"error": f"{type(exc).__name__}: {exc}"}


async def main():
    student = await login("student1", "studentpass123")
    faculty = await login("faculty1", "facultypass123")

    # ── 1. Positive: valid token + active assignment → RDP stream ─────────
    status_before = await get_status(student)
    ok, counts = await ws_probe(
        f"{WS_URL}?token={student}&width=1280&height=720&dpi=96")
    streamed = ok and any(op in counts for op in ("img", "blob", "rect", "size", "sync"))
    record("1. valid token → RDP stream flows", streamed,
           f"opcodes={ {k: v for k, v in list(counts.items())[:8]} }" if streamed else str(counts)[:120])

    # ── 5. WS close must NOT release the assignment ───────────────────────
    status_after = await get_status(student)
    record("5. assignment still active after WS close (no teardown release)",
           status_after.get("has_assignment") is True
           and status_after.get("instance_id") == status_before.get("instance_id"),
           f"instance={status_after.get('instance_id')}")

    # ── 2. No token → 1008 ────────────────────────────────────────────────
    ok, counts = await ws_probe(f"{WS_URL}?width=1280&height=720&dpi=96", seconds=5)
    record("2. no token → rejected", not ok and counts.get("close_code") == 1008,
           str(counts))

    # ── 3. Garbage token → 1008 ───────────────────────────────────────────
    ok, counts = await ws_probe(f"{WS_URL}?token=not-a-real-token&width=100&height=100", seconds=5)
    record("3. garbage token → rejected", not ok and counts.get("close_code") == 1008,
           str(counts))

    # ── 4. Valid token, no active assignment → 1011 ───────────────────────
    ok, counts = await ws_probe(f"{WS_URL}?token={faculty}&width=100&height=100", seconds=5)
    record("4. valid token, no assignment → rejected", not ok and counts.get("close_code") == 1011,
           str(counts))

    # ── 6. API surfaces no static VM host ─────────────────────────────────
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get("http://localhost:8000/api/health")
        health = r.json()
        r2 = await c.get("http://localhost:8000/api/session")
        session_api = r2.json()
    record("6a. /api/health has no vm_host", "vm_host" not in health,
           json.dumps(health)[:150])
    record("6b. /api/session host is per-session (None)", session_api["connection"]["host"] is None,
           json.dumps(session_api)[:120])

    print("\n════════ SUMMARY ════════")
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"{passed}/{len(results)} passed")
    if passed < len(results):
        raise SystemExit(1)


asyncio.run(main())
