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

// ── Staff pane refs (admin dashboard / teacher dashboard) ─────────────────────
var adminPaneEl     = document.getElementById("admin-pane");
var teacherPaneEl   = document.getElementById("teacher-pane");
var adminUserForm   = document.getElementById("admin-user-form");
var auRole          = document.getElementById("au-role");
var auFullname      = document.getElementById("au-fullname");
var auUsername      = document.getElementById("au-username");
var auEmail         = document.getElementById("au-email");
var auPassword      = document.getElementById("au-password");
var auSid           = document.getElementById("au-sid");
var auDept          = document.getElementById("au-dept");
var auStudentRow    = document.getElementById("au-student-row");
var adminMsg        = document.getElementById("admin-msg");
var adminRefreshBtn = document.getElementById("admin-refresh");
var adminUserList   = document.getElementById("admin-user-list");
var tabUsersBtn     = document.getElementById("tab-users");
var tabPoolsBtn     = document.getElementById("tab-pools");
var consoleUsersEl  = document.getElementById("console-users");
var consolePoolsEl  = document.getElementById("console-pools");
var poolsRefreshBtn = document.getElementById("pools-refresh");
var adminPoolMsg    = document.getElementById("admin-pool-msg");
var adminPoolList   = document.getElementById("admin-pool-list");
var poolCreateForm  = document.getElementById("pool-create-form");
var tpName          = document.getElementById("tp-name");
var tpCount         = document.getElementById("tp-count");
var poolCreateMsg   = document.getElementById("pool-create-msg");
var poolRefreshBtn  = document.getElementById("pool-refresh");
var teacherPoolList = document.getElementById("teacher-pool-list");

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
  // mode: "login" | "idle" | "connected" | "admin" | "teacher"
  // Explicit display values (never "") so inline styles always win over
  // stylesheet/[hidden]-attribute defaults — "" silently leaves elements
  // hidden (this was the blank-screen bug after connecting).
  var showLogin   = (mode === "login");
  var showIdle    = (mode === "idle");
  var showDisp    = (mode === "connected");
  var showAdmin   = (mode === "admin");
  var showTeacher = (mode === "teacher");

  if (showLogin) {
    showAuthPane(authPane);
  } else {
    loginEl.style.display  = "none";
    signupEl.style.display = "none";
  }
  placeholderEl.style.display = showIdle   ? "flex"  : "none";
  displayEl.style.display     = showDisp   ? "block" : "none";
  adminPaneEl.style.display   = showAdmin   ? "flex" : "none";
  teacherPaneEl.style.display = showTeacher ? "flex" : "none";
  logoutBtn.style.display     = showLogin ? "none"  : "inline-flex";

  // Staff roles manage pools — they never open an RDP session themselves.
  connectBtn.disabled    = showLogin || showAdmin || showTeacher;
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

// ── Staff dashboard helpers (admin & teacher panes) ───────────────────────────
// Small string/format helpers shared by the dashboards.
function escHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function fmtRam(mb) {
  mb = parseInt(mb, 10);
  if (!mb) return "—";
  return (mb >= 1024 && mb % 1024 === 0) ? (mb / 1024) + " GB" : mb + " MB";
}

