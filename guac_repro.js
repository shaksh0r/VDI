#!/usr/bin/env node
/**
 * Browser-equivalent reproduction: runs the REAL vendored Guacamole client
 * (guacamole-common-js 1.3.0) in Node via jsdom, against the live stack.
 *
 * Usage: node guac_repro.js [vm_ip_override]
 *   - claims a VM as student1 (or connects to an existing assignment)
 *   - opens the WS via the same RawTunnel as static/app.js
 *   - logs every state change, received instruction opcode, and sent message
 */
const { JSDOM } = require("jsdom");

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  pretendToBeVisual: true,
});
global.window = dom.window;
global.document = dom.window.document;
global.navigator = dom.window.navigator;

// jsdom has no canvas 2d; stub it so display draw calls are no-ops
const ctxStub = new Proxy({}, {
  get(t, prop) {
    if (prop === "canvas") return null;
    if (prop === "measureText") return () => ({ width: 0 });
    return () => {};
  },
  set() { return true; },
});
dom.window.HTMLCanvasElement.prototype.getContext = function () { return ctxStub; };
dom.window.HTMLCanvasElement.prototype.toDataURL = () => "";

// no-op Image: display draws stay pending instead of crashing the client
class FakeImage {
  constructor() { this.onload = null; this.onerror = null; }
  set src(v) { /* never decode — fine for keepalive testing */ }
  get src() { return ""; }
}
dom.window.Image = FakeImage;
global.Image = FakeImage;

const Guacamole = require("./mirroring-service/static/guacamole-common-js.min.js");

const AUTH = "http://localhost:8003";
const PROV = "http://localhost:8001";
const WS_BASE = "ws://localhost:8000/ws/guacd";
const TARGET_IP = process.argv[2] || null;

const t0 = Date.now();
const log = (tag, msg) => console.log(
  `[${((Date.now() - t0) / 1000).toFixed(1).padStart(6)}s] ${tag.padEnd(14)} ${msg}`
);

