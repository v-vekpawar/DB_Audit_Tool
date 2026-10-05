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

/** Renders an ISO timestamp in local time. Garbage passes through. Pure. */
function formatScanTime(stamp) {
  if (!stamp) return "—";
  const dated = new Date(String(stamp));
  if (Number.isNaN(dated.getTime())) return String(stamp);
  return dated.toLocaleString();
}

/** XML-escapes a value for SVG text. Pure. */
function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Greedy word wrap to ~maxChars columns. Pure — Node-tested. */
function wrapText(s, maxChars) {
  const words = String(s || "").split(/\s+/).filter(Boolean);
  const lines = [];
  let line = "";
  for (const w of words) {
    if ((line + " " + w).trim().length > maxChars && line) {
      lines.push(line);
      line = w;
    } else {
      line = (line + " " + w).trim();
    }
  }
  if (line) lines.push(line);
  return lines.length > 0 ? lines : [""];
}

const SVG_COLORS = {
  ink: "#1c211d",
  muted: "#5c655e",
  border: "#d7dbd2",
  accent: "#2f4b8f",
  accentSoft: "#e3e9f6",
  danger: "#a3291f",
  dangerSoft: "#f6e4e2",
  warning: "#8a5a00",
  warningSoft: "#f6ecd9",
};
const SVG_SANS = "system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif";
const SVG_MONO = "ui-monospace, Menlo, Consolas, monospace";

/**
 * Builds the full graph-like faulted-table card as an SVG string.
 * `full`: table result (columns, violations, fds, recommendation, sampled).
 * `status`: "faulted" | "suspected" (suspected renders dimmed).
 * Pure — Node-tested against a fixture covering every violation kind.
 */