function fmtDate(iso) {
  if (!iso) return "—";
  var d = new Date(iso);
  return isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

function setManageMsg(el, text, ok) {
  el.textContent = text || "";
  el.classList.toggle("manage-msg--ok", !!ok && !!text);
  el.classList.toggle("manage-msg--err", !ok && !!text);
}

// Fetch against a staff endpoint with the Bearer token attached.
function staffFetch(base, path, opts) {
  opts = opts || {};
  opts.headers = Object.assign({}, opts.headers || {});
  opts.headers["Authorization"] = "Bearer " + token;
  return fetch(base + path, opts).then(function (resp) {
    if (resp.status === 401) { handleAuthExpired(); throw new Error("auth-expired"); }
    return resp;
  });
}

// ── Admin console: accounts ───────────────────────────────────────────────────
function roleLabel(r) {
  return { student: "Student", faculty: "Teacher", admin: "Admin", guest: "Guest" }[r] || r;
}

function loadAdminUsers() {
  return staffFetch(authApiBase(), "/auth/admin/users")
    .then(function (resp) {
      if (!resp.ok) return apiError(resp).then(function (m) { throw new Error(m); });
      return resp.json();
    })
    .then(function (data) {
      var users = (data && data.users) || [];
      adminUserList.innerHTML = users.length
        ? users.map(function (u) {
            var isAdmin = (u.role === "admin");
            var delBtn = isAdmin
              ? '<span class="chip chip--muted">protected</span>'
              : '<button type="button" class="btn btn--mini btn--danger" ' +
                'data-action="del-user" data-id="' + u.user_id + '" ' +
                'data-name="' + escHtml(u.username) + '">Delete</button>';
            return '<div class="staff-row">' +
              '<div class="staff-row__main">' +
                '<span class="staff-row__name">' + escHtml(u.full_name || u.username) + '</span>' +
                '<span class="staff-row__sub">@' + escHtml(u.username) + ' · ' + escHtml(u.email) + '</span>' +
              '</div>' +
              '<div class="staff-row__meta">' +
                '<span class="chip chip--role">' + escHtml(roleLabel(u.role)) + '</span>' +
                '<span class="staff-row__meta-line">' +
                  delBtn +
                '</span>' +
              '</div>' +
            '</div>';
          }).join("")
        : '<p class="staff-empty">No accounts yet.</p>';
    })
    .catch(function (err) {
      if (err && err.message === "auth-expired") return;
      adminUserList.innerHTML =
        '<p class="staff-empty">Could not load accounts — auth service unreachable?</p>';
    });
}

function createAdminUser(e) {
  e.preventDefault();
  setManageMsg(adminMsg, "", false);
  var isStudent = (auRole.value === "student");
  var payload = {
    full_name: auFullname.value.trim(),
    username:  auUsername.value.trim(),
    email:     auEmail.value.trim(),
    password:  auPassword.value,
    role:      auRole.value,
  };
  if (isStudent) {
    payload.student_id = auSid.value.trim() || null;
    payload.department = auDept.value.trim() || null;
  }
  if (!payload.full_name || !payload.username || !payload.email) {
    setManageMsg(adminMsg, "All fields are required", false);
    return;
  }
  if (payload.username.length < 4) {
    setManageMsg(adminMsg, "Username must be at least 4 characters", false);
    return;
  }
  if (payload.password.length < 8) {
    setManageMsg(adminMsg, "Password must be at least 8 characters", false);
    return;
  }
  setStatus("Creating account…", false);
  return staffFetch(authApiBase(), "/auth/admin/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then(function (resp) {
    if (!resp.ok) return apiError(resp).then(function (m) { throw new Error(m); });
    return resp.json();
  }).then(function (u) {
    adminUserForm.reset();
    auRole.value = "student";
    auStudentRow.style.display = "flex";
    setManageMsg(adminMsg, (isStudent ? "Student" : "Teacher") + " @" + u.username +
      " created — share the credentials with them.", true);
    setStatus("Ready", false);
    loadAdminUsers();
  }).catch(function (err) {
    if (err && err.message === "auth-expired") return;
    setManageMsg(adminMsg, err.message || "Creation failed", false);
    setStatus("Disconnected", false);
  });
}

function deleteAdminUser(userId, username) {
  var msg = "Delete account “" + username + "”?\n\n" +
    "Their login sessions and assignments are removed immediately. " +
    "If they are using a VM right now, it is returned to its pool " +
    "within ~30 seconds (a walk-in VM is destroyed and refilled).";
  if (!window.confirm(msg)) return;
  setStatus("Deleting account…", false);
  return staffFetch(authApiBase(), "/auth/admin/users/" + encodeURIComponent(userId), {
    method: "DELETE",
  }).then(function (resp) {
    if (!resp.ok) return apiError(resp).then(function (m) { throw new Error(m); });
    return resp.json();
  }).then(function (out) {
    setStatus("Ready", false);
    setManageMsg(adminMsg, "Deleted “" + out.deleted_user + "”" +
      (out.note ? " — " + out.note : "") + ".", true);
    loadAdminUsers();
  }).catch(function (err) {
    if (err && err.message === "auth-expired") return;
    setStatus("Ready", false);
    setManageMsg(adminMsg, err.message || "Delete failed", false);
  });
}

