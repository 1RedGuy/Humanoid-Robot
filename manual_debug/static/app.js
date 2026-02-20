/* ── state ── */
let servoData = {};   // { groups: { "Eyes": [...], ... }, calibrate_angle }
let positions  = {};  // name -> current angle
let connected  = false;

/* ── helpers ── */
async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch (_) {
    throw new Error(res.ok ? "Invalid response" : (res.statusText || "Server error"));
  }
  if (!res.ok) throw new Error(data.detail || res.statusText || "Request failed");
  return data;
}

function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

/* ── connection ── */
const connIndicator = document.getElementById("conn-indicator");
const connLabel     = document.getElementById("conn-label");
const btnConnect    = document.getElementById("btn-connect");
const btnCalibrate  = document.getElementById("btn-calibrate");
const btnStop       = document.getElementById("btn-stop");
const btnReload     = document.getElementById("btn-reload");

const btnDisconnect = document.getElementById("btn-disconnect");

function setConnected(state, port) {
  connected = state;
  connIndicator.className = "indicator " + (state ? "connected" : "disconnected");
  connLabel.textContent   = state ? `Connected (${port})` : "Disconnected";
  btnConnect.textContent  = "Connect";
  btnConnect.disabled     = state;
  btnDisconnect.disabled  = !state;
  btnCalibrate.disabled   = !state;
  btnStop.disabled        = !state;
}

btnConnect.addEventListener("click", async () => {
  btnConnect.disabled = true;
  btnConnect.textContent = "Connecting…";
  try {
    const data = await api("POST", "/api/connect");
    setConnected(true, data.port);
    const fresh = await api("GET", "/api/servos");
    renderServos(fresh);
  } catch (e) {
    alert("Connection failed: " + e.message);
    setConnected(false, null);
  }
});

btnDisconnect.addEventListener("click", async () => {
  btnDisconnect.disabled = true;
  try {
    await api("POST", "/api/disconnect");
    setConnected(false, null);
    const fresh = await api("GET", "/api/servos");
    renderServos(fresh);
  } catch (e) {
    alert("Disconnect failed: " + e.message);
  }
});

/* ── servo sliders ── */
const servoGroupsEl = document.getElementById("servo-groups");
const loadMessageEl = document.getElementById("load-message");
const serverErrorEl = document.getElementById("server-error");

function showServerError(msg) {
  if (serverErrorEl) {
    serverErrorEl.textContent = msg;
    serverErrorEl.style.display = "block";
  }
  if (loadMessageEl) loadMessageEl.style.display = "none";
}

function hideServerError() {
  if (serverErrorEl) serverErrorEl.style.display = "none";
}

function sliderRange(servo) {
  const lo = Math.min(servo.min_angle, servo.max_angle);
  const hi = Math.max(servo.min_angle, servo.max_angle);
  return { lo, hi };
}

function renderServos(data) {
  if (!servoGroupsEl) return;
  servoData = data;
  hideServerError();
  if (loadMessageEl) loadMessageEl.style.display = "none";
  servoGroupsEl.innerHTML = "";
  const groups = data.groups || {};
  for (const [groupName, servos] of Object.entries(groups)) {
    const groupEl = document.createElement("div");
    groupEl.className = "servo-group";
    const h3 = document.createElement("h3");
    h3.textContent = groupName;
    groupEl.appendChild(h3);

    for (const s of servos) {
      const { lo, hi } = sliderRange(s);
      const cur = s.current_angle ?? data.calibrate_angle ?? 90;
      positions[s.name] = cur;

      const row = document.createElement("div");
      row.className = "slider-row";

      const label = document.createElement("label");
      label.textContent = s.name;

      const slider = document.createElement("input");
      slider.type  = "range";
      slider.min   = lo;
      slider.max   = hi;
      slider.value = Math.max(lo, Math.min(hi, cur));
      slider.dataset.name = s.name;

      const val = document.createElement("span");
      val.className   = "angle-val";
      val.textContent = Math.round(slider.value) + "°";

      const sendMove = debounce((name, angle) => {
        if (!connected) return;
        api("POST", `/api/servo/${encodeURIComponent(name)}/move`, { angle, duration: 0.15 })
          .catch(err => console.error("move error:", err));
      }, 40);

      slider.addEventListener("input", () => {
        const angle = Number(slider.value);
        val.textContent = Math.round(angle) + "°";
        positions[s.name] = angle;
        sendMove(s.name, angle);
      });

      row.appendChild(label);
      row.appendChild(slider);
      row.appendChild(val);
      groupEl.appendChild(row);
    }
    servoGroupsEl.appendChild(groupEl);
  }
}

