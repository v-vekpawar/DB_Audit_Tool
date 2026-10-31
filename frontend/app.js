/* ---------------------------------------------------------------------
   Database Normalization Checker — frontend logic.

   Pure helper functions (no DOM) live in the `logic` object and are
   exported via module.exports under Node, so they can be unit-tested
   without a browser. Everything DOM-dependent is wired up inside
   initApp(), which only runs in an actual browser.
   --------------------------------------------------------------------- */

const DEFAULT_PORTS = { mysql: 3306, postgresql: 5432, oracle: 1521, mssql: 1433 };

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
  if (extra && extra.database_count != null) {
    body.database_count = extra.database_count;
  }
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

/** Numeric confidence (0-100) to a High/Medium/Low pill. Pure. */
function confLevel(confidence) {
  const n = Number(confidence);
  if (n >= 80) return "High";
  if (n >= 60) return "Medium";
  return "Low";
}

/** Sanitises a value for use in id/aria-controls attributes. Pure. */
function escId(value) {
  return String(value == null ? "" : value)
    .replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 60) || "t";
}

/** Thousands separator for row counts. Pure. */
function fmtInt(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return String(n == null ? "—" : n);
  return v.toLocaleString("en-US");
}

const STRIP_TITLES = {
  transitive: "Transitive dependency (violates 3NF).",
  partial: "Partial dependency (violates 2NF).",
  bcnf: "Non-key determinant (violates BCNF).",
  "no-key": "No candidate key established (higher forms cannot be confirmed).",
  "unknown-key": "Candidate-key search incomplete.",
  delimited: "Multi-valued cells (below 1NF).",
  repeating: "Repeating group (below 1NF).",
};

/** Bold strip title for a violation. Pure. */
function stripTitle(v) {
  if (v && STRIP_TITLES[v.kind]) return STRIP_TITLES[v.kind];
  return `Violates ${((v && v.form) || "?")}.`;
}

/** Worst violation first: 1NF > 2NF > 3NF > BCNF (same order as recommend). Pure. */
function worstViolation(full) {
  const order = { "1NF": 0, "2NF": 1, "3NF": 2, "BCNF": 3 };
  let worst = null;
  for (const v of (full && full.violations) || []) {
    if (!worst || (order[v.form] ?? 9) < (order[worst.form] ?? 9)) worst = v;
  }
  return worst;
}

/** Primary key columns: backend keys first, PK flags as fallback. Pure. */
function keyColumns(full) {
  if (full && full.keys && full.keys.length > 0 && full.keys[0].columns) {
    return full.keys[0].columns;
  }
  return ((full && full.columns) || []).filter((c) => c.pk).map((c) => c.name);
}

/** The FD behind the worst violation (for the graph + example table). Pure. */
function primaryFD(full, worst) {
  const fds = (full && full.fds) || [];
  if (!worst || !worst.determinant || worst.determinant.length === 0) return null;
  const key = worst.determinant.join("\0");
  return fds.find((f) => (f.determinant || []).join("\0") === key &&
    (worst.dependents || []).includes(f.dependent))
    || fds.find((f) => (f.determinant || []).join("\0") === key)
    || null;
}

function graphNodeW(label) {
  return Math.min(220, Math.max(72, 16 + String(label).length * 7.2));
}

