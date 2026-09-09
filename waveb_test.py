#!/usr/bin/env python3
"""
Wave B verification — frontend call sequence + CORS.

Mimics what the browser does after the Wave B UI lands:
  1. GET  /             → page served, login form present
  2. GET  /static/app.js → new frontend script served
  3. POST /auth/login (cross-origin)          → token
  4. POST /provision/connect (cross-origin)   → floating_ip
  5. GET  /provision/status (cross-origin)    → expires_at
  6. WS   /ws/guacd?token=…                   → RDP stream flows
  7. POST /provision/disconnect (cross-origin) → release
  8. CORS preflight headers on auth + provisioning
"""
import asyncio
import json
import time

import httpx
import websockets

PAGE = "http://localhost:8000"
AUTH = "http://localhost:8003"
PROV = "http://localhost:8001"
ORIGIN = "http://localhost:8000"

results = []


def record(name: str, ok: bool, detail: str = ""):
    results.append((name, ok))
    print(f"{'✅' if ok else '❌'} {name:55s} {detail}")


async def main():
    # ── 1/2. Static serving ─────────────────────────────────────────────
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(PAGE + "/")
        page_ok = r.status_code == 200 and "login-form" in r.text and "login-username" in r.text
        record("1. page served with login form", page_ok, f"http {r.status_code}")
        r = await c.get(PAGE + "/static/app.js")
        js_ok = r.status_code == 200 and "provision/connect" in r.text and "token=" in r.text
        record("2. new app.js served", js_ok, f"http {r.status_code}, {len(r.content)} bytes")

        # ── 3. Login (cross-origin) ─────────────────────────────────────
        r = await c.post(AUTH + "/auth/login",
                         headers={"Origin": ORIGIN},
                         json={"username": "student1", "password": "studentpass123"})
        cors_ok = r.headers.get("access-control-allow-origin") == "*"
        token = r.json().get("access_token") if r.status_code == 200 else None
        record("3. cross-origin login + CORS header", r.status_code == 200 and cors_ok and bool(token),
               f"http {r.status_code}")

        # ── 4. Connect (cross-origin) ───────────────────────────────────
        r = await c.post(PROV + "/provision/connect",
                         headers={"Origin": ORIGIN, "Authorization": f"Bearer {token}"},
                         json={"pool_type": "non_persistent"})
        payload = r.json() if r.status_code == 200 else {}
        record("4. cross-origin connect", r.status_code == 200 and bool(payload.get("floating_ip")),
               f"http {r.status_code} ip={payload.get('floating_ip')}")

        # ── 5. Status ───────────────────────────────────────────────────
        r = await c.get(PROV + "/provision/status",
                        headers={"Origin": ORIGIN, "Authorization": f"Bearer {token}"})
        st = r.json() if r.status_code == 200 else {}
        record("5. status with expires_at", r.status_code == 200 and bool(st.get("expires_at")),
               f"http {r.status_code} expires={st.get('expires_at')}")

        # ── 8. CORS preflight ───────────────────────────────────────────
        r = await c.options(PROV + "/provision/connect",
                            headers={"Origin": ORIGIN,
                                     "Access-Control-Request-Method": "POST",
                                     "Access-Control-Request-Headers": "authorization,content-type"})
        record("8a. provisioning CORS preflight", r.headers.get("access-control-allow-origin") == "*",
               f"http {r.status_code}")
        r = await c.options(AUTH + "/auth/login",
                            headers={"Origin": ORIGIN,
                                     "Access-Control-Request-Method": "POST"})
        record("8b. auth CORS preflight", r.headers.get("access-control-allow-origin") == "*",
               f"http {r.status_code}")

    # ── 6. WS with token → RDP stream ──────────────────────────────────
    try:
        async with websockets.connect(
            f"ws://localhost:8000/ws/guacd?token={token}&width=1280&height=720&dpi=96",
            subprotocols=["guacamole"], open_timeout=10,
        ) as ws:
            ops = {}
            start = time.time()
            while time.time() - start < 10:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=3)
                except asyncio.TimeoutError:
                    continue
                head = msg.split(",", 1)[0]
                ops[head.split(".", 1)[-1]] = ops.get(head.split(".", 1)[-1], 0) + 1
        streamed = any(op in ops for op in ("img", "blob", "rect", "sync"))
        record("6. WS stream flows with token", streamed, str({k: v for k, v in list(ops.items())[:6]}))
    except Exception as exc:
        record("6. WS stream flows with token", False, f"{type(exc).__name__}: {exc}")

    # ── 7. Disconnect ──────────────────────────────────────────────────
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(PROV + "/provision/disconnect",
                         headers={"Origin": ORIGIN, "Authorization": f"Bearer {token}"},
                         json={"reason": "user_logout"})
        record("7. disconnect releases VM", r.status_code == 200, f"http {r.status_code}")
        r = await c.get(PROV + "/provision/status",
                        headers={"Origin": ORIGIN, "Authorization": f"Bearer {token}"})
        record("7b. status after disconnect", r.json().get("has_assignment") is False,
               json.dumps(r.json())[:100])

    passed = sum(1 for _, ok in results if ok)
    print(f"\n════════ {passed}/{len(results)} passed ════════")
    raise SystemExit(0 if passed == len(results) else 1)


asyncio.run(main())
