// app.js  –  VDI mirror frontend: sign in → allocate a VM → Guacamole session.
//            RawTunnel direct WebSocket relay (no internal UUID handshake)
//            + auto-reconnect with exponential backoff for transient xrdp drops.
//
// Service topology (plan.md Workflow 1):
//   browser ── POST /auth/login ────────────────────► auth-service (token)
//   browser ── POST /provision/connect (Bearer) ────► provisioning (floating_ip)
//   browser ── WS  /ws/guacd?token=… (Guacamole) ───► mirroring  (RDP stream)
//   browser ── GET  /provision/status (poll) ───────► provisioning (expiry)
//   browser ── POST /provision/disconnect ──────────► provisioning (release)

// ── Service endpoints ─────────────────────────────────────────────────────────
// Ports match the published compose ports. Overridable via
// ?auth=http://host:port&prov=http://host:port, or via the mirroring
// service's /api/config (AUTH_PUBLIC_URL / PROVISION_PUBLIC_URL) which
// wins when set (reverse-proxy / TLS deployment).
var AUTH_PORT = 8003;
var PROV_PORT = 8001;
var AUTH_API_BASE   = null;
var PROV_API_BASE   = null;

function authApiBase() {
  if (AUTH_API_BASE) return AUTH_API_BASE;
  var p = new URLSearchParams(location.search);
  if (p.get("auth")) return p.get("auth");
  return location.protocol + "//" + location.hostname + ":" + AUTH_PORT;
}

function provApiBase() {
  if (PROV_API_BASE) return PROV_API_BASE;
  var p = new URLSearchParams(location.search);
  if (p.get("prov")) return p.get("prov");
  return location.protocol + "//" + location.hostname + ":" + PROV_PORT;
}

// ── DOM references ────────────────────────────────────────────────────────────
var statusEl      = document.getElementById("status");
var sessionEl     = document.getElementById("session");
var connectBtn    = document.getElementById("connect");
var disconnectBtn = document.getElementById("disconnect");
var logoutBtn     = document.getElementById("logout");
var displayEl     = document.getElementById("display");
var placeholderEl = document.getElementById("placeholder");
var loginEl       = document.getElementById("login");
var loginForm     = document.getElementById("login-form");
var loginUser     = document.getElementById("login-username");
var loginPass     = document.getElementById("login-password");
var loginError    = document.getElementById("login-error");
var countdownEl   = document.getElementById("sb-session");

// ── Signup pane refs ──────────────────────────────────────────────────────────
var signupEl      = document.getElementById("signup");
var signupForm    = document.getElementById("signup-form");
var signupError   = document.getElementById("signup-error");
var regName       = document.getElementById("reg-name");
var regEmail      = document.getElementById("reg-email");
var regSid        = document.getElementById("reg-sid");
var regDept       = document.getElementById("reg-dept");
var regUser       = document.getElementById("reg-username");
var regPass       = document.getElementById("reg-password");
var regPass2      = document.getElementById("reg-password2");
var showSignupBtn = document.getElementById("show-signup");
var showLoginBtn  = document.getElementById("show-login");

var client   = null;
var keyboard = null;
var mouse    = null;

// ── Resize debounce ───────────────────────────────────────────────────────────
var resizeTimer = null;

// ── Auth / session state ──────────────────────────────────────────────────────
var token             = sessionStorage.getItem("vdi_token") || "";
var tokenUser         = null;             // {username, user_id, role}
var activeSession     = null;             // {instance_id, floating_ip, pool_name}
var expiresAt         = null;             // ISO string from /provision/status
var statusPollTimer   = null;             // /provision/status interval
var countdownTimer    = null;             // 1 s session-countdown tick

// ── Auto-reconnect state ──────────────────────────────────────────────────────
var manualDisconnect  = false;   // user clicked Disconnect — no auto-reconnect
var reconnectTimer    = null;    // setTimeout handle
var reconnectAttempts = 0;       // current retry count
var MAX_RECONNECT     = 5;       // give up after this many attempts
var RECONNECT_BASE_MS = 2000;    // 2 s → 4 s → 8 s → 16 s → 32 s
var STATUS_POLL_MS    = 15000;   // plan.md: poll /provision/status every 15–30 s

