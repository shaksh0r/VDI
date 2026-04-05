# main.py
import asyncio
import logging
import os

import asyncpg
import httpx
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
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

# guacd
GUACD_HOST = os.getenv("GUACD_HOST", "127.0.0.1")
GUACD_PORT = int(os.getenv("GUACD_PORT", "4822"))

# Auth service — used to resolve a token to a user_id
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://localhost:8001")



# VM credentials — shared across all VMs in the pool
# The hostname (floating IP) is looked up per-user from the database
VM_PORT     = os.getenv("VM_PORT",     "3389")
VM_USERNAME = os.getenv("VM_USERNAME", "")
VM_PASSWORD = os.getenv("VM_PASSWORD", "")
VM_PROTOCOL = os.getenv("VM_PROTOCOL", "rdp")
VM_DOMAIN   = os.getenv("VM_DOMAIN",   "")
VM_SECURITY = os.getenv("VM_SECURITY", "any")

# Display defaults (used as fallback if query params are missing)
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
logger.info("  guacd        : %s:%s", GUACD_HOST, GUACD_PORT)
logger.info("  auth service : %s", AUTH_SERVICE_URL)
logger.info("  VM port      : %s  protocol=%s", VM_PORT, VM_PROTOCOL)
logger.info("  (VM host resolved per-user from database)")
logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


# ─────────────────────────────────────────────────────────────────────────────
#  Guacamole Protocol Helpers  (pure string encoding — no I/O)
# ─────────────────────────────────────────────────────────────────────────────

def guac_encode(*args: str) -> bytes:
    """
    Encode a Guacamole instruction to bytes.
    Format: <len>.<value>,<len>.<value>,...;
    Example: guac_encode("select", "rdp") → b"6.select,3.rdp;"
    """
    parts = ",".join(f"{len(str(a))}.{a}" for a in args)
    return (parts + ";").encode("utf-8")


def guac_decode(raw: str) -> list[str]:
    """
    Decode one raw Guacamole instruction string (without trailing ';').
    Returns a list of string elements: [opcode, arg1, arg2, ...]
    """
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
#  Fully async TCP client using asyncio.StreamReader / StreamWriter.
#  No blocking I/O, no threads, no run_in_executor.
# ─────────────────────────────────────────────────────────────────────────────