// ── Admin console: pools & VMs ────────────────────────────────────────────────
var expandedPoolId = null;   // pool detail currently expanded (after reloads)

function statusChip(status) {
  var cls = "chip--muted";
  if (status === "ready" || status === "active") cls = "chip--ok";
  else if (status === "in_use" || status === "deleting") cls = "chip--warn";
  else if (status === "error") cls = "chip--err";
  return '<span class="chip ' + cls + '">' + escHtml(status) + '</span>';
}

function poolModeLabel(p) {
  var parts = [];
  parts.push(p.desktop_type === "persistent" ? "reserved" : "walk-in");
  parts.push(p.access_mode === "code" ? "code-gated" : "open");
  return parts.join(" · ");
}

function loadAdminPools() {
  setManageMsg(adminPoolMsg, "", false);
  return staffFetch(provApiBase(), "/admin/pools")
    .then(function (resp) {
      if (!resp.ok) return apiError(resp).then(function (m) { throw new Error(m); });
      return resp.json();
    })
    .then(function (data) {
      var pools = (data && data.pools) || [];
      adminPoolList.innerHTML = pools.length
        ? pools.map(function (p) {
            var isOpen = (expandedPoolId === p.pool_id);
            return '<div class="pool-card" data-pool="' + p.pool_id + '">' +
              '<div class="staff-row">' +
                '<div class="staff-row__main">' +
                  '<span class="staff-row__name">' + escHtml(p.name) + '</span>' +
                  '<span class="staff-row__sub">' + p.current_count + "/" + p.max_vms +
                    " VMs · min " + p.min_vms + " · session " + p.max_session_minutes +
                    " min · " + escHtml(poolModeLabel(p)) + '</span>' +
                '</div>' +
                '<div class="staff-row__meta">' +
                  statusChip(p.status) +
                  '<span class="staff-row__meta-line">' +
                    '<button type="button" class="btn btn--mini" data-action="toggle-detail" data-id="' + p.pool_id + '">' +
                      (isOpen ? "Close" : "Manage") + '</button>' +
                    '<button type="button" class="btn btn--mini btn--danger" data-action="del-pool" data-id="' + p.pool_id + '" data-name="' + escHtml(p.name) + '">Delete pool</button>' +
                  '</span>' +
                '</div>' +
              '</div>' +
              (isOpen ? '<div class="pool-detail" data-detail="' + p.pool_id + '"></div>' : '') +
            '</div>';
          }).join("")
        : '<p class="staff-empty">No pools.</p>';
      if (expandedPoolId) {
        var host = adminPoolList.querySelector('[data-pool="' + expandedPoolId + '"] .pool-detail');
        if (host) loadPoolDetail(expandedPoolId, host);
      }
    })
    .catch(function (err) {
      if (err && err.message === "auth-expired") return;
      adminPoolList.innerHTML =
        '<p class="staff-empty">Could not load pools — provisioning service unreachable?</p>';
    });
}

function loadPoolDetail(poolId, hostEl) {
  hostEl.innerHTML = '<p class="staff-empty">Loading…</p>';
  return staffFetch(provApiBase(), "/admin/pools/" + encodeURIComponent(poolId) + "/detail")
    .then(function (resp) {
      if (!resp.ok) return apiError(resp).then(function (m) { throw new Error(m); });
      return resp.json();
    })
    .then(function (d) { renderPoolDetail(d, hostEl); })
    .catch(function (err) {
      if (err && err.message === "auth-expired") return;
      hostEl.innerHTML = '<p class="staff-empty">Could not load pool detail.</p>';
    });
}

