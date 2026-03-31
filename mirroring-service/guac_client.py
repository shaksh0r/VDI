# guac_client.py
import socket
import logging

logger = logging.getLogger(__name__)


class GuacamoleClient:
    """
    Minimal synchronous guacd TCP client.
    Handles the full Guacamole protocol handshake:
      select → args → size → audio → video → image → connect → ready
    Designed to be used with asyncio.run_in_executor() inside FastAPI.
    """

    def __init__(self, host: str, port: int, timeout: int = 10):
        self._host    = host
        self._port    = port
        self._timeout = timeout
        self._sock    = None
        self._buffer  = ""

    # ── Low-level protocol ────────────────────────────────────────────────────

    @staticmethod
    def _encode(*args: str) -> str:
        """Encode values into a Guacamole protocol instruction."""
        parts = ",".join(f"{len(a)}.{a}" for a in args)
        return parts + ";"

    @staticmethod
    def _decode(raw: str) -> list[str]:
        """Decode one raw Guacamole instruction into a list of string values."""
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
        """
        Read exactly one complete instruction from the stream.
        Loops until a ';' terminator is found — handles TCP fragmentation.
        """
        while ";" not in self._buffer:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("guacd closed the connection")
            self._buffer += chunk.decode("utf-8", errors="ignore")

        raw, self._buffer = self._buffer.split(";", 1)
        return self._decode(raw)

    # ── Public API ────────────────────────────────────────────────────────────

    def connect(self) -> None:
        """Open the TCP connection to guacd."""
        self._sock = socket.create_connection(
            (self._host, self._port), timeout=self._timeout
        )
        logger.debug("TCP connected to guacd %s:%s", self._host, self._port)

    def handshake(
        self,
        protocol:                   str  = "rdp",
        hostname:                   str  = "",
        port:                       str  = "3389",
        username:                   str  = "",
        password:                   str  = "",
        domain:                     str  = "",
        security:                   str  = "any",
        ignore_cert:                str  = "true",
        width:                      str  = "1024",
        height:                     str  = "768",
        dpi:                        str  = "96",
        color_depth:                str  = "32",
        resize_method:              str  = "display-update",
        enable_wallpaper:           str  = "true",
        enable_font_smoothing:      str  = "true",
        enable_full_window_drag:    str  = "true",
        enable_desktop_composition: str  = "true",
        enable_menu_animations:     str  = "true",
        disable_bitmap_caching:     str  = "false",
        client_name:                str  = "vdi-mirroring",
    ) -> str:
        """
        Perform the full Guacamole handshake and return the connection ID.
        Must call connect() first.
        """

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

        # ── 1. SELECT ─────────────────────────────────────────
        self._send_raw(self._encode("select", protocol))
        logger.debug("→ select %s", protocol)

        # ── 2. Read ARGS (guacd tells us what params it needs) ─
        args_parts = self._read_instruction()
        if not args_parts or args_parts[0] != "args":
            raise ConnectionError(f"Expected 'args', got: {args_parts}")
        arg_names = args_parts[1:]
        logger.debug("← args: %s", arg_names)

        # ── 3. SIZE ───────────────────────────────────────────
        self._send_raw(self._encode("size", width, height, dpi))
        logger.debug("→ size %sx%s @%s", width, height, dpi)

        # ── 4. AUDIO / VIDEO / IMAGE ──────────────────────────
        self._send_raw(self._encode("audio", "audio/L8", "audio/L16"))
        self._send_raw(self._encode("video"))
        self._send_raw(self._encode("image", "image/png", "image/jpeg", "image/webp"))

        # ── 5. CONNECT — values in EXACT guacd-requested order ─
        connect_values = [param_map.get(n, "") for n in arg_names]
        self._send_raw(self._encode("connect", *connect_values))
        logger.debug("→ connect (hostname=%s)", hostname)

        # ── 6. Read READY ─────────────────────────────────────
        ready_parts = self._read_instruction()
        if not ready_parts or ready_parts[0] != "ready":
            raise ConnectionError(f"Expected 'ready', got: {ready_parts}")

        connection_id = ready_parts[1] if len(ready_parts) > 1 else "unknown"
        logger.info("← ready  connection_id=%s", connection_id)
        return connection_id

    def send(self, data: str) -> None:
        """Send a raw Guacamole instruction string to guacd."""
        if self._sock:
            self._sock.sendall(data.encode("utf-8"))

    def receive(self) -> str | None:
        """
        Receive one complete Guacamole instruction from guacd.
        Returns the raw string (with semicolon) or None if disconnected.
        """
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
        """Close the TCP connection to guacd."""
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            finally:
                self._sock = None
            logger.debug("guacd connection closed")
