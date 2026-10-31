"""Coincidence scoring for candidate FDs (pure functions).

Data can disprove an FD but never prove one: every candidate is scored by
how unlikely the data would look this way by pure chance. Closed form in
log space (log-gamma), corrected for the number of candidates tested.
"""

import math
from dataclasses import dataclass, field

from . import config

_GENERIC_TOKENS = frozenset({
    "id", "key", "code", "no", "num", "number", "name", "desc", "description",
    "title", "value", "val", "date", "time", "flag", "status", "type", "kind",
    "text", "note", "memo", "ref", "seq", "pk", "fk",
})


def _log10_fall(a, g):
    """log10([a]_g) = log10(a*(a-1)*...*(a-g+1)), -inf if a < g."""
    if g <= 0:
        return 0.0
    if a < g:
        return float("-inf")
    return (math.lgamma(a + 1) - math.lgamma(a - g + 1)) / math.log(10)


def coincidence_exponent(group_sizes, y_counts, n):
    """e_raw = -log10(chance every determinant group is uniform by luck)."""
    log_n = {}
    e = 0.0
    for g in group_sizes:
        if g not in log_n:
            log_n[g] = _log10_fall(n, g)
        terms = [_log10_fall(c, g) for c in y_counts]
        best = max(terms)
        if best == float("-inf"):
            return float("inf")  # cannot happen for a holding FD; be safe
        s = sum(10.0 ** (t - best) for t in terms if t != float("-inf"))
        q = best + math.log10(s) - log_n[g]
        e -= q
    return e


def adjust_exponent(e_raw, m, k=0, n=0):
    """Correct for m candidates tested and C(n,k) dirty-row choices."""
    e = e_raw - math.log10(m) if m and m > 0 else e_raw
    if k:
        e -= (math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)) / math.log(10)
    return max(0.0, e)


def score_from_exponent(e):
    return min(99, round(100 * e / (e + 2)))


def coincidence_text(e):
    if e >= 7:
        return "1 in 10^%d" % round(e)
    return "1 in %s" % f"{10 ** e:,.0f}"


def naming_affinity(det, dep):
    """Display-only hint: determinant and dependent share a name stem."""
    def tokens(name):
        return {t for t in name.lower().split("_") if len(t) >= 3} - _GENERIC_TOKENS
    dep_tok = tokens(dep)
    if not dep_tok:
        return False
    for d in det:
        if tokens(d) & dep_tok:
            return True
    return False


@dataclass
class ScoredFD:
    determinant: list
    dependent: str
    kind: str  # exact | near
    equivalent: bool
    confidence: int
    tier: str  # strong | suspected | discarded
    exceptions: int = 0
    exception_rows: list = field(default_factory=list)
    exponent: float = 0.0
    coincidence: str = ""
    evidence: dict = field(default_factory=dict)


def group_rows(coded, det):
    """{key-tuple: [row indices]} over rows with no NULL in det (incl. singletons)."""
    cols = [coded[c] for c in det]
    groups = {}
    examined = 0
    for j in range(len(cols[0])):
        key = []
        for col in cols:
            v = col[j]
            if v is None:
                break
            key.append(v)
        else:
            examined += 1
            t = tuple(key)
            groups.setdefault(t, []).append(j)
    return groups, examined