function renderPoolDetail(d, hostEl) {
  var p = d.pool;
  var instances = d.instances || [];
  var jobs = (d.job_summary || []).map(function (j) {
    return j.count + " " + j.job_type.replace("_vm", "") + " " + j.status;
  }).join(" · ");
  hostEl.innerHTML =
    '<div class="pool-detail__grid">' +
      '<div class="pool-detail__col">' +
        '<div class="pool-detail__head">' +
          '<span class="pool-detail__title">Settings</span>' +
          '<button type="button" class="btn btn--mini btn--connect" data-action="save-settings" data-id="' + p.pool_id + '">Save changes</button>' +
        '</div>' +
        '<div class="settings-grid">' +
          '<label class="settings-field"><span>Name</span>' +
            '<input data-field="name" type="text" value="' + escHtml(p.name) + '" /></label>' +
          '<label class="settings-field"><span>Status</span>' +
            '<select data-field="status">' +
              '<option value="active"' + (p.status === "active" ? " selected" : "") + '>active</option>' +
              '<option value="inactive"' + (p.status === "inactive" ? " selected" : "") + '>inactive</option>' +
            '</select></label>' +
          '<label class="settings-field"><span>Min VMs</span>' +
            '<input data-field="min_vms" type="number" min="0" max="500" value="' + p.min_vms + '" /></label>' +
          '<label class="settings-field"><span>Max VMs</span>' +
            '<input data-field="max_vms" type="number" min="1" max="500" value="' + p.max_vms + '" /></label>' +
          '<label class="settings-field"><span>Session limit (min)</span>' +
            '<input data-field="max_session_minutes" type="number" min="5" max="1440" value="' + p.max_session_minutes + '" /></label>' +
          '<label class="settings-field settings-field--check"><span>Auto-scaling</span>' +
            '<input data-field="auto_scaling_enabled" type="checkbox"' + (p.auto_scaling_enabled ? " checked" : "") + ' /></label>' +
        '</div>' +
        '<p class="pool-detail__fixed">desktop ' + escHtml(p.desktop_type) + ' · access ' + escHtml(p.access_mode) +
          ' · roles ' + escHtml((p.allowed_roles || []).join(", ")) + '</p>' +
        '<p class="manage-msg" data-field-msg></p>' +
      '</div>' +
      '<div class="pool-detail__col">' +
        '<div class="pool-detail__head"><span class="pool-detail__title">VMs (' + instances.length + ")</span></div>" +
        (instances.length
          ? instances.map(function (v) {
              var actions = '';
              if (v.status !== "deleting") {
                if (v.assigned_username) {
                  actions += '<button type="button" class="btn btn--mini" data-action="vm-release" data-vm="' + v.instance_id + '" data-name="' + escHtml(v.pool_name) + '">End session</button>';
                }
                actions += '<button type="button" class="btn btn--mini btn--danger" data-action="vm-delete" data-vm="' + v.instance_id + '">Destroy VM</button>';
              } else {
                actions = '<span class="chip chip--warn">deleting…</span>';
              }
              var who = v.assigned_username ? "@" + escHtml(v.assigned_username) : '<span class="staff-empty--inline">free</span>';
              return '<div class="vm-row">' +
                '<div class="vm-row__main">' +
                  '<span class="vm-row__name">VM ' + v.instance_id.slice(0, 8) + statusChip(v.status) + '</span>' +
                  '<span class="vm-row__sub">' + escHtml(v.pool_name) + ' · ' +
                    escHtml(v.floating_ip || "no IP") + ' · assigned to ' + who + '</span>' +
                '</div>' +
                '<div class="vm-row__actions">' + actions + '</div>' +
              '</div>';
            }).join("")
          : '<p class="staff-empty">No VMs in this pool.</p>') +
        (jobs ? '<p class="pool-detail__fixed">jobs · ' + escHtml(jobs) + '</p>' : '') +
      '</div>' +
    '</div>';
}

