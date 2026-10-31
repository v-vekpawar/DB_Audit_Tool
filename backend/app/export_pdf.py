"""PDF export: headline, scan details and faulted-table cards.

Drawn with reportlab platypus flowables to match the on-screen cards.
Built in memory (BytesIO) and returned directly; nothing is written
to disk. `compress=False` exists for tests (searchable content).
"""

import io

from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

INK = colors.HexColor("#1c211d")
MUTED = colors.HexColor("#5c655e")
ACCENT = colors.HexColor("#2f4b8f")
ACCENT_SOFT = colors.HexColor("#e3e9f6")
DANGER = colors.HexColor("#a3291f")
DANGER_SOFT = colors.HexColor("#f6e4e2")
WARNING = colors.HexColor("#8a5a00")
WARNING_SOFT = colors.HexColor("#f6ecd9")
GREY_FILL = colors.HexColor("#f4f5f2")
GREY_EDGE = colors.HexColor("#b9c0b3")
BORDER = colors.HexColor("#d7dbd2")


def _styles():
    base = getSampleStyleSheet()
    s = {}
    s["title"] = ParagraphStyle("title", parent=base["Title"], fontSize=16,
                                leading=20, textColor=INK,
                                alignment=1, spaceAfter=8)
    s["h2"] = ParagraphStyle("h2", parent=base["Heading2"], fontSize=12,
                             leading=15, textColor=INK, spaceBefore=10,
                             spaceAfter=4)
    s["h3"] = ParagraphStyle("h3", parent=base["Heading3"], fontSize=11,
                             leading=14, textColor=INK, spaceBefore=8,
                             spaceAfter=2)
    s["body"] = ParagraphStyle("body", parent=base["Normal"], fontSize=9,
                               leading=12.5, textColor=INK)
    s["mono"] = ParagraphStyle("mono", parent=s["body"],
                               fontName="Courier", fontSize=8.5)
    s["muted"] = ParagraphStyle("muted", parent=s["body"], textColor=MUTED)
    s["headline"] = ParagraphStyle("headline", parent=s["body"], fontSize=11,
                                   leading=14, spaceAfter=6)
    return s


def _esc(value):
    return str(value if value is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt_int(n):
    try:
        return "{:,}".format(int(n))
    except (TypeError, ValueError):
        return "—"


def _key_columns(table):
    keys = table.get("keys") or []
    if keys and keys[0].get("columns"):
        return list(keys[0]["columns"])
    return [c["name"] for c in table.get("columns") or [] if c.get("pk")]


def _strip_title(v):
    titles = {
        "transitive": "Transitive dependency (violates 3NF).",
        "partial": "Partial dependency (violates 2NF).",
        "bcnf": "Non-key determinant (violates BCNF).",
        "no-key": "No candidate key established (higher forms cannot be confirmed).",
        "unknown-key": "Candidate-key search incomplete (higher forms cannot be confirmed).",
        "delimited": "Multi-valued cells (below 1NF).",
        "repeating": "Repeating group (below 1NF).",
    }
    if v.get("kind") in titles:
        return titles[v["kind"]]
    return "Violates %s." % (v.get("form") or "?")


def _worst_violation(table):
    order = {"1NF": 0, "2NF": 1, "3NF": 2, "BCNF": 3}
    worst = None
    for v in table.get("violations") or []:
        if worst is None or order.get(v.get("form"), 9) < order.get(worst.get("form"), 9):
            worst = v
    return worst


def _primary_fd(table, worst):
    fds = table.get("fds") or []
    if not worst or not worst.get("determinant"):
        return None
    key = "\0".join(worst["determinant"])
    for f in fds:
        if "\0".join(f.get("determinant") or []) == key and f.get("dependent") in (worst.get("dependents") or []):
            return f
    for f in fds:
        if "\0".join(f.get("determinant") or []) == key:
            return f
    return None


def _indented(block, width=174 * mm):
    """Indent one content block under its subheading (page-split safe)."""
    t = Table([["", block]], colWidths=[8 * mm, width])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                           ("TOPPADDING", (0, 0), (-1, -1), 0),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    return t


def _pill_table(text, bg, fg, s):
    w = stringWidth(text, "Helvetica-Bold", 9) + 20
    t = Table([[Paragraph('<font color="%s"><b>%s</b></font>' % (
        fg.hexval(), _esc(text)), s["body"])]], colWidths=[w])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg),
                           ("ROUNDEDCORNERS", [8, 8, 8, 8]),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 10),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                           ("TOPPADDING", (0, 0), (-1, -1), 3),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    return t