function truncLabel(label, n) {
  const s = String(label == null ? "?" : label);
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

/**
 * Choose graph nodes from explicit FD evidence where possible.
 * For a 3NF/transitive graph, prefer an actual two-FD chain A→B→C.
 * If no such chain exists, fall back to the candidate-key context but mark
 * that first relationship as key-implied rather than presenting it as a
 * discovered FD.
 * Pure.
 */
function dependencyGraphContext(full, worst) {
  const fds = (full && full.fds) || [];
  const det = (worst && worst.determinant) || [];
  const dep = ((worst && worst.dependents) || ["?"])[0];
  if (worst && worst.kind === "transitive" && det.length > 0 && dep) {
    const target = fds.find((f) =>
      (f.determinant || []).join("\0") === det.join("\0") && f.dependent === dep);
    if (target) {
      const second = fds.find((f) =>
        f.dependent === dep &&
        (f.determinant || []).join("\0") !== det.join("\0") &&
        (f.determinant || []).length === 1);
      if (second) {
        const middle = (second.determinant || [])[0];
        const first = fds.find((f) =>
          (f.determinant || []).join("\0") === det.join("\0") &&
          f.dependent === middle);
        if (first) {
          return {
            first: (first.determinant || []).join(", ") || "?",
            middle,
            last: dep,
            firstEdgeLabel: null,
          };
        }
      }
    }

    // Candidate keys functionally determine every attribute. If the
    // discovered FD list does not contain key -> determinant, use that
    // logical key-closure edge for the explanation, but label it clearly as
    // key-implied rather than presenting it as a discovered FD.
    const keys = keyColumns(full) || [];
    if (keys.length > 0 && keys.join("\0") !== det.join("\0") && !keys.includes(dep)) {
      return {
        first: keys.join(", "),
        middle: det.join(", "),
        last: dep,
        firstEdgeLabel: "key-implied",
      };
    }

    return {
      first: det.join(", ") || "?",
      middle: dep || "?",
      last: null,
      compact: true,
      firstEdgeLabel: null,
    };
  }
  return {
    first: (keyColumns(full) || []).join(", ") || "?",
    middle: det.join(", ") || "?",
    last: dep || "?",
    firstEdgeLabel: null,
  };
}

/**
 * Dependency-graph SVG. Graph edges are grounded in explicit FD evidence
 * whenever a chain exists; a fallback key edge is visibly marked
 * "key-implied" rather than being presented as a discovered FD.
 * Pure.
 */
function buildGraphSVG(uid, keyLabel, detLabel, depLabel, opts) {
  const o = opts || {};
  const ink = "#1c211d", danger = "#a3291f", accent = "#2f4b8f";
  if (o.compact) {
    const w1 = graphNodeW(keyLabel), w2 = graphNodeW(detLabel);
    const gap = 34, pad = 4, nh = 32, ny = 4;
    const x1 = pad, x2 = x1 + w1 + gap;
    const W = Math.round(x2 + w2 + pad), H = 92, cy = ny + nh / 2;
    const node = (x, w, label, fill, stroke, color) =>
      `<rect x="${x}" y="${ny}" width="${w}" height="${nh}" rx="4" fill="${fill}" stroke="${stroke}" stroke-width="1.5"/>` +
      `<text x="${x + w / 2}" y="${ny + 20}" text-anchor="middle" font-size="12" font-weight="600" fill="${color}" font-family="ui-monospace, Menlo, Consolas, monospace">${esc(label)}</text>`;
    let out = `<svg class="rc-graph" viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="${esc(keyLabel)} determines ${esc(detLabel)}; dashed arc marks the transitive dependency relative to the candidate key">`;
    out += `<defs><marker id="${uid}m" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6" fill="none" stroke="${ink}" stroke-width="1.5"/></marker><marker id="${uid}r" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6" fill="none" stroke="${danger}" stroke-width="1.5"/></marker></defs>`;
    out += node(x1, w1, keyLabel, "#e6ebe6", "#b9c0b3", ink);
    out += node(x2, w2, detLabel, "#f6e4e2", danger, danger);
    out += `<line x1="${x1 + w1 + 2}" y1="${cy}" x2="${x2 - 2}" y2="${cy}" stroke="${ink}" stroke-width="1.5" marker-end="url(#${uid}m)"/>`;
    out += `<path d="M${x1 + w1 / 2},${ny + nh + 2} Q${W / 2},${H - 2} ${x2 + w2 / 2},${ny + nh + 4}" fill="none" stroke="${danger}" stroke-width="1.5" stroke-dasharray="5 4" marker-end="url(#${uid}r)"/>`;
    out += `<text x="${W / 2}" y="${H - 12}" text-anchor="middle" font-size="11" fill="${danger}" font-family="system-ui, sans-serif">transitive</text>`;
    return out + `</svg>`;
  }
  const w1 = graphNodeW(keyLabel), w2 = graphNodeW(detLabel),
    w3 = graphNodeW(depLabel);
  const gap = 30, pad = 4, nh = 32;
  // Give the optional key-implied label its own top margin so it never
  // overlaps the candidate-key box.
  const ny = o.firstEdgeLabel ? 14 : 4;
  const x1 = pad, x2 = x1 + w1 + gap, x3 = x2 + w2 + gap;
  const W = Math.round(x3 + w3 + pad);
  const H = o.arc ? (o.firstEdgeLabel ? 104 : 92) : (o.firstEdgeLabel ? 60 : 44);
  const cy = ny + nh / 2;
  const node = (x, w, label, fill, stroke, color) =>
    `<rect x="${x}" y="${ny}" width="${w}" height="${nh}" rx="4" fill="${fill}" stroke="${stroke}" stroke-width="1.5"/>` +
    `<text x="${x + w / 2}" y="${ny + 20}" text-anchor="middle" font-size="12" font-weight="600" fill="${color}" font-family="ui-monospace, Menlo, Consolas, monospace">${esc(label)}</text>`;
  const arrow = o.double
    ? ` marker-start="url(#${uid}m)" marker-end="url(#${uid}m)"`
    : ` marker-end="url(#${uid}m)"`;
  const color2 = o.double ? danger : ink;
  let s = `<svg class="rc-graph" viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="${esc(keyLabel)} determines ${esc(detLabel)}, which determines ${esc(depLabel)}${o.firstEdgeLabel ? `; first relationship is ${esc(o.firstEdgeLabel)}` : ""}${o.arc ? `; dashed arrow marks the ${esc(o.arc)} dependency` : ""}${o.double ? "; double-headed arrow marks equivalent columns" : ""}">`;
  s += `<defs><marker id="${uid}m" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6" fill="none" stroke="${o.double ? danger : ink}" stroke-width="1.5"/></marker>` +
    `<marker id="${uid}r" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6" fill="none" stroke="${danger}" stroke-width="1.5"/></marker></defs>`;
  s += node(x1, w1, keyLabel, "#e3e9f6", accent, accent);
  s += node(x2, w2, detLabel, "#e6ebe6", "#b9c0b3", ink);
  s += node(x3, w3, depLabel, "#f6e4e2", danger, danger);
  const firstDash = o.firstEdgeLabel ? ` stroke-dasharray="4 3"` : "";
  s += `<line x1="${x1 + w1 + 2}" y1="${cy}" x2="${x2 - 2}" y2="${cy}" stroke="${ink}" stroke-width="1.5"${firstDash} marker-end="url(#${uid}m)"/>`;
  if (o.firstEdgeLabel) {
    s += `<text x="${(x1 + w1 + x2) / 2}" y="${ny - 5}" text-anchor="middle" font-size="9" fill="${ink}" font-family="system-ui, sans-serif">${esc(o.firstEdgeLabel)}</text>`;
  }
  s += `<line x1="${x2 + w2 + 2}" y1="${cy}" x2="${x3 - 2}" y2="${cy}" stroke="${color2}" stroke-width="1.5"${arrow}/>`;
  if (o.arc) {
    const kx = x1 + w1 / 2, dx = x3 + w3 / 2;
    s += `<path d="M${kx},${ny + nh + 2} Q${W / 2},${H - 2} ${dx},${ny + nh + 4}" fill="none" stroke="${danger}" stroke-width="1.5" stroke-dasharray="5 4" marker-end="url(#${uid}r)"/>`;
    s += `<text x="${W / 2}" y="${H - 12}" text-anchor="middle" font-size="11" fill="${danger}" font-family="system-ui, sans-serif">${esc(o.arc)}</text>`;
  }
  return s + `</svg>`;
}

/**
 * Builds the faulted-table card as an HTML string (compact redesign).
 * `full`: table result (columns, keys, violations, fds, recommendation...).
 * `status`: "faulted" | "suspected" (suspected renders amber, not dimmed).
 * `expanded`: Set of open toggle keys (`<table>‖ev‖<i>`, `<table>‖sugg`).
 * Pure — Node-tested against a fixture covering every violation kind.
 */
function violationGroups(full, kind) {
  const groups = [];
  const seen = new Map();
  for (const v of (full.violations || [])) {
    if (v.kind !== kind) continue;
    const det = (v.determinant || []).join("\0");
    let g = seen.get(det);
    if (!g) {
      g = { determinant: v.determinant || [], dependents: [] };
      seen.set(det, g); groups.push(g);
    }
    for (const dep of (v.dependents || [])) if (!g.dependents.includes(dep)) g.dependents.push(dep);
  }
  return groups;
}

function keySourceLabel(source) {
  if (source === "declared") return "schema-declared";
  if (source === "inferred_verified") return "data-inferred (full-table verified)";
  if (source === "inferred_sample") return "data-inferred (sample)";
  if (source === "inferred") return "data-inferred";
  return source ? String(source).replaceAll("_", " ") : "";
}

function buildCardHTML(name, full, status, expanded) {
  const exp = expanded instanceof Set ? expanded : new Set();
  const f = full || {};
  const suspected = status === "suspected";
  const nf = f.normal_form || "?";
  const safe = escId(name);
  const parts = [];

  // Header: name, NF pill, Hide card; meta: rows · columns · Key chips · confidence.
  const cols = f.columns || [];
  const keys = keyColumns(f);
  const keyChips = keys.map((k) => `<span class="key-chip">${esc(k)}</span>`).join(" ");
  const candidateKeys = (f.keys || []).map((k) => (k.columns || []).join(", "));
  const keySource = (f.keys && f.keys[0] && f.keys[0].source) || "";
  const conf = f.key_confidence;
  const confPill = conf
    ? `<span class="pill pill-${conf === "High" ? "high" : "medium"}">${esc(conf)}</span>` : "";
  parts.push(`<div class="rc-head"><span class="rc-title mono">${esc(name)}</span>` +
    `<span class="pill ${suspected ? "pill-nf-suspected" : "pill-nf-faulted"}">${esc(nf)}</span>` +
    `<button type="button" class="btn btn-secondary btn-small rc-hide" data-act="hide">Hide card</button></div>`);
  const keyInfo = keys.length > 0
    ? ` · Key ${keyChips} ${confPill}` +
      (candidateKeys.length > 1 ? ` <span class="rc-muted">+${candidateKeys.length - 1} candidate key${candidateKeys.length > 2 ? "s" : ""}</span>` : "") +
      (keySource ? ` <span class="rc-muted">${esc(keySourceLabel(keySource))}</span>` : "")
    : ` · <span class="rc-muted">candidate key not established</span>`;
  const completeness = f.analysis_complete === false
    ? ` · <span class="rc-muted">not confirmed / evidence-dependent</span>` : "";
  parts.push(`<div class="rc-meta">${fmtInt(f.row_count)} rows · ${cols.length} columns${keyInfo}` +
    (f.sampled ? ` · <span class="rc-muted">sampled for discovery; exact candidates validated on full table where possible</span>` : "") +
    completeness + `</div>`);

  for (const limitation of f.analysis_limitations || []) {
    parts.push(`<div class="rc-strip rc-strip-faulted"><span class="rc-strip-title">Analysis limitation.</span> <span>${esc(limitation)}</span></div>`);
  }

  const fs = f.fd_summary || {};
  if (fs.total_discovered != null && fs.weak_suppressed > 0) {
    parts.push(`<div class="rc-muted rc-fd-summary">FD evidence: ${esc(fs.strong || 0)} strong · ${esc(fs.suspected || 0)} material suspected · ${esc(fs.weak_suppressed || 0)} weak observations suppressed from the main report.</div>`);
  }

  // Violation strips, one per violation.
  for (const v of f.violations || []) {
    parts.push(`<div class="rc-strip ${suspected ? "rc-strip-suspected" : "rc-strip-faulted"}">` +
      `<span class="rc-strip-title">${esc(stripTitle(v))}</span> ` +
      `<span>${esc(v.reason || "")}</span></div>`);
  }

  // Body: graph + example (FD kinds) or column chips (1NF kinds).
  const worst = worstViolation(f);
  const pfd = primaryFD(f, worst);
  const equiv = !!(worst && (worst.equivalent || (pfd && pfd.equivalent)));
  if (worst && (worst.kind === "transitive" || worst.kind === "partial")) {
    const graph = dependencyGraphContext(f, worst);
    const keyLabel = truncLabel(graph.first, 28);
    const detLabel = truncLabel(graph.middle, 28);
    const depLabel = truncLabel(graph.last, 28);
    const labels = `<div class="rc-graphwrap"><p class="rc-lbl">Dependency graph</p>` +
      buildGraphSVG(`g-${safe}-`, keyLabel, detLabel, depLabel,
        { arc: !equiv ? (worst.kind === "transitive" ? "transitive" : "partial dependency") : null,
          firstEdgeLabel: graph.firstEdgeLabel, compact: !!graph.compact, double: equiv }) +
      (equiv ? `<p class="rc-eq"><span class="eq-chip">EQ</span> equivalent columns</p>` : "") +
      `</div>`;
    let example = "";
    const examples = (pfd && pfd.evidence && pfd.evidence.examples) || [];
    if (pfd && examples.length > 0) {
      const detCols = pfd.determinant || [];
      const ths = detCols.map((c) => `<th>${esc(c)}</th>`).join("") +
        `<th>${esc(pfd.dependent)}</th><th>rows</th>`;
      const rows = examples.slice(0, 3).map((ex) => {
        const det = detCols.map((_, i) => String(((ex.det || [])[i]) == null ? "" : (ex.det || [])[i]).slice(0, 24));
        const tds = det.map((v) => `<td>${esc(v)}</td>`).join("") +
          `<td>${esc(String(ex.dep == null ? "" : ex.dep).slice(0, 24))}${ex.exception ? " (exception)" : ""}</td>` +
          `<td>${esc(ex.rows)}</td>`;
        return `<tr${ex.exception ? ` class="rc-except"` : ""}>${tds}</tr>`;
      }).join("");
      example = `<div class="rc-example"><p class="rc-lbl">Example from data</p>` +
        `<div class="rc-tablewrap"><table class="rc-table mono"><tbody><tr>${ths}</tr>${rows}</tbody></table></div></div>`;
    }
    parts.push(`<div class="rc-body${example ? "" : " rc-body-single"}">${labels}${example}</div>`);
  } else if (worst && (worst.kind === "delimited" || worst.kind === "repeating" || worst.kind === "no-key" || worst.kind === "unknown-key")) {
    const chipCols = worst.kind === "no-key"
      ? cols.map((c) => c.name) : (worst.dependents || []);
    const chips = chipCols.map((c) => `<span class="chip-mono">${esc(c)}</span>`).join(" ");
    let extra = "";
    if (worst.kind === "delimited" && worst.sample != null) {
      extra = `<p class="rc-sample">e.g. <span class="mono">${esc(worst.sample)}</span></p>`;
    }
    parts.push(`<div class="rc-body rc-body-single"><div><p class="rc-lbl">Offending columns</p>` +
      `<p class="rc-chips">${chips}</p>${extra}</div></div>`);
  }

  // FD lines with collapsed Evidence panels.
  (f.fds || []).forEach((fd, i) => {
    const level = confLevel(fd.confidence);
    const key = `${name}‖ev‖${i}`;
    const open = exp.has(key);
    const eid = `ev-${safe}-${i}`;
    const eq = fd.equivalent ? `<span class="eq-chip">EQ</span>` : "";
    const exc = (fd.exceptions || 0) > 0
      ? `<span class="rc-exc">${fd.exceptions} exception row${fd.exceptions === 1 ? "" : "s"}</span>` : "";
    parts.push(`<div class="rc-fd"><span class="rc-fdnum">FD ${i + 1}</span>` +
      `<span class="mono"><strong>${esc((fd.determinant || []).join(", "))}</strong> → ${esc(fd.dependent)}${eq}</span>` +
      `<span class="pill pill-${level.toLowerCase()}">${level} · ${esc(fd.confidence)}</span>${exc}` +
      `<button type="button" class="mini-toggle" data-act="ev" data-key="${esc(key)}" aria-expanded="${open}" aria-controls="${eid}">Evidence ${open ? "▴" : "▾"}</button></div>`);
    const ev = fd.evidence || {};
    const lines = [];
    lines.push(`${ev.distinct_det ?? "—"} distinct values · ${ev.repeated_rows ?? "—"} repeated rows · ${ev.candidates_tested ?? "—"} candidates tested`);
    if (ev.repeated_row_coverage != null) lines.push(`Repeated-row coverage: ${(Number(ev.repeated_row_coverage) * 100).toFixed(2)}%`);
    lines.push(`Chance of coincidence: about ${((fd.coincidence && fd.coincidence.text) || "—")}`);
    if ((fd.exceptions || 0) > 0) {
      lines.push(`Holds with ${fd.exceptions} exception rows`);
    }
    const cross = ev.cross_table;
    if (cross && cross.supported) {
      const pct = cross.match_rate != null ? (cross.match_rate * 100).toFixed(1) : "—";
      lines.push(`Also seen in ${cross.ref_table}.${cross.ref_column}: ${pct}% match`);
    }
    if (ev.naming) {
      lines.push(`Similar names: ${(fd.determinant || []).join(", ")} / ${fd.dependent}`);
    }
    for (const ex of (ev.examples || []).slice(0, 3)) {
      const detVals = (ex.det || []).map((v) => String(v).slice(0, 24)).join(", ");
      lines.push(`e.g. ${detVals} → ${String(ex.dep).slice(0, 24)} in ${ex.rows} rows${ex.exception ? " (exception)" : ""}`);
    }
    parts.push(`<div class="rc-evidence" id="${eid}"${open ? "" : " hidden"}>` +
      lines.map((ln) => `<p>${esc(ln)}</p>`).join("") + `</div>`);
  });

  // Recommendation with collapsed suggested-tables panel (FD kinds only).
  if (f.recommendation) {
    parts.push(`<div class="rc-rec"><span class="rc-rec-title">Recommendation.</span> ` +
      `${esc(f.recommendation)}` +
      (worst && (worst.kind === "transitive" || worst.kind === "partial")
        ? (() => {
          const key = `${name}‖sugg`;
          const open = exp.has(key);
          const eid = `sugg-${safe}`;
          return ` <div class="rc-suggtoggle"><button type="button" class="mini-toggle" data-act="sugg" data-key="${esc(key)}" aria-expanded="${open}" aria-controls="${eid}">Show suggested tables ${open ? "▴" : "▾"}</button></div>`;
        })() : "") +
      `</div>`);
    if (worst && (worst.kind === "transitive" || worst.kind === "partial")) {
      const key = `${name}‖sugg`;
      const open = exp.has(key);
      const eid = `sugg-${safe}`;
      const groups = violationGroups(f, worst.kind);
      const li = (n, tag) => `<li>${esc(n)}${tag ? ` <span class="rc-tag">${esc(tag)}</span>` : ""}</li>`;
      const boxes = groups.map((g) => {
        const det = g.determinant || [], deps = g.dependents || [];
        const rest = cols.map((c) => c.name).filter((n) => !keys.includes(n) && !det.includes(n) && !deps.includes(n));
        const shown = rest.slice(0, 8), more = rest.length - shown.length;
        const orig = keys.map((n) => li(n, "PK")).join("") + det.map((n) => li(n, "link")).join("") + shown.map((n) => li(n, "")).join("") + (more > 0 ? `<li class="rc-muted">+ ${more} more</li>` : "");
        const newTitle = `new table (keyed by ${det.join(", ")})`;
        const newbox = det.map((n) => li(n, "key")).join("") + deps.map((n) => li(n, "")).join("");
        return `<div class="rc-split"><div class="rc-tbox rc-tbox-orig"><h5>${esc(name)}</h5><ul>${orig}</ul></div><span class="rc-splitarr" aria-hidden="true">→</span><div class="rc-tbox rc-tbox-new"><h5>${esc(newTitle)}</h5><ul>${newbox}</ul></div></div>`;
      }).join("");
      parts.push(`<div class="rc-sugg" id="${eid}"${open ? "" : " hidden"}>${boxes}<p class="rc-muted rc-caption">Illustrative sketch — not runnable DDL.</p></div>`);
    }
  }
  return parts.join("\n");
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
    ["bcnf_faulted", "BCNF violation"], ["bcnf_suspected", "suspected BCNF"],
    ["incomplete", "unconfirmed"], ["insufficient_data", "insufficient data"],
    ["clean", "clean"], ["error", "error"], ["not_run", "not run"]];
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
    case "bcnf_faulted": return ["BCNF violation", "badge-faulted"];
    case "bcnf_suspected": return ["BCNF suspected", "badge-suspected"];
    case "incomplete": return ["Faulted", "badge-faulted"];
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
  escId,
  wrapText,
  confLevel,
  stripTitle,
  buildGraphSVG,
  dependencyGraphContext,
  keySourceLabel,
  worstViolation,
  keyColumns,
  buildCardHTML,
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
    driver: document.getElementById("odbc-driver"),
    schema: document.getElementById("schema"),
    ssl: document.getElementById("ssl"),
    fieldService: document.getElementById("field-service"),
    fieldDriver: document.getElementById("field-driver"),
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
    expanded: new Set(), // open card toggles: "<table>‖ev‖<i>", "<table>‖sugg"
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
    els.fieldDriver.hidden = t !== "mssql";
    els.fieldSchema.hidden = !(t === "postgresql" || t === "mssql");
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
    state.expanded = new Set();
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
      driver: els.driver.value,
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
          tables: [...state.selected],
          database_count: state.databases.length })),
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
    state.expanded = new Set();
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
    const counts = { faulted: 0, suspected: 0, bcnf_faulted: 0, bcnf_suspected: 0,
      incomplete: 0, insufficient_data: 0, clean: 0, error: 0, not_run: 0 };
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
      if (["faulted", "suspected", "bcnf_faulted", "bcnf_suspected", "incomplete"].includes(st.state) &&
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
    // Only faulted/suspected tables have viewable cards; insufficient_data
    // entries travel in the payload for the PDF but open no card.
    const viewable = [...state.statuses.values()]
      .some((st) => ["faulted", "suspected", "bcnf_faulted", "bcnf_suspected", "incomplete"].includes(st.state));
    const openShown = state.openCard && state.cards.has(state.openCard);
    els.cardsHint.hidden = !(viewable && !openShown);
  }

  // Full HTML result card. Same container and `card-<name>` ids
  // as before, so status-row click-to-scroll is unchanged. Toggle
  // buttons are wired here; open/closed state lives in state.expanded
  // so it survives the full re-render on every stream event.
  function renderSimpleCard(name, full) {
    const st = state.statuses.get(name) || {};
    const article = document.createElement("article");
    article.className = "result-card" + (["suspected", "bcnf_suspected"].includes(st.state) ? " suspected" : "");
    article.id = `card-${name}`;
    article.innerHTML = logic.buildCardHTML(name, full,
      ["suspected", "bcnf_suspected"].includes(st.state) ? "suspected" : "faulted", state.expanded);
    article.querySelectorAll("button[data-act]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const act = btn.getAttribute("data-act");
        if (act === "hide") {
          state.openCard = null;
          state.autoFollow = false;
        } else {
          const key = btn.getAttribute("data-key");
          if (state.expanded.has(key)) state.expanded.delete(key);
          else state.expanded.add(key);
        }
        renderAll();
      });
    });
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
      if (["faulted", "suspected", "bcnf_faulted", "bcnf_suspected", "incomplete"].includes(s.status)) continue;
      state.statuses.set(s.name, { state: s.status,
        normalForm: s.current_normal_form || s.normal_form || undefined,
        secs: s.duration_seconds || 0 });
    }
    state.cards = new Map();
    state.selectedCard = null;
    state.autoFollow = false;
    for (const t of payload.tables || []) {
      state.cards.set(t.name, t);
      state.statuses.set(t.name, { state: t.status,
        normalForm: t.current_normal_form || t.normal_form || undefined, secs: 0 });
    }
    state.notices = payload.notices || [];
    const counts = { faulted: 0, suspected: 0, bcnf_faulted: 0, bcnf_suspected: 0,
      incomplete: 0, insufficient_data: 0, clean: 0, error: 0, not_run: 0 };
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
      state.expanded = new Set();
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