// ── UI state helpers ──────────────────────────────────────────────────────────
function setStatus(text, ok) {
  var chip  = document.getElementById("status");
  var label = chip.querySelector(".status-chip__label");
  chip.dataset.ok = ok ? "true" : "false";
  if (label) label.textContent = text;
  document.body.classList.toggle("is-connected", !!ok);
}

// Which auth pane is open ("login" | "signup") — used when the auth view
// is shown again (logout, expired token, first load).
var authPane = "login";

function showAuthPane(pane) {
  authPane = (pane === "signup") ? "signup" : "login";
  loginEl.style.display  = (authPane === "login")  ? "flex" : "none";
  signupEl.style.display = (authPane === "signup") ? "flex" : "none";
}

function setView(mode) {
  // mode: "login" | "idle" | "connected"
  // Explicit display values (never "") so inline styles always win over
  // stylesheet/[hidden]-attribute defaults — "" silently leaves elements
  // hidden (this was the blank-screen bug after connecting).
  var showLogin = (mode === "login");
  var showIdle  = (mode === "idle");
  var showDisp  = (mode === "connected");

  if (showLogin) {
    showAuthPane(authPane);
  } else {
    loginEl.style.display  = "none";
    signupEl.style.display = "none";
  }
  placeholderEl.style.display = showIdle  ? "flex"  : "none";
  displayEl.style.display     = showDisp  ? "block" : "none";
  logoutBtn.style.display     = showLogin ? "none"  : "inline-flex";

  connectBtn.disabled    = showLogin;
  disconnectBtn.disabled = !showDisp;
}

// ── Auth ──────────────────────────────────────────────────────────────────────
function setToken(value, user) {
  token    = value || "";
  tokenUser = user || null;
  if (token) {
    sessionStorage.setItem("vdi_token", token);
    sessionStorage.setItem("vdi_user", JSON.stringify(user));
  } else {
    sessionStorage.removeItem("vdi_token");
    sessionStorage.removeItem("vdi_user");
  }
}

function apiError(resp) {
  return resp.json()
    .then(function (d) {
      if (d && Array.isArray(d.detail)) {
        // FastAPI 422 validation errors: detail is a list of {loc,msg,type}
        return d.detail.map(function (x) { return x.msg || ""; })
                      .filter(Boolean)
                      .join("; ");
      }
      return (d && d.detail) || ("HTTP " + resp.status);
    })
    .catch(function () { return "HTTP " + resp.status; });
}

function login(username, password) {
  setStatus("Signing in…", false);
  return fetch(authApiBase() + "/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: username, password: password }),
  }).then(function (resp) {
    if (!resp.ok) return apiError(resp).then(function (m) { throw new Error(m); });
    return resp.json();
  }).then(function (payload) {
    setToken(payload.access_token, {
      username: username,
      user_id: payload.user_id,
      role: payload.role,
    });
    sessionEl.textContent = "Signed in as " + username;
    setView("idle");
    setStatus("Ready", false);
    loginForm.reset();
    loginError.textContent = "";
    return true;
  }).catch(function (err) {
    loginError.textContent = err.message || "Login failed";
    setStatus("Disconnected", false);
    throw err;
  });
}

