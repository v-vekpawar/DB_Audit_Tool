/* ---------------------------------------------------------------------
   Database Normalization Checker — frontend logic.

   Pure helper functions (no DOM) live in the `logic` object and are
   exported via module.exports under Node, so they can be unit-tested
   without a browser. Everything DOM-dependent is wired up inside
   initApp(), which only runs in an actual browser.
   --------------------------------------------------------------------- */

const DEFAULT_PORTS = { mysql: 3306, postgresql: 5432, oracle: 1521 };

/**
 * Field-level validation of the connect form. `s` shape:
 *   { dbType, host, port, serviceName }
 * The database itself is picked after connecting. Pure — Node-tested.
 */
function validateConnect(s) {
  const errors = {};
  const host = (s.host || "").trim();
  if (!host) errors["err-host"] = "Host is required.";
  const portText = (s.port || "").trim();
  let port = 0;
  if (portText) {
    port = Number(portText);
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      errors["err-port"] = "Port must be 1-65535 (empty means the default).";
    }
  }
  const values = {
    dbType: s.dbType, host, port,
    serviceName: (s.serviceName || "").trim(),
    schema: (s.schema || "").trim(),
  };
  if (s.dbType === "oracle" && !values.serviceName) {
    errors["err-service-name"] = "Service name is required for Oracle.";
  }
  return Object.keys(errors).length > 0 ? { errors } : { values };
}

/**
 * Builds the POST /api/connect, /api/connect/tables and /api/scan/start
 * bodies from form state plus extras. Pure — Node-tested.
 */
function buildConnectPayload(form, extra) {
  const body = {
    db_type: form.dbType,
    host: form.host,
    port: form.port || DEFAULT_PORTS[form.dbType] || 0,
    username: form.username || "",
    password: form.password || "",
    database: (extra && extra.database) || "",
    schema: (extra && extra.schema) || form.schema || null,
    service_name: form.serviceName || null,
    ssl: !!form.ssl,
  };
  if (extra && extra.tables) body.tables = extra.tables;
  return body;
}

/** Case-insensitive substring filter for the table picker. Pure. */
function filterTables(tables, text) {
  const q = (text || "").trim().toLowerCase();
  if (!q) return tables;
  return tables.filter((t) => t.name.toLowerCase().includes(q));
}