// ── instruction parser — identical to static/app.js ──────────────────────────
function parseInstructions(data) {
  const results = [];
  let pos = 0;
  const len = data.length;
  while (pos < len) {
    const elements = [];
    let complete = false;
    while (pos < len) {
      const dotPos = data.indexOf(".", pos);
      if (dotPos === -1) return results;
      const elemLen = parseInt(data.substring(pos, dotPos), 10);
      if (isNaN(elemLen)) return results;
      const valStart = dotPos + 1;
      const valEnd = valStart + elemLen;
      if (valEnd > len) return results;
      elements.push(data.substring(valStart, valEnd));
      const terminator = data.charAt(valEnd);
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

// ── RawTunnel — identical to static/app.js, with instruction logging ─────────
function RawTunnel(wsUrl) {
  Guacamole.Tunnel.call(this);
  const self = this;
  let socket = null;

  this.sendMessage = function () {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    const parts = [];
    for (let i = 0; i < arguments.length; i++) {
      const val = String(arguments[i]);
      parts.push(new TextEncoder().encode(val).length + "." + val);
    }
    log("SEND", parts.join(",") + ";");
    socket.send(parts.join(",") + ";");
  };

  this.connect = function (data) {
    const url = wsUrl + (data ? "?" + data : "");
    self.setState(Guacamole.Tunnel.State.CONNECTING);
    socket = new WebSocket(url, "guacamole");
    socket.onopen = () => { log("WS", "open"); self.setState(Guacamole.Tunnel.State.OPEN); };
    socket.onmessage = (event) => {
      const raw = event.data;
      const instructions = parseInstructions(raw);
      for (const instr of instructions) {
        log("RECV", (instr.opcode + " " + instr.args.join(" ")).substring(0, 90));
        if (self.oninstruction) self.oninstruction(instr.opcode, instr.args);
      }
    };
    socket.onerror = (e) => { log("WS", "ERROR " + (e && e.message || "")); };
    socket.onclose = (e) => { log("WS", `closed code=${e.code} reason=${e.reason || "(none)"}`); };
  };

  this.disconnect = function () {
    self.setState(Guacamole.Tunnel.State.CLOSED);
    if (socket) { socket.close(); socket = null; }
  };
}
RawTunnel.prototype = Object.create(Guacamole.Tunnel.prototype);
RawTunnel.prototype.constructor = RawTunnel;

// ── drive the real client ────────────────────────────────────────────────────
async function main() {
  const login = await fetch(AUTH + "/auth/login", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: process.env.GUAC_USER || "student1", password: process.env.GUAC_PASS || "studentpass123" }),
  }).then((r) => r.json());
  log("AUTH", "token ok role=" + login.role);

  let claim = null;
  for (let i = 0; i < 30 && !claim; i++) {
    const r = await fetch(PROV + "/provision/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + login.access_token },
      body: JSON.stringify({ pool_type: "non_persistent" }),
    });
    if (r.status === 200) claim = await r.json();
    else { log("CLAIM", "waiting for VM… (" + r.status + ")"); await new Promise((r) => setTimeout(r, 12000)); }
  }
  if (!claim) { log("CLAIM", "FAILED — no VM"); process.exit(1); }
  log("CLAIM", `instance=${claim.instance_id.slice(0, 8)} ip=${claim.floating_ip}`);

  if (TARGET_IP) log("CLAIM", `NOTE: overriding target expectation → ${TARGET_IP}`);

  const client = new Guacamole.Client(new RawTunnel(WS_BASE));
  let currentState = 0;
  client.onstatechange = (state) => {
    currentState = state;
    const labels = ["IDLE", "CONNECTING", "WAITING", "CONNECTED", "DISCONNECTING", "DISCONNECTED"];
    log("STATE", labels[state] || String(state));
    if (APPJS && state === 3) {
      // like app.js: onresize + on CONNECTED → fitDisplay
      setTimeout(fitDisplay, 300);
    }
  };
  client.onerror = (err) => { log("ERROR", (err && err.message) || String(err)); };

  const APPJS = process.env.APPJS === "1";   // mimic app.js: fitDisplay + sendSize on CONNECTED
  const DPI = process.env.DPI || "96";

  const params = new URLSearchParams({
    token: login.access_token,
    width: "1197", height: "747", dpi: DPI,
  }).toString();
  log("CONNECT", (APPJS ? "[appjs-mode] " : "") + params.replace(/token=[^&]+/, "token=***"));
  client.connect(params);

  // app.js's fitDisplay: scale the display to the container and tell
  // guacd to resize the RDP session to the container size.
  const fitDisplay = () => {
    try {
      const display = client.getDisplay();
      const rw = display.getWidth(), rh = display.getHeight();
      if (!rw || !rh) return;
      const cw = 1197, ch = 633;
      const scale = Math.min(cw / rw, ch / rh);
      display.scale(scale);
      log("FIT", `sendSize(${cw},${ch}) remote=${rw}x${rh} scale=${scale.toFixed(3)}`);
      client.sendSize(cw, ch);
    } catch (e) { log("FIT", "error: " + e.message); }
  };

  // watch for 40s; report whether the display received screen data
  let gotScreen = false;
  const display = client.getDisplay();
  const orig = display.onresize;
  display.onresize = (w, h) => { gotScreen = gotScreen || (w > 0 && h > 0); if (orig) orig(w, h); };

  await new Promise((r) => setTimeout(r, 40000));
  log("RESULT", `display sized: ${display.getWidth()}x${display.getHeight()} (screen data received: ${gotScreen})`);
  log("RESULT", client.getState() === 3 ? "SESSION STABLE — client still CONNECTED" : "SESSION BROKEN — client state " + client.getState());
  client.disconnect();
  process.exit(0);
}

main().catch((e) => { console.error("fatal:", e); process.exit(1); });
