# main.py
import asyncio
import logging
import os
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from prometheus_fastapi_instrumentator import Instrumentator

# ─────────────────────────────────────────────────────────────────────────────
#  Logging
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  Environment
# ─────────────────────────────────────────────────────────────────────────────

load_dotenv()

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

# guacd
GUACD_HOST = os.getenv("GUACD_HOST", "127.0.0.1")
GUACD_PORT = int(os.getenv("GUACD_PORT", "4822"))

# Service-to-service endpoints
AUTH_SERVICE_URL         = os.getenv("AUTH_SERVICE_URL",         "http://auth-service:8003")
PROVISIONING_SERVICE_URL = os.getenv("PROVISIONING_SERVICE_URL", "http://provisioning-server:8001")

# Public (browser-reachable) URLs — empty means the frontend derives them
# from window.location (used when a reverse proxy terminates TLS and routes
# /auth and /provision on the same origin).
AUTH_PUBLIC_URL      = os.getenv("AUTH_PUBLIC_URL", "").rstrip("/")
PROVISION_PUBLIC_URL = os.getenv("PROVISION_PUBLIC_URL", "").rstrip("/")

# Hardening knobs
GUACD_CONNECT_TIMEOUT   = float(os.getenv("GUACD_CONNECT_TIMEOUT",   "10"))
GUACD_HANDSHAKE_TIMEOUT = float(os.getenv("GUACD_HANDSHAKE_TIMEOUT", "20"))
GUACD_READ_TIMEOUT      = float(os.getenv("GUACD_READ_TIMEOUT",      "120"))
MAX_INSTRUCTION_BYTES   = int(os.getenv("MAX_INSTRUCTION_BYTES",     str(32 * 1024 * 1024)))
MAX_CONCURRENT_SESSIONS = int(os.getenv("MAX_CONCURRENT_SESSIONS",   "20"))
MAX_SESSIONS_PER_IP     = int(os.getenv("MAX_SESSIONS_PER_IP",       "3"))

# Browser origin policy: comma-separated list; empty = allow any origin
# (development). Set these in production. ALLOWED_ORIGINS drives CORS,
# WS_ALLOWED_ORIGINS is enforced on the WebSocket handshake.
ALLOWED_ORIGINS     = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
WS_ALLOWED_ORIGINS  = [o.strip() for o in os.getenv("WS_ALLOWED_ORIGINS", "").split(",") if o.strip()]

# Target VM / RDP — VM_HOST is now per-session: it is resolved from the
# user's active provisioning assignment at WebSocket connect time, never
# from client-supplied parameters or module-level config.
VM_PORT     = os.getenv("VM_PORT",     "3389")
VM_USERNAME = os.getenv("VM_USERNAME", "")
VM_PASSWORD = os.getenv("VM_PASSWORD", "")
VM_PROTOCOL = os.getenv("VM_PROTOCOL", "rdp")
VM_DOMAIN   = os.getenv("VM_DOMAIN",   "")
VM_SECURITY = os.getenv("VM_SECURITY", "any")

# Display defaults
VM_WIDTH  = int(os.getenv("VM_WIDTH",  "1280"))
VM_HEIGHT = int(os.getenv("VM_HEIGHT", "720"))
VM_DPI    = int(os.getenv("VM_DPI",    "96"))

# RDP feature flags
VM_COLOR_DEPTH             = os.getenv("VM_COLOR_DEPTH",             "32")
VM_RESIZE_METHOD           = os.getenv("VM_RESIZE_METHOD",           "display-update")
VM_ENABLE_WALLPAPER        = os.getenv("VM_ENABLE_WALLPAPER",        "true")
VM_ENABLE_FONT_SMOOTHING   = os.getenv("VM_ENABLE_FONT_SMOOTHING",   "true")
VM_ENABLE_FULL_WINDOW_DRAG = os.getenv("VM_ENABLE_FULL_WINDOW_DRAG", "true")
VM_ENABLE_DESKTOP_COMP     = os.getenv("VM_ENABLE_DESKTOP_COMP",     "true")
VM_ENABLE_MENU_ANIMATIONS  = os.getenv("VM_ENABLE_MENU_ANIMATIONS",  "true")
VM_DISABLE_BITMAP_CACHING  = os.getenv("VM_DISABLE_BITMAP_CACHING",  "false")
VM_CLIENT_NAME             = os.getenv("VM_CLIENT_NAME",             "vdi-mirroring")

logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
logger.info("  guacd : %s:%s", GUACD_HOST, GUACD_PORT)
logger.info("  auth  : %s", AUTH_SERVICE_URL)
logger.info("  prov  : %s", PROVISIONING_SERVICE_URL)
logger.info("  RDP   : port=%s protocol=%s user=%s", VM_PORT, VM_PROTOCOL, VM_USERNAME)
logger.info("  caps  : %d concurrent, %d/IP  |  origins: %s",
            MAX_CONCURRENT_SESSIONS, MAX_SESSIONS_PER_IP,
            WS_ALLOWED_ORIGINS or "any")
logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


# ─────────────────────────────────────────────────────────────────────────────
#  Guacamole Protocol Helpers
# ─────────────────────────────────────────────────────────────────────────────

def guac_encode(*args: str) -> bytes:
    # Length prefixes are UTF-8 BYTE counts per the Guacamole protocol —
    # using len(str) (characters) corrupts non-ASCII values.
    parts = []
    for a in args:
        value = str(a)
        parts.append(f"{len(value.encode('utf-8'))}.{value}")
    return (",".join(parts) + ";").encode("utf-8")


def guac_decode(raw: str) -> list[str]:
    elements = []
    for part in raw.split(","):
        if not part:
            continue
        try:
            dot    = part.index(".")
            length = int(part[:dot])
            if length < 0:
                raise ValueError("negative element length")
            value = part[dot + 1: dot + 1 + length]
            if len(value) != length:
                raise ValueError("truncated element value")
            elements.append(value)
        except (ValueError, IndexError) as exc:
            raise ValueError(f"malformed guacamole element: {part[:60]!r}") from exc
    return elements


# ─────────────────────────────────────────────────────────────────────────────
#  AsyncGuacamoleClient
# ─────────────────────────────────────────────────────────────────────────────