/* ── controls ── */
btnCalibrate.addEventListener("click", async () => {
  try {
    const data = await api("POST", "/api/calibrate");
    const fresh = await api("GET", "/api/servos");
    renderServos(fresh);
  } catch (e) { alert("Calibrate failed: " + e.message); }
});

btnStop.addEventListener("click", async () => {
  try { await api("POST", "/api/stop"); } catch (e) { alert("Stop failed: " + e.message); }
});

btnReload.addEventListener("click", async () => {
  try {
    const data = await api("POST", "/api/config/reload");
    renderServos(data);
    await renderExpressions();
  } catch (e) { alert("Reload failed: " + e.message); }
});

/* ── expressions ── */
const exprListEl = document.getElementById("expr-list");
const exprNameEl = document.getElementById("expr-name");
const btnSaveExpr = document.getElementById("btn-save-expr");

async function renderExpressions() {
  try {
    const data = await api("GET", "/api/expressions");
    const exprs = data.expressions || {};
    exprListEl.innerHTML = "";
    for (const [name, angles] of Object.entries(exprs)) {
      const item = document.createElement("div");
      item.className = "expr-item";

      const nameSpan = document.createElement("span");
      nameSpan.className = "expr-name";
      nameSpan.textContent = name;

      const actions = document.createElement("div");
      actions.className = "expr-actions";

      const applyBtn = document.createElement("button");
      applyBtn.textContent = "Apply";
      applyBtn.className   = "primary";
      applyBtn.addEventListener("click", async () => {
        try {
          const result = await api("POST", `/api/expressions/${encodeURIComponent(name)}/apply`);
          const fresh = await api("GET", "/api/servos");
          renderServos(fresh);
        } catch (e) { alert("Apply failed: " + e.message); }
      });

      const delBtn = document.createElement("button");
      delBtn.textContent = "Delete";
      delBtn.className   = "danger";
      delBtn.addEventListener("click", async () => {
        if (!confirm(`Delete expression "${name}"?`)) return;
        try {
          await api("DELETE", `/api/expressions/${encodeURIComponent(name)}`);
          await renderExpressions();
        } catch (e) { alert("Delete failed: " + e.message); }
      });

      actions.appendChild(applyBtn);
      actions.appendChild(delBtn);
      item.appendChild(nameSpan);
      item.appendChild(actions);
      exprListEl.appendChild(item);
    }
  } catch (e) { console.error("expressions load error:", e); }
}

btnSaveExpr.addEventListener("click", async () => {
  const name = exprNameEl.value.trim();
  if (!name) { alert("Enter an expression name"); return; }
  try {
    await api("POST", `/api/expressions/${encodeURIComponent(name)}`, { angles: { ...positions } });
    exprNameEl.value = "";
    await renderExpressions();
  } catch (e) { alert("Save failed: " + e.message); }
});

/* ── init ── */
(function init() {
  if (!servoGroupsEl) return;

  (async () => {
    try {
      const st = await api("GET", "/api/status");
      if (st.connected && st.port) setConnected(true, st.port);
    } catch (_) {}

    try {
      const data = await api("GET", "/api/servos");
      renderServos(data);
    } catch (e) {
      console.error("init servos error:", e);
      showServerError(
        "Could not load servos. Open this app at http://localhost:8000/ (not as a file). " +
        "Start the server with: python -m manual_debug"
      );
      if (loadMessageEl) loadMessageEl.style.display = "none";
      renderServos({ groups: {}, calibrate_angle: 90 });
    }

    try {
      await renderExpressions();
    } catch (_) {}
  })();
})();
