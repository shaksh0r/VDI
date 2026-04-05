// app.js  –  RawTunnel direct WebSocket relay, no internal UUID handshake
//            + auto-reconnect with exponential backoff for transient xrdp drops

// ── DOM references ────────────────────────────────────────────────────────────
var statusEl      = document.getElementById("status");
var sessionEl     = document.getElementById("session");
var connectBtn    = document.getElementById("connect");
var disconnectBtn = document.getElementById("disconnect");
var displayEl     = document.getElementById("display");

var client   = null;
var keyboard = null;
var mouse    = null;

// ── Resize debounce ───────────────────────────────────────────────────────────
var resizeTimer = null;

// ── Auto-reconnect state ──────────────────────────────────────────────────────
// Tracks whether the disconnect was user-initiated (manual) or unexpected.
// Only unexpected disconnects trigger auto-reconnect.
var manualDisconnect   = false;   // set to true when user clicks Disconnect
var reconnectTimer     = null;    // setTimeout handle
var reconnectAttempts  = 0;       // current retry count
var MAX_RECONNECT      = 5;       // give up after this many attempts
var RECONNECT_BASE_MS  = 2000;    // base delay: 2 s → 4 s → 8 s → 16 s → 32 s

function setStatus(text, ok) {
  var chip  = document.getElementById("status");
  var label = chip.querySelector(".status-chip__label");
  chip.dataset.ok = ok ? "true" : "false";
  if (label) label.textContent = text;
  document.body.classList.toggle("is-connected", !!ok);

  var placeholder = document.getElementById("placeholder");
  if (placeholder) placeholder.style.display = ok ? "none" : "";
}

// ── Session fetch ─────────────────────────────────────────────────────────────
function fetchSession() {
  return fetch("/api/session")
    .then(function(r) { return r.json(); })
    .then(function(payload) {
      if (!payload.ok) throw new Error(payload.detail || "No active session");
      return payload.connection;
    });
}

// ── Guacamole instruction parser ──────────────────────────────────────────────
// Handles multiple batched instructions per WebSocket frame.
// Format: <len>.<value>[,<len>.<value>]*;
function parseInstructions(data) {
  var results = [];
  var pos     = 0;
  var len     = data.length;

  while (pos < len) {
    var elements = [];
    var complete = false;

    while (pos < len) {
      var dotPos = data.indexOf(".", pos);
      if (dotPos === -1) return results;

      var elemLen = parseInt(data.substring(pos, dotPos), 10);
      if (isNaN(elemLen)) return results;

      var valStart = dotPos + 1;
      var valEnd   = valStart + elemLen;
      if (valEnd > len) return results;

      elements.push(data.substring(valStart, valEnd));

      var terminator = data.charAt(valEnd);
      pos = valEnd + 1;

      if (terminator === ";") { complete = true; break; }
      if (terminator !== ",") return results;
    }

    if (complete && elements.length > 0) {
      results.push({ opcode: elements[0], args: elements.slice(1) });
    }
  }

  return results;
}

// ── RawTunnel ─────────────────────────────────────────────────────────────────
function RawTunnel(wsUrl) {
  Guacamole.Tunnel.call(this);
  var self   = this;
  var socket = null;

  this.sendMessage = function() {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    if (arguments.length === 0) return;
    var parts = [];
    for (var i = 0; i < arguments.length; i++) {
      var val = String(arguments[i]);
      parts.push(val.length + "." + val);
    }
    socket.send(parts.join(",") + ";");
  };

  this.connect = function(data) {
    var url = wsUrl + (data ? "?" + data : "");
    self.setState(Guacamole.Tunnel.State.CONNECTING);
    socket = new WebSocket(url, "guacamole");

    socket.onopen = function() {
      console.log("[RawTunnel] WebSocket open");
      self.setState(Guacamole.Tunnel.State.OPEN);
    };

    socket.onmessage = function(event) {
      var instructions = parseInstructions(event.data);
      for (var i = 0; i < instructions.length; i++) {
        var instr = instructions[i];
        if (self.oninstruction) {
          self.oninstruction(instr.opcode, instr.args);
        }
      }
    };

    socket.onerror = function(event) {
      console.error("[RawTunnel] WebSocket error", event);
      if (self.onerror) {
        self.onerror(new Guacamole.Status(
          Guacamole.Status.Code.SERVER_ERROR, "WebSocket error"
        ));
      }
      self.setState(Guacamole.Tunnel.State.CLOSED);
    };

    socket.onclose = function(event) {
      console.log("[RawTunnel] closed  code=" + event.code +
                  "  reason=" + (event.reason || "(none)"));
      self.setState(Guacamole.Tunnel.State.CLOSED);
    };
  };

  this.disconnect = function() {
    self.setState(Guacamole.Tunnel.State.CLOSED);
    if (socket) { socket.close(); socket = null; }
  };
}
RawTunnel.prototype             = Object.create(Guacamole.Tunnel.prototype);
RawTunnel.prototype.constructor = RawTunnel;