def _title_bar(name, badge, bg, fg, s):
    left = Paragraph("Table: <font face=\"Courier\" size=\"11\"><b>%s</b></font>" % (
        _esc(name)), s["body"])
    badge_w = stringWidth(badge, "Helvetica-Bold", 9) + 20
    t = Table([[left, _pill_table(badge, bg, fg, s)]],
              colWidths=[174 * mm - badge_w, badge_w])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                           ("TOPPADDING", (0, 0), (-1, -1), 0),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                           ("ALIGN", (1, 0), (1, 0), "RIGHT")]))
    return t


def _band(title, body_text, bg, fg, s):
    rows = [[Paragraph('<font color="%s"><b>%s</b></font> %s' % (
        fg.hexval(), _esc(title), _esc(body_text or "")), s["body"])]]
    t = Table(rows, colWidths=[174 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg),
                           ("ROUNDEDCORNERS", [4, 4, 4, 4]),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                           ("TOPPADDING", (0, 0), (-1, -1), 6),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return t


def _chips_row(items, s, chip_from=0):
    if not items:
        return Paragraph("", s["body"])
    cells, widths, boxed = [], [], []
    for i, n in enumerate(items):
        if i > 0:
            cells.append(Paragraph("", s["body"]))
            widths.append(4)
            boxed.append(False)
        cells.append(Paragraph("<font face=\"Courier\">%s</font>" % _esc(n),
                               s["body"]))
        if i < chip_from:
            widths.append(stringWidth(n, "Helvetica", 9) + 2)
        else:
            widths.append(stringWidth(n, "Courier", 9) + 14)
        boxed.append(i >= chip_from)
    t = Table([cells], colWidths=widths, spaceBefore=2 * mm, hAlign="LEFT")
    style = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
             ("LEFTPADDING", (0, 0), (-1, -1), 0),
             ("RIGHTPADDING", (0, 0), (-1, -1), 0),
             ("TOPPADDING", (0, 0), (-1, -1), 0),
             ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]
    for i, is_box in enumerate(boxed):
        if is_box:
            style += [("BOX", (i, 0), (i, 0), 0.5, ACCENT),
                      ("BACKGROUND", (i, 0), (i, 0), ACCENT_SOFT),
                      ("LEFTPADDING", (i, 0), (i, 0), 6),
                      ("RIGHTPADDING", (i, 0), (i, 0), 6),
                      ("TOPPADDING", (i, 0), (i, 0), 2),
                      ("BOTTOMPADDING", (i, 0), (i, 0), 2)]
    t.setStyle(TableStyle(style))
    return t




def _major_violation_fds(table, kind):
    """Select the small set of material FDs that explain the violation.

    PDF-only presentation: the analyzer retains every discovered FD, but the
    report shows only the strongest violation-linked rules. Transitive findings
    may need three rules to explain the dependency chain; partial/BCNF findings
    use two at most.
    """
    violations = [v for v in (table.get("violations") or []) if v.get("kind") == kind]
    fds = table.get("fds") or []
    linked = []
    seen = set()
    for v in violations:
        ef = v.get("evidence_fd") or {}
        det = tuple(ef.get("determinant") or v.get("determinant") or [])
        dep = ef.get("dependent") or ((v.get("dependents") or [None])[0])
        if not det or dep is None:
            continue
        for f in fds:
            if tuple(f.get("determinant") or []) == det and f.get("dependent") == dep:
                key = (det, dep)
                if key not in seen:
                    linked.append(f)
                    seen.add(key)
                break
    if not linked:
        return []
    def score(f):
        return (float(f.get("confidence") or 0),
                int((f.get("evidence") or {}).get("repeated_rows") or 0))
    linked.sort(key=score, reverse=True)
    limit = 3 if kind == "transitive" else 2
    return linked[:limit]