function savePoolSettings(poolId, hostEl) {
  var fields = {};
  hostEl.querySelectorAll("[data-field]").forEach(function (el) {
    var key = el.getAttribute("data-field");
    if (key === "auto_scaling_enabled") fields[key] = el.checked;
    else if (key === "min_vms" || key === "max_vms" || key === "max_session_minutes") {
      var n = parseInt(el.value, 10);
      fields[key] = isNaN(n) ? null : n;
    } else fields[key] = el.value.trim();
  });
  var msgEl = hostEl.querySelector("[data-field-msg]");
  setManageMsg(msgEl, "", false);
  return staffFetch(provApiBase(), "/admin/pools/" + encodeURIComponent(poolId), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fields),
  }).then(function (resp) {
    if (!resp.ok) return apiError(resp).then(function (m) { throw new Error(m); });
    return resp.json();
  }).then(function (updated) {
    setManageMsg(msgEl, "Settings saved for “" + updated.name + "”.", true);
    loadAdminPools();  // re-render with fresh counts (keeps detail open)
  }).catch(function (err) {
    if (err && err.message === "auth-expired") return;
    setManageMsg(msgEl, err.message || "Save failed", false);
  });
}

function deleteAdminPool(poolId, name) {
  var msg = "Delete pool “" + name + "”?\n\n" +
    "Cascading delete:\n" +
    "• every active student session ends\n" +
    "• queued VM creations are cancelled\n" +
    "• every VM is destroyed in OpenStack (servers + IPs)\n\n" +
    "This cannot be undone.";
  if (!window.confirm(msg)) return;
  setStatus("Deleting pool…", false);
  return staffFetch(provApiBase(), "/admin/pools/" + encodeURIComponent(poolId), {
    method: "DELETE",
  }).then(function (resp) {
    if (resp.status === 204) return { ok: true };
    return apiError(resp).then(function (m) { throw new Error(m); });
  }).then(function () {
    setStatus("Ready", false);
    setManageMsg(adminPoolMsg, "Pool “" + name + "” deleted — VMs are being destroyed.", true);
    expandedPoolId = null;
    loadAdminPools();
  }).catch(function (err) {
    if (err && err.message === "auth-expired") return;
    setStatus("Ready", false);
    setManageMsg(adminPoolMsg, err.message || "Delete failed", false);
  });
}

function vmAction(action, vmId, extra) {
  var verb = (action === "vm-release")
    ? 'End the session of this VM' + (extra ? " in “" + extra + "”" : "") + "?"
    : "Destroy this VM in OpenStack (server + IP)? The student's session ends. This cannot be undone.";
  if (!window.confirm(verb)) return;
  setStatus(action === "vm-release" ? "Releasing VM…" : "Destroying VM…", false);
  return staffFetch(provApiBase(), "/admin/vms/" + encodeURIComponent(vmId) +
    (action === "vm-release" ? "/release" : "/delete"), {
    method: "POST",
  }).then(function (resp) {
    if (!resp.ok) return apiError(resp).then(function (m) { throw new Error(m); });
    return resp.json();
  }).then(function (out) {
    setStatus("Ready", false);
    var note = out.destroyed
      ? " — walk-in VM will be destroyed and the pool refills."
      : (out.assignment_closed ? " — session ended, VM returned to pool." : "");
    setManageMsg(adminPoolMsg, "Done" + note, true);
    loadAdminPools();
  }).catch(function (err) {
    if (err && err.message === "auth-expired") return;
    setStatus("Ready", false);
    setManageMsg(adminPoolMsg, err.message || "Action failed", false);
  });
}

function onAdminUserListClick(e) {
  var btn = e.target.closest("[data-action]");
  if (!btn || btn.getAttribute("data-action") !== "del-user") return;
  deleteAdminUser(btn.getAttribute("data-id"), btn.getAttribute("data-name"));
}

function onAdminPoolClick(e) {
  var btn = e.target.closest("[data-action]");
  if (!btn) return;
  var action = btn.getAttribute("data-action");
  var id = btn.getAttribute("data-id");
  if (action === "toggle-detail") {
    expandedPoolId = (expandedPoolId === id) ? null : id;
    loadAdminPools();
  } else if (action === "del-pool") {
    deleteAdminPool(id, btn.getAttribute("data-name"));
  } else if (action === "save-settings") {
    var hostEl = btn.closest(".pool-detail");
    if (hostEl) savePoolSettings(id, hostEl);
  } else if (action === "vm-release" || action === "vm-delete") {
    vmAction(action, btn.getAttribute("data-vm"), btn.getAttribute("data-name"));
  }
}

