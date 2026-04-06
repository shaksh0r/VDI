import { useState, useRef, useEffect, useCallback } from "react";
import "./styles.css";

// ── Main App ──────────────────────────────────────────────────────────────────
export default function App({ onLogout }) {
  const [statusText, setStatusText] = useState("Disconnected");
  const [statusOk, setStatusOk] = useState(false);
  const [sessionText, setSessionText] = useState("No active session");
  const [isConnected, setIsConnected] = useState(false);
  const [showPlaceholder, setShowPlaceholder] = useState(true);

  const displayRef = useRef(null);
  const clientRef = useRef(null);
  const keyboardRef = useRef(null);
  const mouseRef = useRef(null);

  useEffect(() => {
    document.body.classList.toggle("is-connected", isConnected);
  }, [isConnected]);

  const setStatus = useCallback((text, ok) => {
    setStatusText(text);
    setStatusOk(!!ok);
    setIsConnected(!!ok);
    if (ok) setShowPlaceholder(false);
  }, []);

  // ── Guacamole instruction parser ────────────────────────────────────────────
  function parseInstructions(data) {
    var results = [];
    var pos = 0;
    var len = data.length;

    while (pos < len) {
      var elements = [];
      var complete = false;

      while (pos < len) {
        var dotPos = data.indexOf(".", pos);
        if (dotPos === -1) return results;

        var elemLen = parseInt(data.substring(pos, dotPos), 10);
        if (isNaN(elemLen)) return results;

        var valStart = dotPos + 1;
        var valEnd = valStart + elemLen;

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

  // ── RawTunnel ─────────────────────────────────────────────────────────────
  function RawTunnel(wsUrl) {
    window.Guacamole.Tunnel.call(this);
    var self = this;
    var socket = null;

    this.sendMessage = function () {
      if (!socket || socket.readyState !== WebSocket.OPEN) return;
      if (arguments.length === 0) return;
      var parts = [];
      for (var i = 0; i < arguments.length; i++) {
        var val = String(arguments[i]);
        parts.push(val.length + "." + val);
      }
      socket.send(parts.join(",") + ";");
    };

    this.connect = function (data) {
      var url = wsUrl + (data ? "&" + data : "");
      self.setState(window.Guacamole.Tunnel.State.CONNECTING);
      socket = new WebSocket(url, "guacamole");

      socket.onopen = function () {
        console.log("[RawTunnel] WebSocket open");
        self.setState(window.Guacamole.Tunnel.State.OPEN);
      };

      socket.onmessage = function (event) {
        var instructions = parseInstructions(event.data);
        for (var i = 0; i < instructions.length; i++) {
          var instr = instructions[i];
          if (self.oninstruction) self.oninstruction(instr.opcode, instr.args);
        }
      };

      socket.onerror = function (event) {
        console.error("[RawTunnel] WebSocket error", event);
        if (self.onerror) {
          self.onerror(new window.Guacamole.Status(
            window.Guacamole.Status.Code.SERVER_ERROR, "WebSocket error"
          ));
        }
        self.setState(window.Guacamole.Tunnel.State.CLOSED);
      };

      socket.onclose = function (event) {
        console.log("[RawTunnel] closed  code=" + event.code + "  reason=" + (event.reason || "(none)"));
        self.setState(window.Guacamole.Tunnel.State.CLOSED);
      };
    };

    this.disconnect = function () {
      self.setState(window.Guacamole.Tunnel.State.CLOSED);
      if (socket) { socket.close(); socket = null; }
    };
  }

  // ── Token ──────────────────────────────────────────────────────────────────
  function getToken() {
    return localStorage.getItem("token") || "";
  }

  // ── URL builders ───────────────────────────────────────────────────────────
  function buildTunnelUrl() {
    var proto = location.protocol === "https:" ? "wss" : "ws";
    var token = getToken();
    return proto + "://" + location.host + "/ws/guacd?token=" + encodeURIComponent(token);
  }

  function buildConnectParam() {
    var el = displayRef.current;
    var width = (el && el.clientWidth) || window.innerWidth || 1280;
    var height = (el && el.clientHeight) || window.innerHeight || 720;
    var dpi = Math.round((window.devicePixelRatio || 1) * 96);
    return "width=" + width + "&height=" + height + "&dpi=" + dpi;
  }

  // ── Display ────────────────────────────────────────────────────────────────
  function fitDisplay(c) {
    var display = c.getDisplay();
    var remoteWidth = display.getWidth();
    var remoteHeight = display.getHeight();
    if (!remoteWidth || !remoteHeight) return;

    var el = displayRef.current;
    var containerWidth = (el && el.clientWidth) || window.innerWidth || 1280;
    var scale = containerWidth / remoteWidth;
    display.scale(scale);
    if (el) el.style.height = Math.round(remoteHeight * scale) + "px";
  }

  // ── Input handlers ─────────────────────────────────────────────────────────
  function attachInputHandlers(c) {
    var el = c.getDisplay().getElement();

    var m = new window.Guacamole.Mouse(el);
    m.onmousedown = function (s) { c.sendMouseState(s, true); };
    m.onmouseup = function (s) { c.sendMouseState(s, true); };
    m.onmousemove = function (s) { c.sendMouseState(s, true); };
    mouseRef.current = m;

    var kb = new window.Guacamole.Keyboard(document);
    kb.onkeydown = function (k) { c.sendKeyEvent(1, k); };
    kb.onkeyup = function (k) { c.sendKeyEvent(0, k); };
    keyboardRef.current = kb;
  }

  function detachInputHandlers() {
    if (keyboardRef.current) {
      keyboardRef.current.onkeydown = null;
      keyboardRef.current.onkeyup = null;
      keyboardRef.current = null;
    }
    if (mouseRef.current) {
      mouseRef.current.onmousedown = null;
      mouseRef.current.onmouseup = null;
      mouseRef.current.onmousemove = null;
      mouseRef.current = null;
    }
  }

  // ── Provisioning calls ─────────────────────────────────────────────────────
  function provisionConnect() {
    var token = getToken();
    return fetch("/provision/connect", {
      method: "POST",
      headers: {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
      },
    }).then(function (r) {
      return r.json().then(function (body) {
        if (!r.ok) throw new Error(body.detail || "Provisioning failed");
        return body;
      });
    });
  }

  function provisionDisconnect() {
    var token = getToken();
    fetch("/provision/disconnect", {
      method: "POST",
      headers: {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
      },
    }).catch(function (err) {
      console.warn("[provision] disconnect call failed:", err);
    });
  }

  // ── Connect ────────────────────────────────────────────────────────────────
  function connect() {
    if (!window.Guacamole) {
      setStatus("Guacamole library not loaded", false);
      return;
    }

    RawTunnel.prototype = Object.create(window.Guacamole.Tunnel.prototype);
    RawTunnel.prototype.constructor = RawTunnel;

    var token = getToken();
    if (!token) {
      setStatus("Not logged in", false);
      return;
    }

    setStatus("Requesting VM...", false);

    provisionConnect()
      .then(function (provision) {
        setSessionText(
          "RDP @ " + provision.floating_ip +
          "  (expires in " + provision.session_expires_in_minutes + " min)"
        );
        setStatus("Connecting...", false);
      })
      .catch(function (err) {
        setSessionText("No session assigned.");
        setStatus(err.message, false);
        return Promise.reject(err);
      })
      .then(function () {
        if (clientRef.current) {
          clientRef.current.disconnect();
          clientRef.current = null;
        }
        detachInputHandlers();

        var el = displayRef.current;
        if (el) el.innerHTML = "";

        var tunnelUrl = buildTunnelUrl();
        var connectParam = buildConnectParam();

        console.log("[guac] tunnel URL   :", tunnelUrl);
        console.log("[guac] connect param:", connectParam);

        var tunnel = new RawTunnel(tunnelUrl);
        var c = new window.Guacamole.Client(tunnel);
        clientRef.current = c;

        var displayElem = c.getDisplay().getElement();
        if (el) el.appendChild(displayElem);

        c.getDisplay().onresize = function () { fitDisplay(c); };
        attachInputHandlers(c);

        c.onerror = function (err) {
          console.error("[guac] client error:", err);
          setStatus((err && err.message) || "Connection error", false);
        };

        c.onstatechange = function (state) {
          var labels = ["Idle", "Connecting…", "Waiting…", "Connected", "Disconnecting…", "Disconnected"];
          var ok = state === 3;
          setStatus(labels[state] || "State " + state, ok);
          if (ok) fitDisplay(c);
        };

        c.connect(connectParam);
      })
      .catch(function () { });
  }

  // ── Disconnect ─────────────────────────────────────────────────────────────
  function disconnect() {
    if (clientRef.current) {
      clientRef.current.disconnect();
      clientRef.current = null;
    }
    detachInputHandlers();

    var el = displayRef.current;
    if (el) el.innerHTML = "";

    setShowPlaceholder(true);
    setStatus("Disconnected", false);
    setSessionText("No active session");

    provisionDisconnect();
  }

  // ── Logout ─────────────────────────────────────────────────────────────────
  async function handleLogout() {
    // Disconnect VM if active
    if (clientRef.current) disconnect();

    // Revoke token on auth service
    const token = getToken();
    if (token) {
      await fetch("/auth/logout", {
        method: "POST",
        headers: { "Authorization": "Bearer " + token },
      }).catch(() => { });
    }

    onLogout();
  }

  useEffect(() => {
    function onResize() {
      if (clientRef.current) fitDisplay(clientRef.current);
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  return (
    <div className="shell">

      {/* ── CHROME ── */}
      <header className="chrome">
        <div className="chrome__brand">
          <div className="chrome__mark">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <rect x="1" y="1" width="6" height="6" rx="1" fill="currentColor" opacity="0.9" />
              <rect x="9" y="1" width="6" height="6" rx="1" fill="currentColor" opacity="0.5" />
              <rect x="1" y="9" width="6" height="6" rx="1" fill="currentColor" opacity="0.5" />
              <rect x="9" y="9" width="6" height="6" rx="1" fill="currentColor" opacity="0.25" />
            </svg>
          </div>
          <span className="chrome__wordmark">VDI Mirror</span>
          <span className="chrome__rule"></span>
          <span className="chrome__caption">Remote Desktop Relay</span>
        </div>

        <div className="chrome__session">
          <div className="chrome__session-dot"></div>
          <span className="chrome__session-text">{sessionText}</span>
        </div>

        <div className="chrome__right">
          <div className="status-chip" data-ok={statusOk ? "true" : "false"}>
            <span className="status-chip__ring"></span>
            <span className="status-chip__label">{statusText}</span>
          </div>
          <button className="btn btn--disconnect" onClick={handleLogout} aria-label="Log out">
            <svg className="btn__icon" width="12" height="12" viewBox="0 0 12 12" fill="none">
              <path d="M5 2H2v8h3M8 4l2 2-2 2M10 6H5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            Log out
          </button>
        </div>
      </header>

      {/* ── ACTIONBAR ── */}
      <div className="actionbar">
        <div className="actionbar__group">
          <button className="btn btn--connect" onClick={connect} aria-label="Connect to remote session">
            <svg className="btn__icon" width="12" height="12" viewBox="0 0 12 12" fill="none">
              <circle cx="6" cy="6" r="2" fill="currentColor" />
              <path d="M1.5 6a4.5 4.5 0 0 1 4.5-4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              <path d="M10.5 6a4.5 4.5 0 0 1-4.5 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
            Connect
          </button>

          <div className="btn-divider"></div>

          <button className="btn btn--disconnect" onClick={disconnect} aria-label="Disconnect session">
            <svg className="btn__icon" width="12" height="12" viewBox="0 0 12 12" fill="none">
              <path d="M3 3l6 6M9 3l-6 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
            Disconnect
          </button>
        </div>

        <div className="actionbar__meta">
          <span className="meta-tag">guacd</span>
          <span className="meta-sep">/</span>
          <span className="meta-tag">WebSocket</span>
          <span className="meta-sep">/</span>
          <span className="meta-tag">RDP</span>
        </div>
      </div>

      {/* ── VIEWPORT ── */}
      <main className="viewport">
        {showPlaceholder && (
          <div className="idle-state">
            <div className="idle-state__glyph">
              <svg width="40" height="40" viewBox="0 0 40 40" fill="none">
                <rect x="4" y="6" width="32" height="22" rx="3" stroke="currentColor" strokeWidth="1.5" />
                <path d="M13 34h14M20 28v6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                <path d="M14 17l4 4 8-8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" opacity="0.4" />
              </svg>
            </div>
            <p className="idle-state__heading">No Session Active</p>
            <p className="idle-state__body">
              Press <kbd>Connect</kbd> to initiate the remote desktop stream
            </p>
          </div>
        )}
        <div className="rdp-surface" ref={displayRef}></div>
      </main>

      {/* ── INFOBAR ── */}
      <footer className="infobar">
        <div className="infobar__left">
          <span className="infobar__item">
            <span className="infobar__key">Transport</span>
            <span className="infobar__val">guacd 4822 / WebSocket</span>
          </span>
          <span className="infobar__divider"></span>
          <span className="infobar__item">
            <span className="infobar__key">Protocol</span>
            <span className="infobar__val">RDP</span>
          </span>
        </div>
        <div className="infobar__right">
          <span className="infobar__item">
            <span className="infobar__key">VDI Mirror</span>
            <span className="infobar__val">1.0.0</span>
          </span>
        </div>
      </footer>

    </div>
  );
}