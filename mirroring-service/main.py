# main.py  —  VDI Mirroring Service
import asyncio
import logging
import os
import uuid

import asyncpg
import httpx
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

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
STATIC_DIR = os.path.join(BASE_DIR, "mirroring-service", "dist")

# guacd — service name on guac_net Docker network
GUACD_HOST = os.getenv("GUACD_HOST", "guacd")
GUACD_PORT = int(os.getenv("GUACD_PORT", "4822"))

# Auth service
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://auth-service:8003")

# VM RDP credentials — shared across all pool VMs
# Hostname (floating IP) is resolved per-user from the database
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

# Database — host.docker.internal to reach PostgreSQL on the host from Docker
DB_USER     = os.getenv("DB_USER",     "myuser")
DB_PASSWORD = os.getenv("DB_PASSWORD", "mypassword")
DB_NAME     = os.getenv("DB_NAME",     "mydatabase")
DB_HOST     = os.getenv("DB_HOST",     "host.docker.internal")
DB_PORT     = int(os.getenv("DB_PORT", "5432"))

logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
logger.info("  guacd        : %s:%s", GUACD_HOST, GUACD_PORT)
logger.info("  auth service : %s",    AUTH_SERVICE_URL)
logger.info("  db host      : %s:%s", DB_HOST, DB_PORT)
logger.info("  VM port      : %s  protocol=%s", VM_PORT, VM_PROTOCOL)
logger.info("  (VM host resolved per-user from database)")
logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


# ─────────────────────────────────────────────────────────────────────────────
#  Lifespan — DB pool
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db_pool = await asyncpg.create_pool(
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        host=DB_HOST,
        port=DB_PORT,
        min_size=5,
        max_size=20,
    )
    logger.info("Database pool created")
    yield
    await app.state.db_pool.close()
    logger.info("Database pool closed")


# ─────────────────────────────────────────────────────────────────────────────
#  Guacamole Protocol Helpers
# ─────────────────────────────────────────────────────────────────────────────

def guac_encode(*args: str) -> bytes:
    parts = ",".join(f"{len(str(a))}.{a}" for a in args)
    return (parts + ";").encode("utf-8")


def guac_decode(raw: str) -> list[str]:
    elements = []
    for part in raw.split(","):
        if not part:
            continue
        try:
            dot    = part.index(".")
            length = int(part[:dot])
            value  = part[dot + 1: dot + 1 + length]
            elements.append(value)
        except (ValueError, IndexError):
            continue
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
            chunk = await self._reader.read(4096)
            if not chunk:
                raise ConnectionError("guacd closed the TCP connection unexpectedly")
            self._buffer += chunk.decode("utf-8", errors="ignore")
        raw, self._buffer = self._buffer.split(";", 1)
        return guac_decode(raw)

    async def _send(self, *args: str) -> None:
        self._writer.write(guac_encode(*args))
        await self._writer.drain()

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(self._host, self._port)
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

        await self._send("size", width, height, dpi)
        await self._send("audio", "audio/L8", "audio/L16")
        await self._send("video")
        await self._send("image", "image/png", "image/jpeg", "image/webp")

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
                chunk = await self._reader.read(4096)
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
                await asyncio.wait_for(self._reader.read(4096), timeout=2.0)
            except asyncio.TimeoutError:
                pass
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
#  Auth helper
# ─────────────────────────────────────────────────────────────────────────────