def score_fd(coded, labels, fd, m, sampled=False, cross=None):
    """Score one candidate FD. Never raises on odd data (defensive)."""
    det = list(fd.determinant)
    dep = fd.dependent
    groups, examined = group_rows(coded, det)
    repeated_groups = {k: g for k, g in groups.items() if len(g) > 1}
    repeated_rows = sum(len(g) - 1 for g in repeated_groups.values())
    rows_in_groups = sum(len(g) for g in repeated_groups.values())
    distinct_det = len(groups)

    if examined == 0:
        return ScoredFD(det, dep, fd.kind, fd.equivalent, 0, "suspected",
                        exceptions=fd.k, exception_rows=list(fd.exception_rows),
                        exponent=0.0, coincidence="insufficient comparable rows",
                        evidence=_evidence(det, dep, coded, labels, fd, m,
                                           distinct_det, repeated_rows,
                                           rows_in_groups, cross))

    dep_col = coded[dep]
    if fd.kind == "near" and fd.exception_rows:
        skip = set(fd.exception_rows)
        clean_dep = [v for j, v in enumerate(dep_col)
                     if j not in skip and all(coded[c][j] is not None for c in det)]
        n_clean = len(clean_dep)
        from collections import Counter
        y_counts = list(Counter(clean_dep).values())
        # group sizes on cleaned data: drop exception rows from groups
        sizes = []
        for g in repeated_groups.values():
            rest = [r for r in g if r not in skip]
            if len(rest) >= 2:
                sizes.append(len(rest))
        e_raw = coincidence_exponent(sizes, y_counts, n_clean)
        e_adj = adjust_exponent(e_raw, m, k=fd.k, n=examined)
    else:
        from collections import Counter
        y_counts = list(Counter(v for j, v in enumerate(dep_col)
                                if all(coded[c][j] is not None for c in det)).values())
        sizes = [len(g) for g in repeated_groups.values()]
        e_raw = coincidence_exponent(sizes, y_counts, examined)
        e_adj = adjust_exponent(e_raw, m)

    score = score_from_exponent(e_adj)
    if not math.isfinite(e_adj):
        e_adj = 999.0  # unreachable for holding FDs; defensive only
        score = 99
    if fd.kind == "near":
        score = min(score, config.NEAR_FD_SCORE_CAP)
    if sampled:
        score = min(score, config.SAMPLED_SCORE_CAP)

    cross_ok = bool(cross and cross.get("supported"))
    coverage = (rows_in_groups / examined) if examined else 0.0
    enough_repetition = (repeated_rows >= config.STRONG_MIN_REPEATED_ROWS and
                         coverage >= config.STRONG_MIN_REPEATED_COVERAGE)
    if fd.kind == "near":
        # Near-FDs are never formal proof and therefore never become strong.
        # Keep only material near-FDs in the main evidence stream.
        tier = "suspected" if (repeated_rows >= config.MIN_REPEATED_ROWS and
                                score >= config.SUSPECT_CONFIDENCE) else "discarded"
    elif enough_repetition and (score >= config.STRONG_CONFIDENCE or (
            cross_ok and score >= config.STRONG_CONFIDENCE_CROSS_TABLE)):
        tier = "strong"
    elif (repeated_rows >= config.MIN_REPEATED_ROWS and
          score >= config.SUSPECT_CONFIDENCE):
        tier = "suspected"
    else:
        # Exact observations with too little repetition or too little
        # coincidence evidence are retained only as an aggregate count. They
        # must not drive normal-form classification or flood the main report.
        tier = "discarded"

    return ScoredFD(det, dep, fd.kind, fd.equivalent, score, tier,
                    exceptions=fd.k, exception_rows=list(fd.exception_rows),
                    exponent=e_adj, coincidence=coincidence_text(e_adj),
                    evidence=_evidence(det, dep, coded, labels, fd, m,
                                       distinct_det, repeated_rows,
                                       rows_in_groups, cross))


def _evidence(det, dep, coded, labels, fd, m, distinct_det, repeated_rows,
              rows_in_groups, cross):
    nulls = {}
    n = len(next(iter(coded.values()), []))
    for c in det + [dep]:
        col = coded.get(c, [])
        nulls[c] = sum(1 for v in col if v is None) / len(col) if col else 0.0
    examples = _examples(coded, labels, det, dep, fd)
    ev = {"distinct_det": distinct_det, "repeated_rows": repeated_rows,
          "rows_in_repeated_groups": rows_in_groups,
          "candidates_tested": m, "cross_table": cross,
          "naming": naming_affinity(det, dep), "null_ratio": nulls,
          "repeated_coverage": (rows_in_groups / n if n else 0.0),
          "repeated_row_coverage": (repeated_rows / n if n else 0.0),
          "examples": examples, "n": n}
    return ev


def _examples(coded, labels, det, dep, fd, limit=5):
    groups, _ = group_rows(coded, det)
    multi = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    out = []
    dep_labels = labels.get(dep, [])
    det_labels = [labels.get(c, []) for c in det]

    def show(code, lab):
        if isinstance(code, int) and 0 <= code < len(lab):
            return lab[code]
        return code

    for key, rows in multi[:limit]:
        det_vals = [show(c, lab) for c, lab in zip(key, det_labels)]
        dep_vals = {coded[dep][r] for r in rows}
        dep_show = show(next(iter(dep_vals)), dep_labels) if len(dep_vals) == 1 else "..."
        out.append({"det": det_vals, "dep": dep_show, "rows": len(rows)})
    for r in list(fd.exception_rows)[:limit]:
        det_vals = [show(coded[c][r], lab) for c, lab in zip(det, det_labels)]
        out.append({"det": det_vals, "dep": show(coded[dep][r], dep_labels),
                    "rows": 1, "exception": True})
    return out[: limit + len(fd.exception_rows[:limit])]