function switchAdminTab(tab) {
  var poolsOn = (tab === "pools");
  tabUsersBtn.classList.toggle("tab--on", !poolsOn);
  tabPoolsBtn.classList.toggle("tab--on", poolsOn);
  consoleUsersEl.style.display = poolsOn ? "none" : "block";
  consolePoolsEl.style.display = poolsOn ? "block" : "none";
  if (poolsOn) loadAdminPools();
}

function loadAdminConsole() {
  loadAdminUsers();
  if (consolePoolsEl.style.display !== "none") loadAdminPools();
}

// ── Teacher pane: class pools ─────────────────────────────────────────────────
function loadPoolTemplate() {
  return staffFetch(provApiBase(), "/teacher/pool-template")
    .then(function (resp) {
      if (!resp.ok) return apiError(resp).then(function (m) { throw new Error(m); });
      return resp.json();
    })
    .then(function (t) {
      document.getElementById("tp-spec-vcpus").textContent   = t.vcpus + " vCPU" + (t.vcpus === 1 ? "" : "s");
      document.getElementById("tp-spec-ram").textContent     = fmtRam(t.ram_mb);
      document.getElementById("tp-spec-disk").textContent    = t.disk_gb + " GB";
      document.getElementById("tp-spec-session").textContent = t.max_session_minutes + " min per session";
    })
    .catch(function (err) {
      if (err && err.message === "auth-expired") return;
      // Spec stays "…" — the create form will surface the real error.
    });
}

var expandedTeacherPoolId = null;   // pool whose codes panel is open

function loadTeacherPools() {
  return staffFetch(provApiBase(), "/teacher/pools")
    .then(function (resp) {
      if (!resp.ok) return apiError(resp).then(function (m) { throw new Error(m); });
      return resp.json();
    })
    .then(function (data) {
      var pools = (data && data.pools) || [];
      teacherPoolList.innerHTML = pools.length
        ? pools.map(function (p) {
            var counts = [
              p.ready_count + " ready",
              p.in_use_count + " in use",
              p.provisioning_count + " provisioning",
            ].join(" · ");
            var stateChip = (p.status === "active")
              ? '<span class="chip chip--ok">' + escHtml(p.status) + '</span>'
              : '<span class="chip chip--muted">' + escHtml(p.status) + '</span>';
            var isOpen = (expandedTeacherPoolId === p.pool_id);
            return '<div class="pool-card" data-tpool="' + p.pool_id + '">' +
              '<div class="staff-row">' +
                '<div class="staff-row__main">' +
                  '<span class="staff-row__name">' + escHtml(p.name) + '</span>' +
                  '<span class="staff-row__sub">' + counts +
                    ' · total ' + p.total_instances + "/" + p.max_vms + " VMs · created " +
                    escHtml(fmtDate(p.created_at)) + '</span>' +
                '</div>' +
                '<div class="staff-row__meta">' + stateChip +
                  '<span class="staff-row__meta-line">' +
                    '<button type="button" class="btn btn--mini" data-action="tg-codes" data-id="' + p.pool_id + '">' +
                      (isOpen ? "Close" : "Access codes") + '</button>' +
                  '</span>' +
                '</div>' +
              '</div>' +
              (isOpen ? '<div class="pool-detail" data-tdetail="' + p.pool_id + '"></div>' : '') +
            '</div>';
          }).join("")
        : '<p class="staff-empty">No class pools yet. Create one on the left — ' +
          'provisioning takes a few minutes per VM.</p>';
      if (expandedTeacherPoolId) {
        var host = teacherPoolList.querySelector(
          '[data-tpool="' + expandedTeacherPoolId + '"] [data-tdetail]');
        if (host) loadTeacherCodes(expandedTeacherPoolId, host);
      }
    })
    .catch(function (err) {
      if (err && err.message === "auth-expired") return;
      teacherPoolList.innerHTML =
        '<p class="staff-empty">Could not load pools — provisioning service unreachable?</p>';
    });
}

// ── Teacher codes panel (Part 2) ──────────────────────────────────────────────
function toggleTeacherCodes(poolId) {
  expandedTeacherPoolId = (expandedTeacherPoolId === poolId) ? null : poolId;
  loadTeacherPools();
}