async def _resolve_token(token: str) -> dict:
    """
    Validate token via auth service GET /auth/me.
    Returns { user_id, username, role, expires_at }.
    Raises ValueError on failure so WebSocket handler can close cleanly.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                f"{AUTH_SERVICE_URL}/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            logger.error("Auth service unreachable: %s", exc)
            raise ValueError("Auth service unreachable")

    if resp.status_code == 401:
        raise ValueError("Invalid or expired token")
    if resp.status_code != 200:
        raise ValueError(f"Unexpected auth service response: {resp.status_code}")

    return resp.json()


# ─────────────────────────────────────────────────────────────────────────────
#  DB helper
# ─────────────────────────────────────────────────────────────────────────────

async def _get_assigned_vm_ip(user_id: str, db: asyncpg.Connection) -> str:
    """
    Look up floating_ip for the VM currently assigned to this user.
    Casts user_id string → UUID so asyncpg does not reject it.
    Raises ValueError if no active assignment found.
    """
    row = await db.fetchrow(
        """
        SELECT floating_ip
        FROM desktop_instances
        WHERE assigned_user_id = $1
          AND status           = 'in_use'
        """,
        uuid.UUID(user_id),  # cast: auth service returns str, DB column is UUID
    )
    if not row:
        raise ValueError(f"No active VM assignment found for user {user_id}")
    return str(row["floating_ip"])


# ─────────────────────────────────────────────────────────────────────────────
#  guacd client factory
# ─────────────────────────────────────────────────────────────────────────────

async def _make_guac_client(
    vm_host: str,
    width: int,
    height: int,
    dpi: int,
) -> AsyncGuacamoleClient:
    client = AsyncGuacamoleClient(host=GUACD_HOST, port=GUACD_PORT)
    await client.connect()
    await client.handshake(
        protocol                   = VM_PROTOCOL,
        hostname                   = vm_host,
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
#  FastAPI Application
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title    = "VDI Mirror",
    version  = "1.0.0",
    lifespan = lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)


class NoCacheStaticMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/assets/") and \
           request.url.path.endswith((".js", ".css")):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"]        = "no-cache"
            response.headers["Expires"]       = "0"
        return response

app.add_middleware(NoCacheStaticMiddleware)

app.mount(
    "/assets",
    StaticFiles(directory=os.path.join(STATIC_DIR, "assets")),
    name="assets",
)


# ─────────────────────────────────────────────────────────────────────────────
#  HTTP Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/guacamole-common-js.min.js", include_in_schema=False)
async def guac_js() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "guacamole-common-js.min.js"))


@app.get("/api/health")
async def health_check():
    return {
        "ok":          True,
        "guacd":       f"{GUACD_HOST}:{GUACD_PORT}",
        "auth_service": AUTH_SERVICE_URL,
        "vm_port":     VM_PORT,
        "vm_protocol": VM_PROTOCOL,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  WebSocket Tunnel  /ws/guacd
# ─────────────────────────────────────────────────────────────────────────────

@app.websocket("/ws/guacd")
async def guacd_tunnel(websocket: WebSocket):
    """
    Bidirectional relay: Browser WebSocket ↔ guacd TCP.

    URL: ws://host/ws/guacd?token=<token>&width=W&height=H&dpi=D

    Flow:
      1. Extract token from query params
      2. Validate via auth service → get user_id
      3. Look up assigned VM floating_ip from DB using user_id (UUID cast)
      4. Handshake with guacd using that IP
      5. Relay frames bidirectionally
    """

    # ── Step 1: Parse query params ────────────────────────────────────────
    params = dict(websocket.query_params)
    token  = params.get("token", "").strip()

    try:
        width  = int(params.get("width",  VM_WIDTH))
        height = int(params.get("height", VM_HEIGHT))
        dpi    = int(params.get("dpi",    VM_DPI))
    except (ValueError, TypeError):
        width, height, dpi = VM_WIDTH, VM_HEIGHT, VM_DPI

    # ── Step 2: Accept WebSocket (must happen before any close()) ─────────
    await websocket.accept(subprotocol="guacamole")

    # ── Step 3: Validate token ────────────────────────────────────────────
    if not token:
        logger.warning("WS rejected: no token provided")
        await websocket.close(code=1008, reason="Missing token")
        return

    try:
        user    = await _resolve_token(token)
        user_id = user["user_id"]
        logger.info("WS authenticated: user=%s  viewport=%dx%d @%ddpi", user_id, width, height, dpi)
    except ValueError as exc:
        logger.warning("WS auth failed: %s", exc)
        await websocket.close(code=1008, reason=str(exc))
        return

    # ── Step 4: Resolve VM IP from database ───────────────────────────────
    try:
        async with websocket.app.state.db_pool.acquire() as db:
            vm_host = await _get_assigned_vm_ip(user_id, db)
        logger.info("VM resolved: user=%s  vm_ip=%s", user_id, vm_host)
    except ValueError as exc:
        logger.warning("WS rejected — no VM assigned: %s", exc)
        await websocket.close(code=1008, reason="No active VM session. Please click Connect first.")
        return
    except Exception as exc:
        logger.error("DB error for user %s: %s", user_id, exc)
        await websocket.close(code=1011, reason="Internal server error")
        return

    # ── Step 5: guacd handshake ───────────────────────────────────────────
    guac_client: AsyncGuacamoleClient | None = None
    try:
        guac_client = await _make_guac_client(vm_host, width, height, dpi)
        logger.info("guacd handshake OK ✅  vm=%s:%s  viewport=%dx%d", vm_host, VM_PORT, width, height)
    except Exception as exc:
        logger.error("guacd handshake failed: %s", exc)
        await websocket.close(code=1011, reason="guacd handshake failed")
        return

    # ── Step 6: Bidirectional relay ───────────────────────────────────────

    async def browser_to_guacd() -> None:
        try:
            while True:
                data     = await websocket.receive_text()
                stripped = data.strip()
                if stripped == "3.nop;":
                    continue
                if stripped.startswith("0.,") or stripped == "0.;":
                    continue
                await guac_client.send_text(data)
        except WebSocketDisconnect:
            logger.info("browser→guacd: browser disconnected  user=%s", user_id)
        except Exception as exc:
            logger.warning("browser→guacd error: %s", exc)

    async def guacd_to_browser() -> None:
        try:
            while True:
                instruction = await guac_client.receive_instruction()
                if instruction is None:
                    logger.info("guacd→browser: guacd closed the stream  user=%s", user_id)
                    break
                await websocket.send_text(instruction)
        except WebSocketDisconnect:
            logger.info("guacd→browser: browser disconnected while sending  user=%s", user_id)
        except Exception as exc:
            logger.warning("guacd→browser error: %s", exc)

    logger.info("Relay started ▶  user=%s  vm=%s  viewport=%dx%d", user_id, vm_host, width, height)

    task_b2g = asyncio.create_task(browser_to_guacd(), name="browser→guacd")
    task_g2b = asyncio.create_task(guacd_to_browser(), name="guacd→browser")

    try:
        done, _ = await asyncio.wait(
            [task_b2g, task_g2b],
            return_when=asyncio.FIRST_COMPLETED,
        )
        logger.info("Relay ended ■  user=%s  vm=%s  finished=%s", user_id, vm_host, [t.get_name() for t in done])
    finally:
        for task in [task_b2g, task_g2b]:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        if guac_client is not None:
            await guac_client.disconnect()
            await guac_client.close()
            logger.info("guacd connection closed cleanly  user=%s", user_id)


# ─────────────────────────────────────────────────────────────────────────────
#  SPA fallback — must be last
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str) -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))