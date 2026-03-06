/* ── state ── */
let servoData = {}; // { groups: { "Eyes": [...], ... }, calibrate_angle }
let positions = {}; // name -> current angle
let connected = false;
let currentMode = "manual"; // "manual" or "auto"

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

function formatTime(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString("en-GB", { hour12: false }) + "." + String(d.getMilliseconds()).padStart(3, "0");
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
const btnWinkRight = document.getElementById("btn-wink-right");
const btnWinkLeft = document.getElementById("btn-wink-left");
const btnLipSyncPlay = document.getElementById("btn-lip-sync-play");
const lipSyncTextEl = document.getElementById("lip-sync-text");
const lipSyncStatusEl = document.getElementById("lip-sync-status");

/* ── neck buttons ── */
const btnNeckCenter = document.getElementById("btn-neck-center");
const btnNeckLeft   = document.getElementById("btn-neck-left");
const btnNeckRight  = document.getElementById("btn-neck-right");
const btnNeckUp     = document.getElementById("btn-neck-up");
const btnNeckDown   = document.getElementById("btn-neck-down");
const btnNeckNod    = document.getElementById("btn-neck-nod");
const btnNeckShake  = document.getElementById("btn-neck-shake");

/* ── person tracking (auto mode) ── */
const btnTrackingOn  = document.getElementById("btn-tracking-on");
const btnTrackingOff = document.getElementById("btn-tracking-off");
const trackingStatusEl = document.getElementById("tracking-status");
const pitchOverrideWrap = document.getElementById("pitch-override-wrap");
const pitchAngleSlider = document.getElementById("pitch-angle-slider");
const pitchAngleVal = document.getElementById("pitch-angle-val");
const trackingFeedWrap = document.getElementById("tracking-feed-wrap");
const trackingFeedImg = document.getElementById("tracking-feed-img");

// Safari doesn't support MJPEG in <img>; all other browsers do.
const _isSafari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);

let _trackingFeedActive = false;
let _trackingFeedTimer = null;

// Safari fallback: JS polling via snapshot endpoint
function _pollNextFrame() {
  if (!_trackingFeedActive || !trackingFeedImg) return;
  const offscreen = new Image();
  offscreen.onload = function () {
    if (_trackingFeedActive && trackingFeedImg) trackingFeedImg.src = this.src;
    _trackingFeedTimer = setTimeout(_pollNextFrame, 100);
  };
  offscreen.onerror = function () {
    _trackingFeedTimer = setTimeout(_pollNextFrame, 500);
  };
  offscreen.src = "/api/person-tracking/camera-snapshot?" + Date.now();
}

function _startTrackingFeed() {
  if (_trackingFeedActive) return;
  _trackingFeedActive = true;
  if (!trackingFeedImg) return;
  if (_isSafari) {
    // Safari: JS polling
    _pollNextFrame();
  } else {
    // Chrome / Firefox: native MJPEG stream — browser handles it automatically
    trackingFeedImg.src = "/api/person-tracking/camera-feed";
  }
}

