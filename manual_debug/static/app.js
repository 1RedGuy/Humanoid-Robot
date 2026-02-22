/* ── state ── */
let servoData = {}; // { groups: { "Eyes": [...], ... }, calibrate_angle }
let positions = {}; // name -> current angle
let connected = false;

/* ── helpers ── */
async function api(method, path, body, options = {}) {
  const opts = { method, headers: options.headers || {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  if (options.timeoutMs != null)
    opts.signal = AbortSignal.timeout(options.timeoutMs);
  const res = await fetch(path, opts);
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch (_) {
    throw new Error(
      res.ok ? "Invalid response" : res.statusText || "Server error",
    );
  }
  if (!res.ok)
    throw new Error(data.detail || res.statusText || "Request failed");
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
const connLabel = document.getElementById("conn-label");
const btnConnect = document.getElementById("btn-connect");
const btnCalibrate = document.getElementById("btn-calibrate");
const btnStop = document.getElementById("btn-stop");
const btnReload = document.getElementById("btn-reload");
const btnIdleOn = document.getElementById("btn-idle-on");
const btnIdleOff = document.getElementById("btn-idle-off");
const idleStatusEl = document.getElementById("idle-status");

const btnDisconnect = document.getElementById("btn-disconnect");
const btnEyesCenter = document.getElementById("btn-eyes-center");
const btnEyesOpen = document.getElementById("btn-eyes-open");
const btnEyesClose = document.getElementById("btn-eyes-close");
const btnRandomLook = document.getElementById("btn-random-look");
const btnBlinkLeft = document.getElementById("btn-blink-left");
const btnBlinkRight = document.getElementById("btn-blink-right");
const btnBlinkBoth = document.getElementById("btn-blink-both");
const btnLipSyncPlay = document.getElementById("btn-lip-sync-play");
const lipSyncTextEl = document.getElementById("lip-sync-text");
const lipSyncStatusEl = document.getElementById("lip-sync-status");

function setConnected(state, port) {
  connected = state;
  connIndicator.className =
    "indicator " + (state ? "connected" : "disconnected");
  connLabel.textContent = state ? `Connected (${port})` : "Disconnected";
  btnConnect.textContent = "Connect";
  btnConnect.disabled = state;
  btnDisconnect.disabled = !state;
  btnStop.disabled = false;
  if (btnLipSyncPlay) btnLipSyncPlay.disabled = !state || idleRunning;
  updateIdleUI(idleRunning);
}

let idleRunning = false;

function updateIdleUI(running) {
  idleRunning = running;
  if (idleStatusEl) idleStatusEl.textContent = running ? "On" : "Off";
  if (btnIdleOn) btnIdleOn.disabled = running;
  if (btnIdleOff) btnIdleOff.disabled = !running;
  // While Idle is on, lock servos and expressions
  const locked = running;
  if (btnCalibrate) btnCalibrate.disabled = !connected || locked;
  if (btnEyesCenter) btnEyesCenter.disabled = !connected || locked;
  if (btnEyesOpen) btnEyesOpen.disabled = !connected || locked;
  if (btnEyesClose) btnEyesClose.disabled = !connected || locked;
  if (btnRandomLook) btnRandomLook.disabled = !connected || locked;
  if (btnBlinkLeft) btnBlinkLeft.disabled = !connected || locked;
  if (btnBlinkRight) btnBlinkRight.disabled = !connected || locked;
  if (btnBlinkBoth) btnBlinkBoth.disabled = !connected || locked;
  if (btnLipSyncPlay) btnLipSyncPlay.disabled = !connected || locked;
  servoGroupsEl?.querySelectorAll("input[type=range]").forEach((el) => {
    el.disabled = locked;
  });
  exprListEl?.querySelectorAll(".expr-actions button.primary").forEach((el) => {
    el.disabled = locked;
  });
}

if (btnIdleOn) {
  btnIdleOn.addEventListener("click", async () => {
    try {
      const data = await api("POST", "/api/idle-running", { idle_running: true });
      updateIdleUI(data.idle_running);
    } catch (e) {
      alert("Failed to start Idle: " + e.message);
    }
  });
}
if (btnIdleOff) {
  btnIdleOff.addEventListener("click", async () => {
    try {
      const data = await api("POST", "/api/idle-running", { idle_running: false });
      updateIdleUI(data.idle_running);
    } catch (e) {
      alert("Failed to stop Idle: " + e.message);
    }
  });
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

/* ── eyes actions ── */
async function eyesAction(endpoint, btn) {
  if (!connected) return;
  if (btn) btn.disabled = true;
  try {
    await api("POST", endpoint);
    const fresh = await api("GET", "/api/servos");
    renderServos(fresh);
  } catch (e) {
    alert("Eyes action failed: " + e.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

if (btnEyesCenter) {
  btnEyesCenter.addEventListener("click", () => eyesAction("/api/eyes/center", btnEyesCenter));
}
if (btnEyesOpen) {
  btnEyesOpen.addEventListener("click", () => eyesAction("/api/eyes/open", btnEyesOpen));
}
if (btnEyesClose) {
  btnEyesClose.addEventListener("click", () => eyesAction("/api/eyes/close", btnEyesClose));
}
if (btnRandomLook) {
  btnRandomLook.addEventListener("click", () => {
    if (!connected) return;
    btnRandomLook.disabled = true;
    btnRandomLook.textContent = "Looking…";
    api("POST", "/api/eyes/random-look")
      .then((data) => api("GET", "/api/servos").then(renderServos))
      .catch((e) => alert("Random look failed: " + e.message))
      .finally(() => {
        btnRandomLook.disabled = false;
        btnRandomLook.textContent = "Random look (then back)";
      });
  });
}
if (btnBlinkLeft) {
  btnBlinkLeft.addEventListener("click", () => eyesAction("/api/eyes/blink-left", btnBlinkLeft));
}
if (btnBlinkRight) {
  btnBlinkRight.addEventListener("click", () => eyesAction("/api/eyes/blink-right", btnBlinkRight));
}
if (btnBlinkBoth) {
  btnBlinkBoth.addEventListener("click", () => eyesAction("/api/eyes/blink-both", btnBlinkBoth));
}

/* ── lip sync test ── */
if (btnLipSyncPlay) {
  btnLipSyncPlay.addEventListener("click", async () => {
    const text = lipSyncTextEl?.value?.trim() || "";
    if (!text) {
      alert("Enter some text to speak.");
      return;
    }
    btnLipSyncPlay.disabled = true;
    if (lipSyncStatusEl) lipSyncStatusEl.textContent = "Playing…";
    try {
      const data = await api("POST", "/api/lip-sync/test", { text }, { timeoutMs: 90000 });
      if (lipSyncStatusEl) lipSyncStatusEl.textContent = data.lip_sync ? "Done." : (data.reason || "Done.");
    } catch (e) {
      alert("Lip sync test failed: " + e.message);
      if (lipSyncStatusEl) lipSyncStatusEl.textContent = "";
    } finally {
      btnLipSyncPlay.disabled = !connected || idleRunning;
    }
  });
}

/* ── servo sliders ── */
const servoGroupsEl = document.getElementById("servo-groups");
const loadMessageEl = document.getElementById("load-message");
const serverErrorEl = document.getElementById("server-error");
let servoSliders = {}; // name -> { slider, val } for linked control sync

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
  servoSliders = {};
  hideServerError();
  if (loadMessageEl) loadMessageEl.style.display = "none";
  servoGroupsEl.innerHTML = "";
  const groups = data.groups || {};
  const linked = data.linked_controls || {};

  for (const [groupName, servos] of Object.entries(groups)) {
    const groupEl = document.createElement("div");
    groupEl.className = "servo-group";
    const h3 = document.createElement("h3");
    h3.textContent = groupName;
    groupEl.appendChild(h3);

    /* ── linked control slider (e.g. Jaw) ── */
    if (linked[groupName]) {
      const lc = linked[groupName];
      const row = document.createElement("div");
      row.className = "slider-row linked-slider";

      const label = document.createElement("label");
      label.textContent = lc.label;

      const slider = document.createElement("input");
      slider.type = "range";
      slider.min = lc.slider_min;
      slider.max = lc.slider_max;
      slider.value = lc.slider_default;

      const val = document.createElement("span");
      val.className = "angle-val";
      val.textContent = lc.slider_default + "°";

      const sendLinkedMove = debounce((cmds) => {
        if (!connected) return;
        api("POST", "/api/servos/move-multiple", { servos: cmds }).catch(
          (err) => console.error("linked move error:", err),
        );
      }, 150);

      slider.addEventListener("input", () => {
        const offset = Number(slider.value);
        val.textContent = Math.round(offset) + "°";
        const cmds = [];
        for (const [servoName, mapping] of Object.entries(lc.servos)) {
          const angle = mapping.center + mapping.direction * offset;
          positions[servoName] = angle;
          cmds.push({ name: servoName, angle, duration: 0.15 });
          if (servoSliders[servoName]) {
            servoSliders[servoName].slider.value = angle;
            servoSliders[servoName].val.textContent = Math.round(angle) + "°";
          }
        }
        sendLinkedMove(cmds);
      });

      row.appendChild(label);
      row.appendChild(slider);
      row.appendChild(val);
      groupEl.appendChild(row);
    }

    /* ── individual servo sliders ── */
    for (const s of servos) {
      const { lo, hi } = sliderRange(s);
      const cur = s.current_angle ?? data.calibrate_angle ?? 90;
      positions[s.name] = cur;

      const row = document.createElement("div");
      row.className = "slider-row";

      const label = document.createElement("label");
      label.textContent = s.name;

      const slider = document.createElement("input");
      slider.type = "range";
      slider.min = lo;
      slider.max = hi;
      slider.value = Math.max(lo, Math.min(hi, cur));
      slider.dataset.name = s.name;

      const val = document.createElement("span");
      val.className = "angle-val";
      val.textContent = Math.round(slider.value) + "°";

      servoSliders[s.name] = { slider, val };

      const sendMove = debounce((name, angle) => {
        if (!connected) return;
        api("POST", `/api/servo/${encodeURIComponent(name)}/move`, {
          angle,
          duration: 0.15,
        }).catch((err) => console.error("move error:", err));
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
  if (idleRunning) {
    servoGroupsEl.querySelectorAll("input[type=range]").forEach((el) => {
      el.disabled = true;
    });
  }
}

/* ── controls ── */
btnCalibrate.addEventListener("click", async () => {
  try {
    const data = await api("POST", "/api/calibrate");
    const fresh = await api("GET", "/api/servos");
    renderServos(fresh);
  } catch (e) {
    alert("Calibrate failed: " + e.message);
  }
});

btnStop.addEventListener("click", async () => {
  try {
    await api("POST", "/api/stop");
    setConnected(false, null);
    const fresh = await api("GET", "/api/servos");
    renderServos(fresh);
  } catch (e) {
    alert("Stop failed: " + e.message);
  }
});

btnReload.addEventListener("click", async () => {
  try {
    const data = await api("POST", "/api/config/reload");
    renderServos(data);
    await renderExpressions();
  } catch (e) {
    alert("Reload failed: " + e.message);
  }
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
      applyBtn.className = "primary";
      applyBtn.addEventListener("click", async () => {
        try {
          const result = await api(
            "POST",
            `/api/expressions/${encodeURIComponent(name)}/apply`,
          );
          const fresh = await api("GET", "/api/servos");
          renderServos(fresh);
        } catch (e) {
          alert("Apply failed: " + e.message);
        }
      });

      const delBtn = document.createElement("button");
      delBtn.textContent = "Delete";
      delBtn.className = "danger";
      delBtn.addEventListener("click", async () => {
        if (!confirm(`Delete expression "${name}"?`)) return;
        try {
          await api("DELETE", `/api/expressions/${encodeURIComponent(name)}`);
          await renderExpressions();
        } catch (e) {
          alert("Delete failed: " + e.message);
        }
      });

      actions.appendChild(applyBtn);
      actions.appendChild(delBtn);
      item.appendChild(nameSpan);
      item.appendChild(actions);
      exprListEl.appendChild(item);
    }
    if (idleRunning) {
      exprListEl.querySelectorAll(".expr-actions button.primary").forEach((el) => {
        el.disabled = true;
      });
    }
  } catch (e) {
    console.error("expressions load error:", e);
  }
}

btnSaveExpr.addEventListener("click", async () => {
  const name = exprNameEl.value.trim();
  if (!name) {
    alert("Enter an expression name");
    return;
  }
  try {
    await api("POST", `/api/expressions/${encodeURIComponent(name)}`, {
      angles: { ...positions },
    });
    exprNameEl.value = "";
    await renderExpressions();
  } catch (e) {
    alert("Save failed: " + e.message);
  }
});

/* ── init ── */
(function init() {
  if (!servoGroupsEl) return;

  (async () => {
    try {
      const st = await api("GET", "/api/status");
      if (st.connected && st.port) setConnected(true, st.port);
      if (st.idle_running !== undefined) updateIdleUI(st.idle_running);
    } catch (_) {}

    try {
      const data = await api("GET", "/api/servos");
      renderServos(data);
    } catch (e) {
      console.error("init servos error:", e);
      showServerError(
        "Could not load servos. Open this app at http://localhost:8000/ (not as a file). " +
          "Start the server with: python -m manual_debug",
      );
      if (loadMessageEl) loadMessageEl.style.display = "none";
      renderServos({ groups: {}, calibrate_angle: 90 });
    }

    try {
      await renderExpressions();
    } catch (_) {}
  })();
})();
