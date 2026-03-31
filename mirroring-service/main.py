# main.py
import asyncio
import logging
import os
import socket as _socket

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Environment ───────────────────────────────────────────────────────────────
load_dotenv()

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

# ── guacd ─────────────────────────────────────────────────────────────────────
GUACD_HOST = os.getenv("GUACD_HOST", "127.0.0.1")
GUACD_PORT = int(os.getenv("GUACD_PORT", "4822"))

# ── Target VM ─────────────────────────────────────────────────────────────────
VM_HOST     = os.getenv("VM_HOST",     "")
VM_PORT     = os.getenv("VM_PORT",     "3389")
VM_USERNAME = os.getenv("VM_USERNAME", "")
VM_PASSWORD = os.getenv("VM_PASSWORD", "")
VM_PROTOCOL = os.getenv("VM_PROTOCOL", "rdp")
VM_DOMAIN   = os.getenv("VM_DOMAIN",   "")
VM_SECURITY = os.getenv("VM_SECURITY", "any")

# ── Display defaults ──────────────────────────────────────────────────────────
VM_WIDTH  = int(os.getenv("VM_WIDTH",  "1280"))
VM_HEIGHT = int(os.getenv("VM_HEIGHT", "720"))
VM_DPI    = int(os.getenv("VM_DPI",    "96"))

# ── RDP feature flags ─────────────────────────────────────────────────────────
VM_COLOR_DEPTH     = os.getenv("VM_COLOR_DEPTH",              "32")
VM_RESIZE_METHOD   = os.getenv("VM_RESIZE_METHOD",            "display-update")
VM_ENABLE_WALLPAPER         = os.getenv("VM_ENABLE_WALLPAPER",         "true")
VM_ENABLE_FONT_SMOOTHING    = os.getenv("VM_ENABLE_FONT_SMOOTHING",    "true")
VM_ENABLE_FULL_WINDOW_DRAG  = os.getenv("VM_ENABLE_FULL_WINDOW_DRAG",  "true")
VM_ENABLE_DESKTOP_COMP      = os.getenv("VM_ENABLE_DESKTOP_COMP",      "true")
VM_ENABLE_MENU_ANIMATIONS   = os.getenv("VM_ENABLE_MENU_ANIMATIONS",   "true")
VM_DISABLE_BITMAP_CACHING   = os.getenv("VM_DISABLE_BITMAP_CACHING",   "false")
VM_CLIENT_NAME              = os.getenv("VM_CLIENT_NAME",              "vdi-mirroring")

logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
logger.info("  guacd : %s:%s", GUACD_HOST, GUACD_PORT)
logger.info("  VM    : %s:%s  protocol=%s", VM_HOST or "⚠ NOT SET", VM_PORT, VM_PROTOCOL)
logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


# ─────────────────────────────────────────────────────────────────────────────
#  GuacamoleClient  (self-contained — no external library)
# ─────────────────────────────────────────────────────────────────────────────