function _stopTrackingFeed() {
  _trackingFeedActive = false;
  if (_trackingFeedTimer) { clearTimeout(_trackingFeedTimer); _trackingFeedTimer = null; }
  if (trackingFeedImg) trackingFeedImg.src = "";
}

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
  const locked = running;
  if (btnCalibrate) btnCalibrate.disabled = !connected || locked;
  if (btnEyesCenter) btnEyesCenter.disabled = !connected || locked;
  if (btnEyesOpen) btnEyesOpen.disabled = !connected || locked;
  if (btnEyesClose) btnEyesClose.disabled = !connected || locked;
  if (btnRandomLook) btnRandomLook.disabled = !connected || locked;
  if (btnBlinkLeft) btnBlinkLeft.disabled = !connected || locked;
  if (btnBlinkRight) btnBlinkRight.disabled = !connected || locked;
  if (btnBlinkBoth) btnBlinkBoth.disabled = !connected || locked;
  if (btnWinkRight) btnWinkRight.disabled = !connected || locked;
  if (btnWinkLeft) btnWinkLeft.disabled = !connected || locked;
  if (btnLipSyncPlay) btnLipSyncPlay.disabled = !connected || locked;
  // Neck buttons
  [btnNeckCenter, btnNeckLeft, btnNeckRight, btnNeckUp, btnNeckDown, btnNeckNod, btnNeckShake].forEach((b) => {
    if (b) b.disabled = !connected || locked;
  });
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
      const data = await api("POST", "/api/idle-running", {
        idle_running: true,
      });
      updateIdleUI(data.idle_running);
    } catch (e) {
      alert("Failed to start Idle: " + e.message);
    }
  });
}
if (btnIdleOff) {
  btnIdleOff.addEventListener("click", async () => {
    try {
      const data = await api("POST", "/api/idle-running", {
        idle_running: false,
      });
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
  btnEyesCenter.addEventListener("click", () =>
    eyesAction("/api/eyes/center", btnEyesCenter),
  );
}
if (btnEyesOpen) {
  btnEyesOpen.addEventListener("click", () =>
    eyesAction("/api/eyes/open", btnEyesOpen),
  );
}
if (btnEyesClose) {
  btnEyesClose.addEventListener("click", () =>
    eyesAction("/api/eyes/close", btnEyesClose),
  );
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
  btnBlinkLeft.addEventListener("click", () =>
    eyesAction("/api/eyes/blink-left", btnBlinkLeft),
  );
}
if (btnBlinkRight) {
  btnBlinkRight.addEventListener("click", () =>
    eyesAction("/api/eyes/blink-right", btnBlinkRight),
  );
}
if (btnBlinkBoth) {
  btnBlinkBoth.addEventListener("click", () =>
    eyesAction("/api/eyes/blink-both", btnBlinkBoth),
  );
}
if (btnWinkRight) {
  btnWinkRight.addEventListener("click", () =>
    eyesAction("/api/eyes/wink-right", btnWinkRight),
  );
}
if (btnWinkLeft) {
  btnWinkLeft.addEventListener("click", () =>
    eyesAction("/api/eyes/wink-left", btnWinkLeft),
  );
}

/* ── neck actions ── */
async function neckAction(endpoint, btn, label) {
  if (!connected) return;
  const origText = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; if (label) btn.textContent = label; }
  try {
    await api("POST", endpoint);
  } catch (e) {
    alert("Neck action failed: " + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = origText; }
  }
}

if (btnNeckCenter) btnNeckCenter.addEventListener("click", () => neckAction("/api/neck/center", btnNeckCenter));
if (btnNeckLeft)   btnNeckLeft.addEventListener("click",   () => neckAction("/api/neck/look-left", btnNeckLeft));
if (btnNeckRight)  btnNeckRight.addEventListener("click",  () => neckAction("/api/neck/look-right", btnNeckRight));
if (btnNeckUp)     btnNeckUp.addEventListener("click",     () => neckAction("/api/neck/look-up", btnNeckUp));
if (btnNeckDown)   btnNeckDown.addEventListener("click",   () => neckAction("/api/neck/look-down", btnNeckDown));
if (btnNeckNod) {
  btnNeckNod.addEventListener("click", () => neckAction("/api/neck/nod", btnNeckNod, "Nodding…"));
}
if (btnNeckShake) {
  btnNeckShake.addEventListener("click", () => neckAction("/api/neck/shake", btnNeckShake, "Shaking…"));
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
      const data = await api(
        "POST",
        "/api/lip-sync/test",
        { text },
        { timeoutMs: 90000 },
      );
      if (lipSyncStatusEl)
        lipSyncStatusEl.textContent = data.lip_sync
          ? "Done."
          : data.reason || "Done.";
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
      exprListEl
        .querySelectorAll(".expr-actions button.primary")
        .forEach((el) => {
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

/* ================================================================
   MODE SWITCHING
   ================================================================ */
const manualModeEl = document.getElementById("manual-mode");
const autoModeEl = document.getElementById("auto-mode");
const historyModeEl = document.getElementById("history-mode");
const modeTabs = document.querySelectorAll(".mode-tab");

function switchMode(mode) {
  currentMode = mode;
  modeTabs.forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.mode === mode);
  });
  manualModeEl.style.display = mode === "manual" ? "" : "none";
  autoModeEl.style.display = mode === "auto" ? "" : "none";
  historyModeEl.style.display = mode === "history" ? "" : "none";

  if (mode === "auto") {
    connectWebSocket();
    refreshBrainStatus();
    api("GET", "/api/person-tracking").then((d) => updateTrackingUI(d)).catch(() => {});
    _startTrackingFeed();
  } else {
    _stopTrackingFeed();
  }
  if (mode === "history") {
    loadHistorySection();
  }
}

modeTabs.forEach((tab) => {
  tab.addEventListener("click", async () => {
    const mode = tab.dataset.mode;
    if (mode === currentMode) return;
    if (mode === "history") {
      switchMode(mode);
      return;
    }
    try {
      await api("POST", "/api/mode", { mode });
      switchMode(mode);
    } catch (e) {
      alert("Mode switch failed: " + e.message);
    }
  });
});

/* ── person tracking toggle (auto mode) ── */
let personTrackingEnabled = false;

function updateTrackingUI(data) {
  // Accept either a full status object or a plain boolean (backwards compat)
  const enabled = typeof data === "boolean" ? data : Boolean(data?.person_tracking_enabled);
  const pitchDisabled = typeof data === "object" ? Boolean(data?.pitch_disabled) : false;
  const pitchAngle = typeof data === "object" ? (data?.pitch_angle ?? 90) : 90;
  const pitchMin = typeof data === "object" ? (data?.pitch_min ?? 0) : 0;
  const pitchMax = typeof data === "object" ? (data?.pitch_max ?? 360) : 360;

  personTrackingEnabled = enabled;
  if (trackingStatusEl) trackingStatusEl.textContent = enabled ? "On" : "Off";
  if (btnTrackingOn)  btnTrackingOn.disabled  = enabled;
  if (btnTrackingOff) btnTrackingOff.disabled = !enabled;

  // Pitch manual override — visible only when tracking is on AND pitch is disabled
  if (pitchOverrideWrap) {
    pitchOverrideWrap.style.display = (enabled && pitchDisabled) ? "" : "none";
    if (pitchAngleSlider) {
      pitchAngleSlider.min = pitchMin;
      pitchAngleSlider.max = pitchMax;
      pitchAngleSlider.value = Math.round(pitchAngle);
    }
    if (pitchAngleVal) pitchAngleVal.textContent = Math.round(pitchAngle) + "°";
  }

  // Camera feed — always show, poll continuously
  _startTrackingFeed();
}

async function setPersonTracking(enabled) {
  try {
    const data = await api("POST", "/api/person-tracking", { person_tracking_enabled: enabled });
    updateTrackingUI(data);
  } catch (e) {
    alert("Person tracking toggle failed: " + e.message);
  }
}

if (btnTrackingOn)  btnTrackingOn.addEventListener("click",  () => setPersonTracking(true));
if (btnTrackingOff) btnTrackingOff.addEventListener("click", () => setPersonTracking(false));

/* ── pitch angle override slider ── */
const _sendPitchAngle = debounce(async (angle) => {
  try {
    await api("POST", "/api/person-tracking/pitch-angle", { angle });
  } catch (e) {
    console.error("Pitch angle update failed:", e.message);
  }
}, 120);

if (pitchAngleSlider) {
  pitchAngleSlider.addEventListener("input", () => {
    const angle = Number(pitchAngleSlider.value);
    if (pitchAngleVal) pitchAngleVal.textContent = Math.round(angle) + "°";
    _sendPitchAngle(angle);
  });
}

/* ================================================================
   AUTO MODE: Brain Controls
   ================================================================ */
const btnBrainStart = document.getElementById("btn-brain-start");
const btnBrainStop = document.getElementById("btn-brain-stop");
const stateBadge = document.getElementById("state-badge");
const expressionBadge = document.getElementById("expression-badge");

btnBrainStart.addEventListener("click", async () => {
  btnBrainStart.disabled = true;
  try {
    await api("POST", "/api/brain/start");
    btnBrainStop.disabled = false;
    clearConversation();
  } catch (e) {
    alert("Brain start failed: " + e.message);
    btnBrainStart.disabled = false;
  }
});

btnBrainStop.addEventListener("click", async () => {
  btnBrainStop.disabled = true;
  try {
    await api("POST", "/api/brain/stop");
    btnBrainStart.disabled = false;
    updateStateBadge("idle");
  } catch (e) {
    alert("Brain stop failed: " + e.message);
    btnBrainStop.disabled = false;
  }
});

async function refreshBrainStatus() {
  try {
    const data = await api("GET", "/api/brain/status");
    btnBrainStart.disabled = data.running;
    btnBrainStop.disabled = !data.running;
    updateStateBadge(data.activity || "idle");
    expressionBadge.textContent = data.expression || "neutral";
  } catch (_) {}
}

function updateStateBadge(activity) {
  const label = activity.toUpperCase();
  stateBadge.textContent = label;
  stateBadge.className = "state-badge state-" + activity;
}

/* ================================================================
   AUTO MODE: WebSocket
   ================================================================ */
let ws = null;
let wsReconnectTimer = null;

function connectWebSocket() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws`);

  ws.onopen = () => {
    console.log("[WS] connected");
    if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
  };

  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === "history") {
        for (const event of msg.events) {
          handleEvent(event);
        }
      } else if (msg.type === "ping") {
        // keep-alive, ignore
      } else {
        handleEvent(msg);
      }
    } catch (_) {}
  };

  ws.onclose = () => {
    console.log("[WS] disconnected");
    if (currentMode === "auto" && !wsReconnectTimer) {
      wsReconnectTimer = setTimeout(connectWebSocket, 2000);
    }
  };

  ws.onerror = () => {
    ws.close();
  };
}

/* ================================================================
   AUTO MODE: Event Handling
   ================================================================ */
const eventLogEl = document.getElementById("event-log");
const eventCountEl = document.getElementById("event-count");
const conversationEl = document.getElementById("conversation-messages");
const metricTranscription = document.getElementById("metric-transcription");
const metricLlm = document.getElementById("metric-llm");
const metricTts = document.getElementById("metric-tts");
const metricTotal = document.getElementById("metric-total");

let eventCount = 0;
let activeFilter = "all";
let responseStartTime = null;

document.querySelectorAll(".filter-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".filter-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    activeFilter = btn.dataset.filter;
    applyEventFilter();
  });
});

document.getElementById("btn-clear-events")?.addEventListener("click", () => {
  eventLogEl.innerHTML = "";
  eventCount = 0;
  eventCountEl.textContent = "0 events";
});

function eventCategory(type) {
  if (type.startsWith("activity.") || type.startsWith("expression.")) return "state";
  if (type.startsWith("conversation.") || type.startsWith("transcription.") ||
      type.startsWith("llm.") || type.startsWith("tts.") || type.startsWith("lip_sync.") ||
      type.startsWith("audio.")) return "conversation";
  if (type.startsWith("brain.")) return "brain";
  if (type.startsWith("idle.")) return "idle";
  if (type.includes("error")) return "error";
  return "other";
}

function applyEventFilter() {
  eventLogEl.querySelectorAll(".event-row").forEach((row) => {
    if (activeFilter === "all") {
      row.style.display = "";
    } else {
      row.style.display = row.dataset.category === activeFilter ? "" : "none";
    }
  });
}

function addEventRow(event) {
  const cat = eventCategory(event.type);
  const row = document.createElement("div");
  row.className = `event-row event-cat-${cat}`;
  row.dataset.category = cat;

  const timeEl = document.createElement("span");
  timeEl.className = "event-time";
  timeEl.textContent = formatTime(event.timestamp);

  const typeEl = document.createElement("span");
  typeEl.className = "event-type";
  typeEl.textContent = event.type;

  const dataEl = document.createElement("span");
  dataEl.className = "event-data";
  const dataStr = event.data && Object.keys(event.data).length > 0 ? JSON.stringify(event.data) : "";
  dataEl.textContent = dataStr;
  dataEl.title = dataStr;

  row.appendChild(timeEl);
  row.appendChild(typeEl);
  row.appendChild(dataEl);

  if (activeFilter !== "all" && cat !== activeFilter) {
    row.style.display = "none";
  }

  eventLogEl.appendChild(row);
  eventCount++;
  eventCountEl.textContent = eventCount + " event" + (eventCount !== 1 ? "s" : "");

  // Auto-scroll to bottom
  eventLogEl.scrollTop = eventLogEl.scrollHeight;
}

function handleEvent(event) {
  addEventRow(event);

  const type = event.type;
  const data = event.data || {};

  switch (type) {
    case "activity.changed":
      updateStateBadge(data.new || "idle");
      break;

    case "expression.changed":
      expressionBadge.textContent = data.new || "neutral";
      break;

    case "brain.started":
      btnBrainStart.disabled = true;
      btnBrainStop.disabled = false;
      addConversationStatus("Brain started");
      break;

    case "brain.stopped":
      btnBrainStart.disabled = false;
      btnBrainStop.disabled = true;
      updateStateBadge("idle");
      addConversationStatus("Brain stopped");
      break;

    case "brain.error":
      addConversationStatus("Brain error: " + (data.error || "unknown"));
      break;

    case "wake_word.listening":
      addConversationStatus("Listening for wake word...");
      break;

    case "wake_word.detected":
      addConversationStatus("Wake word detected: " + (data.keyword || ""));
      break;

    case "conversation.started":
      addConversationStatus("Conversation started");
      responseStartTime = null;
      break;

    case "conversation.ended":
      addConversationStatus(
        `Conversation ended (${data.duration_s || "?"}s, ${data.message_count || 0} messages)`
      );
      break;

    case "audio.capture_start":
      responseStartTime = performance.now();
      break;

    case "transcription.completed":
      if (data.text) {
        addChatBubble("user", data.text);
      }
      metricTranscription.textContent = data.duration_s != null ? data.duration_s + "s" : "--";
      break;

    case "llm.completed":
      if (data.text) {
        addChatBubble("assistant", data.text);
      }
      metricLlm.textContent = data.duration_s != null ? data.duration_s + "s" : "--";
      break;

    case "tts.completed":
      metricTts.textContent = data.duration_s != null ? data.duration_s + "s" : "--";
      break;

    case "audio.playback_end":
      if (responseStartTime) {
        const total = ((performance.now() - responseStartTime) / 1000).toFixed(1);
        metricTotal.textContent = total + "s";
        responseStartTime = null;
      }
      break;
  }
}

/* ================================================================
   AUTO MODE: Conversation Panel
   ================================================================ */
function clearConversation() {
  conversationEl.innerHTML = '<div class="conversation-empty">Waiting for conversation...</div>';
}

function addConversationStatus(text) {
  removeEmptyMessage();
  const el = document.createElement("div");
  el.className = "conversation-status";
  el.textContent = text;
  conversationEl.appendChild(el);
  conversationEl.scrollTop = conversationEl.scrollHeight;
}

function addChatBubble(role, text) {
  removeEmptyMessage();
  const bubble = document.createElement("div");
  bubble.className = "chat-bubble " + role;

  const roleEl = document.createElement("div");
  roleEl.className = "chat-role";
  roleEl.textContent = role === "user" ? "You" : "Robot";

  const textEl = document.createElement("div");
  textEl.textContent = text;

  bubble.appendChild(roleEl);
  bubble.appendChild(textEl);
  conversationEl.appendChild(bubble);
  conversationEl.scrollTop = conversationEl.scrollHeight;
}

function removeEmptyMessage() {
  const empty = conversationEl.querySelector(".conversation-empty");
  if (empty) empty.remove();
}

/* ================================================================
   HISTORY MODE
   ================================================================ */
let activeHistorySection = "conversations";

document.querySelectorAll(".history-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    const section = tab.dataset.section;
    if (section === activeHistorySection) return;
    document.querySelectorAll(".history-tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    activeHistorySection = section;
    document.querySelectorAll(".history-section").forEach((el) => (el.style.display = "none"));
    document.getElementById("history-" + section).style.display = "";
    loadHistorySection();
  });
});

function loadHistorySection() {
  switch (activeHistorySection) {
    case "conversations": loadConversations(); break;
    case "surroundings": loadSurroundings(); break;
    case "logs": loadLogs(); break;
  }
}

/* ---- Conversations ---- */
async function loadConversations() {
  const listEl = document.getElementById("conv-list");
  const detailEl = document.getElementById("conv-detail");
  detailEl.style.display = "none";
  listEl.style.display = "";
  listEl.innerHTML = '<p class="history-empty">Loading...</p>';
  try {
    const data = await api("GET", "/api/data/conversations");
    const convs = data.conversations || [];
    if (convs.length === 0) {
      listEl.innerHTML = '<p class="history-empty">No conversations yet.</p>';
      return;
    }
    listEl.innerHTML = "";
    for (const c of convs) {
      const item = document.createElement("div");
      item.className = "conv-list-item";

      const startDate = c.start_time ? new Date(c.start_time * 1000) : null;
      const dateStr = startDate
        ? startDate.toLocaleString("en-GB", { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
        : "Unknown time";

      item.innerHTML = `
        <div class="conv-item-left">
          <span class="conv-item-time">${dateStr}</span>
          <span class="conv-item-meta">${c.message_count} message${c.message_count !== 1 ? "s" : ""}${c.duration_s != null ? " &middot; " + c.duration_s + "s" : ""}</span>
        </div>
        <div class="conv-item-right">
          ${c.audio_files ? c.audio_files.length + " audio files" : ""}
        </div>
      `;

      item.addEventListener("click", () => openConversation(c.id));
      listEl.appendChild(item);
    }
  } catch (e) {
    listEl.innerHTML = '<p class="history-empty">Failed to load conversations.</p>';
  }
}

async function openConversation(id) {
  const listEl = document.getElementById("conv-list");
  const detailEl = document.getElementById("conv-detail");
  listEl.style.display = "none";
  detailEl.style.display = "";

  const headerEl = document.getElementById("conv-detail-header");
  const messagesEl = document.getElementById("conv-detail-messages");

  headerEl.innerHTML = '<p class="history-empty">Loading...</p>';
  messagesEl.innerHTML = "";

  try {
    const data = await api("GET", `/api/data/conversations/${encodeURIComponent(id)}`);

    const startDate = data.conversation_start_time ? new Date(data.conversation_start_time * 1000) : null;
    const endDate = data.conversation_end_time ? new Date(data.conversation_end_time * 1000) : null;
    const startStr = startDate ? startDate.toLocaleString("en-GB") : "?";
    const endStr = endDate ? endDate.toLocaleString("en-GB") : "ongoing";
    const duration = data.conversation_start_time && data.conversation_end_time
      ? Math.round(data.conversation_end_time - data.conversation_start_time) + "s"
      : "?";
    const msgs = data.messages || [];

    headerEl.innerHTML = `
      <h3>Conversation ${id.slice(0, 8)}...</h3>
      <div class="detail-meta">
        <span>Start: ${startStr}</span>
        <span>End: ${endStr}</span>
        <span>Duration: ${duration}</span>
        <span>Messages: ${msgs.length}</span>
      </div>
    `;

    // Show surroundings images if any
    const surroundings = data.surroundings || [];
    if (surroundings.length > 0) {
      const surrBlock = document.createElement("div");
      surrBlock.className = "conv-surroundings-block";
      surrBlock.innerHTML = '<div class="conv-msg-role" style="color:var(--warning)">Context Snapshots</div>';
      const gallery = document.createElement("div");
      gallery.className = "conv-surr-gallery";
      for (const s of surroundings) {
        const imgWrap = document.createElement("div");
        imgWrap.className = "conv-surr-item";
        imgWrap.innerHTML = `
          <img src="/api/data/surroundings/images/${encodeURIComponent(s.image)}" alt="${s.timestamp}" loading="lazy" />
          <span class="conv-surr-time">${s.timestamp.replace(/_/g, " ")}</span>
        `;
        if (s.has_context) {
          imgWrap.style.cursor = "pointer";
          imgWrap.addEventListener("click", async () => {
            try {
              const ctx = await fetch(`/api/data/surroundings/contexts/${encodeURIComponent(s.timestamp + ".txt")}`).then(r => r.text());
              alert(ctx);
            } catch (_) {}
          });
        }
        gallery.appendChild(imgWrap);
      }
      surrBlock.appendChild(gallery);
      messagesEl.appendChild(surrBlock);
    }

    for (const msg of msgs) {
      const block = document.createElement("div");
      block.className = "conv-msg-block " + msg.role;

      const roleEl = document.createElement("div");
      roleEl.className = "conv-msg-role";
      roleEl.textContent = msg.role === "user" ? "You" : "Robot";

      const textEl = document.createElement("div");
      textEl.className = "conv-msg-text";
      textEl.textContent = msg.content || "";

      block.appendChild(roleEl);
      block.appendChild(textEl);

      if (msg.audio_file) {
        const audioFileName = msg.audio_file.split("/").pop();
        const audioEl = document.createElement("div");
        audioEl.className = "conv-msg-audio";
        audioEl.innerHTML = `<audio controls preload="none" src="/api/data/conversations/${encodeURIComponent(id)}/audio/${encodeURIComponent(audioFileName)}"></audio>`;
        block.appendChild(audioEl);
      }

      if (msg.timestamp) {
        const tsEl = document.createElement("div");
        tsEl.className = "conv-msg-timestamp";
        tsEl.textContent = new Date(msg.timestamp * 1000).toLocaleTimeString("en-GB");
        block.appendChild(tsEl);
      }

      messagesEl.appendChild(block);
    }
  } catch (e) {
    headerEl.innerHTML = '<p class="history-empty">Failed to load conversation.</p>';
  }
}

document.getElementById("btn-conv-back")?.addEventListener("click", () => {
  document.getElementById("conv-detail").style.display = "none";
  document.getElementById("conv-list").style.display = "";
});

/* ---- Surroundings ---- */
async function loadSurroundings() {
  const galleryEl = document.getElementById("surroundings-gallery");
  const detailEl = document.getElementById("surroundings-detail");
  detailEl.style.display = "none";
  galleryEl.style.display = "";
  galleryEl.innerHTML = '<p class="history-empty">Loading...</p>';

  try {
    const data = await api("GET", "/api/data/surroundings");
    const items = data.surroundings || [];
    if (items.length === 0) {
      galleryEl.innerHTML = '<p class="history-empty">No surroundings captured yet.</p>';
      return;
    }
    galleryEl.innerHTML = "";
    for (const item of items) {
      const card = document.createElement("div");
      card.className = "surr-card";
      card.innerHTML = `
        <img src="/api/data/surroundings/images/${encodeURIComponent(item.image)}" alt="${item.timestamp}" loading="lazy" />
        <div class="surr-card-info">
          <div class="surr-card-time">${item.timestamp.replace(/_/g, " ")}</div>
          <div class="surr-card-context">${item.has_context ? "Has context" : "No context"}</div>
        </div>
      `;
      card.addEventListener("click", () => openSurrounding(item));
      galleryEl.appendChild(card);
    }
  } catch (e) {
    galleryEl.innerHTML = '<p class="history-empty">Failed to load surroundings.</p>';
  }
}

async function openSurrounding(item) {
  const galleryEl = document.getElementById("surroundings-gallery");
  const detailEl = document.getElementById("surroundings-detail");
  galleryEl.style.display = "none";
  detailEl.style.display = "";

  const contentEl = document.getElementById("surr-detail-content");
  contentEl.innerHTML = `
    <h3 style="font-size:15px;font-weight:600;color:var(--text);margin-bottom:8px">${item.timestamp.replace(/_/g, " ")}</h3>
    <img src="/api/data/surroundings/images/${encodeURIComponent(item.image)}" alt="${item.timestamp}" />
    <div id="surr-context-loading" class="history-empty">${item.has_context ? "Loading context..." : "No context available."}</div>
  `;

  if (item.has_context) {
    try {
      const contextText = await fetch(`/api/data/surroundings/contexts/${encodeURIComponent(item.timestamp + ".txt")}`).then(r => r.text());
      const loadingEl = document.getElementById("surr-context-loading");
      if (loadingEl) {
        loadingEl.className = "surr-context-box";
        loadingEl.textContent = contextText;
      }
    } catch (e) {
      const loadingEl = document.getElementById("surr-context-loading");
      if (loadingEl) loadingEl.textContent = "Failed to load context.";
    }
  }
}

document.getElementById("btn-surr-back")?.addEventListener("click", () => {
  document.getElementById("surroundings-detail").style.display = "none";
  document.getElementById("surroundings-gallery").style.display = "";
});

/* ---- Logs ---- */
async function loadLogs() {
  const listEl = document.getElementById("log-list");
  const detailEl = document.getElementById("log-detail");
  detailEl.style.display = "none";
  listEl.style.display = "";
  listEl.innerHTML = '<p class="history-empty">Loading...</p>';

  try {
    const data = await api("GET", "/api/data/logs");
    const logs = data.logs || [];
    if (logs.length === 0) {
      listEl.innerHTML = '<p class="history-empty">No session logs yet. Run the brain in auto mode to generate logs.</p>';
      return;
    }
    listEl.innerHTML = "";
    for (const log of logs) {
      const item = document.createElement("div");
      item.className = "log-list-item";

      const sizeKb = (log.size_bytes / 1024).toFixed(1);
      const dateStr = log.filename.replace("_session.jsonl", "").replace(/_/g, " ");

      item.innerHTML = `
        <span class="log-item-name">${dateStr}</span>
        <span class="log-item-meta">${sizeKb} KB</span>
      `;

      item.addEventListener("click", () => openLog(log.filename));
      listEl.appendChild(item);
    }
  } catch (e) {
    listEl.innerHTML = '<p class="history-empty">Failed to load logs.</p>';
  }
}

async function openLog(filename) {
  const listEl = document.getElementById("log-list");
  const detailEl = document.getElementById("log-detail");
  listEl.style.display = "none";
  detailEl.style.display = "";

  const headerEl = document.getElementById("log-detail-header");
  const eventsEl = document.getElementById("log-detail-events");
  const dateStr = filename.replace("_session.jsonl", "").replace(/_/g, " ");

  headerEl.innerHTML = `<h3>Session: ${dateStr}</h3>`;
  eventsEl.innerHTML = '<p class="history-empty">Loading...</p>';

  try {
    const data = await api("GET", `/api/data/logs/${encodeURIComponent(filename)}`);
    const events = data.events || [];
    eventsEl.innerHTML = "";

    if (events.length === 0) {
      eventsEl.innerHTML = '<p class="history-empty">No events in this log.</p>';
      return;
    }

    for (const event of events) {
      const cat = eventCategory(event.type);
      const row = document.createElement("div");
      row.className = `event-row event-cat-${cat}`;

      const timeEl = document.createElement("span");
      timeEl.className = "event-time";
      timeEl.textContent = formatTime(event.timestamp);

      const typeEl = document.createElement("span");
      typeEl.className = "event-type";
      typeEl.textContent = event.type;

      const dataEl = document.createElement("span");
      dataEl.className = "event-data";
      const dataStr = event.data && Object.keys(event.data).length > 0 ? JSON.stringify(event.data) : "";
      dataEl.textContent = dataStr;
      dataEl.title = dataStr;

      row.appendChild(timeEl);
      row.appendChild(typeEl);
      row.appendChild(dataEl);
      eventsEl.appendChild(row);
    }
  } catch (e) {
    eventsEl.innerHTML = '<p class="history-empty">Failed to load log.</p>';
  }
}

document.getElementById("btn-log-back")?.addEventListener("click", () => {
  document.getElementById("log-detail").style.display = "none";
  document.getElementById("log-list").style.display = "";
});

/* ================================================================
   INIT
   ================================================================ */
(function init() {
  if (!servoGroupsEl) return;

  (async () => {
    try {
      const st = await api("GET", "/api/status");
      if (st.connected && st.port) setConnected(true, st.port);
      if (st.idle_running !== undefined) updateIdleUI(st.idle_running);
    } catch (_) {}

    try {
      const modeData = await api("GET", "/api/mode");
      if (modeData.mode === "auto") {
        switchMode("auto");
      }
    } catch (_) {}

    _startTrackingFeed();

    try {
      const trackingData = await api("GET", "/api/person-tracking");
      updateTrackingUI(trackingData);
    } catch (_) {}

    try {
      const data = await api("GET", "/api/servos");
      renderServos(data);
    } catch (e) {
      console.error("init servos error:", e);
      showServerError(
        "Could not load servos. Open this app at http://localhost:8000/ (not as a file). " +
          "Start the server with: python -m robot_studio",
      );
      if (loadMessageEl) loadMessageEl.style.display = "none";
      renderServos({ groups: {}, calibrate_angle: 90 });
    }

    try {
      await renderExpressions();
    } catch (_) {}
  })();
})();