def _evidence_grid(f, s):
    ev = f.get("evidence") or {}
    conf = f.get("confidence")
    level = "High" if conf is not None and conf >= 80 else (
        "Medium" if conf is not None and conf >= 60 else "Low")
    rows = [
        ("Rule", "%s → %s%s" % (", ".join(f.get("determinant") or []),
                                f.get("dependent"),
                                " (equivalent)" if f.get("equivalent") else "")),
        ("Confidence", "%s · %s (chance of coincidence about %s)" % (
            level, conf, (f.get("coincidence") or {}).get("text") or "—")),
        ("Distinct values", str(ev.get("distinct_det", "—"))),
        ("Repeated rows", str(ev.get("repeated_rows", "—"))),
        ("Candidates tested", str(ev.get("candidates_tested", "—"))),
    ]
    if (f.get("exceptions") or 0) > 0:
        rows.append(("Exceptions",
                     "Holds with %s exception rows" % f["exceptions"]))
    cross = ev.get("cross_table") or {}
    if cross.get("supported"):
        rate = cross.get("match_rate")
        rows.append(("Cross-table",
                     "Also seen in %s.%s: %s%% match" % (
                         cross.get("ref_table"), cross.get("ref_column"),
                         ("%.1f" % (rate * 100)) if rate is not None else "—")))
    if ev.get("naming"):
        rows.append(("Names", "Similar names: %s / %s" % (
            ", ".join(f.get("determinant") or []), f.get("dependent"))))
    body = [[Paragraph("<b>%s</b>" % _esc(k), s["body"]),
             Paragraph(_esc(v), s["body"])] for k, v in rows]
    t = Table(body, colWidths=[36 * mm, 138 * mm])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, BORDER),
                           ("BACKGROUND", (0, 0), (0, -1),
                            colors.HexColor("#f7f8f6")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return t


def _example_table(pfd, s):
    examples = ((pfd.get("evidence") or {}).get("examples") or [])[:5]
    if not examples:
        return None
    det_cols = pfd.get("determinant") or []
    head = [Paragraph("<b>%s</b>" % _esc(c), s["body"]) for c in det_cols] + [
        Paragraph("<b>%s</b>" % _esc(pfd.get("dependent")), s["body"]),
        Paragraph("<b>rows</b>", s["body"])]
    rows = [head]
    for ex in examples:
        det = [str((ex.get("det") or [None] * len(det_cols))[i] or "")[:24]
               for i in range(len(det_cols))]
        dep = str(ex.get("dep") or "")[:24] + (" (exception)" if ex.get("exception") else "")
        rows.append([Paragraph("<font face=\"Courier\">%s</font>" % _esc(v), s["body"]) for v in det] + [
            Paragraph("<font face=\"Courier\">%s</font>" % _esc(dep), s["body"]),
            Paragraph(_esc(ex.get("rows")), s["body"])])
    widths = [40 * mm] * (len(det_cols) + 1) + [22 * mm]
    t = Table(rows, colWidths=widths[:len(head)])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, BORDER),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return t


def _node_w(label, font_size=9):
    return min(150, max(46, 10 + stringWidth(str(label), "Courier-Bold", font_size)))




def _graph_context(table, worst):
    """Return graph labels grounded in FD evidence and key closure.

    For transitive 3NF findings, prefer a fully explicit A->B->C chain.
    When the first edge is not present in the discovered-FD list but A is a
    candidate key, A->B is still logically guaranteed by key closure. Mark
    that edge as key-implied rather than pretending it was independently
    discovered.
    """
    det = list(worst.get("determinant") or []) if worst else []
    dep = ((worst.get("dependents") or ["?"])[0]) if worst else "?"
    fds = table.get("fds") or []
    if worst and worst.get("kind") == "transitive" and det and dep:
        target = next((f for f in fds
                       if list(f.get("determinant") or []) == det
                       and f.get("dependent") == dep), None)
        if target:
            second = next((f for f in fds
                           if f.get("dependent") == dep
                           and list(f.get("determinant") or []) != det
                           and len(f.get("determinant") or []) == 1), None)
            if second:
                middle = (second.get("determinant") or [None])[0]
                first = next((f for f in fds
                              if list(f.get("determinant") or []) == det
                              and f.get("dependent") == middle), None)
                if first:
                    return {
                        "first": ", ".join(first.get("determinant") or ["?"]),
                        "middle": middle or "?",
                        "last": dep,
                        "first_edge_label": None,
                    }

        # A candidate key functionally determines every attribute in the
        # relation. If the violating determinant is not itself that key, the
        # key -> determinant edge can therefore explain the transitive path
        # without inventing an unsupported discovered FD.
        keys = _key_columns(table)
        if keys and keys != det and dep not in keys:
            return {
                "first": ", ".join(keys),
                "middle": ", ".join(det),
                "last": dep,
                "first_edge_label": "key-implied",
            }

        return {
            "first": ", ".join(det or ["?"]),
            "middle": dep or "?",
            "last": None,
            "compact": True,
            "first_edge_label": None,
        }
    keys = _key_columns(table)
    return {
        "first": ", ".join(keys or ["?"]),
        "middle": ", ".join(det or ["?"]),
        "last": dep or "?",
        "first_edge_label": None,
    }