// ── URL helpers ───────────────────────────────────────────────────────────────
function buildTunnelUrl() {
  var proto = location.protocol === "https:" ? "wss" : "ws";
  return proto + "://" + location.host + "/ws/guacd";
}

function buildConnectParam() {
  var width  = displayEl.clientWidth  || window.innerWidth  || 1280;
  var height = displayEl.clientHeight || window.innerHeight || 720;
  var dpi    = Math.round((window.devicePixelRatio || 1) * 96);
  return "width=" + width + "&height=" + height + "&dpi=" + dpi;
}


// ── Input handlers ────────────────────────────────────────────────────────────
function attachInputHandlers(c) {
  var el = c.getDisplay().getElement();

  mouse             = new Guacamole.Mouse(el);
  mouse.onmousedown = function(s) { c.sendMouseState(s, true); };
  mouse.onmouseup   = function(s) { c.sendMouseState(s, true); };
  mouse.onmousemove = function(s) { c.sendMouseState(s, true); };

  keyboard           = new Guacamole.Keyboard(document);
  keyboard.onkeydown = function(k) { c.sendKeyEvent(1, k); };
  keyboard.onkeyup   = function(k) { c.sendKeyEvent(0, k); };
}

function detachInputHandlers() {
  if (keyboard) {
    keyboard.onkeydown = null;
    keyboard.onkeyup   = null;
    keyboard           = null;
  }
  if (mouse) {
    mouse.onmousedown = null;
    mouse.onmouseup   = null;
    mouse.onmousemove = null;
    mouse             = null;
  }
}


// ── Display scaling ───────────────────────────────────────────────────────────
// Sends a real Guacamole "size" instruction to guacd AND scales visually.
function fitDisplay(c) {
  var display      = c.getDisplay();
  var remoteWidth  = display.getWidth();
  var remoteHeight = display.getHeight();
  if (!remoteWidth || !remoteHeight) return;

  var containerWidth  = displayEl.clientWidth  || window.innerWidth  || 1280;
  var containerHeight = displayEl.clientHeight || window.innerHeight || 720;

  var scaleX = containerWidth  / remoteWidth;
  var scaleY = containerHeight / remoteHeight;
  var scale  = Math.min(scaleX, scaleY);
  display.scale(scale);

  // Send actual resize instruction to the VM via guacd
  c.sendSize(containerWidth, containerHeight);
}


// ── Auto-reconnect ────────────────────────────────────────────────────────────
// Called whenever a non-manual disconnect is detected.
// Uses exponential backoff: 2s, 4s, 8s, 16s, 32s, then gives up.
//
// Why this is needed for ubuntu-mint-custom:
//   xrdp + LightDM on a snapshot boot have a race condition where xrdp
//   accepts the TCP connection from guacd before LightDM's PAM/display
//   manager session is ready. This causes the session to drop within the
//   first 1–5 seconds. A retry after a short delay consistently succeeds
//   because LightDM finishes initializing in that window.
function scheduleReconnect() {
  if (manualDisconnect) return;               // user clicked Disconnect — don't retry
  if (reconnectAttempts >= MAX_RECONNECT) {   // too many attempts — give up
    setStatus("Could not reconnect — check VM / xrdp", false);
    console.warn("[reconnect] max attempts reached, giving up");
    return;
  }

  var delay = RECONNECT_BASE_MS * Math.pow(2, reconnectAttempts); // exponential backoff
  reconnectAttempts++;

  var countdown = Math.round(delay / 1000);
  setStatus("Reconnecting in " + countdown + "s… (attempt " +
            reconnectAttempts + "/" + MAX_RECONNECT + ")", false);

  console.log("[reconnect] attempt " + reconnectAttempts +
              " in " + delay + "ms");

  reconnectTimer = setTimeout(function() {
    reconnectTimer = null;
    _doConnect();   // inner connect — skip session re-fetch, go straight to WS
  }, delay);
}

