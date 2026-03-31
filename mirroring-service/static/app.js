// app.v3.js  –  RawTunnel direct WebSocket relay, no internal UUID handshake

// ── DOM references ────────────────────────────────────────────────────────────
var statusEl      = document.getElementById("status");
var sessionEl     = document.getElementById("session");
var connectBtn    = document.getElementById("connect");
var disconnectBtn = document.getElementById("disconnect");
var displayEl     = document.getElementById("display");

var client   = null;
var keyboard = null;
var mouse    = null;

// ── Status helper ─────────────────────────────────────────────────────────────
function setStatus(text, ok) {
  statusEl.textContent = text;
  statusEl.dataset.ok  = ok ? "true" : "false";
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
// Correctly handles multiple instructions per WebSocket frame.
// guacd routinely batches many instructions into one WS message.
//
// Format per instruction:  <len>.<value>[,<len>.<value>]*;
// Example (two instructions in one frame):
//   "4.sync,10.1234567890;5.mouse,1.0,3.100,3.200;"
//
// Returns array of { opcode, args } objects parsed from `data`.

function parseInstructions(data) {
  var results = [];
  var pos     = 0;
  var len     = data.length;

  while (pos < len) {
    var elements = [];
    var complete = false;

    while (pos < len) {
      // Read the length prefix (digits before the '.')
      var dotPos = data.indexOf(".", pos);
      if (dotPos === -1) return results; // truncated frame — stop

      var elemLen = parseInt(data.substring(pos, dotPos), 10);
      if (isNaN(elemLen)) return results; // malformed — stop

      var valStart = dotPos + 1;
      var valEnd   = valStart + elemLen;

      if (valEnd > len) return results; // incomplete element — stop

      elements.push(data.substring(valStart, valEnd));

      var terminator = data.charAt(valEnd);
      pos = valEnd + 1; // advance past value + terminator character

      if (terminator === ";") {
        complete = true;
        break; // end of this instruction
      }
      if (terminator !== ",") return results; // unexpected character — stop
    }

    if (complete && elements.length > 0) {
      results.push({ opcode: elements[0], args: elements.slice(1) });
    }
  }

  return results;
}

// ── RawTunnel ─────────────────────────────────────────────────────────────────
//
// Implements the Guacamole.Tunnel interface as a plain WebSocket tunnel.
// NO internal UUID handshake, NO INTERNAL_DATA_OPCODE processing.
// This is a transparent pipe — exactly what main.py's relay expects.
//
// Why not Guacamole.WebSocketTunnel?
//   That class expects a full Guacamole app server (Java/Tomcat) to respond
//   with a tunnel UUID on the first INTERNAL_DATA_OPCODE ping. Our relay
//   has no concept of tunnel UUIDs — it passes raw instructions directly.
//   Using WebSocketTunnel causes instant disconnect because the tunnel never
//   transitions from CONNECTING → OPEN (no UUID received → state stuck).

function RawTunnel(wsUrl) {
  Guacamole.Tunnel.call(this);
  var self   = this;
  var socket = null;

  // sendMessage is called by Guacamole.Client to send instructions:
  //   sendMessage("nop")
  //   sendMessage("mouse", x, y, buttonMask)
  //   sendMessage("key", keysym, pressed)
  //   sendMessage("size", width, height)
  //   sendMessage("disconnect")
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
    // data = "width=W&height=H&dpi=D" — append as query string
    // main.py reads these via websocket.query_params
    var url = wsUrl + (data ? "?" + data : "");
    self.setState(Guacamole.Tunnel.State.CONNECTING);

    // Open with "guacamole" subprotocol — MUST match server's accept()
    socket = new WebSocket(url, "guacamole");

    socket.onopen = function() {
      console.log("[RawTunnel] WebSocket open");
      // Transition to OPEN immediately — no UUID handshake needed
      self.setState(Guacamole.Tunnel.State.OPEN);
    };

    socket.onmessage = function(event) {
      // Parse ALL instructions in this frame and dispatch each one.
      // guacd batches multiple instructions per WS message, especially
      // during the initial screen paint — if we only parse the first one
      // the canvas stays black.
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
    if (socket) {
      socket.close();
      socket = null;
    }
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
  // displayEl may not have dimensions yet if CSS hasn't applied —
  // fall back to window dimensions in that case
  var width  = displayEl.clientWidth  || window.innerWidth  || 1280;
  var height = displayEl.clientHeight || window.innerHeight || 720;
  var dpi    = Math.round((window.devicePixelRatio || 1) * 96);
  return "width=" + width + "&height=" + height + "&dpi=" + dpi;
}


// ── Input handlers ────────────────────────────────────────────────────────────
function attachInputHandlers(c) {
  var el = c.getDisplay().getElement();

  mouse             = new Guacamole.Mouse(el);
  // Pass true as second arg to sendMouseState so it scales by display zoom
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
function fitDisplay(c) {
  var display      = c.getDisplay();
  var remoteWidth  = display.getWidth();
  var remoteHeight = display.getHeight();
  if (!remoteWidth || !remoteHeight) return;

  var containerWidth = displayEl.clientWidth || window.innerWidth || 1280;
  var scale          = containerWidth / remoteWidth;
  display.scale(scale);
  displayEl.style.height = Math.round(remoteHeight * scale) + "px";
}


// ── Connect ───────────────────────────────────────────────────────────────────
function connect() {
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
      return Promise.reject(err); // stop the chain
    })
    .then(function() {
      // Tear down any existing client
      if (client) { client.disconnect(); client = null; }
      detachInputHandlers();
      displayEl.innerHTML = "";

      var tunnelUrl    = buildTunnelUrl();
      var connectParam = buildConnectParam();

      console.log("[guac] tunnel URL   :", tunnelUrl);
      console.log("[guac] connect param:", connectParam);

      var tunnel = new RawTunnel(tunnelUrl);
      client     = new Guacamole.Client(tunnel);

      // Mount the display element (a <div> containing the canvas layers)
      var displayElem = client.getDisplay().getElement();
      displayEl.appendChild(displayElem);

      // Wire up display resize — called every time guacd sends a resize
      // instruction (e.g. on connect, and when the remote desktop resizes)
      client.getDisplay().onresize = function() {
        fitDisplay(client);
      };

      attachInputHandlers(client);

      client.onerror = function(err) {
        console.error("[guac] client error:", err);
        setStatus((err && err.message) || "Connection error", false);
      };

      client.onstatechange = function(state) {
        // 0=IDLE 1=CONNECTING 2=WAITING 3=CONNECTED 4=DISCONNECTING 5=DISCONNECTED
        var labels = [
          "Idle", "Connecting…", "Waiting…",
          "Connected ✅", "Disconnecting…", "Disconnected"
        ];
        var ok = (state === 3);
        setStatus(labels[state] || ("State " + state), ok);
        if (ok) fitDisplay(client);
      };

      // connect() sends connectParam to RawTunnel.connect(data)
      // which appends it as ?width=W&height=H&dpi=D on the WebSocket URL
      client.connect(connectParam);
    })
    .catch(function() { /* already handled above */ });
}


// ── Disconnect ────────────────────────────────────────────────────────────────
function disconnect() {
  if (client) {
    client.disconnect();
    client = null;
  }
  detachInputHandlers();
  displayEl.innerHTML = "";
  setStatus("Disconnected", false);
}


// ── Event listeners ───────────────────────────────────────────────────────────
connectBtn.addEventListener("click",    connect);
disconnectBtn.addEventListener("click", disconnect);
window.addEventListener("resize", function() {
  if (client) fitDisplay(client);
});

setStatus("Disconnected", false);