class AsyncGuacamoleClient:
    """
    Async Guacamole protocol client over a raw TCP connection to guacd.

    Lifecycle:
        client = AsyncGuacamoleClient(host, port)
        await client.connect()
        await client.handshake(...)     # performs full protocol handshake
        await client.send_text(data)    # relay browser → guacd
        await client.receive_instruction()  # relay guacd → browser
        await client.disconnect()       # send clean disconnect instruction
        await client.close()            # close TCP stream

    The disconnect() call before close() is critical — it prevents guacd
    from logging:
        WARNING: Guacamole connection failure: Error filling instruction buffer
    which happens when the TCP socket is closed while guacd is mid-write.
    """

    def __init__(self, host: str, port: int):
        self._host   = host
        self._port   = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._buffer = ""

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _read_instruction(self) -> list[str]:
        """
        Block (async) until one complete Guacamole instruction arrives.
        Instructions are terminated by ';'.
        Returns decoded list: [opcode, arg1, arg2, ...]
        Raises ConnectionError if the stream closes before a full instruction.
        """
        while ";" not in self._buffer:
            chunk = await self._reader.read(4096)
            if not chunk:
                raise ConnectionError("guacd closed the TCP connection unexpectedly")
            self._buffer += chunk.decode("utf-8", errors="ignore")
        raw, self._buffer = self._buffer.split(";", 1)
        return guac_decode(raw)

    async def _send(self, *args: str) -> None:
        """Encode and send a Guacamole instruction immediately."""
        self._writer.write(guac_encode(*args))
        await self._writer.drain()

    # ── Public API ────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Open the async TCP connection to guacd."""
        self._reader, self._writer = await asyncio.open_connection(
            self._host, self._port
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
        """
        Perform the full Guacamole protocol handshake with guacd.

        Sequence:
            → select <protocol>
            ← args   <param_names...>
            → size   <width> <height> <dpi>
            → audio  <formats...>
            → video
            → image  <formats...>
            → connect <param_values_in_args_order...>
            ← ready  <connection_id>

        Returns the guacd-assigned connection ID string.
        Raises ConnectionError on any protocol violation.
        """
        # Build a lookup of all possible RDP parameters
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

        # Step 1 — SELECT: tell guacd which protocol we want
        await self._send("select", protocol)
        logger.debug("→ select %s", protocol)

        # Step 2 — ARGS: guacd responds with the parameter names it needs
        #          in the exact order it expects them in CONNECT
        args_parts = await self._read_instruction()
        if not args_parts or args_parts[0] != "args":
            raise ConnectionError(f"Handshake: expected 'args', got: {args_parts}")
        arg_names = args_parts[1:]
        logger.debug("← args (%d params): %s", len(arg_names), arg_names)

        # Step 3 — SIZE: send the display viewport dimensions
        await self._send("size", width, height, dpi)
        logger.debug("→ size %sx%s @%sdpi", width, height, dpi)

        # Step 4 — AUDIO / VIDEO / IMAGE: declare our media capabilities
        await self._send("audio", "audio/L8", "audio/L16")
        await self._send("video")
        await self._send("image", "image/png", "image/jpeg", "image/webp")
        logger.debug("→ audio / video / image capabilities sent")

        # Step 5 — CONNECT: send parameter values in the order guacd requested
        connect_values = [param_map.get(name, "") for name in arg_names]
        self._writer.write(guac_encode("connect", *connect_values))
        await self._writer.drain()
        logger.debug("→ connect hostname=%s port=%s user=%s", hostname, port, username)

        # Step 6 — READY: guacd confirms connection and assigns a connection ID
        ready_parts = await self._read_instruction()
        if not ready_parts or ready_parts[0] != "ready":
            raise ConnectionError(f"Handshake: expected 'ready', got: {ready_parts}")

        connection_id = ready_parts[1] if len(ready_parts) > 1 else "unknown"
        logger.info("← ready  connection_id=%s", connection_id)
        return connection_id

    async def send_text(self, data: str) -> None:
        """
        Forward a raw Guacamole instruction string from the browser to guacd.
        No-op if the writer is already closing.
        """
        if self._writer and not self._writer.is_closing():
            self._writer.write(data.encode("utf-8"))
            await self._writer.drain()

    async def receive_instruction(self) -> str | None:
        """
        Receive one complete Guacamole instruction from guacd.
        Returns the raw instruction string including the trailing ';'.
        Returns None if the guacd stream has closed.
        """
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
        """
        Send a Guacamole-level 'disconnect' instruction before closing TCP.

        This is the CRITICAL call that prevents guacd from logging:
            WARNING: Guacamole connection failure: Error filling instruction buffer

        Without this, closing the TCP socket while guacd is mid-write
        (e.g. in the middle of sending a large screen update frame) causes
        guacd's socket reader to receive an unexpected EOF mid-instruction,
        which it correctly reports as an instruction buffer fill failure.

        Sending '10.disconnect;' first signals guacd to:
            1. Stop writing new instructions to us
            2. Cleanly terminate the RDP session
            3. Release all resources for this connection

        We then wait up to 2 seconds for guacd to drain and close its side
        before we hard-close the TCP socket.
        """
        if self._writer and not self._writer.is_closing():
            try:
                self._writer.write(guac_encode("disconnect"))
                await self._writer.drain()
                logger.debug("→ sent 'disconnect' instruction to guacd")

                # Wait for guacd to acknowledge and stop sending — 2 second timeout
                await asyncio.wait_for(self._reader.read(4096), timeout=2.0)

            except asyncio.TimeoutError:
                # guacd did not respond within 2s — safe to hard-close
                logger.debug("guacd disconnect: timeout waiting for acknowledgement")
            except Exception as exc:
                # Connection may already be closing — not an error
                logger.debug("guacd disconnect: %s", exc)

    async def close(self) -> None:
        """
        Close the TCP stream to guacd.
        Always call disconnect() before this to ensure a clean guacd shutdown.
        """
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
#  Auth helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _resolve_token(token: str) -> dict:
    """
    Call GET /auth/me on the auth service to validate the token.
    Returns { user_id, username, role, expires_at } on success.
    Raises ValueError on invalid/expired token or if the auth service is down.
    We use ValueError (not HTTPException) so the WebSocket handler can
    close with a proper code instead of raising an unhandled exception.
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
#  Database helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _get_assigned_vm_ip(user_id: str, db: asyncpg.Connection) -> str:
    """
    Look up the floating IP of the VM currently assigned to this user.
    Raises ValueError if no active assignment is found.
    """
    row = await db.fetchrow(
        """
        SELECT floating_ip
        FROM desktop_instances
        WHERE assigned_user_id = $1
          AND status           = 'in_use'
        """,
        user_id,
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
    """
    Create, connect, and fully handshake an AsyncGuacamoleClient.
    vm_host is the floating IP resolved from the database for this user.
    Raises ValueError if shared credentials are missing.
    Raises ConnectionError if the guacd handshake fails.
    """
    missing = [k for k, v in {"VM_USERNAME": VM_USERNAME, "VM_PASSWORD": VM_PASSWORD}.items() if not v]
    if missing:
        raise ValueError(f"Missing required env vars: {', '.join(missing)}")

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
#  Lifespan — DB pool
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db_pool = await asyncpg.create_pool(
    user=os.getenv("DB_USER", "myuser"),
    password=os.getenv("DB_PASSWORD", "mypassword"),
    database=os.getenv("DB_NAME", "mydatabase"),
    host=os.getenv("DB_HOST", "localhost"),
    port=int(os.getenv("DB_PORT", "5432")),
    min_size=10,
    max_size=20,
    )
    logger.info("Database pool created")
    yield
    await app.state.db_pool.close()
    logger.info("Database pool closed")


# ─────────────────────────────────────────────────────────────────────────────
#  FastAPI Application
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title       = "VDI Mirror",
    description = "VM desktop mirroring via Apache Guacamole protocol + FastAPI",
    version     = "1.0.0",
    lifespan    = lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# ── No-cache middleware for static JS/CSS (prevents stale code in dev) ────────
class NoCacheStaticMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/assets/") and \
           request.url.path.endswith((".js", ".css")):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"]         = "no-cache"
            response.headers["Expires"]        = "0"
        return response

app.add_middleware(NoCacheStaticMiddleware)

# ── Static files ──────────────────────────────────────────────────────────────
app.mount("/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets")


# ─────────────────────────────────────────────────────────────────────────────
#  HTTP Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """Serve the React SPA entry point."""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

@app.get("/guacamole-common-js.min.js", include_in_schema=False)
async def guac_js() -> FileResponse:
    """Serve the Guacamole JS library (copied to dist/ by Vite from public/)."""
    return FileResponse(os.path.join(STATIC_DIR, "guacamole-common-js.min.js"))


@app.get("/api/health")
async def health_check():
    """
    Returns server configuration status.
    VM_HOST is intentionally absent — it is resolved per-user from the database.
    """
    missing = [
        k for k, v in {
            "VM_USERNAME": VM_USERNAME,
            "VM_PASSWORD": VM_PASSWORD,
        }.items()
        if not v
    ]
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
    }


# ─────────────────────────────────────────────────────────────────────────────
#  WebSocket Tunnel  /ws/guacd
# ─────────────────────────────────────────────────────────────────────────────

@app.websocket("/ws/guacd")
async def guacd_tunnel(websocket: WebSocket):
    """
    Fully async bidirectional relay: Browser WebSocket ↔ guacd TCP.

    ┌─────────┐   WebSocket (text frames)   ┌────────┐   TCP (Guacamole)   ┌─────────┐
    │ Browser │ ◄──────────────────────────► │  This  │ ◄─────────────────► │  guacd  │
    │ (JS)    │   subprotocol="guacamole"    │ server │   raw instructions  │         │
    └─────────┘                              └────────┘                     └─────────┘

    URL format:
        ws://host/ws/guacd?token=<jwt>&width=1280&height=720&dpi=96

    Auth + VM resolution flow:
        1. Extract token from query params
        2. Call /auth/me to get user_id
        3. Look up floating_ip from desktop_instances for that user_id
        4. Pass floating_ip into guacd handshake
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

    # ── Step 2: Accept WebSocket with "guacamole" subprotocol ────────────
    # MUST accept before sending any close frame.
    await websocket.accept(subprotocol="guacamole")

    # ── Step 3: Validate token ────────────────────────────────────────────
    if not token:
        logger.warning("WS rejected: no token provided")
        await websocket.close(code=1008, reason="Missing token")
        return

    try:
        user = await _resolve_token(token)
    except ValueError as exc:
        logger.warning("WS rejected: %s", exc)
        await websocket.close(code=1008, reason=str(exc))
        return

    user_id = user["user_id"]
    logger.info(
        "WS accepted  user=%s  viewport=%dx%d @%ddpi  client=%s",
        user_id, width, height, dpi, websocket.client,
    )

    # ── Step 4: Look up the user's assigned VM IP from the database ───────
    try:
        async with websocket.app.state.db_pool.acquire() as db:
            vm_host = await _get_assigned_vm_ip(user_id, db)
    except ValueError as exc:
        logger.warning("WS rejected: %s", exc)
        await websocket.close(code=1008, reason="No active VM session. Please connect first.")
        return
    except Exception as exc:
        logger.error("DB error resolving VM for user %s: %s", user_id, exc)
        await websocket.close(code=1011, reason="Internal server error")
        return

    logger.info("Resolved VM  user=%s  vm_host=%s", user_id, vm_host)

    # ── Step 5: Perform async Guacamole handshake with guacd ─────────────
    guac_client: AsyncGuacamoleClient | None = None
    try:
        guac_client = await _make_guac_client(vm_host, width, height, dpi)
        logger.info(
            "guacd handshake OK ✅  vm=%s:%s  viewport=%dx%d",
            vm_host, VM_PORT, width, height,
        )
    except Exception as exc:
        logger.error("guacd handshake failed: %s", exc)
        await websocket.close(code=1011, reason="guacd handshake failed")
        return

    # ── Step 6: Bidirectional async relay ─────────────────────────────────

    async def browser_to_guacd() -> None:
        """
        Forward: browser ──► guacd
        Filters keepalive and internal opcode frames that must not reach guacd.
        """
        try:
            while True:
                data = await websocket.receive_text()
                stripped = data.strip()
                # Filter 1: Guacamole JS nop keepalive
                if stripped == "3.nop;":
                    continue
                # Filter 2: Guacamole internal tunnel opcode (0. prefix)
                # Format: "0.,<args>;" — used for ping/UUID by WebSocketTunnel
                # RawTunnel never sends these but guard anyway
                if stripped.startswith("0.,") or stripped == "0.;":
                    continue
                await guac_client.send_text(data)
        except WebSocketDisconnect:
            logger.info("browser→guacd: browser disconnected")
        except Exception as exc:
            logger.warning("browser→guacd error: %s", exc)

    async def guacd_to_browser() -> None:
        """
        Forward: guacd ──► browser

        Receives Guacamole instructions from guacd TCP stream and forwards
        them as WebSocket text frames to the browser for rendering.
        """
        try:
            while True:
                instruction = await guac_client.receive_instruction()
                if instruction is None:
                    logger.info("guacd→browser: guacd closed the stream")
                    break
                await websocket.send_text(instruction)
        except WebSocketDisconnect:
            logger.info("guacd→browser: browser disconnected while sending")
        except Exception as exc:
            logger.warning("guacd→browser error: %s", exc)

    # Launch both relay tasks concurrently.
    # When EITHER side closes (browser navigates away OR guacd terminates),
    # asyncio.wait returns and we shut down the other side cleanly.
    logger.info("Relay started ▶  user=%s  vm=%s  viewport=%dx%d", user_id, vm_host, width, height)

    task_b2g = asyncio.create_task(browser_to_guacd(), name="browser→guacd")
    task_g2b = asyncio.create_task(guacd_to_browser(), name="guacd→browser")

    try:
        done, pending = await asyncio.wait(
            [task_b2g, task_g2b],
            return_when=asyncio.FIRST_COMPLETED,
        )
        logger.info(
            "Relay ended ■  user=%s  vm=%s  finished=%s",
            user_id, vm_host, [t.get_name() for t in done],
        )

    finally:
        # ── Cancel the still-running task ────────────────────────────────
        for task in [task_b2g, task_g2b]:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        # ── Graceful guacd shutdown ───────────────────────────────────────
        # disconnect() MUST come before close().
        # It sends "10.disconnect;" so guacd stops writing mid-frame,
        # preventing: WARNING: Error filling instruction buffer
        if guac_client is not None:
            await guac_client.disconnect()
            await guac_client.close()
            logger.info("guacd connection closed cleanly  user=%s", user_id)


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str) -> FileResponse:
    """Catch-all: serve index.html for any unmatched path (SPA routing)."""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))