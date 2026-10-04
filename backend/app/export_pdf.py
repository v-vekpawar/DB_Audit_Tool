"""PDF export: headline, scan details and faulted-table cards.

Drawn with reportlab platypus flowables to match the on-screen cards.
Built in memory (BytesIO) and returned directly; nothing is written
to disk. `compress=False` exists for tests (searchable content).
"""

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

INK = colors.HexColor("#1c211d")
MUTED = colors.HexColor("#5c655e")
ACCENT = colors.HexColor("#2f4b8f")
DANGER = colors.HexColor("#a3291f")
DANGER_SOFT = colors.HexColor("#f6e4e2")
WARNING = colors.HexColor("#8a5a00")
WARNING_SOFT = colors.HexColor("#f6ecd9")
BORDER = colors.HexColor("#d7dbd2")


def _styles():
    base = getSampleStyleSheet()
    s = {}
    s["title"] = ParagraphStyle("title", parent=base["Title"], fontSize=16,
                                leading=20, textColor=INK)
    s["h2"] = ParagraphStyle("h2", parent=base["Heading2"], fontSize=12,
                             leading=15, textColor=INK, spaceBefore=10,
                             spaceAfter=4)
    s["body"] = ParagraphStyle("body", parent=base["Normal"], fontSize=9,
                               leading=12.5, textColor=INK)
    s["mono"] = ParagraphStyle("mono", parent=s["body"],
                               fontName="Courier", fontSize=8.5)
    s["muted"] = ParagraphStyle("muted", parent=s["body"], textColor=MUTED)
    s["headline"] = ParagraphStyle("headline", parent=s["body"], fontSize=11,
                                   leading=14, spaceAfter=6)
    return s


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(15 * mm, 12 * mm, "Database Normalization Checker")
    canvas.drawRightString(A4[0] - 15 * mm, 12 * mm, "page %d" % doc.page)
    canvas.restoreState()


def _esc(value):
    return str(value if value is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _col_table(table, s):
    dets, deps = set(), set()
    for f in table.get("fds") or []:
        dets.update(f.get("determinant") or [])
        deps.add(f.get("dependent"))
    rows = [[Paragraph("<b>Column</b>", s["body"]),
             Paragraph("<b>Type</b>", s["body"]),
             Paragraph("<b>Key</b>", s["body"]),
             Paragraph("<b>Role</b>", s["body"])]]
    row_bg = []
    for c in table.get("columns") or []:
        key = "PK" if c.get("pk") else ("FK" if c.get("fk") else "")
        role = "dependent" if c["name"] in deps else (
            "determinant" if c["name"] in dets else "")
        rows.append([Paragraph("<font face=\"Courier\">%s</font>" % _esc(c["name"]), s["body"]),
                     Paragraph(_esc(c.get("type", "")), s["body"]),
                     Paragraph(key, s["body"]),
                     Paragraph(("<b>%s</b>" % role) if role == "determinant" else role,
                               s["body"])])
        row_bg.append(DANGER_SOFT if c["name"] in deps else colors.white)
    t = Table(rows, colWidths=[62 * mm, 38 * mm, 22 * mm, 48 * mm])
    style = [("GRID", (0, 0), (-1, -1), 0.5, BORDER),
             ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f8f6")])]
    for i, bg in enumerate(row_bg):
        if bg != colors.white:
            style.append(("BACKGROUND", (0, i + 1), (-1, i + 1), bg))
    t.setStyle(TableStyle(style))
    return t


def _fd_paras(f, s):
    ev = f.get("evidence") or {}
    out = [Paragraph("<b>%s → %s</b>%s" % (
        _esc(", ".join(f.get("determinant") or [])), _esc(f.get("dependent")),
        " (equivalent)" if f.get("equivalent") else ""), s["body"])]
    coinc = (f.get("coincidence") or {}).get("text") or "—"
    out.append(Paragraph("Confidence Score: %s (chance of coincidence about %s)"
                         % (_esc(f.get("confidence")), _esc(coinc)), s["body"]))
    out.append(Paragraph("%s distinct values · %s repeated rows · %s candidates tested" % (
        _esc(ev.get("distinct_det", "—")), _esc(ev.get("repeated_rows", "—")),
        _esc(ev.get("candidates_tested", "—"))), s["body"]))
    if (f.get("exceptions") or 0) > 0:
        out.append(Paragraph("%s exception rows — holds with exceptions"
                             % _esc(f["exceptions"]), s["body"]))
    cross = ev.get("cross_table") or {}
    if cross.get("supported"):
        rate = cross.get("match_rate")
        pct = ("%.1f" % (rate * 100)) if rate is not None else "—"
        out.append(Paragraph("supported by %s.%s: %s%% match" % (
            _esc(cross.get("ref_table")), _esc(cross.get("ref_column")), pct),
            s["body"]))
    if ev.get("naming"):
        out.append(Paragraph("naming affinity: %s / %s" % (
            _esc(", ".join(f.get("determinant") or [])), _esc(f.get("dependent"))),
            s["body"]))
    for ex in (ev.get("examples") or [])[:3]:
        det = ", ".join(str(v)[:24] for v in (ex.get("det") or []))
        out.append(Paragraph("e.g. %s → %s in %s rows%s" % (
            _esc(det), _esc(str(ex.get("dep"))[:24]), _esc(ex.get("rows")),
            " (exception)" if ex.get("exception") else ""), s["mono"]))
    return out


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
    story = [Paragraph("Database Normalization Checker", s["title"])]
    meta = " · ".join(str(x) for x in [
        scan.get("db_type", "—"), scan.get("database", "—"),
        scan.get("started_at", "—"),
        ("%.1fs" % scan["duration_seconds"]) if scan.get("duration_seconds") is not None else None,
        scan.get("status", "—")] if x)
    story += [Paragraph(_esc(meta), s["muted"])]
    from .analyzer import headline
    story += [Paragraph(_esc(headline(
        counts, len(scan.get("selected_tables") or []),
        counts.get("not_selected", 0))), s["headline"])]
    tables = result.get("tables") or []
    if not tables:
        story += [Paragraph("No faulted or suspected tables.", s["body"])]
    for i, t in enumerate(tables):
        if i > 0:
            story.append(PageBreak())
        status = t.get("status", "faulted")
        nf = ("possibly " if status == "suspected" else "") + (t.get("normal_form") or "?")
        story += [Paragraph("%s <font color=\"%s\">[%s]</font>" % (
            _esc(t.get("name")), WARNING.hexval() if status == "suspected" else DANGER.hexval(),
            _esc(nf)), s["h2"])]
        if t.get("sampled"):
            story += [Paragraph("analysed first 50,000 rows in key order (sampled).", s["muted"])]
        story += [_col_table(t, s), Spacer(1, 3 * mm)]
        for f in t.get("fds") or []:
            story.extend(_fd_paras(f, s))
            story.append(Spacer(1, 2 * mm))
        for v in t.get("violations") or []:
            if v.get("reason"):
                story += [Paragraph("<b>Why this fails %s:</b> %s" % (
                    _esc(v.get("form")), _esc(v["reason"])), s["body"])]
        if t.get("recommendation"):
            story += [Paragraph("<b>Recommendation:</b> %s" % _esc(t["recommendation"]),
                                s["body"])]
    notices = result.get("notices") or []
    errs = [x for x in (result.get("table_summary") or []) if x.get("status") == "error"]
    if notices or errs:
        story += [Paragraph("Tables that could not be analysed", s["h2"])]
        for n in notices:
            story += [Paragraph("%s — %s" % (_esc(n.get("table")),
                                             _esc(n.get("code"))), s["body"])]
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