class AsyncGuacamoleClient:
    def __init__(self, host: str, port: int):
        self._host   = host
        self._port   = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._buffer = ""

    async def _read_instruction(self) -> list[str]:
        while ";" not in self._buffer:
            if len(self._buffer) > MAX_INSTRUCTION_BYTES:
                raise ConnectionError(
                    f"guacd instruction exceeded MAX_INSTRUCTION_BYTES "
                    f"({MAX_INSTRUCTION_BYTES})"
                )
            try:
                chunk = await asyncio.wait_for(
                    self._reader.read(4096), timeout=GUACD_READ_TIMEOUT
                )
            except asyncio.TimeoutError:
                raise ConnectionError("guacd read timeout")
            if not chunk:
                raise ConnectionError("guacd closed the TCP connection unexpectedly")
            self._buffer += chunk.decode("utf-8", errors="ignore")
        raw, self._buffer = self._buffer.split(";", 1)
        try:
            return guac_decode(raw)
        except ValueError as exc:
            raise ConnectionError(f"malformed guacamole instruction: {exc}") from exc

    async def _send(self, *args: str) -> None:
        self._writer.write(guac_encode(*args))
        await self._writer.drain()

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port),
            timeout=GUACD_CONNECT_TIMEOUT,
        )
        logger.debug("TCP connected → guacd %s:%s", self._host, self._port)

    async def handshake(
        self,
        protocol:                   str = "rdp",
        hostname:                   str = "",
        port:                       str = "3389",
        username:                   str = "",
        password:                   str = "",
        domain:                     str = "",
        security:                   str = "any",
        ignore_cert:                str = "true",
        width:                      str = "1280",
        height:                     str = "720",
        dpi:                        str = "96",
        color_depth:                str = "32",
        resize_method:              str = "display-update",
        enable_wallpaper:           str = "true",
        enable_font_smoothing:      str = "true",
        enable_full_window_drag:    str = "true",
        enable_desktop_composition: str = "true",
        enable_menu_animations:     str = "true",
        disable_bitmap_caching:     str = "false",
        client_name:                str = "vdi-mirroring",
    ) -> str:
        param_map = {
            "hostname":                   hostname,
            "port":                       port,
            "username":                   username,
            "password":                   password,
            "domain":                     domain,
            "security":                   security,
            "ignore-cert":                ignore_cert,
            "width":                      width,
            "height":                     height,
            "dpi":                        dpi,
            "color-depth":                color_depth,
            "resize-method":              resize_method,
            "enable-wallpaper":           enable_wallpaper,
            "enable-font-smoothing":      enable_font_smoothing,
            "enable-full-window-drag":    enable_full_window_drag,
            "enable-desktop-composition": enable_desktop_composition,
            "enable-menu-animations":     enable_menu_animations,
            "disable-bitmap-caching":     disable_bitmap_caching,
            "client-name":                client_name,
        }

        await self._send("select", protocol)
        logger.debug("→ select %s", protocol)

        args_parts = await self._read_instruction()
        if not args_parts or args_parts[0] != "args":
            raise ConnectionError(f"Handshake: expected 'args', got: {args_parts}")
        arg_names = args_parts[1:]
        logger.debug("← args (%d params): %s", len(arg_names), arg_names)

        await self._send("size", width, height, dpi)
        logger.debug("→ size %sx%s @%sdpi", width, height, dpi)

        await self._send("audio", "audio/L8", "audio/L16")
        await self._send("video")
        await self._send("image", "image/png", "image/jpeg", "image/webp")
        logger.debug("→ audio / video / image capabilities sent")

        connect_values = [param_map.get(name, "") for name in arg_names]
        self._writer.write(guac_encode("connect", *connect_values))
        await self._writer.drain()
        logger.debug("→ connect hostname=%s port=%s user=%s", hostname, port, username)

        ready_parts = await self._read_instruction()
        if not ready_parts or ready_parts[0] != "ready":
            raise ConnectionError(f"Handshake: expected 'ready', got: {ready_parts}")

        connection_id = ready_parts[1] if len(ready_parts) > 1 else "unknown"
        logger.info("← ready  connection_id=%s", connection_id)
        return connection_id

    async def send_text(self, data: str) -> None:
        if self._writer and not self._writer.is_closing():
            self._writer.write(data.encode("utf-8"))
            await self._writer.drain()

    async def receive_instruction(self) -> str | None:
        try:
            while ";" not in self._buffer:
                if len(self._buffer) > MAX_INSTRUCTION_BYTES:
                    logger.warning("guacd→browser: instruction too large, dropping session")
                    return None
                try:
                    chunk = await asyncio.wait_for(
                        self._reader.read(4096), timeout=GUACD_READ_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    logger.info("guacd→browser: read timeout — treating stream as closed")
                    return None
                if not chunk:
                    return None
                self._buffer += chunk.decode("utf-8", errors="ignore")
            raw, self._buffer = self._buffer.split(";", 1)
            return raw + ";"
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            return None

    async def disconnect(self) -> None:
        if self._writer and not self._writer.is_closing():
            try:
                self._writer.write(guac_encode("disconnect"))
                await self._writer.drain()
                logger.debug("→ sent 'disconnect' instruction to guacd")
                await asyncio.wait_for(self._reader.read(4096), timeout=2.0)
            except asyncio.TimeoutError:
                logger.debug("guacd disconnect: timeout waiting for acknowledgement")
            except Exception as exc:
                logger.debug("guacd disconnect: %s", exc)

    async def close(self) -> None:
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            finally:
                self._writer = None
                self._reader = None
            logger.debug("guacd TCP stream closed")


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _missing_vars() -> list[str]:
    required = {
        "VM_USERNAME": VM_USERNAME,
        "VM_PASSWORD": VM_PASSWORD,
    }
    return [k for k, v in required.items() if not v]


async def _make_guac_client(hostname: str, width: int, height: int, dpi: int) -> AsyncGuacamoleClient:
    missing = _missing_vars()
    if missing:
        raise ValueError(f"Missing required env vars: {', '.join(missing)}")

    client = AsyncGuacamoleClient(host=GUACD_HOST, port=GUACD_PORT)
    await client.connect()
    await client.handshake(
        protocol                   = VM_PROTOCOL,
        hostname                   = hostname,
        port                       = VM_PORT,
        username                   = VM_USERNAME,
        password                   = VM_PASSWORD,
        domain                     = VM_DOMAIN,
        security                   = VM_SECURITY,
        ignore_cert                = "true",
        width                      = str(width),
        height                     = str(height),
        dpi                        = str(dpi),
        color_depth                = VM_COLOR_DEPTH,
        resize_method              = VM_RESIZE_METHOD,
        enable_wallpaper           = VM_ENABLE_WALLPAPER,
        enable_font_smoothing      = VM_ENABLE_FONT_SMOOTHING,
        enable_full_window_drag    = VM_ENABLE_FULL_WINDOW_DRAG,
        enable_desktop_composition = VM_ENABLE_DESKTOP_COMP,
        enable_menu_animations     = VM_ENABLE_MENU_ANIMATIONS,
        disable_bitmap_caching     = VM_DISABLE_BITMAP_CACHING,
        client_name                = VM_CLIENT_NAME,
    )
    return client


# ─────────────────────────────────────────────────────────────────────────────
#  Session Auth Helpers (service-to-service)
# ─────────────────────────────────────────────────────────────────────────────

class AuthTokenError(Exception):
    """The supplied token is missing, invalid, or expired."""


class AuthServiceUnreachable(Exception):
    """auth-service or provisioning-service could not be reached."""


async def _validate_auth_token(token: str) -> dict:
    """Validate the user's Bearer token against auth-service /auth/me.

    Mirrors provisioning's get_current_user: returns the user dict
    {user_id, username, role, expires_at} on success.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                f"{AUTH_SERVICE_URL}/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise AuthServiceUnreachable("auth-service unreachable") from exc

    if resp.status_code == status.HTTP_401_UNAUTHORIZED:
        raise AuthTokenError("invalid or expired token")
    if resp.status_code != status.HTTP_200_OK:
        raise AuthServiceUnreachable(
            f"unexpected auth-service response: {resp.status_code}"
        )
    return resp.json()


async def _fetch_active_session(token: str) -> dict | None:
    """Resolve the user's active VM assignment via provisioning /provision/status.

    Returns the response dict {has_assignment, instance_id, floating_ip,
    pool_name, desktop_type, status, assigned_at, expires_at} or None when
    the user has no active assignment. The VM target is always taken from
    this server-side lookup — never from client-supplied parameters.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                f"{PROVISIONING_SERVICE_URL}/provision/status",
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise AuthServiceUnreachable("provisioning-service unreachable") from exc

    if resp.status_code == status.HTTP_401_UNAUTHORIZED:
        raise AuthTokenError("token rejected by provisioning-service")
    if resp.status_code != status.HTTP_200_OK:
        raise AuthServiceUnreachable(
            f"unexpected provisioning-service response: {resp.status_code}"
        )

    data = resp.json()
    if not data.get("has_assignment"):
        return None
    return data


# ─────────────────────────────────────────────────────────────────────────────
#  Hardening helpers
# ─────────────────────────────────────────────────────────────────────────────

# Process-local session accounting (adequate for a single replica; a
# multi-replica deployment needs a shared store).
_sessions: dict[str, int] = {}
_sessions_lock = asyncio.Lock()


async def _acquire_session_slot(client_host: str) -> bool:
    async with _sessions_lock:
        if sum(_sessions.values()) >= MAX_CONCURRENT_SESSIONS:
            return False
        if _sessions.get(client_host, 0) >= MAX_SESSIONS_PER_IP:
            return False
        _sessions[client_host] = _sessions.get(client_host, 0) + 1
        return True


async def _release_session_slot(client_host: str) -> None:
    async with _sessions_lock:
        count = _sessions.get(client_host, 0) - 1
        if count <= 0:
            _sessions.pop(client_host, None)
        else:
            _sessions[client_host] = count


def _clamp(value, lo: int, hi: int, default: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


# Instructions a browser is allowed to send into guacd. Everything else
# (select/connect/args/size-bombs, tunnel internals, malformed frames)
# is dropped. `nop` is allowed AND forwarded: guacd treats any received
# instruction as user activity and aborts the session with "User is not
# responding" after ~15s without traffic — the browser's 5s nop keepalive
# is what keeps idle sessions alive.
ALLOWED_CLIENT_OPCODES = {
    "mouse", "key", "size", "clipboard", "sync", "disconnect", "nop",
}


def _client_frame_allowed(data: str) -> bool:
    """True when every instruction in the frame uses an allowed opcode."""
    for instr in data.split(";"):
        instr = instr.strip()
        if not instr:
            continue
        try:
            elements = guac_decode(instr)
        except ValueError:
            return False
        if not elements or elements[0] not in ALLOWED_CLIENT_OPCODES:
            return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
#  FastAPI Application
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="VDI Mirror", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    # Empty list default → "*" for development; set ALLOWED_ORIGINS in prod.
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response


app.add_middleware(NoCacheMiddleware)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
# ─────────────────────────────────────────────────────────────────────────────
#  Metrics
# ─────────────────────────────────────────────────────────────────────────────

# Exposes /metrics in Prometheus text format. The dashboard's up/down signal
# is Prometheus' own `up` series (1 when this endpoint scrapes cleanly), so
# the service needs no health metric of its own. Request counters and
# latency histograms come along for free and are there when we need them.
Instrumentator().instrument(app).expose(
    app, endpoint="/metrics", include_in_schema=False
)


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/config")
async def config_endpoint():
    """Browser-reachable service URLs. Nulls mean the frontend derives
    them from window.location (plain port-based deployment); set
    AUTH_PUBLIC_URL / PROVISION_PUBLIC_URL when a reverse proxy routes
    /auth and /provision on this same origin (TLS deployment)."""
    return {
        "auth":      AUTH_PUBLIC_URL or None,
        "provision": PROVISION_PUBLIC_URL or None,
    }


@app.get("/api/health")
async def health_check():
    missing = _missing_vars()
    if missing:
        return {
            "ok":     False,
            "status": "misconfigured",
            "errors": [f"{v} is not set" for v in missing],
        }
    return {
        "ok":          True,
        "status":      "ready",
        "guacd_host":  GUACD_HOST,
        "guacd_port":  GUACD_PORT,
        "vm_port":     VM_PORT,
        "vm_protocol": VM_PROTOCOL,
        "vm_security": VM_SECURITY,
        "vm_domain":   VM_DOMAIN or None,
        "target":      "per-session (resolved via provisioning-service)",
    }


@app.get("/api/session")
async def get_session():
    missing = _missing_vars()
    if missing:
        raise HTTPException(
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail      = f"Server misconfigured — missing: {', '.join(missing)}",
        )
    return {
        "ok": True,
        "connection": {
            "host":           None,  # per-session: resolved at WS connect time
            "port":           VM_PORT,
            "protocol":       VM_PROTOCOL,
            "username":       VM_USERNAME,
            "password_set":   bool(VM_PASSWORD),
            "domain":         VM_DOMAIN or None,
            "security":       VM_SECURITY,
            "default_width":  VM_WIDTH,
            "default_height": VM_HEIGHT,
            "default_dpi":    VM_DPI,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
#  WebSocket Tunnel  /ws/guacd
# ─────────────────────────────────────────────────────────────────────────────

@app.websocket("/ws/guacd")
async def guacd_tunnel(websocket: WebSocket):
    """
    Authenticated per-session relay: Browser WebSocket ↔ guacd TCP.

    The VM target is resolved server-side from the caller's active
    provisioning assignment (token → auth-service → provisioning-service),
    never from client-supplied query parameters.

    FIX: Uses a shared asyncio.Event (ws_closed) to signal both relay
    tasks the moment the WebSocket closes. This prevents the ASGI race
    condition where guacd_to_browser attempts websocket.send_text()
    after the socket has already been closed by browser_to_guacd's exit.
    """

    # ── Step 1: Parse query params (clamped) ─────────────────────────────
    params = dict(websocket.query_params)
    token = params.get("token", "")
    width  = _clamp(params.get("width",  VM_WIDTH),  640, 7680, VM_WIDTH)
    height = _clamp(params.get("height", VM_HEIGHT), 480, 4320, VM_HEIGHT)
    dpi    = _clamp(params.get("dpi",    VM_DPI),     48,  288, VM_DPI)

    # ── Step 2: Accept WebSocket ──────────────────────────────────────────
    await websocket.accept(subprotocol="guacamole")
    logger.info(
        "WS accepted  viewport=%dx%d @%ddpi  client=%s",
        width, height, dpi, websocket.client,
    )

    # ── Step 3: Origin policy (only enforced when WS_ALLOWED_ORIGINS set) ─
    if WS_ALLOWED_ORIGINS:
        origin = websocket.headers.get("origin", "")
        if origin not in WS_ALLOWED_ORIGINS:
            logger.warning("WS rejected: origin %r not allowed (client=%s)",
                           origin, websocket.client)
            await websocket.close(code=1008, reason="origin not allowed")
            return

    # ── Step 4: Authenticate the caller ───────────────────────────────────
    if not token:
        logger.warning("WS rejected: missing token (client=%s)", websocket.client)
        await websocket.close(code=1008, reason="missing token")
        return

    try:
        user = await _validate_auth_token(token)
    except AuthTokenError as exc:
        logger.warning("WS rejected: %s (client=%s)", exc, websocket.client)
        await websocket.close(code=1008, reason="invalid or expired token")
        return
    except AuthServiceUnreachable as exc:
        logger.error("WS rejected: %s", exc)
        await websocket.close(code=1011, reason="auth service unreachable")
        return

    # ── Step 4: Resolve the user's active VM assignment ───────────────────
    try:
        session = await _fetch_active_session(token)
    except AuthTokenError as exc:
        logger.warning("WS rejected: %s", exc)
        await websocket.close(code=1008, reason="invalid or expired token")
        return
    except AuthServiceUnreachable as exc:
        logger.error("WS rejected: %s", exc)
        await websocket.close(code=1011, reason="provisioning service unreachable")
        return

    if session is None:
        logger.warning(
            "WS rejected: no active VM session (user=%s)",
            user.get("user_id"),
        )
        await websocket.close(code=1011, reason="no active VM session")
        return

    vm_host = session.get("floating_ip")
    if not vm_host:
        logger.error(
            "WS rejected: assignment has no floating_ip (instance=%s)",
            session.get("instance_id"),
        )
        await websocket.close(code=1011, reason="assigned VM has no address")
        return

    logger.info(
        "WS authenticated  user=%s  vm=%s  instance=%s",
        user.get("user_id"), vm_host, session.get("instance_id"),
    )

    # ── Step 5: Validate environment ──────────────────────────────────────
    missing = _missing_vars()
    if missing:
        reason = f"Server misconfigured: missing {', '.join(missing)}"
        logger.error(reason)
        await websocket.close(code=1011, reason=reason)
        return

    # ── Step 6: Session slot (concurrency caps) ───────────────────────────
    client_host = websocket.client.host if websocket.client else "unknown"
    if not await _acquire_session_slot(client_host):
        logger.warning("WS rejected: session cap reached (client=%s)", client_host)
        await websocket.close(code=1013, reason="too many active sessions")
        return

    # ── Step 7: Guacamole handshake (per-session VM target) ───────────────
    guac_client: AsyncGuacamoleClient | None = None
    try:
        guac_client = await asyncio.wait_for(
            _make_guac_client(vm_host, width, height, dpi),
            timeout=GUACD_HANDSHAKE_TIMEOUT,
        )
        logger.info(
            "guacd handshake OK ✅  vm=%s:%s  viewport=%dx%d",
            vm_host, VM_PORT, width, height,
        )
    except Exception as exc:
        logger.error("guacd handshake failed: %s", exc)
        await _release_session_slot(client_host)
        await websocket.close(code=1011, reason="guacd handshake failed")
        return

    # ── Step 8: Bidirectional async relay + session-expiry watchdog ───────

    # FIX: Shared shutdown event — set by whichever side closes first.
    # Both tasks check this before attempting any further sends/receives.
    ws_closed = asyncio.Event()

    async def session_expiry_watcher() -> None:
        """
        Close the WS when the provisioning session expires. This is a UX
        signal only — the authoritative release lives in provisioning's
        expire-sessions beat, so no release call is made here.
        """
        expires_at = session.get("expires_at")
        if not expires_at:
            return
        try:
            expires_dt = datetime.fromisoformat(expires_at)
        except (TypeError, ValueError):
            logger.warning("session_expiry_watcher: unparseable expires_at %r", expires_at)
            return
        if expires_dt.tzinfo is None:
            now = datetime.utcnow()
        else:
            now = datetime.now(timezone.utc)
        remaining = (expires_dt - now).total_seconds()
        if remaining > 0:
            await asyncio.sleep(remaining)
        if ws_closed.is_set():
            return
        logger.info(
            "session expired — closing WS (user=%s, instance=%s)",
            user.get("user_id"), session.get("instance_id"),
        )
        ws_closed.set()
        try:
            await websocket.close(code=1012, reason="session expired")
        except Exception:
            pass

    async def browser_to_guacd() -> None:
        """
        Forward: browser ──► guacd
        Sets ws_closed the moment the browser disconnects so that
        guacd_to_browser stops sending immediately.
        """
        try:
            while True:
                data = await websocket.receive_text()
                stripped = data.strip()

                # NOTE: the browser's `nop` keepalive is FORWARDED to guacd.
                # guacd treats any instruction as user activity and aborts
                # idle sessions after ~15s ("User is not responding"); the
                # browser's 5s nop is what keeps the session alive. Previous
                # versions stripped it, which killed every idle session.

                # Opcode whitelist — only mouse/key/size/clipboard/sync/
                # disconnect/nop frames reach guacd. This blocks handshake
                # hijacking (select/connect), size bombs and malformed frames.
                if not _client_frame_allowed(stripped):
                    logger.warning(
                        "browser→guacd: dropped disallowed frame: %.80s",
                        stripped,
                    )
                    continue

                await guac_client.send_text(data)

        except WebSocketDisconnect:
            logger.info("browser→guacd: browser disconnected")
        except Exception as exc:
            logger.warning("browser→guacd error: %s", exc)
        finally:
            # FIX: Signal the shutdown event so guacd_to_browser exits its
            # loop cleanly without attempting further websocket.send_text()
            # calls on an already-closed socket.
            ws_closed.set()

    async def guacd_to_browser() -> None:
        """
        Forward: guacd ──► browser
        FIX: Checks ws_closed before every send. If the WebSocket is
        already closed, exits silently instead of raising an ASGI error.
        """
        try:
            while True:
                # FIX: Exit immediately if the WebSocket has already closed
                if ws_closed.is_set():
                    logger.info("guacd→browser: ws_closed signalled, stopping relay")
                    break

                instruction = await guac_client.receive_instruction()

                if instruction is None:
                    logger.info("guacd→browser: guacd closed the stream")
                    break

                # FIX: Double-check before sending — the event may have been
                # set between receive_instruction() returning and this send
                if ws_closed.is_set():
                    logger.info("guacd→browser: ws_closed before send, dropping frame")
                    break

                try:
                    await websocket.send_text(instruction)
                except Exception:
                    # WebSocket closed between the is_set() check and the send
                    # This is safe to ignore — ws_closed will be set already
                    logger.info("guacd→browser: send failed (ws already closed)")
                    break

        except WebSocketDisconnect:
            logger.info("guacd→browser: browser disconnected while sending")
        except Exception as exc:
            logger.warning("guacd→browser error: %s", exc)
        finally:
            # Also signal ws_closed in case guacd side closed first,
            # so browser_to_guacd unblocks on next receive_text()
            ws_closed.set()

    logger.info("Relay started ▶  viewport=%dx%d", width, height)

    task_b2g = asyncio.create_task(browser_to_guacd(), name="browser→guacd")
    task_g2b = asyncio.create_task(guacd_to_browser(), name="guacd→browser")
    task_exp = asyncio.create_task(session_expiry_watcher(), name="session-expiry")

    try:
        done, pending = await asyncio.wait(
            [task_b2g, task_g2b, task_exp],
            return_when=asyncio.FIRST_COMPLETED,
        )
        logger.info(
            "Relay ended ■  finished=%s",
            [t.get_name() for t in done],
        )

    finally:
        # Cancel the still-running tasks (relays + expiry watchdog)
        for task in [task_b2g, task_g2b, task_exp]:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        # Graceful guacd shutdown
        if guac_client is not None:
            await guac_client.disconnect()
            await guac_client.close()
            logger.info("guacd connection closed cleanly")

        # Release the session slot
        await _release_session_slot(client_host)
