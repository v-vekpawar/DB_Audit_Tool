"""1NF-BCNF classification from candidate keys plus scored FDs.

Exact normal form uses strong FDs only; violations visible only through
suspected FDs set the status to suspected. Tables below MIN_ROWS get
pattern checks (delimited, repeating) only, else insufficient_data.
"""

import re

from . import config
from .keys import canonical_map, is_superkey

_DELIMS = {",": "comma", ";": "semicolon", "|": "pipe"}
DELIM_MIN_SHARE = 0.3
DELIM_MIN_ROWS = 10
_STEM = re.compile(r"^(.+?)(\d+)$")


def _has_list_value(s):
    for d in _DELIMS:
        parts = s.split(d)
        if len(parts) >= 2 and all(p.strip() for p in parts):
            return d
    t = s.strip()
    if len(t) >= 2 and t.startswith("[") and t.endswith("]"):
        return ","
    return None


def delimited_columns(col_stats, n_rows=None):
    """[(col, delim_word, pct)] for multi-valued cells. Skips binary/json."""
    out = []
    for col, st in col_stats.items():
        if st.get("excluded_in") in ("binary", "json"):
            continue
        labels = st.get("labels") or []
        counts = st.get("counts") or []
        total = sum(counts)
        if not total:
            continue
        # Absolute floors must scale down to tiny tables, else a 5-row
        # table could never trip the pattern check.
        floor = DELIM_MIN_ROWS if n_rows is None else min(DELIM_MIN_ROWS, max(3, n_rows // 2))
        hits, delim = 0, None
        for lab, c in zip(labels, counts):
            d = _has_list_value(str(lab))
            if d is not None:
                hits += c
                delim = delim or d
        if hits >= floor and hits / total >= DELIM_MIN_SHARE:
            out.append((col, _DELIMS[delim], round(100 * hits / total)))
    return out


def _delim_sample(stat):
    """Most frequent multi-valued cell (read-only, from loaded rows)."""
    best, best_c = None, -1
    for lab, c in zip(stat.get("labels") or [], stat.get("counts") or []):
        if _has_list_value(str(lab)) is not None and c > best_c:
            best, best_c = str(lab), c
    if best is None:
        return None
    return best if len(best) <= 48 else best[:45] + "..."


def repeating_groups(columns):
    """{stem: [cols]} for stem+number groups (stems under 2 letters ignored)."""
    by_stem = {}
    for c in columns or []:
        m = _STEM.match(c.lower())
        if not m:
            continue
        stem = m.group(1)
        if sum(ch.isalpha() for ch in stem) < 2:
            continue
        by_stem.setdefault(stem, []).append(c)
    return {s: v for s, v in by_stem.items() if len(v) >= 2}


def _violations_for(table, fds, keys, canon):
    """One violation per canonical determinant.

    Members of an equivalence clique are one attribute here, so a clique
    reports once. Kind is decided from the displayed determinant and the
    original dependents: prime is tested on original columns (an
    equivalent of a key member is still non-prime), which keeps
    product_id -> product_name partial rather than BCNF.
    """
    prime_orig = set()
    for k in keys:
        prime_orig.update(k["columns"])
    groups = {}
    for f in fds:
        det = list(f["determinant"])
        dep = f["dependent"]
        if is_superkey(det, keys, canon):
            continue
        rdet = tuple(sorted({canon.get(c, c) for c in det}))
        g = groups.setdefault(rdet, {"dets": [], "deps": [], "equiv": False,
                                     "seen": 0})
        if det not in g["dets"]:
            g["dets"].append(det)
        if dep not in g["deps"]:
            g["deps"].append(dep)
        g["equiv"] = g["equiv"] or bool(f.get("equivalent"))
    violations = []
    for rdet, g in groups.items():
        def rank(d):
            return (0 if any(c in prime_orig for c in d) else 1,
                    sum(len(c) for c in d), len(d))
        det = sorted(g["dets"], key=rank)[0]
        deps = sorted(d for d in g["deps"] if d not in det)
        if not deps:
            continue
        if any(d in prime_orig for d in deps):
            form, kind = "BCNF", "bcnf"
        elif any(set(det) < set(k["columns"]) for k in keys):
            form, kind = "2NF", "partial"
        else:
            form, kind = "3NF", "transitive"
        # A mixed group (prime and non-prime dependents) reports the
        # lower form; the BCNF-only aspect is subsumed.
        if form == "BCNF" and any(d not in prime_orig for d in deps):
            if any(set(det) < set(k["columns"]) for k in keys):
                form, kind = "2NF", "partial"
            else:
                form, kind = "3NF", "transitive"
        violations.append({"form": form, "kind": kind,
                           "determinant": det, "dependents": deps,
                           "equivalent": g["equiv"]})
    return violations


def _key_text(keys):
    if not keys:
        return "(no key)"
    k = keys[0]["columns"]
    return "(%s)" % ", ".join(k)


def _add_reasons(table, violations, keys):
    key_text = _key_text(keys)
    for v in violations:
        det = ", ".join(v["determinant"])
        deps = ", ".join(v["dependents"])
        kind = v["kind"]
        if kind == "no-key":
            v["reason"] = "This table has no primary key and no unique column set, so rows cannot be told apart."
        elif kind == "delimited":
            v["reason"] = "Column %s holds %s-separated lists in %s%% of its non-null values." % (
                v["dependents"][0], v.get("detail", "comma"), v.get("pct", 0))
        elif kind == "repeating":
            v["reason"] = "Columns %s form a repeating group." % deps
        elif kind == "partial":
            v["reason"] = "%s depends on %s, which is only part of the key %s." % (deps, det, key_text)
        elif kind == "transitive":
            v["reason"] = "%s depends on %s, not on the key %s." % (deps, det, key_text)
        elif kind == "bcnf":
            v["reason"] = "%s determines %s but is not a key." % (det, deps)
    return violations


def classify(table, detail, strong, suspected, keys, col_stats, n_rows):
    """Returns {"normal_form", "status", "violations"}."""
    columns = [c["name"] for c in detail.get("columns", [])]
    key_list = keys.get("keys", [])

    one_nf = []
    for col, word, pct in delimited_columns(col_stats, n_rows):
        one_nf.append({"form": "1NF", "kind": "delimited",
                       "determinant": [], "dependents": [col],
                       "detail": word, "pct": pct,
                       "sample": _delim_sample(col_stats.get(col, {})),
                       "equivalent": False})
    for stem, cols in repeating_groups(columns).items():
        one_nf.append({"form": "1NF", "kind": "repeating",
                       "determinant": [], "dependents": sorted(cols),
                       "detail": stem.rstrip("_"), "equivalent": False})
    if not key_list and n_rows >= config.MIN_ROWS_FOR_DATA_FDS:
        one_nf.append({"form": "1NF", "kind": "no-key",
                       "determinant": [], "dependents": [],
                       "equivalent": False})

    if n_rows < config.MIN_ROWS_FOR_DATA_FDS:
        if one_nf:
            return {"normal_form": "below 1NF", "status": "faulted",
                    "violations": _add_reasons(table, one_nf, key_list)}
        return {"normal_form": None, "status": "insufficient_data", "violations": []}

    equiv_pairs = [(f["determinant"][0], f["dependent"])
                   for f in (strong + suspected)
                   if f.get("equivalent") and len(f["determinant"]) == 1]
    canon = canonical_map(equiv_pairs)

    strong_v = one_nf + _violations_for(table, strong, key_list, canon)
    below = [v for v in strong_v if v["form"] in ("1NF", "2NF", "3NF")]
    if below:
        order = {"1NF": 0, "2NF": 1, "3NF": 2}
        worst = min(below, key=lambda v: order[v["form"]])
        exact = {"1NF": "below 1NF", "2NF": "1NF", "3NF": "2NF"}[worst["form"]]
        return {"normal_form": exact, "status": "faulted",
                "violations": _add_reasons(table, strong_v, key_list)}

    susp_v = _violations_for(table, suspected, key_list, canon)
    below_s = [v for v in susp_v if v["form"] in ("1NF", "2NF", "3NF")]
    if below_s:
        order = {"1NF": 0, "2NF": 1, "3NF": 2}
        worst = min(below_s, key=lambda v: order[v["form"]])
        exact = {"1NF": "below 1NF", "2NF": "1NF", "3NF": "2NF"}[worst["form"]]
        return {"normal_form": exact, "status": "suspected",
                "violations": _add_reasons(table, susp_v, key_list)}

    bcnf_only = [v for v in strong_v if v["form"] == "BCNF"]
    exact = "3NF" if bcnf_only else "BCNF"
    return {"normal_form": exact, "status": "clean",
            "violations": _add_reasons(table, strong_v, key_list)}