function register() {
  // Student self-registration → auth-service /auth/signup, then auto sign-in.
  setStatus("Creating account…", false);
  signupError.textContent = "";

  var registered = false;   // signup succeeded (auto sign-in may still fail)

  var payload = {
    full_name:   regName.value.trim(),
    email:       regEmail.value.trim(),
    student_id:  regSid.value.trim(),
    department:  regDept.value.trim(),
    username:    regUser.value.trim(),
    password:    regPass.value,
  };

  // Client-side checks (server enforces the rest)
  if (payload.username.length < 4) {
    signupError.textContent = "Username must be at least 4 characters";
    setStatus("Disconnected", false);
    return Promise.reject(new Error("validation"));
  }
  if (payload.password.length < 8) {
    signupError.textContent = "Password must be at least 8 characters";
    setStatus("Disconnected", false);
    return Promise.reject(new Error("validation"));
  }
  if (payload.password !== regPass2.value) {
    signupError.textContent = "Passwords do not match";
    setStatus("Disconnected", false);
    return Promise.reject(new Error("validation"));
  }

  return fetch(authApiBase() + "/auth/signup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then(function (resp) {
    if (!resp.ok) return apiError(resp).then(function (m) { throw new Error(m); });
    return resp.json();
  }).then(function () {
    signupForm.reset();
    registered = true;
    // Account created — sign the student straight in.
    return login(payload.username, payload.password);
  }).then(function () {
    return true;  // fully signed in (login() switched to the idle view)
  }).catch(function (err) {
    if (err && err.message === "validation") return;
    if (registered) {
      // Account exists; only the auto sign-in failed — hand over to login.
      signupError.textContent = "";
      showAuthPane("login");
      loginError.textContent = "Account created — please sign in to continue.";
      setStatus("Disconnected", false);
      return;
    }
    signupError.textContent = err.message || "Registration failed";
    setStatus("Disconnected", false);
    throw err;
  });
}

function logout() {
  manualDisconnect = true;
  stopPolling();
  cancelReconnect();
  cancelClaimRetry();
  if (client) { client.disconnect(); client = null; }
  detachInputHandlers();
  displayEl.innerHTML = "";

  // Release any active VM BEFORE the token is cleared — signing out must
  // not leave the pool slot occupied for the whole session duration.
  releaseAssignment().then(function (out) {
    if (out && out.error) {
      setStatus("Signed out — but VM release failed (" + out.error + ")", false);
      sessionEl.textContent = "Sign out: VM release failed — it will expire later";
    }
  });

  activeSession = null;
  expiresAt = null;
  setToken(null, null);
  sessionEl.textContent = "Not signed in";
  showAuthPane("login");
  setView("login");
  setStatus("Disconnected", false);
}

function handleAuthExpired() {
  // 401 from any authenticated call — the token is gone/expired.
  stopPolling();
  cancelReconnect();
  cancelClaimRetry();
  if (client) { client.disconnect(); client = null; }
  detachInputHandlers();
  displayEl.innerHTML = "";
  activeSession = null;
  expiresAt = null;
  setToken(null, null);
  sessionEl.textContent = "Not signed in";
  showAuthPane("login");
  setView("login");
  setStatus("Session expired — sign in again", false);
  loginError.textContent = "Your session expired. Please sign in again.";
}

// ── Session allocation ────────────────────────────────────────────────────────
function connect() {
  if (!token) { setView("login"); return; }
  manualDisconnect = false;
  cancelClaimRetry();
  cancelReconnect();
  setStatus("Requesting a VM…", false);

  return fetch(provApiBase() + "/provision/connect", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": "Bearer " + token,
    },
    body: JSON.stringify({ pool_type: "non_persistent" }),
  }).then(function (resp) {
    if (resp.status === 401) { handleAuthExpired(); throw new Error("auth"); }
    if (!resp.ok) return apiError(resp).then(function (m) {
      var err = new Error(m);
      err.status = resp.status;
      throw err;
    });
    return resp.json();
  }).then(function (payload) {
    activeSession = {
      instance_id: payload.instance_id,
      floating_ip: payload.floating_ip,
      pool_name: payload.pool_name,
    };
    expiresAt = null; // refreshed by status polling
    sessionEl.textContent = "RDP @ " + payload.floating_ip;
    setStatus("Connecting…", false);
    startPolling();
    _doConnect();
  }).catch(function (err) {
    if (err && err.message === "auth") return;
    if (err && err.status === 503) {
      // Pool is empty (claim waited and timed out server-side) — show a
      // message and auto-retry until a VM becomes available.
      sessionEl.textContent = "No VMs available right now";
      setStatus("No VMs available — will retry automatically…", false);
      scheduleClaimRetry();
      return;
    }
    sessionEl.textContent = "No session assigned.";
    setStatus((err && err.message) || "Connection error", false);
  });
}