function buildCardSVG(name, full, status) {
  const C = SVG_COLORS;
  const W = 760;
  const pad = 16;
  const rowH = 24;
  const suspected = status === "suspected";
  const nf = (suspected ? "possibly " : "") + (full.normal_form || "?");
  const badgeColor = suspected ? C.warning : C.danger;
  const badgeSoft = suspected ? C.warningSoft : C.dangerSoft;

  const detCols = new Set();
  const depCols = new Set();
  const equivCols = new Set();
  const detGroups = []; // {key, det:[cols], deps:[cols], equiv}
  for (const f of full.fds || []) {
    let g = detGroups.find((x) => x.key === f.determinant.join("\0"));
    if (!g) {
      g = { key: f.determinant.join("\0"), det: f.determinant, deps: [],
        equiv: false };
      detGroups.push(g);
    }
    if (!g.deps.includes(f.dependent)) g.deps.push(f.dependent);
    g.equiv = g.equiv || !!f.equivalent;
    for (const d of f.determinant) detCols.add(d);
    depCols.add(f.dependent);
    if (f.equivalent) {
      for (const d of f.determinant) equivCols.add(d);
      equivCols.add(f.dependent);
    }
  }

  let h = 0;
  const parts = [];
  const open = suspected
    ? `<svg width="${W}" viewBox="0 0 ${W} PLACEHOLDER" font-family="${SVG_SANS}" role="img" aria-label="${esc(name)} card"><g opacity="0.72">`
    : `<svg width="${W}" viewBox="0 0 ${W} PLACEHOLDER" font-family="${SVG_SANS}" role="img" aria-label="${esc(name)} card"><g>`;
  parts.push(open);
  h += 12;
  // Header.
  parts.push(`<text x="${pad}" y="${h + 20}" font-size="16" font-weight="600" font-family="${SVG_MONO}" fill="${C.ink}">${esc(name)}</text>`);
  const badgeW = 12 + nf.length * 7.2;
  parts.push(`<rect x="${W - pad - badgeW}" y="${h + 4}" width="${badgeW}" height="24" rx="12" fill="${badgeSoft}"/>`);
  parts.push(`<text x="${W - pad - badgeW / 2}" y="${h + 21}" font-size="12" font-weight="600" text-anchor="middle" fill="${badgeColor}">${esc(nf)}</text>`);
  h += 40;
  if (full.sampled) {
    parts.push(`<text x="${pad}" y="${h + 4}" font-size="12" fill="${C.muted}">analysed first 50,000 rows in key order (sampled)</text>`);
    h += 20;
  }
  // Column rows.
  const y0 = h + 8;
  const cy = (i) => y0 + i * rowH + 15;
  const cols = full.columns || [];
  cols.forEach((col, i) => {
    const y = y0 + i * rowH;
    if (depCols.has(col.name)) {
      parts.push(`<rect x="8" y="${y}" width="${W - 16}" height="22" rx="4" fill="${C.dangerSoft}"/>`);
    }
    let x = 76;
    if (col.pk) {
      parts.push(`<rect x="${x}" y="${y + 3}" width="26" height="16" rx="8" fill="${C.accent}"/>`);
      parts.push(`<text x="${x + 13}" y="${y + 16}" font-size="10" font-weight="700" text-anchor="middle" fill="#ffffff">PK</text>`);
      x += 30;
    }
    if (col.fk) {
      parts.push(`<rect x="${x}" y="${y + 3}" width="26" height="16" rx="8" fill="none" stroke="${C.accent}" stroke-width="1.5"/>`);
      parts.push(`<text x="${x + 13}" y="${y + 16}" font-size="10" font-weight="700" text-anchor="middle" fill="${C.accent}">FK</text>`);
      x += 30;
    }
    const bold = detCols.has(col.name) ? ' font-weight="700"' : "";
    parts.push(`<text x="${x}" y="${y + 16}" font-size="13" font-family="${SVG_MONO}" fill="${C.ink}"${bold}>${esc(col.name)}</text>`);
    let tx = x + col.name.length * 7.8 + 8;
    if (detCols.has(col.name)) {
      parts.push(`<rect x="${tx}" y="${y + 3}" width="32" height="16" rx="8" fill="none" stroke="${C.muted}" stroke-width="1"/>`);
      parts.push(`<text x="${tx + 16}" y="${y + 16}" font-size="10" text-anchor="middle" fill="${C.muted}">DET</text>`);
      tx += 36;
    }
    if (equivCols.has(col.name)) {
      parts.push(`<rect x="${tx}" y="${y + 3}" width="28" height="16" rx="8" fill="none" stroke="${C.muted}" stroke-width="1"/>`);
      parts.push(`<text x="${tx + 14}" y="${y + 16}" font-size="10" text-anchor="middle" fill="${C.muted}">EQ</text>`);
    }
  });
  const colBlockH = cols.length * rowH;
  // Arrows in the gutter (x 8..64): one spine per determinant.
  detGroups.forEach((g, gi) => {
    const sx = 52 - Math.min(gi, 3) * 13;
    const involved = [];
    cols.forEach((col, i) => {
      if (g.det.includes(col.name) || g.deps.includes(col.name)) involved.push(i);
    });
    if (involved.length < 2) return;
    const top = cy(involved[0]);
    const bottom = cy(involved[involved.length - 1]);
    const color = C.danger;
    const markers = g.equiv
      ? ` marker-start="url(#arrEq)" marker-end="url(#arrEq)"`
      : ` marker-end="url(#arr)"`;
    parts.push(`<line x1="${sx}" y1="${top}" x2="${sx}" y2="${bottom}" stroke="${color}" stroke-width="2"${markers}/>`);
    cols.forEach((col, i) => {
      if (g.det.includes(col.name)) {
        parts.push(`<line x1="${sx}" y1="${cy(i)}" x2="68" y2="${cy(i)}" stroke="${C.accent}" stroke-width="2"/>`);
      } else if (g.deps.includes(col.name)) {
        parts.push(`<line x1="${sx}" y1="${cy(i)}" x2="68" y2="${cy(i)}" stroke="${color}" stroke-width="1.5"/>`);
      }
    });
  });
  h = y0 + colBlockH + 12;
  // Evidence boxes, one per FD.
  for (const f of full.fds || []) {
    const ev = f.evidence || {};
    const lines = [];
    lines.push(`Confidence Score: ${f.confidence} (chance of coincidence about ${((f.coincidence && f.coincidence.text) || "—")})`);
    lines.push(`${ev.distinct_det ?? "—"} distinct values · ${ev.repeated_rows ?? "—"} repeated rows · ${ev.candidates_tested ?? "—"} candidates tested`);
    if ((f.exceptions || 0) > 0) {
      lines.push(`${f.exceptions} exception rows (marked below) — holds with exceptions`);
    }
    const cross = ev.cross_table;
    if (cross && cross.supported) {
      const pct = cross.match_rate != null ? (cross.match_rate * 100).toFixed(1) : "—";
      lines.push(`supported by ${cross.ref_table}.${cross.ref_column}: ${pct}% match`);
    }
    if (ev.naming) {
      lines.push(`naming affinity: ${(f.determinant || []).join(", ")} / ${f.dependent}`);
    }
    for (const ex of (ev.examples || []).slice(0, 3)) {
      const detVals = (ex.det || []).map((v) => String(v).slice(0, 24)).join(", ");
      lines.push(`e.g. ${detVals} → ${String(ex.dep).slice(0, 24)} in ${ex.rows} rows${ex.exception ? " (exception)" : ""}`);
    }
    const boxH = 12 + (lines.length + 1) * 17 + 10;
    parts.push(`<rect x="${pad}" y="${h}" width="${W - pad * 2}" height="${boxH}" rx="4" fill="#ffffff" stroke="${C.border}" stroke-width="1"/>`);
    parts.push(`<text x="${pad + 10}" y="${h + 19}" font-size="12" font-weight="600" fill="${C.ink}">${esc((f.determinant || []).join(", "))} → ${esc(f.dependent)}${f.equivalent ? " (equivalent)" : ""}</text>`);
    lines.forEach((ln, li) => {
      parts.push(`<text x="${pad + 10}" y="${h + 19 + (li + 1) * 17}" font-size="12" fill="${C.ink}">${esc(ln)}</text>`);
    });
    h += boxH + 10;
  }
  // Reason + recommendation boxes.
  const boxes = [];
  for (const v of full.violations || []) {
    if (v.reason) boxes.push({ title: "Why this fails " + v.form + ": ", body: v.reason, fill: C.warningSoft });
  }
  if (full.recommendation) {
    boxes.push({ title: "Recommendation: ", body: full.recommendation, fill: C.accentSoft });
  }
  for (const b of boxes) {
    const lines = wrapText(b.title + b.body, 100);
    const boxH = 12 + lines.length * 17 + 10;
    parts.push(`<rect x="${pad}" y="${h}" width="${W - pad * 2}" height="${boxH}" rx="4" fill="${b.fill}"/>`);
    lines.forEach((ln, li) => {
      parts.push(`<text x="${pad + 10}" y="${h + 19 + li * 17}" font-size="12" fill="${C.ink}">${esc(ln)}</text>`);
    });
    h += boxH + 10;
  }
  h += 4;
  parts.push(`<defs><marker id="arr" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6" fill="none" stroke="${C.danger}" stroke-width="1.5"/></marker><marker id="arrEq" markerWidth="8" markerHeight="8" refX="3" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 M6,0 L0,3 L6,6" fill="none" stroke="${C.danger}" stroke-width="1.2"/></marker></defs>`);
  parts.push(`</g></svg>`);
  return parts.join("\n").replace("PLACEHOLDER", String(h));
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
    case "not_run": return ["Scan stopped", "badge-notrun"];
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
  esc,
  wrapText,
  buildCardSVG,
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
    connSummary: document.getElementById("conn-summary"),
    connSummaryText: document.getElementById("conn-summary-text"),
    dbSection: document.getElementById("db-section"),
    dbList: document.getElementById("db-list"),
    listTablesBtn: document.getElementById("list-tables-btn"),
    pickerSection: document.getElementById("picker-section"),
    tableList: document.getElementById("table-list"),
    tableFilter: document.getElementById("table-filter"),
    pickerCounter: document.getElementById("picker-counter"),
    selectAllBtn: document.getElementById("select-all-btn"),
    selectNoneBtn: document.getElementById("select-none-btn"),
    toggleTablesBtn: document.getElementById("toggle-tables-btn"),
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
    exportPdfBtn: document.getElementById("export-pdf-btn"),
    exportStatus: document.getElementById("export-status"),
    notices: document.getElementById("notices"),
    cardsHint: document.getElementById("cards-hint"),
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
    openCard: null, // name with its card expanded below its row
    autoFollow: true, // follow the latest finished card during live scans
    lastExpanded: false, // last-scan details unfolded
    notices: [],
    counts: null,
    scanStatus: null, // running | completed | stopped | failed
    estimate: null,
    t0: 0,
    endElapsed: null,
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
  }

  els.form.querySelectorAll('input[name="db_type"]').forEach((radio) => {
    radio.addEventListener("change", updateEngineFields);
  });
  updateEngineFields();

  function lockForm(locked) {
    els.form.querySelectorAll("input, button, fieldset").forEach((el) => {
      if (el.id !== "change-conn-btn") el.disabled = locked;
    });
    els.form.querySelectorAll("fieldset.field, .field, #connect-btn").forEach((el) => {
      el.hidden = locked;
    });
    els.changeConnBtn.hidden = !locked;
    els.connSummary.hidden = !locked;
    if (locked) updateConnSummary();
  }

  function updateConnSummary() {
    const c = state.connection;
    if (!c) return;
    const db = c.dbType === "oracle" ? (state.selectedDb || c.serviceName)
      : (state.selectedDb || c.database || "");
    els.connSummaryText.textContent =
      `Connected to ${db} (${c.dbType}) as ${c.username || "—"} @ ${c.host}`;
  }

  els.changeConnBtn.addEventListener("click", () => {
    resetScanState();
    lockForm(false);
    updateEngineFields();
    els.dbSection.hidden = true;
    els.pickerSection.hidden = true;
  });

  // Clears every trace of the previous run: progress, results, cards,
  // timeline, banners and selection. Form field values stay as typed.
  function resetScanState() {
    closeStream();
    stopElapsedTimer();
    state.scanId = null;
    state.lastSeq = 0;
    state.statuses = new Map();
    state.cards = new Map();
    state.openCard = null;
    state.autoFollow = true;
    state.notices = [];
    state.counts = null;
    state.scanStatus = null;
    state.estimate = null;
    state.stopped = false;
    state.t0 = 0;
    els.progressSection.hidden = true;
    els.resultsSection.hidden = true;
    els.progressLine.textContent = "";
    els.statusList.innerHTML = "";
    els.notices.innerHTML = "";
    els.notices.hidden = true;
    els.stopScanBtn.hidden = false;
    els.stopScanBtn.disabled = false;
    els.stopScanBtn.textContent = "Stop scan";
    hideStreamBanner();
    clearError();
    state.lastExpanded = false;
    els.viewLastBtn.textContent = "View Last Results";
  }

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
    ["err-host", "err-port", "err-service-name"].forEach((id) => {
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
            : (state.connection.schema || null),
        })),
      });
      state.schema = data.schema || null;
      state.totalInDb = data.tables.length;
      state.briefs = data.tables;
      state.selected = new Set(data.tables.map((t) => t.name));
      updateConnSummary();
      renderPicker();
      els.tableList.hidden = false;
      els.toggleTablesBtn.textContent = "Hide tables";
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
    els.scanBtn.disabled = state.scanStatus === "running"
      || state.selected.size === 0;
  }

  els.selectAllBtn.addEventListener("click", () => {
    state.briefs.forEach((t) => state.selected.add(t.name));
    renderPicker();
  });
  els.selectNoneBtn.addEventListener("click", () => {
    state.selected.clear();
    renderPicker();
  });
  els.toggleTablesBtn.addEventListener("click", () => {
    els.tableList.hidden = !els.tableList.hidden;
    els.toggleTablesBtn.textContent = els.tableList.hidden ? "Show tables" : "Hide tables";
  });
  els.tableFilter.addEventListener("input", renderPicker);

  // ---------- scan lifecycle

  els.scanBtn.addEventListener("click", async () => {
    clearError();
    hideStreamBanner();
    if (state.selected.size === 0) return;
    if (!state.connection) {
      showError({ code: "VALIDATION_ERROR",
        message: "Connect to a database first.",
        hint: "Fill the connection form and click Connect.",
        step: "scan", scan_id: "-" });
      return;
    }
    if (state.scanStatus === "running") return;
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
      els.scanBtn.disabled = state.scanStatus === "running"
        || state.selected.size === 0;
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
    state.openCard = null;
    state.autoFollow = true;
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
        if (data.result) {
          state.cards.set(data.table, data.result);
          if (state.autoFollow) state.openCard = data.table;
        }
        break;
      }
      case "table_error":
        state.statuses.set(data.table, { state: "error", error: data.error });
        break;
      case "scan_done":
        state.scanStatus = data.status;
        state.counts = data.counts || state.counts;
        state.stopped = data.status === "stopped";
        state.endElapsed = (Date.now() - state.t0) / 1000;
        els.scanBtn.disabled = state.selected.size === 0;
        if (data.status === "stopped") {
          for (const [name, st] of state.statuses) {
            if (st.state === "queued" || st.state === "checking") {
              state.statuses.set(name, { state: "not_run" });
            }
          }
        }
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

  function timelineText() {
    const terminal = state.scanStatus === "completed" ||
      state.scanStatus === "stopped" || state.scanStatus === "failed";
    if (terminal) {
      const secs = state.endElapsed ?? (Date.now() - state.t0) / 1000;
      const scope = `Checked ${finishedCount()} of ${state.statuses.size} tables.`;
      if (state.scanStatus === "stopped") {
        return `${scope} Scan stopped — partial results kept`;
      }
      if (state.scanStatus === "failed") {
        return `${scope} Scan failed after ${logic.formatClock(secs)}`;
      }
      return `${scope} Scan completed in ${logic.formatClock(secs)}`;
    }
    const secs = (Date.now() - state.t0) / 1000;
    const done = finishedCount();
    const est = state.estimate || {};
    return logic.formatTimeline({
      done, selected: state.statuses.size, elapsedSeconds: secs,
      remainingSeconds: est.remaining_seconds ?? null,
      totalSeconds: est.total_seconds ?? null,
      basis: est.basis || null, stopped: state.stopped });
  }

  function startElapsedTimer() {
    stopElapsedTimer();
    state.elapsedTimer = setInterval(() => {
      els.progressLine.textContent = timelineText();
    }, 1000);
  }

  function stopElapsedTimer() {
    if (state.elapsedTimer) {
      clearInterval(state.elapsedTimer);
      state.elapsedTimer = null;
    }
  }

  function renderTimeline() {
    els.progressLine.textContent = timelineText();
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
        if (state.openCard === name) li.classList.add("selected");
        li.addEventListener("click", () => {
          state.openCard = state.openCard === name ? null : name;
          state.autoFollow = false;
          renderAll();
          if (state.openCard === name) {
            const card = document.getElementById(`card-${name}`);
            if (card) card.scrollIntoView({ block: "nearest" });
          }
        });
      }
      els.statusList.appendChild(li);
      if (state.openCard === name && state.cards.has(name)) {
        const row = document.createElement("li");
        row.className = "card-row";
        row.appendChild(renderSimpleCard(name, state.cards.get(name)));
        els.statusList.appendChild(row);
      }
    }
  }

  function finishedCount() {
    // Tables actually scanned (verdict or error). not_run never started,
    // so it must not inflate the "a of b tables scanned" head.
    let n = 0;
    for (const st of state.statuses.values()) {
      if (st.state !== "queued" && st.state !== "checking"
          && st.state !== "not_run") n += 1;
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
    const terminal = state.scanStatus === "completed" || state.scanStatus === "stopped";
    els.exportRow.hidden = !(terminal && state.scanId);
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
    // Accordion: at most one card open, directly below its status row
    // (rendered inside renderStatusList). Clicking its row again hides it.
    const anyCards = state.cards.size > 0;
    const openShown = state.openCard && state.cards.has(state.openCard);
    els.cardsHint.hidden = !(anyCards && !openShown);
  }

  // Full SVG graph card (Step 11). Same container and `card-<name>` ids
  // as the Step-10 placeholder, so status-row click-to-scroll is unchanged.
  function renderSimpleCard(name, full) {
    const st = state.statuses.get(name) || {};
    const article = document.createElement("article");
    article.className = "card-svg" + (st.state === "suspected" ? " suspected" : "");
    article.id = `card-${name}`;
    article.innerHTML = logic.buildCardSVG(name, full, st.state === "suspected" ? "suspected" : "faulted");
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
    state.selectedCard = null;
    state.autoFollow = false;
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
      state.endElapsed = scan.duration_seconds != null
        ? scan.duration_seconds
        : (Date.now() - state.t0) / 1000;
    }
    renderAll();
  }

  // ---------- last scan panel (always visible when data exists).
  // The View button only toggles the *result* below, never this panel.

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
    if (!state.lastExpanded) {
      els.viewLastBtn.textContent = "View Last Results";
    }
  }

  function setLastButton() {
    els.viewLastBtn.textContent = state.lastExpanded ? "Hide result" : "View Last Results";
  }

  els.viewLastBtn.addEventListener("click", async () => {
    if (state.lastExpanded) {
      state.lastExpanded = false;
      setLastButton();
      els.resultsSection.hidden = true;
      return;
    }
    clearError();
    try {
      const data = await apiFetch("/api/scan/last");
      state.totalInDb = (data.scan.counts.total_in_db || 0);
      ingestResults(data, false);
      state.scanId = data.scan.id || null;
      state.lastExpanded = true;
      setLastButton();
      els.resultsSection.hidden = false;
      els.resultsSection.scrollIntoView();
    } catch (e) { /* shown */ }
  });

  els.exportPdfBtn.addEventListener("click", async () => {
    clearError();
    if (!state.scanId) return;
    els.exportStatus.textContent = "Generating…";
    try {
      const resp = await fetch(`/api/scan/${state.scanId}/export?format=pdf`);
      if (!resp.ok) {
        const data = await resp.json().catch(() => null);
        showError((data && data.error) || { message: `Export failed (${resp.status}).` });
        els.exportStatus.textContent = "";
        return;
      }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `dbnorm-${state.scanId}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
      els.exportStatus.textContent = "";
    } catch (e) {
      showError({ code: "BACKEND_UNREACHABLE",
        message: "Cannot reach the tool's backend.", step: "export",
        scan_id: state.scanId });
      els.exportStatus.textContent = "";
    }
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