def _graph_drawing(key_label, det_label, dep_label, kind, equiv, first_edge_label=None, compact=False):
    """Dependency graph as vector boxes and arrows in the card palette."""
    fs = 10
    if compact:
        key_label = str(key_label or "?")
        det_label = str(det_label or "?")
        ws = [_node_w(key_label, fs), _node_w(det_label, fs)]
        gap, pad, nh, ny = 24, 2, 26, 2
        xs = [pad, pad + ws[0] + gap]
        W = xs[1] + ws[1] + pad
        H = 76
        cy = ny + nh / 2
        d = Drawing(W, H)
        fills = [(GREY_FILL, GREY_EDGE, INK), (DANGER_SOFT, DANGER, DANGER)]
        for x, w, lab, (bg, edge, fg) in zip(xs, ws, [key_label, dep_label], fills):
            d.add(Rect(x, H - ny - nh, w, nh, rx=3, fillColor=bg,
                       strokeColor=edge, strokeWidth=1.2))
            d.add(String(x + w / 2, H - ny - nh / 2 - 3, lab,
                         fontName="Courier-Bold", fontSize=fs, fillColor=fg,
                         textAnchor="middle"))
        x1, x2 = xs[0] + ws[0] + 1, xs[1] - 1
        d.add(Line(x1, H - cy, x2, H - cy, strokeColor=INK, strokeWidth=1.2))
        d.add(Line(x2, H - cy, x2 - 5, H - cy + 2.5, strokeColor=INK, strokeWidth=1.2))
        d.add(Line(x2, H - cy, x2 - 5, H - cy - 2.5, strokeColor=INK, strokeWidth=1.2))
        kx, dx = xs[0] + ws[0] / 2, xs[1] + ws[1] / 2
        y0 = H - ny - nh - 3
        ctrl = y0 - 46
        prev = None
        for i in range(25):
            u = i / 24
            x = (1 - u) ** 2 * kx + 2 * (1 - u) * u * (kx + dx) / 2 + u ** 2 * dx
            y = (1 - u) ** 2 * y0 + 2 * (1 - u) * u * ctrl + u ** 2 * y0
            if prev is not None:
                d.add(Line(prev[0], prev[1], x, y, strokeColor=DANGER, strokeWidth=1.2, strokeDashArray=[4, 3]))
            prev = (x, y)
        mid_y = 0.25 * y0 + 0.5 * ctrl + 0.25 * y0
        d.add(String((kx + dx) / 2, mid_y - 12, "transitive",
                     fontName="Helvetica", fontSize=8, fillColor=DANGER, textAnchor="middle"))
        if W > 470:
            f = 470 / W
            d.scale(f, f)
            d.width, d.height = W * f, H * f
        return d
    labels = [str(key_label or "?"), str(det_label or "?"),
              str(dep_label or "?")]
    labels = [l if len(l) <= 28 else l[:27] + "..." for l in labels]
    ws = [_node_w(l, fs) for l in labels]
    gap, pad, nh = 24, 2, 26
    ny = 10 if first_edge_label else 2
    xs = [pad, pad + ws[0] + gap, pad + ws[0] + gap + ws[1] + gap]
    W = xs[2] + ws[2] + pad
    H = (88 if first_edge_label else 76) if kind in ("transitive", "partial") and not equiv else (46 if first_edge_label else 34)
    cy = ny + nh / 2
    d = Drawing(W, H)
    fills = [(ACCENT_SOFT, ACCENT, ACCENT), (GREY_FILL, GREY_EDGE, INK),
             (DANGER_SOFT, DANGER, DANGER)]
    for x, w, lab, (bg, edge, fg) in zip(xs, ws, labels, fills):
        d.add(Rect(x, H - ny - nh, w, nh, rx=3, fillColor=bg,
                   strokeColor=edge, strokeWidth=1.2))
        d.add(String(x + w / 2, H - ny - nh / 2 - 3, lab,
                     fontName="Courier-Bold", fontSize=fs, fillColor=fg,
                     textAnchor="middle"))
    for a, b, color in ((0, 1, INK), (1, 2, DANGER if equiv else INK)):
        x1, x2 = xs[a] + ws[a] + 1, xs[b] - 1
        d.add(Line(x1, H - cy, x2, H - cy, strokeColor=color,
                   strokeWidth=1.2,
                   strokeDashArray=[4, 3] if (a == 0 and first_edge_label) else None))
        if a == 0 and first_edge_label:
            d.add(String((x1 + x2) / 2, H - ny + 4, first_edge_label,
                         fontName="Helvetica", fontSize=7, fillColor=INK,
                         textAnchor="middle"))
        heads = [(x2, 1)]
        if equiv and b == 2:
            heads.append((x1, -1))
        for xe, direction in heads:
            d.add(Line(xe, H - cy, xe - direction * 5, H - cy + 2.5,
                       strokeColor=color, strokeWidth=1.2))
            d.add(Line(xe, H - cy, xe - direction * 5, H - cy - 2.5,
                       strokeColor=color, strokeWidth=1.2))
    if kind in ("transitive", "partial") and not equiv:
        kx, dx = xs[0] + ws[0] / 2, xs[2] + ws[2] / 2
        y0 = H - ny - nh - 3
        n, ctrl = 24, y0 - 46
        prev = None
        for i in range(n + 1):
            u = i / n
            x = (1 - u) ** 2 * kx + 2 * (1 - u) * u * (kx + dx) / 2 + u ** 2 * dx
            y = (1 - u) ** 2 * y0 + 2 * (1 - u) * u * ctrl + u ** 2 * y0
            if prev is not None:
                d.add(Line(prev[0], prev[1], x, y, strokeColor=DANGER,
                           strokeWidth=1.2, strokeDashArray=[4, 3]))
            prev = (x, y)
        mid_y = 0.25 * y0 + 0.5 * ctrl + 0.25 * y0
        d.add(String((kx + dx) / 2, mid_y - 12, "partial dependency" if kind == "partial" else "transitive",
                     fontName="Helvetica", fontSize=8, fillColor=DANGER,
                     textAnchor="middle"))
        d.add(Line(dx, y0, dx - 5, y0 + 2.5, strokeColor=DANGER,
                   strokeWidth=1.2))
        d.add(Line(dx, y0, dx - 5, y0 - 2.5, strokeColor=DANGER,
                   strokeWidth=1.2))
    if W > 470:
        f = 470 / W
        d.scale(f, f)
        d.width, d.height = W * f, H * f
    return d