// ── Empty-pool auto-retry ─────────────────────────────────────────────────────
// The provisioning API holds a claim for ~60 s (waiting for a mid-provision
// VM to become ready), then answers 503. Instead of making the student
// click Connect repeatedly, keep retrying in the background and connect the
// moment a VM is free.
var claimRetryTimer = null;
var waitingForVm    = false;

function scheduleClaimRetry() {
  cancelClaimRetry();
  waitingForVm = true;
  claimRetryTimer = setInterval(function () {
    if (!token || manualDisconnect) { cancelClaimRetry(); return; }
    setStatus("Still waiting for a free VM…", false);
    connect();  // handles success / further 503s itself
  }, 30000);
}

function cancelClaimRetry() {
  waitingForVm = false;
  if (claimRetryTimer) {
    clearInterval(claimRetryTimer);
    claimRetryTimer = null;
  }
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
    var enc = new TextEncoder();
    var parts = [];
    for (var i = 0; i < arguments.length; i++) {
      var val = String(arguments[i]);
      // Length prefix is the UTF-8 BYTE length — val.length (UTF-16 units)
      // corrupts non-ASCII values (e.g. passwords with accented characters).
      parts.push(enc.encode(val).length + "." + val);
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
  // Clamped to the same sane ranges the server enforces.
  var width  = displayEl.clientWidth  || window.innerWidth  || 1280;
  var height = displayEl.clientHeight || window.innerHeight || 720;
  var dpi    = Math.round((window.devicePixelRatio || 1) * 96);
  width  = Math.min(7680, Math.max(640,  width));
  height = Math.min(4320, Math.max(480,  height));
  dpi    = Math.min(288,  Math.max(48,   dpi));
  var parts  = ["width=" + width, "height=" + height, "dpi=" + dpi];
  if (token) parts.push("token=" + encodeURIComponent(token));
  return parts.join("&");
}


// ── Input handlers ────────────────────────────────────────────────────────────
// NOTE: Guacamole.Keyboard attaches keydown/keyup listeners on `document`
// in its constructor and exposes no detach API in 1.3.0. Creating a new
// instance per connect leaks document-level listeners, so a single instance
// is created once and reused; only the callbacks are rewired.
var keyboardSingleton = null;

function attachInputHandlers(c) {
  var el = c.getDisplay().getElement();

  mouse             = new Guacamole.Mouse(el);
  mouse.onmousedown = function(s) { c.sendMouseState(s, true); };
  mouse.onmouseup   = function(s) { c.sendMouseState(s, true); };
  mouse.onmousemove = function(s) { c.sendMouseState(s, true); };

  if (!keyboardSingleton) keyboardSingleton = new Guacamole.Keyboard(document);
  keyboard = keyboardSingleton;
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


// ── Status polling + session countdown ────────────────────────────────────────
function startPolling() {
  stopPolling();
  pollStatus();
  statusPollTimer = setInterval(pollStatus, STATUS_POLL_MS);
}

function stopPolling() {
  if (statusPollTimer) { clearInterval(statusPollTimer); statusPollTimer = null; }
  if (countdownTimer)  { clearInterval(countdownTimer);  countdownTimer  = null; }
  expiresAt = null;
  updateCountdown();
}

function pollStatus() {
  if (!token) return;
  fetch(provApiBase() + "/provision/status", {
    headers: { "Authorization": "Bearer " + token },
  }).then(function (resp) {
    if (resp.status === 401) { handleAuthExpired(); return null; }
    return resp.json();
  }).then(function (st) {
    if (st === null) return;
    if (!st.has_assignment) {
      // Assignment gone — expired, released elsewhere, or admin action.
      handleSessionEnded();
      return;
    }
    expiresAt = st.expires_at;
    startCountdown();
  }).catch(function () {
    // provisioning temporarily unreachable — keep current state
  });
}

function startCountdown() {
  if (countdownTimer) return;
  updateCountdown();
  countdownTimer = setInterval(updateCountdown, 1000);
}

function updateCountdown() {
  if (!expiresAt) {
    if (countdownEl) countdownEl.textContent = "";
    return;
  }
  var remaining = new Date(expiresAt).getTime() - Date.now();
  if (remaining <= 0) {
    if (countdownEl) countdownEl.textContent = "Session expired";
    handleSessionEnded();
    return;
  }
  var mins = Math.floor(remaining / 60000);
  var secs = Math.floor((remaining % 60000) / 1000);
  if (countdownEl) {
    countdownEl.textContent = "expires in " + mins + "m " + secs + "s";
  }
}

function handleSessionEnded() {
  stopPolling();
  cancelReconnect();
  cancelClaimRetry();
  if (client) { client.disconnect(); client = null; }
  detachInputHandlers();
  displayEl.innerHTML = "";
  activeSession = null;
  expiresAt = null;
  sessionEl.textContent = "Signed in as " + (tokenUser ? tokenUser.username : "user");
  setView("idle");
  setStatus("Session expired — press Connect for a new VM", false);
}


// ── Auto-reconnect ────────────────────────────────────────────────────────────
// Called whenever a non-manual disconnect is detected. Before retrying the
// WebSocket, the active assignment is re-checked: if it is gone (expired or
// released), auto-reconnect stops instead of burning retries on a dead session.
function scheduleReconnect() {
  if (manualDisconnect) return;
  if (reconnectAttempts >= MAX_RECONNECT) {
    setStatus("Could not reconnect — check VM / xrdp", false);
    return;
  }

  fetch(provApiBase() + "/provision/status", {
    headers: { "Authorization": "Bearer " + token },
  }).then(function (resp) {
    if (resp.status === 401) { handleAuthExpired(); return null; }
    return resp.json();
  }).then(function (st) {
    if (st === null) return;
    if (!st.has_assignment) {
      setStatus("Session ended — press Connect for a new VM", false);
      stopPolling();
      return;
    }
    _scheduleRetry();
  }).catch(function () {
    // provisioning unreachable — retry the WS anyway, it re-checks on failure
    _scheduleRetry();
  });
}

function _scheduleRetry() {
  var delay = RECONNECT_BASE_MS * Math.pow(2, reconnectAttempts);
  reconnectAttempts++;
  setStatus("Reconnecting in " + Math.round(delay / 1000) + "s… (attempt " +
            reconnectAttempts + "/" + MAX_RECONNECT + ")", false);
  reconnectTimer = setTimeout(function() {
    reconnectTimer = null;
    _doConnect();   // inner connect — WS only, assignment already confirmed
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
  setView("connected");

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
      // → confirm the assignment still exists, then retry with backoff
      console.warn("[guac] unexpected disconnect — checking assignment");
      scheduleReconnect();
    }
  };

  client.connect(connectParam);
}


// ── Connect (user-initiated) ──────────────────────────────────────────────────
function connectUser() {
  if (!token) { setView("login"); return; }
  connect();
}


// ── Release (shared by Disconnect & Sign out) ─────────────────────────────────
// The release call is the ONLY thing that frees the pool slot. It used to
// be fire-and-forget and skipped entirely when the in-memory session was
// lost (page reload) or when signing out — leaving the VM locked for the
// full session duration while everyone else saw "No VMs available".
// Now: posts /provision/disconnect whenever an assignment exists (checked
// via /provision/status if the in-memory state is gone), retries once on
// network failure, and reports the outcome to the caller. Never throws.
function releaseAssignment() {
  var authToken = token;
  var session   = activeSession;
  if (!authToken) return Promise.resolve({ released: false });

  function doPost() {
    return fetch(provApiBase() + "/provision/disconnect", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + authToken,
      },
      body: JSON.stringify({ reason: "user_logout" }),
    }).then(function (resp) {
      if (resp.status === 200) return { released: true };
      return { error: "HTTP " + resp.status };
    });
  }

  function attempt() {
    if (session) return doPost();
    // Session state lost (reload) — check whether an assignment exists.
    return fetch(provApiBase() + "/provision/status", {
      headers: { "Authorization": "Bearer " + authToken },
    }).then(function (resp) {
      if (resp.status === 401) return { error: "auth expired" };
      return resp.json();
    }).then(function (st) {
      if (st && st.has_assignment) return doPost();
      return { released: false };
    });
  }

  return attempt().catch(function () {
    // One retry after a short delay (transient network/server blip).
    return new Promise(function (resolve) { setTimeout(resolve, 4000); })
      .then(attempt)
      .catch(function () { return { error: "network" }; });
  });
}