/** m:ss for elapsed, "X min Y s" words for remaining/total. Pure. */
function formatClock(totalSeconds) {
  if (totalSeconds == null || Number.isNaN(totalSeconds)) return "—";
  const s = Math.max(0, Math.floor(totalSeconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function formatWords(totalSeconds) {
  if (totalSeconds == null || Number.isNaN(totalSeconds)) return "—";
  const s = Math.max(0, Math.round(totalSeconds));
  const m = Math.floor(s / 60);
  const rest = s % 60;
  if (m === 0) return `${rest} s`;
  return rest === 0 ? `${m} min` : `${m} min ${rest} s`;
}

/**
 * Renders an ISO timestamp in local time. Garbage passes through. Pure.
 */
function formatScanTime(stamp) {
  if (!stamp) return "—";
  const dated = new Date(String(stamp));
  if (Number.isNaN(dated.getTime())) return String(stamp);
  return dated.toLocaleString();
}

/**
 * The live time line. `st` shape:
 *   { done, selected, elapsedSeconds, remainingSeconds, totalSeconds,
 *     basis: "rough"|"measured"|null, stopped }
 * Pure — Node-tested.
 */
function formatTimeline(st) {
  const parts = [
    `Checking ${Math.min(st.done + 1, st.selected)} of ${st.selected}`,
    `elapsed ${formatClock(st.elapsedSeconds)}`,
  ];
  if (st.stopped) {
    parts.push("stopped — partial results kept");
    return parts.join(" · ");
  }
  if (st.remainingSeconds == null || st.totalSeconds == null) {
    parts.push("estimating remaining time…");
  } else {
    parts.push(`about ${formatWords(st.remainingSeconds)} remaining`);
    parts.push(`estimated total ${formatWords(st.totalSeconds)}` +
      (st.basis === "rough" ? " (rough)" : ""));
  }
  return parts.join(" · ");
}

/**
 * One-line headline from status counts. Pure — Node-tested.
 * `counts`: {faulted, suspected, insufficient_data, clean, error, not_run}.
 * `finished` counts decided tables only, so queued tables never inflate it.
 */
function formatHeadline(counts, finished, selected, notSelected) {
  let head = `${finished} of ${selected + notSelected} tables scanned`;
  if (notSelected > 0) head += ` (${notSelected} not selected)`;
  const tail = [];
  const order = [["faulted", "faulted"], ["suspected", "suspected"],
    ["insufficient_data", "insufficient data"], ["clean", "clean"],
    ["error", "error"], ["not_run", "not run"]];
  for (const [key, label] of order) {
    if (counts[key]) tail.push(`${counts[key]} ${label}`);
  }
  return tail.length > 0 ? `${head}: ${tail.join(", ")}` : head;
}

/** Status pill text + class for a table state. Pure. */
function statusBadge(state, normalForm) {
  switch (state) {
    case "queued": return ["Queued", "badge-queued"];
    case "checking": return ["Checking", "badge-checking"];
    case "clean": return ["Clean", "badge-clean"];
    case "faulted": return ["Faulted", "badge-faulted"];
    case "suspected": return ["Suspected", "badge-suspected"];
    case "insufficient_data": return ["Insufficient data", "badge-insufficient"];
    case "error": return ["Error", "badge-error"];
    case "not_run": return ["Not run", "badge-notrun"];
    default: return [state, "badge-queued"];
  }
}

/** Renders a structured error into {title, detail}. Pure. */
function formatError(err) {
  const title = (err && err.message) || "Unexpected error.";
  const bits = [];
  if (err && err.hint) bits.push(err.hint);
  const meta = [];
  if (err && err.step) meta.push(`step: ${err.step}`);
  if (err && err.code) meta.push(`code: ${err.code}`);
  if (err && err.scan_id) meta.push(`scan: ${err.scan_id}`);
  if (meta.length > 0) bits.push(meta.join(" · "));
  return { title, detail: bits.join(" — ") };
}

const logic = {
  DEFAULT_PORTS,
  validateConnect,
  buildConnectPayload,
  filterTables,
  formatClock,
  formatWords,
  formatTimeline,
  formatHeadline,
  statusBadge,
  formatError,
  formatScanTime,
};

if (typeof module !== "undefined" && module.exports) {
  module.exports = logic;
}

/* ----------------------------------------------------------------------
   DOM wiring — only runs in a real browser.
   ---------------------------------------------------------------------- */

if (typeof window !== "undefined" && typeof document !== "undefined") {
  initApp();
}

function initApp() {
  const els = {
    lastScanSection: document.getElementById("last-scan"),
    lastScanDb: document.getElementById("last-scan-db"),
    lastScanEngine: document.getElementById("last-scan-engine"),
    lastScanDate: document.getElementById("last-scan-date"),
    lastScanTables: document.getElementById("last-scan-tables"),
    lastScanDuration: document.getElementById("last-scan-duration"),
    lastScanStatus: document.getElementById("last-scan-status"),
    lastScanHeadline: document.getElementById("last-scan-headline"),
    viewLastBtn: document.getElementById("view-last-results-btn"),
    form: document.getElementById("connect-form"),
    connectBtn: document.getElementById("connect-btn"),
    changeConnBtn: document.getElementById("change-conn-btn"),
    host: document.getElementById("host"),
    port: document.getElementById("port"),
    username: document.getElementById("username"),
    password: document.getElementById("login-password"),
    serviceName: document.getElementById("service-name"),
    schema: document.getElementById("schema"),
    ssl: document.getElementById("ssl"),
    fieldService: document.getElementById("field-service"),
    fieldSchema: document.getElementById("field-schema"),
    formError: document.getElementById("form-error"),
    dbSection: document.getElementById("db-section"),
    dbList: document.getElementById("db-list"),
    listTablesBtn: document.getElementById("list-tables-btn"),
    pickerSection: document.getElementById("picker-section"),
    tableList: document.getElementById("table-list"),
    tableFilter: document.getElementById("table-filter"),
    pickerCounter: document.getElementById("picker-counter"),
    selectAllBtn: document.getElementById("select-all-btn"),
    selectNoneBtn: document.getElementById("select-none-btn"),
    scanBtn: document.getElementById("scan-btn"),
    progressSection: document.getElementById("progress-section"),
    progressLine: document.getElementById("progress-line"),
    stopScanBtn: document.getElementById("stop-scan-btn"),
    statusList: document.getElementById("status-list"),
    streamBanner: document.getElementById("stream-banner"),
    errorPanel: document.getElementById("error-panel"),
    resultsSection: document.getElementById("results-section"),
    resultsHeadline: document.getElementById("results-headline"),
    exportRow: document.getElementById("export-row"),
    notices: document.getElementById("notices"),
    cards: document.getElementById("cards"),
  };

  const state = {
    connection: null, // form values incl. password (memory only)
    schema: null,
    databases: [],
    selectedDb: null,
    totalInDb: 0,
    briefs: [],
    selected: new Set(),
    scanId: null,
    eventSource: null,
    lastSeq: 0,
    statuses: new Map(), // name -> {state, step, normalForm, error, secs}
    cards: new Map(), // name -> full table result
    notices: [],
    counts: null,
    scanStatus: null, // running | completed | stopped | failed
    estimate: null,
    t0: 0,
    elapsedTimer: null,
    stopped: false,
  };

  // ---------- tiny fetch wrapper: every failure becomes a visible error

  async function apiFetch(path, options) {
    let resp;
    try {
      resp = await fetch(path, options);
    } catch (e) {
      showError({ code: "BACKEND_UNREACHABLE",
        message: "Cannot reach the tool's backend.",
        hint: "Check the backend is running on 127.0.0.1:8770.",
        step: "api", scan_id: state.scanId || "-" });
      throw e;
    }
    let data = null;
    try {
      data = await resp.json();
    } catch (e) { /* non-JSON: handled below */ }
    if (!resp.ok) {
      const err = (data && data.error) || {
        code: "INTERNAL_ERROR", message: `Request failed (${resp.status}).`,
        hint: "", step: "api", scan_id: state.scanId || "-" };
      showError(err);
      const tagged = new Error(err.message);
      tagged.code = err.code;
      throw tagged;
    }
    return data;
  }

  function showError(err) {
    const f = logic.formatError(err);
    els.errorPanel.innerHTML = "";
    const b = document.createElement("b");
    b.textContent = f.title;
    els.errorPanel.appendChild(b);
    if (f.detail) {
      const p = document.createElement("div");
      p.className = "mono";
      p.textContent = f.detail;
      els.errorPanel.appendChild(p);
    }
    els.errorPanel.hidden = false;
    els.errorPanel.scrollIntoView({ block: "nearest" });
  }

  function clearError() {
    els.errorPanel.hidden = true;
    els.errorPanel.innerHTML = "";
  }

  function showStreamBanner(text) {
    els.streamBanner.textContent = text;
    els.streamBanner.hidden = false;
  }

  function hideStreamBanner() {
    els.streamBanner.hidden = true;
    els.streamBanner.textContent = "";
  }

  // ---------- per-engine form fields

  function currentDbType() {
    const checked = els.form.querySelector('input[name="db_type"]:checked');
    return checked ? checked.value : "mysql";
  }

  function updateEngineFields() {
    const t = currentDbType();
    els.fieldService.hidden = t !== "oracle";
    els.fieldSchema.hidden = t !== "postgresql";
    els.port.placeholder = logic.DEFAULT_PORTS[t] || "";
    if (t === "postgresql") {
      els.schema.placeholder = "public";
    }
  }

  els.form.querySelectorAll('input[name="db_type"]').forEach((radio) => {
    radio.addEventListener("change", updateEngineFields);
  });
  updateEngineFields();

  function lockForm(locked) {
    els.form.querySelectorAll("input, button").forEach((el) => {
      if (el.id !== "change-conn-btn") el.disabled = locked;
    });
    els.changeConnBtn.hidden = !locked;
  }

  els.changeConnBtn.addEventListener("click", () => {
    lockForm(false);
    els.dbSection.hidden = true;
    els.pickerSection.hidden = true;
  });

  function readForm() {
    return {
      dbType: currentDbType(),
      host: els.host.value,
      port: els.port.value,
      username: els.username.value,
      password: els.password.value,
      serviceName: els.serviceName.value,
      schema: els.schema.value,
      ssl: els.ssl.checked,
    };
  }

  function clearFieldErrors() {
    ["err-host", "err-port", "err-service-name",
      "err-schema"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.hidden = true;
    });
  }

  // ---------- Connect -> database picker -> table picker

  els.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearError();
    clearFieldErrors();
    const { values, errors } = logic.validateConnect(readForm());
    if (errors) {
      for (const [id, msg] of Object.entries(errors)) {
        const el = document.getElementById(id);
        if (el) {
          el.textContent = msg;
          el.hidden = false;
        }
      }
      return;
    }
    els.connectBtn.disabled = true;
    try {
      const data = await apiFetch("/api/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(logic.buildConnectPayload({
          ...readForm(), ...values })),
      });
      state.connection = { ...readForm(), ...values };
      state.databases = data.databases || [];
      state.selectedDb = state.databases.length === 1
        ? state.databases[0] : null;
      lockForm(true);
      renderDbPicker();
      els.dbSection.hidden = false;
      els.pickerSection.hidden = true;
      els.dbSection.scrollIntoView();
    } catch (e) { /* shown by apiFetch */ } finally {
      els.connectBtn.disabled = false;
    }
  });

  function renderDbPicker() {
    els.dbList.innerHTML = "";
    const label = state.connection.dbType === "oracle" ? "schema" : "database";
    for (const name of state.databases) {
      const li = document.createElement("li");
      li.className = "picker-row";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = state.selectedDb === name;
      box.setAttribute("aria-label", `Select ${label} ${name}`);
      box.addEventListener("change", () => {
        // One database per run: ticking one unticks the rest.
        state.selectedDb = box.checked ? name : null;
        renderDbPicker();
      });
      const nm = document.createElement("span");
      nm.className = "picker-name";
      nm.textContent = name;
      li.appendChild(box);
      li.appendChild(nm);
      els.dbList.appendChild(li);
    }
    els.listTablesBtn.disabled = !state.selectedDb;
  }

  els.listTablesBtn.addEventListener("click", async () => {
    clearError();
    if (!state.selectedDb) return;
    els.listTablesBtn.disabled = true;
    const isOracle = state.connection.dbType === "oracle";
    try {
      const data = await apiFetch("/api/connect/tables", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(logic.buildConnectPayload(state.connection, {
          database: isOracle ? "" : state.selectedDb,
          schema: isOracle ? state.selectedDb
            : (state.connection.schema || null) })),
      });
      state.schema = data.schema || null;
      state.totalInDb = data.tables.length;
      state.briefs = data.tables;
      state.selected = new Set(data.tables.map((t) => t.name));
      renderPicker();
      els.pickerSection.hidden = false;
      els.pickerSection.scrollIntoView();
    } catch (e) { /* shown by apiFetch */ } finally {
      els.listTablesBtn.disabled = !state.selectedDb;
    }
  });

  function renderPicker() {
    const tables = logic.filterTables(state.briefs, els.tableFilter.value);
    els.tableList.innerHTML = "";
    for (const t of tables) {
      const li = document.createElement("li");
      li.className = "picker-row";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = state.selected.has(t.name);
      box.setAttribute("aria-label", `Select ${t.name}`);
      box.addEventListener("change", () => {
        if (box.checked) state.selected.add(t.name);
        else state.selected.delete(t.name);
        updatePickerCounter();
      });
      const name = document.createElement("span");
      name.className = "picker-name";
      name.textContent = t.name;
      const meta = document.createElement("span");
      meta.className = "picker-meta";
      meta.textContent = `${t.columns} cols · PK ${t.has_pk ? "yes" : "no"} · ${t.fks} FK`;
      li.appendChild(box);
      li.appendChild(name);
      li.appendChild(meta);
      els.tableList.appendChild(li);
    }
    updatePickerCounter();
  }

  function updatePickerCounter() {
    els.pickerCounter.textContent =
      `${state.selected.size} of ${state.briefs.length} selected`;
    els.scanBtn.disabled = state.selected.size === 0;
  }

  els.selectAllBtn.addEventListener("click", () => {
    state.briefs.forEach((t) => state.selected.add(t.name));
    renderPicker();
  });
  els.selectNoneBtn.addEventListener("click", () => {
    state.selected.clear();
    renderPicker();
  });
  els.tableFilter.addEventListener("input", renderPicker);

  // ---------- scan lifecycle

  els.scanBtn.addEventListener("click", async () => {
    clearError();
    hideStreamBanner();
    if (state.selected.size === 0) return;
    els.scanBtn.disabled = true;
    const isOracle = state.connection.dbType === "oracle";
    try {
      const data = await apiFetch("/api/scan/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(logic.buildConnectPayload(state.connection, {
          database: isOracle ? "" : state.selectedDb,
          schema: isOracle ? state.selectedDb
            : (state.connection.schema || null),
          tables: [...state.selected] })),
      });
      enterScan(data.scan_id, [...state.selected], true);
    } catch (e) { /* shown */ } finally {
      els.scanBtn.disabled = state.selected.size === 0;
    }
  });

  els.stopScanBtn.addEventListener("click", async () => {
    if (!state.scanId) return;
    els.stopScanBtn.disabled = true;
    els.stopScanBtn.textContent = "Stopping…";
    try {
      await apiFetch(`/api/scan/${state.scanId}/stop`, { method: "POST" });
      showStreamBanner("Stopping… the current table finishes first; partial results are kept.");
    } catch (e) { /* shown */
      els.stopScanBtn.disabled = false;
      els.stopScanBtn.textContent = "Stop scan";
    }
  });

  function enterScan(scanId, selected, fresh) {
    closeStream();
    state.scanId = scanId;
    state.lastSeq = 0;
    state.statuses = new Map(selected.map((n) => [n, { state: "queued" }]));
    state.cards = new Map();
    state.notices = [];
    state.counts = null;
    state.scanStatus = "running";
    state.estimate = null;
    state.stopped = false;
    state.t0 = Date.now();
    els.progressSection.hidden = false;
    els.resultsSection.hidden = true;
    els.stopScanBtn.hidden = false;
    els.stopScanBtn.disabled = false;
    els.stopScanBtn.textContent = "Stop scan";
    startElapsedTimer();
    openStream(scanId, 0);
    renderAll();
  }

  function closeStream() {
    if (state.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
    }
  }

  function openStream(scanId, since) {
    closeStream();
    const src = new EventSource(`/api/scan/${scanId}/progress?since=${since}`);
    state.eventSource = src;
    const onEvent = (name) => (e) => handleStreamEvent(name, e);
    ["scan_started", "estimate", "table_started", "table_step",
      "table_done", "table_error", "scan_done"].forEach((name) => {
      src.addEventListener(name, onEvent(name));
    });
    src.onerror = () => {
      // The scan keeps running on the server: resync from partial
      // results, then reconnect the stream from the last seen seq.
      closeStream();
      showStreamBanner("Live progress connection was lost; the scan may still be running. Re-syncing…");
      resyncAndReconnect(scanId);
    };
  }

  async function resyncAndReconnect(scanId) {
    try {
      const partial = await apiFetch(`/api/scan/${scanId}/results`);
      ingestResults(partial, true);
      hideStreamBanner();
    } catch (e) { /* backend down: banner from apiFetch stays */ return; }
    if (state.scanStatus === "running") {
      openStream(scanId, state.lastSeq);
    } else {
      hideStreamBanner();
    }
  }

  function handleStreamEvent(name, e) {
    let data = null;
    try {
      data = JSON.parse(e.data);
    } catch (err) { return; }
    if (data && typeof data.seq === "number") {
      state.lastSeq = Math.max(state.lastSeq, data.seq);
    }
    switch (name) {
      case "scan_started":
        state.statuses = new Map(data.tables.map((n) => [n, { state: "queued" }]));
        state.t0 = Date.now();
        break;
      case "estimate":
        state.estimate = data;
        break;
      case "table_started":
        state.statuses.set(data.table, { state: "checking", step: "", secs: 0 });
        break;
      case "table_step":
        state.statuses.set(data.table, { state: "checking", step: data.step, secs: 0 });
        break;
      case "table_done": {
        const st = { state: data.status, normalForm: data.normal_form,
          secs: data.duration_seconds };
        if (data.status === "error" && data.error) st.error = data.error;
        state.statuses.set(data.table, st);
        if (data.result) state.cards.set(data.table, data.result);
        break;
      }
      case "table_error":
        state.statuses.set(data.table, { state: "error", error: data.error });
        break;
      case "scan_done":
        state.scanStatus = data.status;
        state.counts = data.counts || state.counts;
        state.stopped = data.status === "stopped";
        closeStream();
        stopElapsedTimer();
        hideStreamBanner();
        refreshLastScan();
        break;
      default:
        break;
    }
    renderAll();
  }

  // ---------- rendering (full re-render: idempotent, resync-safe)

  function currentCounts() {
    const counts = { faulted: 0, suspected: 0, insufficient_data: 0,
      clean: 0, error: 0, not_run: 0 };
    for (const st of state.statuses.values()) {
      if (st.state === "checking" || st.state === "queued") continue;
      if (st.state in counts) counts[st.state] += 1;
    }
    return counts;
  }

  function renderAll() {
    renderTimeline();
    renderStatusList();
    renderResults();
  }

  function startElapsedTimer() {
    stopElapsedTimer();
    state.elapsedTimer = setInterval(() => {
      const secs = (Date.now() - state.t0) / 1000;
      const done = [...state.statuses.values()]
        .filter((s) => s.state !== "queued" && s.state !== "checking").length;
      const est = state.estimate || {};
      els.progressLine.textContent = logic.formatTimeline({
        done, selected: state.statuses.size, elapsedSeconds: secs,
        remainingSeconds: est.remaining_seconds ?? null,
        totalSeconds: est.total_seconds ?? null,
        basis: est.basis || null, stopped: state.stopped });
    }, 1000);
  }

  function stopElapsedTimer() {
    if (state.elapsedTimer) {
      clearInterval(state.elapsedTimer);
      state.elapsedTimer = null;
    }
  }

  function renderTimeline() {
    // Filled by the 1s ticker; show a first paint immediately.
    const secs = (Date.now() - state.t0) / 1000;
    const done = [...state.statuses.values()]
      .filter((s) => s.state !== "queued" && s.state !== "checking").length;
    const est = state.estimate || {};
    els.progressLine.textContent = logic.formatTimeline({
      done, selected: state.statuses.size, elapsedSeconds: secs,
      remainingSeconds: est.remaining_seconds ?? null,
      totalSeconds: est.total_seconds ?? null,
      basis: est.basis || null, stopped: state.stopped });
    els.stopScanBtn.hidden = state.scanStatus !== "running";
  }

  function renderStatusList() {
    els.statusList.innerHTML = "";
    for (const [name, st] of state.statuses) {
      const li = document.createElement("li");
      li.className = "status-row";
      const [text, cls] = logic.statusBadge(st.state, st.normalForm);
      const nm = document.createElement("span");
      nm.className = "status-name";
      nm.textContent = name;
      const badge = document.createElement("span");
      badge.className = `badge ${cls}`;
      badge.textContent = text;
      const detail = document.createElement("span");
      detail.className = "status-detail mono";
      if (st.state === "checking") {
        detail.textContent = st.step || "checking";
      } else if (st.state === "error" && st.error) {
        detail.textContent = st.error.code || "error";
      } else if (st.normalForm) {
        detail.textContent = st.normalForm;
      }
      li.appendChild(nm);
      li.appendChild(badge);
      li.appendChild(detail);
      if ((st.state === "faulted" || st.state === "suspected") &&
          state.cards.has(name)) {
        li.classList.add("done");
        li.addEventListener("click", () => {
          const card = document.getElementById(`card-${name}`);
          if (card) card.scrollIntoView({ block: "start" });
        });
      }
      els.statusList.appendChild(li);
    }
  }

  function finishedCount() {
    let n = 0;
    for (const st of state.statuses.values()) {
      if (st.state !== "queued" && st.state !== "checking") n += 1;
    }
    return n;
  }

  function renderResults() {
    const counts = state.counts || currentCounts();
    const selected = state.statuses.size;
    const notSelected = Math.max(0, state.totalInDb - selected);
    els.resultsSection.hidden = state.cards.size === 0 && state.scanStatus === "running";
    if (els.resultsSection.hidden) return;
    els.resultsHeadline.textContent =
      logic.formatHeadline(counts, finishedCount(), selected, notSelected);
    // Tables that could not be analysed.
    const errs = [...state.statuses.entries()]
      .filter(([, st]) => st.state === "error");
    els.notices.innerHTML = "";
    els.notices.hidden = errs.length === 0 && state.notices.length === 0;
    if (!els.notices.hidden) {
      const title = document.createElement("b");
      title.textContent = "Tables that could not be analysed:";
      els.notices.appendChild(title);
      const ul = document.createElement("ul");
      for (const [name, st] of errs) {
        const li = document.createElement("li");
        li.className = "mono";
        li.textContent = `${name} — ${(st.error && st.error.code) || "error"}`;
        ul.appendChild(li);
      }
      for (const n of state.notices) {
        const li = document.createElement("li");
        li.className = "mono";
        li.textContent = `${n.table} — ${n.code || "error"}`;
        ul.appendChild(li);
      }
      els.notices.appendChild(ul);
    }
    // Cards, in the order tables finished.
    els.cards.innerHTML = "";
    for (const [name, full] of state.cards) {
      els.cards.appendChild(renderSimpleCard(name, full));
    }
  }

  // Simple Step-10 card. Step 11 replaces this renderer with full SVG
  // graph cards (same container, same `card-<name>` ids).
  function renderSimpleCard(name, full) {
    const st = state.statuses.get(name) || {};
    const article = document.createElement("article");
    article.className = "card-simple" + (st.state === "suspected" ? " suspected" : "");
    article.id = `card-${name}`;
    const h = document.createElement("h3");
    h.textContent = name;
    article.appendChild(h);
    const nf = document.createElement("span");
    nf.className = "badge badge-nf";
    nf.textContent = (st.state === "suspected" ? "possibly " : "") + (full.normal_form || "?");
    article.appendChild(nf);
    const ul = document.createElement("ul");
    ul.className = "fd-list mono";
    for (const f of full.fds || []) {
      const li = document.createElement("li");
      li.textContent = `${f.determinant.join(", ")} → ${f.dependent} (score ${f.confidence})`;
      ul.appendChild(li);
    }
    article.appendChild(ul);
    for (const v of full.violations || []) {
      const p = document.createElement("p");
      p.className = "reason";
      p.textContent = v.reason || "";
      article.appendChild(p);
    }
    if (full.recommendation) {
      const p = document.createElement("p");
      p.className = "recommendation";
      p.textContent = full.recommendation;
      article.appendChild(p);
    }
    return article;
  }

  /** Fold a full/partial results payload into state, then render. */
  function ingestResults(payload, fromResync) {
    const scan = payload.scan || {};
    const selected = scan.selected_tables || [...state.selected];
    if (!fromResync || state.statuses.size === 0) {
      state.statuses = new Map(selected.map((n) => [n, { state: "queued" }]));
    }
    for (const s of payload.table_summary || []) {
      if (s.status === "faulted" || s.status === "suspected") continue;
      state.statuses.set(s.name, { state: s.status,
        normalForm: s.normal_form || undefined,
        secs: s.duration_seconds || 0 });
    }
    state.cards = new Map();
    for (const t of payload.tables || []) {
      state.cards.set(t.name, t);
      state.statuses.set(t.name, { state: t.status,
        normalForm: t.normal_form || undefined, secs: 0 });
    }
    state.notices = payload.notices || [];
    const counts = { faulted: 0, suspected: 0, insufficient_data: 0,
      clean: 0, error: 0, not_run: 0 };
    for (const s of payload.table_summary || []) {
      if (s.status in counts) counts[s.status] += 1;
    }
    state.counts = counts;
    state.scanStatus = scan.status || state.scanStatus;
    state.stopped = scan.status === "stopped";
    if (scan.status && scan.status !== "running") {
      closeStream();
      stopElapsedTimer();
    }
    renderAll();
  }

  // ---------- last scan panel

  async function refreshLastScan() {
    let data = null;
    try {
      const resp = await fetch("/api/scan/last");
      if (!resp.ok) {
        els.lastScanSection.hidden = true;
        return;
      }
      data = await resp.json();
    } catch (e) {
      els.lastScanSection.hidden = true;
      return;
    }
    const scan = data.scan || {};
    els.lastScanDb.textContent = scan.database || "—";
    els.lastScanEngine.textContent = scan.db_type || "—";
    els.lastScanDate.textContent = logic.formatScanTime(scan.started_at);
    const c = scan.counts || {};
    els.lastScanTables.textContent = `${scan.selected_tables ? scan.selected_tables.length : 0} tables`;
    els.lastScanDuration.textContent = `${scan.duration_seconds ?? "—"} s`;
    els.lastScanStatus.textContent = scan.status || "—";
    const sel = scan.selected_tables ? scan.selected_tables.length : 0;
    els.lastScanHeadline.textContent = logic.formatHeadline(c,
      sel - (c.not_run || 0), sel, c.not_selected || 0);
    els.lastScanSection.hidden = false;
  }

  els.viewLastBtn.addEventListener("click", async () => {
    clearError();
    try {
      const data = await apiFetch("/api/scan/last");
      state.totalInDb = (data.scan.counts.total_in_db || 0);
      ingestResults(data, false);
      state.scanId = data.scan.id || null;
      els.resultsSection.hidden = false;
      els.resultsSection.scrollIntoView();
    } catch (e) { /* shown */ }
  });

  // ---------- reload re-attach + global guards

  (async () => {
    refreshLastScan();
    try {
      const resp = await fetch("/api/scan/current");
      if (!resp.ok) return;
      const cur = await resp.json();
      const partial = await apiFetch(`/api/scan/${cur.scan_id}/results`);
      state.totalInDb = partial.scan.selected_tables.length;
      enterScan(cur.scan_id, partial.scan.selected_tables, false);
      ingestResults(partial, true);
    } catch (e) { /* no running scan or backend down */ }
  })();

  window.addEventListener("error", (event) => {
    showStreamBanner(`Something went wrong: ${event.message || "unknown error"}`);
  });
  window.addEventListener("unhandledrejection", (event) => {
    showStreamBanner(`Something went wrong: ${(event.reason && event.reason.message) || "unknown error"}`);
  });
}