function cancelReconnect() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  reconnectAttempts = 0;
}


// ── Core connect logic (shared by connect() and auto-reconnect) ───────────────
function _doConnect() {
  if (client) { client.disconnect(); client = null; }
  detachInputHandlers();
  displayEl.innerHTML = "";

  var tunnelUrl    = buildTunnelUrl();
  var connectParam = buildConnectParam();

  console.log("[guac] tunnel URL   :", tunnelUrl);
  console.log("[guac] connect param:", connectParam);

  var tunnel = new RawTunnel(tunnelUrl);
  client     = new Guacamole.Client(tunnel);

  var displayElem = client.getDisplay().getElement();
  displayEl.appendChild(displayElem);

  client.getDisplay().onresize = function() {
    fitDisplay(client);
  };

  attachInputHandlers(client);

  client.onerror = function(err) {
    console.error("[guac] client error:", err);
    setStatus((err && err.message) || "Connection error", false);
    // onerror is always followed by onstatechange(DISCONNECTED),
    // so reconnect scheduling is handled there.
  };

  client.onstatechange = function(state) {
    // States: 0=IDLE 1=CONNECTING 2=WAITING 3=CONNECTED 4=DISCONNECTING 5=DISCONNECTED
    var labels = [
      "Idle", "Connecting…", "Waiting…",
      "Connected", "Disconnecting…", "Disconnected"
    ];
    var ok = (state === 3);
    setStatus(labels[state] || ("State " + state), ok);

    if (ok) {
      // Successfully connected — reset reconnect counter
      reconnectAttempts = 0;
      fitDisplay(client);
    }

    if (state === 5 && !manualDisconnect) {
      // Unexpected disconnect (xrdp dropped, LightDM race, network blip)
      // → schedule a retry with exponential backoff
      console.warn("[guac] unexpected disconnect — scheduling reconnect");
      scheduleReconnect();
    }
  };

  client.connect(connectParam);
}


// ── Connect (user-initiated) ──────────────────────────────────────────────────
function connect() {
  manualDisconnect  = false;
  cancelReconnect();
  setStatus("Checking session...", false);

  fetchSession()
    .then(function(connection) {
      sessionEl.textContent =
        (connection.protocol || "RDP").toUpperCase() +
        " @ " + (connection.host || connection.hostname || "unknown host");
    })
    .catch(function(err) {
      sessionEl.textContent = "No session assigned.";
      setStatus(err.message, false);
      return Promise.reject(err);
    })
    .then(function() {
      _doConnect();
    })
    .catch(function() { /* already handled above */ });
}


// ── Disconnect (user-initiated) ───────────────────────────────────────────────
function disconnect() {
  // Mark as manual so onstatechange(DISCONNECTED) does NOT trigger auto-reconnect
  manualDisconnect = true;
  cancelReconnect();

  if (client) { client.disconnect(); client = null; }
  detachInputHandlers();
  displayEl.innerHTML = "";
  setStatus("Disconnected", false);
}


// ── Viewport resize handler (debounced) ───────────────────────────────────────
function onViewportResize() {
  if (!client) return;
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(function() {
    fitDisplay(client);
  }, 150);
}


// ── Event listeners ───────────────────────────────────────────────────────────
connectBtn.addEventListener("click",    connect);
disconnectBtn.addEventListener("click", disconnect);

// Window resize — covers drag-resize
window.addEventListener("resize", onViewportResize);

// Fullscreen change — all vendor prefixes for full browser compatibility
document.addEventListener("fullscreenchange",       onViewportResize);
document.addEventListener("webkitfullscreenchange", onViewportResize);
document.addEventListener("mozfullscreenchange",    onViewportResize);
document.addEventListener("MSFullscreenChange",     onViewportResize);

setStatus("Disconnected", false);
