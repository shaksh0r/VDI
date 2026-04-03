// app.js  –  RawTunnel direct WebSocket relay, no internal UUID handshake

// ── DOM references ────────────────────────────────────────────────────────────
var statusEl      = document.getElementById("status");
var sessionEl     = document.getElementById("session");
var connectBtn    = document.getElementById("connect");
var disconnectBtn = document.getElementById("disconnect");
var displayEl     = document.getElementById("display");

// The .viewport element is the 1fr grid row that owns all available
// space between the action bar and info bar. Reading dimensions from
// here gives the true usable area — unaffected by the scaled canvas
// sitting inside .rdp-surface (displayEl) which is position:absolute.
var viewportEl = displayEl.parentElement;

var client   = null;
var keyboard = null;
var mouse    = null;

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

      if (terminator === ";") {
        complete = true;
        break;
      }
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
  // Use viewportEl so the initial RDP session resolution matches
  // the true available area (the 1fr grid row), not the canvas div.
  var width  = viewportEl.clientWidth  || window.innerWidth  || 1280;
  var height = viewportEl.clientHeight || window.innerHeight || 720;
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
// Reads dimensions from viewportEl — the .viewport div that is the
// 1fr CSS grid row. This is always the correct available area:
//
//   shell grid rows:
//     --h-chrome    48px   ← top nav bar
//     --h-actionbar 40px   ← connect/disconnect bar
//     1fr           ←←← viewportEl lives here
//     --h-infobar   26px   ← bottom status bar
//
// displayEl (.rdp-surface) is position:absolute;inset:0 INSIDE
// viewportEl, so its clientWidth/Height gets polluted by the scaled
// Guacamole canvas div (which has hardcoded px width/height set by
// display.scale()). viewportEl is never polluted by the canvas.
//
// Scale is Math.min(scaleX, scaleY) so the canvas fits fully in
// both dimensions — fills width AND height without overflow.
function fitDisplay(c) {
  var display      = c.getDisplay();
  var remoteWidth  = display.getWidth();
  var remoteHeight = display.getHeight();
  if (!remoteWidth || !remoteHeight) return;

  var containerWidth  = viewportEl.clientWidth  || window.innerWidth  || 1280;
  var containerHeight = viewportEl.clientHeight || window.innerHeight || 720;

  var scaleX = containerWidth  / remoteWidth;
  var scaleY = containerHeight / remoteHeight;

  // Apply non-uniform scale directly — fills width AND height fully
  var el = display.getElement();
  el.style.transform         = "scale(" + scaleX + ", " + scaleY + ")";
  el.style.WebkitTransform   = "scale(" + scaleX + ", " + scaleY + ")";
  el.style.transformOrigin   = "0 0";
  el.style.webkitTransformOrigin = "0 0";
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
      return Promise.reject(err);
    })
    .then(function() {
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

      // Called every time guacd sends a resize instruction
      client.getDisplay().onresize = function() {
        fitDisplay(client);
      };

      attachInputHandlers(client);

      client.onerror = function(err) {
        console.error("[guac] client error:", err);
        setStatus((err && err.message) || "Connection error", false);
      };

      client.onstatechange = function(state) {
        var labels = [
          "Idle", "Connecting…", "Waiting…",
          "Connected", "Disconnecting…", "Disconnected"
        ];
        var ok = (state === 3);
        setStatus(labels[state] || ("State " + state), ok);
        if (ok) fitDisplay(client);
      };

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

// window resize covers: window drag-resize, DevTools open/close,
// zoom level changes, split-screen changes.
window.addEventListener("resize", function() {
  if (client) fitDisplay(client);
});

// fullscreenchange covers: F11, browser fullscreen API, and any
// element entering/exiting fullscreen — window resize does NOT fire
// reliably for this in all browsers.
document.addEventListener("fullscreenchange", function() {
  if (client) fitDisplay(client);
});
document.addEventListener("webkitfullscreenchange", function() {
  if (client) fitDisplay(client);
});

setStatus("Disconnected", false);