// ── Disconnect (user-initiated) ───────────────────────────────────────────────
function disconnect() {
  manualDisconnect = true;
  cancelClaimRetry();
  cancelReconnect();
  stopPolling();

  // Release the VM on the provisioning side (non-persistent: it gets
  // deleted and the pool replenishes). Local teardown does not depend on
  // the call, but a failure is surfaced instead of being swallowed.
  releaseAssignment().then(function (out) {
    if (out && out.error) {
      setStatus("Release failed (" + out.error + ") — VM may stay occupied", false);
    }
  });

  if (client) { client.disconnect(); client = null; }
  detachInputHandlers();
  displayEl.innerHTML = "";
  activeSession = null;
  expiresAt = null;
  sessionEl.textContent = "Signed in as " + (tokenUser ? tokenUser.username : "user");
  setView("idle");
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
loginForm.addEventListener("submit", function(e) {
  e.preventDefault();
  loginError.textContent = "";
  login(loginUser.value.trim(), loginPass.value).catch(function() {});
});

signupForm.addEventListener("submit", function(e) {
  e.preventDefault();
  signupError.textContent = "";
  register().catch(function() {});
});

showSignupBtn.addEventListener("click", function() {
  loginError.textContent = "";
  signupError.textContent = "";
  signupForm.reset();
  showAuthPane("signup");
});

showLoginBtn.addEventListener("click", function() {
  loginError.textContent = "";
  signupError.textContent = "";
  showAuthPane("login");
});

connectBtn.addEventListener("click", connectUser);
disconnectBtn.addEventListener("click", disconnect);
logoutBtn.addEventListener("click", logout);

// Window resize — covers drag-resize
window.addEventListener("resize", onViewportResize);

// Fullscreen change — all vendor prefixes for full browser compatibility
document.addEventListener("fullscreenchange",       onViewportResize);
document.addEventListener("webkitfullscreenchange", onViewportResize);
document.addEventListener("mozfullscreenchange",    onViewportResize);
document.addEventListener("MSFullscreenChange",     onViewportResize);


// ── Initial state ─────────────────────────────────────────────────────────────
(function init() {
  // Service URLs from the mirroring service (wins when set — proxy/TLS
  // deployments); falls back to port derivation per function above.
  fetch("/api/config")
    .then(function (r) { return r.json(); })
    .then(function (cfg) {
      if (cfg && cfg.auth)     AUTH_API_BASE = cfg.auth;
      if (cfg && cfg.provision) PROV_API_BASE = cfg.provision;
    })
    .catch(function () { /* same-origin config is optional */ });

  if (token) {
    try {
      tokenUser = JSON.parse(sessionStorage.getItem("vdi_user") || "null");
    } catch (e) {
      tokenUser = null;
    }
    sessionEl.textContent = "Signed in as " + (tokenUser ? tokenUser.username : "user");
    setView("idle");
    setStatus("Ready", false);

    // A previous session may still be active server-side (page reload,
    // tab closed without Disconnect). Surface it so the student can
    // resume it or release it — otherwise the VM silently stays occupied
    // and other students get "No VMs available".
    fetch(provApiBase() + "/provision/status", {
      headers: { "Authorization": "Bearer " + token },
    }).then(function (resp) {
      if (resp.status === 401) { handleAuthExpired(); return null; }
      return resp.json();
    }).then(function (st) {
      if (!st || !st.has_assignment) return;
      activeSession = {
        instance_id: st.instance_id,
        floating_ip: st.floating_ip,
        pool_name:   st.pool_name,
      };
      sessionEl.textContent = "Session active @ " + (st.floating_ip || "…");
      setStatus("Session active — Disconnect to release, or Connect to resume", false);
      disconnectBtn.disabled = false;
    }).catch(function () { /* provisioning unreachable — keep idle state */ });
  } else {
    sessionEl.textContent = "Not signed in";
    setView("login");
    setStatus("Disconnected", false);
  }
})();