function codeStateChip(c) {
  if (c.revoked_at) return '<span class="chip chip--muted">revoked</span>';
  if (c.redeemed_at) {
    return '<span class="chip chip--role">redeemed by @' + escHtml(c.redeemed_by || "?") + '</span>';
  }
  return '<span class="chip chip--ok">available</span>';
}

function loadTeacherCodes(poolId, hostEl) {
  hostEl.innerHTML = '<p class="staff-empty">Loading codes…</p>';
  return staffFetch(provApiBase(), "/teacher/pools/" + encodeURIComponent(poolId) + "/codes")
    .then(function (resp) {
      if (!resp.ok) return apiError(resp).then(function (m) { throw new Error(m); });
      return resp.json();
    })
    .then(function (d) {
      var open = d.codes.filter(function (c) { return !c.revoked_at; });
      var head =
        '<div class="pool-detail__head">' +
          '<span class="pool-detail__title">Access codes · ' + d.active_codes + "/" +
            d.capacity + " active (" + d.redeemed_codes + " redeemed)</span>" +
          '<span class="staff-row__meta-line">' +
            '<button type="button" class="btn btn--mini" data-action="tg-gen" data-id="' + d.pool_id + '">' +
              (d.active_codes < d.capacity ? "Generate missing codes" : "Codes complete") + '</button>' +
            '<button type="button" class="btn btn--mini btn--connect" data-action="tg-download" data-id="' + d.pool_id + '" data-name="' + escHtml(d.pool_name) + '"' +
              (open.length ? '' : ' disabled') + '>Download codes.json</button>' +
          '</span>' +
        '</div>' +
        '<p class="manage-msg" data-codes-msg></p>' +
        (d.codes.length
          ? '<div class="code-list">' + d.codes.map(function (c) {
              var meta = c.redeemed_at ? ' · ' + escHtml(fmtDate(c.redeemed_at)) : '';
              return '<div class="code-row">' +
                '<span class="code-row__code">' + escHtml(c.code) + '</span>' +
                '<span class="code-row__meta">' +
                  codeStateChip(c) +
                  '<span class="code-row__date">created ' + escHtml(fmtDate(c.created_at)) + meta + '</span>' +
                '</span>' +
              '</div>';
            }).join("") + '</div>'
          : '<p class="staff-empty">No codes yet — press “Generate missing codes” to create one per VM.</p>');
      hostEl.innerHTML = head;
    })
    .catch(function (err) {
      if (err && err.message === "auth-expired") return;
      hostEl.innerHTML = '<p class="staff-empty">Could not load codes — ' +
        escHtml(err.message || "unexpected error") + '</p>';
    });
}

function generateTeacherCodes(poolId, hostEl) {
  var msgEl = hostEl.querySelector("[data-codes-msg]");
  setManageMsg(msgEl, "", false);
  return staffFetch(provApiBase(), "/teacher/pools/" + encodeURIComponent(poolId) + "/codes", {
    method: "POST",
  }).then(function (resp) {
    if (!resp.ok) return apiError(resp).then(function (m) { throw new Error(m); });
    return resp.json();
  }).then(function (out) {
    setManageMsg(msgEl, "Generated " + out.generated + " new code(s) — " +
      out.active_codes + "/" + out.capacity + " active now.", true);
    loadTeacherCodes(poolId, hostEl);
  }).catch(function (err) {
    if (err && err.message === "auth-expired") return;
    setManageMsg(msgEl, err.message || "Generation failed", false);
  });
}

function downloadTeacherCodes(data) {
  var clean = {
    pool: {
      name: data.pool_name,
      capacity_vms: data.capacity,
      active_codes: data.active_codes,
    },
    generated_at: new Date().toISOString(),
    instructions: "One code per VM seat. Give each student their own code — " +
      "they enter it to connect to their VM.",
    codes: data.codes.filter(function (c) { return !c.revoked_at; })
                     .map(function (c) { return c.code; }),
  };
  var safeName = String(data.pool_name).replace(/[^A-Za-z0-9_-]+/g, "-");
  var blob = new Blob([JSON.stringify(clean, null, 2)], { type: "application/json" });
  var url = URL.createObjectURL(blob);
  var a = document.createElement("a");
  a.href = url;
  a.download = "codes-" + safeName + ".json";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
}

