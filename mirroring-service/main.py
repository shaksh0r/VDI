# main.py
import asyncio
import logging
import os

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
STATIC_DIR = os.path.join(BASE_DIR, "static")

# guacd
GUACD_HOST = os.getenv("GUACD_HOST", "127.0.0.1")
GUACD_PORT = int(os.getenv("GUACD_PORT", "4822"))

# Target VM / RDP
VM_HOST     = os.getenv("VM_HOST",     "")
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
logger.info("  VM    : %s:%s  protocol=%s", VM_HOST or "⚠ NOT SET", VM_PORT, VM_PROTOCOL)
logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


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
        "VM_HOST":     VM_HOST,
        "VM_USERNAME": VM_USERNAME,
        "VM_PASSWORD": VM_PASSWORD,
    }
    return [k for k, v in required.items() if not v]


async def _make_guac_client(width: int, height: int, dpi: int) -> AsyncGuacamoleClient:
    missing = _missing_vars()
    if missing:
        raise ValueError(f"Missing required env vars: {', '.join(missing)}")

    client = AsyncGuacamoleClient(host=GUACD_HOST, port=GUACD_PORT)
    await client.connect()
    await client.handshake(
        protocol                   = VM_PROTOCOL,
        hostname                   = VM_HOST,
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

app = FastAPI(title="VDI Mirror", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


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
        "vm_host":     VM_HOST,
        "vm_port":     VM_PORT,
        "vm_protocol": VM_PROTOCOL,
        "vm_security": VM_SECURITY,
        "vm_domain":   VM_DOMAIN or None,
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
            "host":           VM_HOST,
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
    Fully async bidirectional relay: Browser WebSocket ↔ guacd TCP.

    FIX: Uses a shared asyncio.Event (ws_closed) to signal both relay
    tasks the moment the WebSocket closes. This prevents the ASGI race
    condition where guacd_to_browser attempts websocket.send_text()
    after the socket has already been closed by browser_to_guacd's exit.
    """

    # ── Step 1: Parse viewport from URL query params ──────────────────────
    params = dict(websocket.query_params)
    try:
        width  = int(params.get("width",  VM_WIDTH))
        height = int(params.get("height", VM_HEIGHT))
        dpi    = int(params.get("dpi",    VM_DPI))
    except (ValueError, TypeError):
        width, height, dpi = VM_WIDTH, VM_HEIGHT, VM_DPI

    # ── Step 2: Accept WebSocket ──────────────────────────────────────────
    await websocket.accept(subprotocol="guacamole")
    logger.info(
        "WS accepted  viewport=%dx%d @%ddpi  client=%s",
        width, height, dpi, websocket.client,
    )

    # ── Step 3: Validate environment ──────────────────────────────────────
    missing = _missing_vars()
    if missing:
        reason = f"Server misconfigured: missing {', '.join(missing)}"
        logger.error(reason)
        await websocket.close(code=1011, reason=reason)
        return

    # ── Step 4: Guacamole handshake ───────────────────────────────────────
    guac_client: AsyncGuacamoleClient | None = None
    try:
        guac_client = await _make_guac_client(width, height, dpi)
        logger.info(
            "guacd handshake OK ✅  vm=%s:%s  viewport=%dx%d",
            VM_HOST, VM_PORT, width, height,
        )
    except Exception as exc:
        logger.error("guacd handshake failed: %s", exc)
        await websocket.close(code=1011, reason="guacd handshake failed")
        return

    # ── Step 5: Bidirectional async relay ─────────────────────────────────

    # FIX: Shared shutdown event — set by whichever side closes first.
    # Both tasks check this before attempting any further sends/receives.
    ws_closed = asyncio.Event()

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

                # Filter 1: Guacamole JS nop keepalive — must not reach guacd
                if stripped == "3.nop;":
                    continue

                # Filter 2: Internal tunnel opcode — RawTunnel never sends
                # these but guard anyway
                if stripped.startswith("0.,") or stripped == "0.;":
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

    try:
        done, pending = await asyncio.wait(
            [task_b2g, task_g2b],
            return_when=asyncio.FIRST_COMPLETED,
        )
        logger.info(
            "Relay ended ■  finished=%s",
            [t.get_name() for t in done],
        )

    finally:
        # Cancel the still-running task
        for task in [task_b2g, task_g2b]:
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