class GuacamoleClient:
    def __init__(self, host: str, port: int, timeout: int = 15):
        self._host    = host
        self._port    = port
        self._timeout = timeout
        self._sock    = None
        self._buffer  = ""

    @staticmethod
    def _encode(*args: str) -> str:
        parts = ",".join(f"{len(str(a))}.{a}" for a in args)
        return parts + ";"

    @staticmethod
    def _decode(raw: str) -> list[str]:
        elements = []
        for part in raw.split(","):
            if not part:
                continue
            try:
                dot    = part.index(".")
                length = int(part[:dot])
                value  = part[dot + 1 : dot + 1 + length]
                elements.append(value)
            except (ValueError, IndexError):
                continue
        return elements

    def _send_raw(self, instruction: str) -> None:
        self._sock.sendall(instruction.encode("utf-8"))

    def _read_instruction(self) -> list[str]:
        while ";" not in self._buffer:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("guacd closed the connection")
            self._buffer += chunk.decode("utf-8", errors="ignore")
        raw, self._buffer = self._buffer.split(";", 1)
        return self._decode(raw)

    def connect(self) -> None:
        self._sock = _socket.create_connection(
            (self._host, self._port), timeout=self._timeout
        )
        logger.debug("TCP connected → guacd %s:%s", self._host, self._port)

    def handshake(
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

        # 1. SELECT
        self._send_raw(self._encode("select", protocol))
        logger.debug("→ select %s", protocol)

        # 2. ARGS
        args_parts = self._read_instruction()
        if not args_parts or args_parts[0] != "args":
            raise ConnectionError(f"Expected 'args', got: {args_parts}")
        arg_names = args_parts[1:]
        logger.debug("← args (%d): %s", len(arg_names), arg_names)

        # 3. SIZE
        self._send_raw(self._encode("size", width, height, dpi))

        # 4. AUDIO / VIDEO / IMAGE
        self._send_raw(self._encode("audio", "audio/L8", "audio/L16"))
        self._send_raw(self._encode("video"))
        self._send_raw(self._encode("image", "image/png", "image/jpeg", "image/webp"))

        # 5. CONNECT — values in exact guacd-requested order
        connect_values = [param_map.get(n, "") for n in arg_names]
        self._send_raw(self._encode("connect", *connect_values))
        logger.debug("→ connect hostname=%s port=%s", hostname, port)

        # 6. READY
        ready_parts = self._read_instruction()
        if not ready_parts or ready_parts[0] != "ready":
            raise ConnectionError(f"Expected 'ready', got: {ready_parts}")

        connection_id = ready_parts[1] if len(ready_parts) > 1 else "unknown"
        logger.info("← ready  connection_id=%s", connection_id)
        return connection_id

    def send(self, data: str) -> None:
        if self._sock:
            self._sock.sendall(data.encode("utf-8"))

    def receive(self) -> str | None:
        try:
            while ";" not in self._buffer:
                chunk = self._sock.recv(4096)
                if not chunk:
                    return None
                self._buffer += chunk.decode("utf-8", errors="ignore")
            raw, self._buffer = self._buffer.split(";", 1)
            return raw + ";"
        except OSError:
            return None

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            finally:
                self._sock = None
            logger.debug("guacd TCP connection closed")


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _missing_vars() -> list[str]:
    required = {"VM_HOST": VM_HOST, "VM_USERNAME": VM_USERNAME, "VM_PASSWORD": VM_PASSWORD}
    return [k for k, v in required.items() if not v]


def _make_guac_client(width: int, height: int, dpi: int) -> GuacamoleClient:
    missing = _missing_vars()
    if missing:
        raise ValueError(f"Missing env vars: {', '.join(missing)}")

    client = GuacamoleClient(host=GUACD_HOST, port=GUACD_PORT)
    client.connect()
    client.handshake(
        protocol    = VM_PROTOCOL,
        hostname    = VM_HOST,
        port        = VM_PORT,
        username    = VM_USERNAME,
        password    = VM_PASSWORD,
        domain      = VM_DOMAIN,
        security    = VM_SECURITY,
        ignore_cert = "true",
        width       = str(width),
        height      = str(height),
        dpi         = str(dpi),
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
#  FastAPI App
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title       = "VDI Mirror",
    description = "VM desktop mirroring via Apache Guacamole + FastAPI",
    version     = "1.0.0",
)

# ── 1. CORS ───────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# ── 2. No-cache for JS/CSS ────────────────────────────────────────────────────
class NoCacheStaticMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/") and request.url.path.endswith((".js", ".css")):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"]         = "no-cache"
            response.headers["Expires"]        = "0"
        return response

app.add_middleware(NoCacheStaticMiddleware)