function onTeacherPoolClick(e) {
  var btn = e.target.closest("[data-action]");
  if (!btn) return;
  var action = btn.getAttribute("data-action");
  var id = btn.getAttribute("data-id");
  if (action === "tg-codes") {
    toggleTeacherCodes(id);
  } else if (action === "tg-gen") {
    var hostEl = btn.closest(".pool-detail");
    if (hostEl) generateTeacherCodes(id, hostEl);
  } else if (action === "tg-download") {
    // Re-fetch fresh data so the file never includes stale codes.
    staffFetch(provApiBase(), "/teacher/pools/" + encodeURIComponent(id) + "/codes")
      .then(function (resp) {
        if (!resp.ok) return apiError(resp).then(function (m) { throw new Error(m); });
        return resp.json();
      })
      .then(downloadTeacherCodes)
      .catch(function () {});
  }
}

function loadTeacherDashboard() {
  loadPoolTemplate();
  loadTeacherPools();
}

function createPool(e) {
  e.preventDefault();
  setManageMsg(poolCreateMsg, "", false);
  var name  = tpName.value.trim();
  var count = parseInt(tpCount.value, 10);
  if (name.length < 3) {
    setManageMsg(poolCreateMsg, "Pool name must be at least 3 characters", false);
    return;
  }
  if (!count || count < 1 || count > 40) {
    setManageMsg(poolCreateMsg, "VM count must be between 1 and 40", false);
    return;
  }
  setStatus("Provisioning " + count + " VM(s)…", false);
  return staffFetch(provApiBase(), "/teacher/pools", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name, vm_count: count }),
  }).then(function (resp) {
    if (!resp.ok) return apiError(resp).then(function (m) { throw new Error(m); });
    return resp.json();
  }).then(function (pool) {
    poolCreateForm.reset();
    setManageMsg(poolCreateMsg, "Pool “" + pool.name + "” created — " +
      pool.max_vms + " VM(s) are provisioning now and will appear below as they become ready (a few minutes each).", true);
    setStatus("Ready", false);
    loadTeacherPools();
  }).catch(function (err) {
    if (err && err.message === "auth-expired") return;
    setManageMsg(poolCreateMsg, err.message || "Pool creation failed", false);
    setStatus("Disconnected", false);
  });
}

// Send the signed-in user to the workspace their role owns.
function enterWorkspace() {
  var role = tokenUser ? tokenUser.role : "";
  if (role === "admin") {
    setView("admin");
    loadAdminConsole();
  } else if (role === "faculty") {
    setView("teacher");
    loadTeacherDashboard();
  } else {
    setView("idle");
  }
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
    enterWorkspace();
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

// Staff dashboards
adminUserForm.addEventListener("submit", createAdminUser);
auRole.addEventListener("change", function() {
  auStudentRow.style.display = (auRole.value === "student") ? "flex" : "none";
});
adminUserList.addEventListener("click", onAdminUserListClick);
adminRefreshBtn.addEventListener("click", function() { loadAdminUsers(); });
tabUsersBtn.addEventListener("click", function() { switchAdminTab("users"); });
tabPoolsBtn.addEventListener("click", function() { switchAdminTab("pools"); });
poolsRefreshBtn.addEventListener("click", function() { loadAdminPools(); });
adminPoolList.addEventListener("click", onAdminPoolClick);
poolCreateForm.addEventListener("submit", createPool);
teacherPoolList.addEventListener("click", onTeacherPoolClick);
poolRefreshBtn.addEventListener("click", function() {
  loadTeacherPools();
  loadPoolTemplate();
});

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

    var role = tokenUser ? tokenUser.role : "";
    if (role === "admin") {
      // Admin dashboard — account management, no RDP sessions.
      setView("admin");
      setStatus("Ready", false);
      loadAdminConsole();
      return;
    }
    if (role === "faculty") {
      // Teacher dashboard — class pool management, no RDP sessions.
      setView("teacher");
      setStatus("Ready", false);
      loadTeacherDashboard();
      return;
    }

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
