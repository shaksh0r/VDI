// app.v3.js
// FIX: buildTunnelUrl() now returns BASE PATH ONLY (no query params).
//      Dimensions are passed to client.connect(param) instead.
//      The Guacamole WebSocketTunnel internally does:
//        new WebSocket(tunnelUrl + "?" + connectParam, "guacamole")
//      So the final URL becomes: ws://host/ws/guacd?width=W&height=H&dpi=D
//      — clean, no trailing "?"

const statusEl      = document.getElementById("status");
const sessionEl     = document.getElementById("session");
const connectBtn    = document.getElementById("connect");
const disconnectBtn = document.getElementById("disconnect");
const displayEl     = document.getElementById("display");

let client;
let keyboard;
let mouse;

function setStatus(text, ok) {
  statusEl.textContent = text;
  statusEl.dataset.ok  = ok ? "true" : "false";
}

async function fetchSession() {
  const response = await fetch("/api/session");
  const payload  = await response.json();
  if (!payload.ok) {
    throw new Error(payload.detail || payload.message || "No active session");
  }
  return payload.connection;
}

// FIX: Returns base path ONLY — no "?width=..." here
function buildTunnelUrl() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return proto + "://" + location.host + "/ws/guacd";
}

// FIX: Returns connect param string — passed to client.connect()
// Guacamole lib appends this as: tunnelUrl + "?" + connectParam
function buildConnectParam() {
  const width  = displayEl.clientWidth  || 1280;
  const height = displayEl.clientHeight || 720;
  const dpi    = Math.round((window.devicePixelRatio || 1) * 96);
  return "width=" + width + "&height=" + height + "&dpi=" + dpi;
}

function attachInputHandlers(clientInstance) {
  const element = clientInstance.getDisplay().getElement();

  mouse = new Guacamole.Mouse(element);
  mouse.onmousedown = function(state) { clientInstance.sendMouseState(state); };
  mouse.onmouseup   = function(state) { clientInstance.sendMouseState(state); };
  mouse.onmousemove = function(state) { clientInstance.sendMouseState(state); };

  keyboard = new Guacamole.Keyboard(window);
  keyboard.onkeydown = function(keysym) { clientInstance.sendKeyEvent(1, keysym); };
  keyboard.onkeyup   = function(keysym) { clientInstance.sendKeyEvent(0, keysym); };
}

function detachInputHandlers() {
  if (keyboard) {
    keyboard.onkeydown = null;
    keyboard.onkeyup   = null;
    keyboard = null;
  }
  if (mouse) {
    mouse.onmousedown = null;
    mouse.onmouseup   = null;
    mouse.onmousemove = null;
    mouse = null;
  }
}

function fitDisplay(clientInstance) {
  var display      = clientInstance.getDisplay();
  var remoteWidth  = display.getWidth();
  var remoteHeight = display.getHeight();
  if (!remoteWidth || !remoteHeight) return;

  var scale = (displayEl.clientWidth || 1280) / remoteWidth;
  display.scale(scale);
  displayEl.style.height = Math.round(remoteHeight * scale) + "px";
}

async function connect() {
  setStatus("Checking session...", false);

  try {
    var connection = await fetchSession();
    sessionEl.textContent = (connection.protocol || "RDP").toUpperCase() +
      " @ " + (connection.host || connection.hostname || "unknown host");
  } catch (error) {
    sessionEl.textContent = "No session assigned.";
    setStatus(error.message, false);
    return;
  }

  if (client) {
    client.disconnect();
    client = null;
  }

  detachInputHandlers();
  displayEl.innerHTML = "";

  // FIX: base URL only — no query string
  var tunnelUrl    = buildTunnelUrl();
  // FIX: dimensions go here — library appends "?" + this to the URL
  var connectParam = buildConnectParam();

  console.log("[guac] tunnel URL   :", tunnelUrl);
  console.log("[guac] connect param:", connectParam);
  // Final WS URL will be: ws://host/ws/guacd?width=W&height=H&dpi=D  ✅

  var tunnel = new Guacamole.WebSocketTunnel(tunnelUrl);
  client     = new Guacamole.Client(tunnel);

  var canvas = client.getDisplay().getElement();
  canvas.style.display = "block";
  displayEl.appendChild(canvas);

  attachInputHandlers(client);

  client.onerror = function(err) {
    console.error("[guac] error:", err);
    setStatus((err && err.message) || "Connection error", false);
  };

  client.onstatechange = function(state) {
    var labels = ["Idle", "Connecting...", "Waiting...", "Connected", "Disconnecting...", "Disconnected"];
    setStatus(labels[state] || ("State " + state), state === 3);
    if (state === 3) { fitDisplay(client); }
  };

  // FIX: pass dimensions as connect param — NOT empty string
  client.connect(connectParam);
}

function disconnect() {
  if (client) {
    client.disconnect();
    client = null;
  }
  detachInputHandlers();
  displayEl.innerHTML = "";
  setStatus("Disconnected", false);
}

connectBtn.addEventListener("click",    connect);
disconnectBtn.addEventListener("click", disconnect);
window.addEventListener("resize", function() { if (client) fitDisplay(client); });

setStatus("Disconnected", false);