# ── 3. Static files ───────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ─────────────────────────────────────────────────────────────────────────────
#  HTTP Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/health")
async def health_check():
    missing = _missing_vars()
    if missing:
        return {"ok": False, "status": "misconfigured",
                "errors": [f"{v} is not set" for v in missing]}
    return {
        "ok": True, "status": "ready",
        "guacd_host": GUACD_HOST, "guacd_port": GUACD_PORT,
        "vm_host": VM_HOST, "vm_port": VM_PORT,
        "vm_protocol": VM_PROTOCOL, "vm_security": VM_SECURITY,
    }


@app.get("/api/session")
async def get_session():
    missing = _missing_vars()
    if missing:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Server misconfigured — missing: {', '.join(missing)}",
        )
    return {
        "ok": True,
        "connection": {
            "host":           VM_HOST,
            "port":           VM_PORT,
            "protocol":       VM_PROTOCOL,
            "username":       VM_USERNAME,
            "password_set":   bool(VM_PASSWORD),
            "domain":         VM_DOMAIN  or None,
            "security":       VM_SECURITY,
            "default_width":  VM_WIDTH,
            "default_height": VM_HEIGHT,
            "default_dpi":    VM_DPI,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
#  WebSocket  /ws/guacd
# ─────────────────────────────────────────────────────────────────────────────

@app.websocket("/ws/guacd")
async def guacd_tunnel(websocket: WebSocket):
    """
    Guacamole WebSocketTunnel connects to: ws://host/ws/guacd
    then calls: new WebSocket(url + "?" + connectParam, "guacamole")

    So FastAPI receives:  /ws/guacd?width=W&height=H&dpi=D
    width/height/dpi are parsed directly from the URL query string.

    FIX: No longer reads dimensions from first WS message.
         They arrive cleanly as URL query params — FastAPI parses them natively.
    """
    # ── Parse viewport from query string ─────────────────────────────────
    # e.g. /ws/guacd?width=868&height=420&dpi=96
    params = dict(websocket.query_params)
    try:
        width  = int(params.get("width",  VM_WIDTH))
        height = int(params.get("height", VM_HEIGHT))
        dpi    = int(params.get("dpi",    VM_DPI))
    except (ValueError, TypeError):
        width, height, dpi = VM_WIDTH, VM_HEIGHT, VM_DPI

    await websocket.accept()
    logger.info("WS connected  viewport=%dx%d @%ddpi  client=%s",
                width, height, dpi, websocket.client)

    # ── Fast-fail if env is misconfigured ─────────────────────────────────
    missing = _missing_vars()
    if missing:
        reason = f"Server misconfigured: missing {', '.join(missing)}"
        logger.error(reason)
        await websocket.close(code=1011, reason=reason)
        return

    loop   = asyncio.get_event_loop()
    client = None

    # ── Blocking handshake in thread executor ─────────────────────────────
    try:
        client: GuacamoleClient = await loop.run_in_executor(
            None,
            lambda: _make_guac_client(width, height, dpi),
        )
        logger.info("guacd handshake OK ✅  vm=%s:%s  viewport=%dx%d",
                    VM_HOST, VM_PORT, width, height)

    except Exception as exc:
        logger.error("Handshake failed: %s", exc)
        await websocket.close(code=1011, reason="guacd handshake failed")
        return

    # ── Bidirectional relay ───────────────────────────────────────────────
    try:
        async def browser_to_guacd():
            while True:
                data = await websocket.receive_text()
                await loop.run_in_executor(None, client.send, data)

        async def guacd_to_browser():
            while True:
                instruction = await loop.run_in_executor(None, client.receive)
                if instruction is None:
                    logger.info("guacd closed the stream")
                    break
                await websocket.send_text(instruction)

        await asyncio.gather(browser_to_guacd(), guacd_to_browser())

    except WebSocketDisconnect:
        logger.info("Browser disconnected cleanly")

    except (BrokenPipeError, ConnectionResetError, ConnectionError) as exc:
        logger.warning("Connection dropped: %s", exc)

    except Exception as exc:
        logger.exception("Unexpected relay error: %s", exc)

    finally:
        if client is not None:
            try:
                client.close()
                logger.info("guacd connection closed cleanly")
            except Exception:
                pass
