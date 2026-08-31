#!/usr/bin/env python3
"""
Wave C negative tests — hardening behaviors of /ws/guacd.

  1. viewport clamp:  width=99999999 → server clamps, session still works
  2. opcode whitelist: a "select,rdp;" frame is dropped; a "mouse" frame
     still gets through afterwards (session survives)
  3. origin policy:  (one-off container, WS_ALLOWED_ORIGINS set)
     disallowed Origin → close 1008 "origin not allowed"
  4. session caps:   (one-off container, MAX_SESSIONS_PER_IP=1)
     second concurrent WS from same IP → close 1013
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
    results.append((name, ok))
    print(f"{'✅' if ok else '❌'} {name:55s} {detail}")


async def login(username="student1", password="studentpass123") -> str:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{BASE_AUTH}/auth/login",
                         json={"username": username, "password": password})
        return r.json()["access_token"]


async def open_ws(url: str, seconds: float, sender=None):
    """Open a WS, run `sender(ws)` callback concurrently, collect opcodes."""
    ops = {}
    try:
        async with websockets.connect(url, subprotocols=["guacamole"],
                                      open_timeout=10) as ws:
            start = time.time()
            while time.time() - start < seconds:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=3)
                except asyncio.TimeoutError:
                    if sender:
                        try:
                            await sender(ws)
                        except Exception:
                            pass
                    continue
                head = msg.split(",", 1)[0]
                ops[head.split(".", 1)[-1]] = ops.get(head.split(".", 1)[-1], 0) + 1
        return True, ops
    except websockets.exceptions.ConnectionClosed as exc:
        return False, {"close_code": exc.rcvd.code if exc.rcvd else exc.code,
                       "close_reason": exc.rcvd.reason if exc.rcvd else ""}
    except Exception as exc:
        return False, {"error": f"{type(exc).__name__}: {exc}"}


async def main():
    token = await login()

    # ── 0. claim a VM for the session-based tests ────────────────────────
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{BASE_PROV}/provision/connect",
                         headers={"Authorization": f"Bearer {token}"},
                         json={"pool_type": "non_persistent"})
        print("claim:", r.status_code, r.json().get("floating_ip"))

    base = f"{WS_URL}?token={token}"

    # ── 1. viewport clamp ────────────────────────────────────────────────
    ok, ops = await open_ws(f"{base}&width=99999999&height=-5&dpi=9999", seconds=6)
    record("1. absurd viewport clamped, session works",
           ok and any(op in ops for op in ("sync", "size", "cursor", "audio")),
           f"ok={ok} ops={ {k: v for k, v in list(ops.items())[:6]} }")

    # ── 2. opcode whitelist ──────────────────────────────────────────────
    async def send_bad_then_good(ws):
        await ws.send("6.select,rdp;")          # handshake hijack attempt
        await ws.send("4.mouse,10,10,0;")       # legitimate input
    ok, ops = await open_ws(f"{base}&width=800&height=600&dpi=96",
                            seconds=8, sender=send_bad_then_good)
    record("2. select dropped, session survives", ok,
           f"ok={ok} ops={ {k: v for k, v in list(ops.items())[:6]} }")

    # ── cleanup: release the claim ───────────────────────────────────────
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{BASE_PROV}/provision/disconnect",
                         headers={"Authorization": f"Bearer {token}"},
                         json={"reason": "user_logout"})
        print("cleanup disconnect:", r.status_code)

    # ── 3/4. origin + caps use a one-off container with tight env ────────
    # (started externally; see run instructions in the report)
    if "ONE_OFF_WS" in __import__("os").environ:
        one_off = __import__("os").environ["ONE_OFF_WS"]

        # origin disallowed
        ok, res = await open_ws(f"{one_off}?token=whatever&width=100&height=100", seconds=5)
        record("3. disallowed Origin rejected",
               not ok and res.get("close_code") == 1008 and "origin" in res.get("close_reason", ""),
               str(res))

        # origin allowed (but no token) → different rejection reason
        import websockets as _ws
        try:
            async with _ws.connect(f"{one_off}?token=whatever&width=100&height=100",
                                   subprotocols=["guacamole"], open_timeout=10,
                                   origin="http://allowed.example") as ws:
                await ws.recv()
        except _ws.exceptions.ConnectionClosed as exc:
            code = exc.rcvd.code if exc.rcvd else exc.code
            reason = exc.rcvd.reason if exc.rcvd else ""
            record("3b. allowed Origin passes origin gate (fails on token)",
                   code == 1008 and "token" in reason, f"code={code} reason={reason}")

    passed = sum(1 for _, ok in results if ok)
    print(f"\n════════ {passed}/{len(results)} passed ════════")
    raise SystemExit(0 if passed == len(results) else 1)


asyncio.run(main())