def _sugg_table(title, items, s):
    rows = [[Paragraph("<b>%s</b>" % _esc(title), s["body"])]]
    for name, tag in items:
        rows.append([Paragraph("<font face=\"Courier\">%s</font>%s" % (
            _esc(name), " (%s)" % _esc(tag) if tag else ""), s["body"])])
    t = Table(rows, colWidths=[82 * mm])
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, BORDER),
                           ("BACKGROUND", (0, 0), (-1, 0), ACCENT_SOFT),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return t


ENGINE_NAMES = {"mysql": "MySQL", "postgresql": "PostgreSQL",
                "oracle": "Oracle"}


def _plural(n, one, many):
    return one if n == 1 else many


def build_pdf(result: dict, compress: bool = True) -> bytes:
    """Render the result (full or partial) to PDF bytes."""
    scan = result.get("scan", {})
    counts = scan.get("counts", {})
    s = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm,
                            rightMargin=15 * mm, topMargin=15 * mm,
                            bottomMargin=18 * mm,
                            title="Normalization report",
                            pageCompression=1 if compress else 0)
    story = [Paragraph("DATABASE NORMALIZATION TEST REPORT", s["title"])]

    story += [Paragraph("What was tested", s["h2"])]
    total = counts.get("total_in_db")
    selected = len(scan.get("selected_tables") or [])
    not_sel = counts.get("not_selected", 0)
    if total is None:
        checked = "%d %s" % (selected, _plural(selected, "table", "tables"))
    else:
        checked = "%d of %d tables%s" % (
            selected, total,
            " (%d not selected)" % not_sel if not_sel else "")
    tested = [
        ("Database engine", ENGINE_NAMES.get(scan.get("db_type"),
                                             scan.get("db_type") or "—")),
        ("Host", scan.get("host") or "—"),
        ("Databases found", str(scan["database_count"])
         if scan.get("database_count") is not None else "—"),
        ("Database tested", scan.get("database") or "—"),
        ("Tables checked", checked),
        ("Scan ran", "%s · took %s · %s" % (
            scan.get("started_at") or "—",
            ("%.1fs" % scan["duration_seconds"])
            if scan.get("duration_seconds") is not None else "—",
            scan.get("status") or "—")),
    ]
    tested_rows = [[Paragraph("<b>%s</b>" % _esc(k), s["body"]),
                     Paragraph(_esc(v), s["body"])] for k, v in tested]
    tested_table = Table(tested_rows, colWidths=[40 * mm, 134 * mm])
    tested_table.setStyle(TableStyle(
        [("BOX", (0, 0), (-1, -1), 0.5, BORDER),
         ("INNERGRID", (0, 0), (-1, -1), 0.25, BORDER),
         ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f7f8f6")),
         ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [_indented(tested_table)]

    story += [Paragraph("Summary", s["h2"])]
    faulted = counts.get("faulted", 0)
    bcnf_faulted = counts.get("bcnf_faulted", 0)
    suspected = counts.get("suspected", 0)
    bcnf_suspected = counts.get("bcnf_suspected", 0)
    incomplete = counts.get("incomplete", 0)
    below = faulted + suspected
    headline_parts = [
        "%d of %d tables %s below 3NF (%d faulted, %d suspected)" % (
            below, selected, _plural(below, "is", "are"), faulted, suspected)]
    if incomplete:
        headline_parts.append("%d incomplete analysis%s" % (incomplete, "es" if incomplete != 1 else ""))
    passed = counts.get("clean", 0) + bcnf_faulted + bcnf_suspected
    headline_parts.append("%d clean" % passed)
    story += [_indented(Paragraph("; ".join(headline_parts) + ".", s["headline"]))]
    extra = []
    if counts.get("insufficient_data"):
        n = counts["insufficient_data"]
        extra.append("%d %s too few rows to judge (listed below)" % (
            n, "table had" if n == 1 else "tables had"))
    problems = counts.get("error", 0) + counts.get("not_run", 0)
    if problems:
        extra.append("%d %s be analysed" % (
            problems,
            "table could not" if problems == 1 else "tables could not"))
    if extra:
        story += [_indented(Paragraph("; ".join(extra) + ".", s["body"]))]
    # PDF scope: a table that reaches 3NF but violates BCNF is considered a
    # pass for this report, because the report's headline is tables below 3NF.
    # Keep BCNF-only results available to the API/UI, but do not list them as
    # findings in the final PDF.
    tables = [t for t in (result.get("tables") or [])
              if t.get("status") not in ("bcnf_faulted", "bcnf_suspected")]
    order = {n: i for i, n in enumerate(scan.get("selected_tables") or [])}
    rank = {"faulted": 0, "suspected": 1, "incomplete": 2, "insufficient_data": 3}
    tables = sorted(tables, key=lambda t: (rank.get(t.get("status"), 9),
                                           order.get(t.get("name"), 10 ** 9),
                                           t.get("name") or ""))
    faulted_names = [t.get("name") for t in tables
                     if t.get("status") == "faulted" and t.get("name")]
    if faulted_names:
        story += [_indented(Paragraph(
            "Faulted tables: %s." % ", ".join(faulted_names), s["body"]))]
    untested = []
    seen = set()
    for src in (result.get("table_summary") or []) + tables:
        name = src.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        if src.get("status") == "insufficient_data":
            untested.append("%s (too few rows to judge)" % name)
        elif src.get("status") == "error":
            untested.append("%s (could not be analysed)" % name)
        elif src.get("status") == "not_run":
            untested.append("%s (scan stopped before it ran)" % name)
    if untested:
        story += [_indented(Paragraph(
            "Tables not tested: %s." % ", ".join(untested), s["body"]))]

    if tables:
        story += [Paragraph("Findings", s["h2"])]
    else:
        story += [_indented(Paragraph("No tables below 3NF.", s["body"]))]
    for t in tables:
        status = t.get("status", "faulted")
        story.append(Spacer(1, 4 * mm))
        if status == "insufficient_data":
            story += [_indented(_title_bar(t.get("name"), "too few rows",
                                 colors.HexColor("#e6ebe6"), MUTED, s))]
            story += [_indented(Paragraph("%s rows · %d columns" % (
                _fmt_int(t.get("row_count")), len(t.get("columns") or [])),
                s["muted"]))]
            story += [_indented(Paragraph(
                "This table has too few rows to judge, so no findings are shown.",
                s["body"]))]
            continue
        nf = t.get("current_normal_form") or t.get("normal_form") or "?"
        violated = t.get("violated_normal_form")
        amber = status in ("suspected", "bcnf_suspected")
        danger = status in ("faulted", "bcnf_faulted", "incomplete")
        badge = "%s" % nf
        if violated:
            badge += " · violates " + str(violated)
        elif status == "incomplete":
            badge += " · not confirmed"
        story += [_indented(_title_bar(
            t.get("name"), badge,
            WARNING_SOFT if amber else (DANGER_SOFT if danger else GREY_FILL),
            WARNING if amber else (DANGER if danger else MUTED), s))]
        keys = _key_columns(t)
        story += [_indented(Paragraph("%s rows · %d columns" % (
            _fmt_int(t.get("row_count")), len(t.get("columns") or [])),
            s["muted"]))]
        story += [_indented(Paragraph(
            "<b>Current normal form:</b> %s" % _esc(t.get("current_normal_form") or t.get("normal_form") or "unknown"),
            s["body"]))]
        if t.get("violated_normal_form"):
            story += [_indented(Paragraph(
                "<b>Violated normal form:</b> %s" % _esc(t.get("violated_normal_form")), s["body"]))]
        for limitation in t.get("analysis_limitations") or []:
            story += [_indented(Paragraph("<b>Analysis limitation:</b> %s" % _esc(limitation), s["muted"]))]
        if keys:
            story += [_indented(_chips_row(["Key:"] + keys, s, chip_from=1))]
            if t.get("key_confidence"):
                story += [_indented(Paragraph("Key confidence: %s." % t["key_confidence"],
                                    s["muted"]))]
            sources = {k.get("source") for k in t.get("keys") or [] if k.get("source")}
            if "declared" in sources:
                key_source = "schema-declared"
            elif "inferred_verified" in sources:
                key_source = "data-inferred (full-table verified)"
            elif "inferred_sample" in sources:
                key_source = "data-inferred (sample)"
            elif "inferred" in sources:
                key_source = "data-inferred"
            else:
                key_source = None
            if key_source:
                story += [_indented(Paragraph("Key source: %s." % _esc(key_source), s["muted"]))]
        else:
            story += [_indented(Paragraph("No key declared or found.", s["muted"]))]
        if t.get("sampled"):
            story += [_indented(Paragraph("sampled for discovery; exact candidates were validated on the full table where possible.", s["muted"]))]
        story.append(Spacer(1, 2 * mm))
        for v in t.get("violations") or []:
            story += [_indented(_band(_strip_title(v), v.get("reason") or "",
                             WARNING_SOFT if amber else DANGER_SOFT,
                             WARNING if amber else DANGER, s))]
            story.append(Spacer(1, 2 * mm))
        worst = _worst_violation(t)
        pfd = _primary_fd(t, worst)
        if worst and worst.get("kind") in ("transitive", "partial"):
            equiv = bool(worst.get("equivalent") or (pfd and pfd.get("equivalent")))
            graph = _graph_context(t, worst)
            story += [_indented(_graph_drawing(
                graph["first"], graph["middle"], graph["last"],
                worst.get("kind"), equiv, graph.get("first_edge_label"), graph.get("compact", False)))]
            story.append(Spacer(1, 2 * mm))
            if equiv:
                story += [_indented(Paragraph("Double-headed arrow: equivalent columns.",
                                    s["muted"]))]
            if pfd:
                tbl = _example_table(pfd, s)
                if tbl is not None:
                    story += [_indented(Paragraph(
                        "<b>Rows from %s showing the problem:</b>" % (
                            _esc(t.get("name"))), s["body"]))]
                    story += [_indented(tbl)]
                    story.append(Spacer(1, 2 * mm))
        elif worst and worst.get("kind") in ("delimited", "repeating", "no-key", "unknown-key"):
            if worst.get("kind") in ("no-key", "unknown-key"):
                story += [_indented(Paragraph("Columns: %s" % ", ".join(
                    c.get("name", "") for c in t.get("columns") or []), s["body"]))]
            else:
                story += [_indented(_chips_row(worst.get("dependents") or [], s))]
            if worst.get("kind") == "delimited" and worst.get("sample") is not None:
                story += [_indented(Paragraph("e.g. <font face=\"Courier\">%s</font>" % (
                    _esc(worst["sample"])), s["body"]))]
            story.append(Spacer(1, 2 * mm))
        fs = t.get("fd_summary") or {}
        if fs:
            story += [_indented(Paragraph(
                "FD evidence: %s strong · %s material suspected · %s weak observations suppressed." % (
                    fs.get("strong", 0), fs.get("suspected", 0), fs.get("weak_suppressed", 0)),
                s["muted"]))]
        # PDF presentation only: show the small set of major violation-linked
        # FDs. The full FD collection remains available to the API/result card.
        if worst and worst.get("kind") in ("partial", "transitive", "bcnf"):
            major_fds = _major_violation_fds(t, worst.get("kind"))
            for i, f in enumerate(major_fds, 1):
                story += [_indented(Paragraph(
                    "<b>Functional Dependency %d</b>" % i, s["h3"]))]
                story += [_indented(_evidence_grid(f, s))]
                story.append(Spacer(1, 2 * mm))
        if t.get("recommendation"):
            story += [_indented(_band("Recommendation.", t["recommendation"],
                             ACCENT_SOFT, ACCENT, s))]
            story.append(Spacer(1, 2 * mm))
            if worst and worst.get("kind") in ("transitive", "partial"):
                groups = []
                seen = {}
                for v in t.get("violations") or []:
                    if v.get("kind") != worst.get("kind"):
                        continue
                    det_key = tuple(v.get("determinant") or [])
                    if det_key not in seen:
                        seen[det_key] = {"det": list(det_key), "deps": []}
                        groups.append(seen[det_key])
                    for dep in v.get("dependents") or []:
                        if dep not in seen[det_key]["deps"]:
                            seen[det_key]["deps"].append(dep)
                # Recommendation decomposition is an illustrative sketch, not a
                # complete rendering of every violation group. Show one canonical
                # sketch only; the recommendation text above already summarizes
                # all groups. This also prevents alternate/contradictory sketches
                # from appearing later in the report when multiple determinants
                # are involved.
                for group in groups[:1]:
                    det = group["det"]
                    deps = group["deps"]
                    rest = [c.get("name", "") for c in t.get("columns") or []
                            if c.get("name") not in keys and c.get("name") not in det
                            and c.get("name") not in deps]
                    orig = [(n, "PK") for n in keys] + [(n, "link") for n in det] + [(n, "") for n in rest[:8]]
                    if len(rest) > 8:
                        orig.append(("+ %d more" % (len(rest) - 8), ""))
                    new_title = "new table (keyed by %s)" % ", ".join(det)
                    new = [(n, "key") for n in det] + [(n, "") for n in deps]
                    pair = Table([[_sugg_table(t.get("name"), orig, s),
                                   _sugg_table(new_title, new, s)]],
                                 colWidths=[86 * mm, 86 * mm], spaceBefore=2 * mm)
                    pair.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
                    story += [_indented(pair)]
                story += [_indented(Paragraph("Illustrative sketch — not runnable DDL.", s["muted"]))]
    doc.build(story)
    return buf.getvalue()
