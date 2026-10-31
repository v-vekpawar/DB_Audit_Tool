"""Formal 1NF/2NF/3NF/BCNF classification with explicit uncertainty.

The classifier never calls a data heuristic a guaranteed normal-form violation.
2NF is based strictly on partial dependencies from proper subsets of composite
candidate keys. 3NF and BCNF are evaluated from candidate keys and exact FDs.
"""

import json
import re

from . import config
from .keys import is_superkey

_DELIMS = {",": "comma", ";": "semicolon", "|": "pipe"}
DELIM_MIN_SHARE = 0.8
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
    """Inspect observed values for possible multi-valued cells.

    This is evidence gathering, not a semantic proof. A recurring token
    pattern is stronger evidence; a delimiter alone remains a suspicion.
    """
    out = []
    for col, st in col_stats.items():
        labels = st.get("labels") or []
        counts = st.get("counts") or []
        total = sum(counts)
        if not total:
            continue
        hits, delim = 0, None
        token_seen = {}
        for lab, c in zip(labels, counts):
            text = str(lab)
            d = _has_list_value(text)
            if d is None:
                continue
            hits += c
            delim = delim or d
            if d in _DELIMS:
                for part in text.split(d):
                    token = part.strip().lower()
                    if 0 < len(token) <= 40:
                        token_seen[token] = token_seen.get(token, 0) + 1
        floor = DELIM_MIN_ROWS if n_rows is None else min(
            DELIM_MIN_ROWS, max(3, n_rows // 2))
        if hits < floor or hits / total < DELIM_MIN_SHARE:
            continue
        recurring = {t for t, seen in token_seen.items() if seen >= 2}
        evidence = "STRONG_HEURISTIC" if len(recurring) >= 2 else "SUSPECTED"
        out.append((col, _DELIMS.get(delim, "list-like"), round(100 * hits / total),
                    evidence))
    return out


def json_array_columns(col_stats):
    """Detect JSON-array-like observed values without treating JSON text as proof."""
    out = []
    for col, st in col_stats.items():
        typ = str(st.get("type", "")).upper()
        labels = st.get("labels") or []
        counts = st.get("counts") or []
        if "JSON" not in typ and "JSONB" not in typ:
            # SQL Server stores JSON in text types, so inspect list-like JSON
            # values there too; only report when the observed value parses as
            # an actual JSON array consistently.
            pass
        total = sum(counts)
        if not total:
            continue
        hits = 0
        for lab, c in zip(labels, counts):
            try:
                parsed = json.loads(str(lab))
            except Exception:
                continue
            if isinstance(parsed, list):
                hits += c
        if hits and hits / total >= DELIM_MIN_SHARE:
            level = "STRONG" if ("JSON" in typ or "JSONB" in typ or "ARRAY" in typ) else "STRONG_HEURISTIC"
            out.append((col, round(100 * hits / total), level))
    return out


def _delim_sample(stat):
    best, best_c = None, -1
    for lab, c in zip(stat.get("labels") or [], stat.get("counts") or []):
        if _has_list_value(str(lab)) is not None and c > best_c:
            best, best_c = str(lab), c
    if best is None:
        return None
    return best if len(best) <= 48 else best[:45] + "..."


def repeating_groups(column_defs):
    """Heuristic repeating-group detector requiring consecutive suffixes and
    compatible database types."""
    by_stem = {}
    for item in column_defs or []:
        if isinstance(item, str):
            name, typ = item, ""
        else:
            name, typ = item.get("name", ""), str(item.get("type", ""))
        m = _STEM.match(name.lower())
        if not m:
            continue
        stem = m.group(1)
        if sum(ch.isalpha() for ch in stem) < 2:
            continue
        by_stem.setdefault(stem, []).append((name, typ))
    out = {}
    for stem, items in by_stem.items():
        nums = []
        types = []
        for name, typ in items:
            m = _STEM.match(name.lower())
            nums.append(int(m.group(2)))
            types.append(typ.upper())
        if len(items) >= 2 and sorted(nums) == list(range(min(nums), max(nums) + 1)):
            if len(set(types)) == 1:
                out[stem] = sorted(name for name, _ in items)
    return out


def _exact_violations(fds, keys, min_confidence=0):
    """Return formal 2NF/3NF/BCNF violations from exact FDs."""
    prime = set()
    for k in keys:
        prime.update(k["columns"])

    partial = []
    third = []
    bcnf = []
    for f in fds:
        if f.get("kind") != "exact":
            continue
        confidence = f.get("confidence")
        if confidence is not None and confidence < min_confidence:
            continue
        det = set(f["determinant"])
        dep = f["dependent"]
        if dep in det:
            continue
        superkey = is_superkey(det, keys)

        # 2NF: a non-prime attribute depends on a proper subset of a composite
        # candidate key. A one-column key can never satisfy this condition.
        for k in keys:
            kset = set(k["columns"])
            if len(kset) > 1 and det < kset and dep not in prime:
                partial.append({"form": "2NF", "kind": "partial",
                                "determinant": list(f["determinant"]),
                                "dependents": [dep],
                                "evidence_fd": f})
                break

        if not superkey and dep not in prime:
            third.append({"form": "3NF", "kind": "transitive",
                          "determinant": list(f["determinant"]),
                          "dependents": [dep], "evidence_fd": f})
        if not superkey:
            bcnf.append({"form": "BCNF", "kind": "bcnf",
                         "determinant": list(f["determinant"]),
                         "dependents": [dep], "evidence_fd": f})

    return partial, third, bcnf


def _keys_are_declared(keys):
    return bool(keys) and all(k.get("source") == "declared" for k in keys)


def _demote_if_inferred(violations, keys):
    if _keys_are_declared(keys):
        return violations
    out = []
    for v in violations:
        v = dict(v)
        v["evidence_level"] = "SUSPECTED"
        v["reason"] = v.get("reason", "") + " Candidate-key evidence is data-derived, so this result is not guaranteed by the schema."
        out.append(v)
    return out


def _exception_driven_exact_fds(strong, suspected):
    """Return strong exact FDs that are likely induced by a near-FD parent.

    A common dirty-data pattern is X -> Y with a few exceptions. In that case
    any strict superset Z of X may happen to determine Y exactly simply because
    Z separates the exception rows. Treating such Z -> Y as a formal
    normalization defect would turn the near-FD into a false hard violation.
    These exact supersets remain available as diagnostic FDs, but are removed
    from the formal set.
    """
    near_parents = []
    for f in suspected:
        if f.get("kind") != "near":
            continue
        det = set(f.get("determinant") or [])
        dep = f.get("dependent")
        if det:
            near_parents.append((det, dep))

    if not near_parents:
        return set()

    demote = set()
    for i, f in enumerate(strong):
        if f.get("kind") != "exact":
            continue
        det = set(f.get("determinant") or [])
        dep = f.get("dependent")
        for parent, parent_dep in near_parents:
            if parent_dep == dep and parent < det:
                demote.add(i)
                break
    return demote


def _filter_exception_driven_exact_fds(strong, suspected):
    demote = _exception_driven_exact_fds(strong, suspected)
    return [f for i, f in enumerate(strong) if i not in demote]


def _heuristic_1nf(col_stats, columns, n_rows):
    out = []
    json_cols = {col: (pct, level) for col, pct, level in json_array_columns(col_stats)}
    for col, (pct, evidence_level) in json_cols.items():
        out.append({"form": "1NF", "kind": "json-array", "determinant": [],
                    "dependents": [col], "detail": "JSON array",
                    "pct": pct, "evidence_level": evidence_level})
    for col, word, pct, evidence_level in delimited_columns(col_stats, n_rows):
        if col in json_cols:
            continue
        out.append({"form": "1NF", "kind": "delimited", "determinant": [],
                    "dependents": [col], "detail": word, "pct": pct,
                    "sample": _delim_sample(col_stats.get(col, {})),
                    "evidence_level": evidence_level})
    for stem, cols in repeating_groups(columns).items():
        # Column naming alone is never treated as definitive. This remains a
        # schema-pattern heuristic and is explicitly marked as suspected.
        out.append({"form": "1NF", "kind": "repeating", "determinant": [],
                    "dependents": sorted(cols), "detail": stem.rstrip("_"),
                    "evidence_level": "SUSPECTED"})
    return out


def _key_warning(keys, key_status, complete_data):
    if keys:
        return None
    if key_status == "incomplete" or not complete_data:
        return {"form": "KEY", "kind": "unknown-key", "determinant": [],
                "dependents": [], "evidence_level": "UNKNOWN"}
    return {"form": "KEY", "kind": "no-key", "determinant": [],
            "dependents": [], "evidence_level": "UNKNOWN"}


def _add_reasons(violations, keys):
    key_texts = ["(%s)" % ", ".join(k["columns"]) for k in keys]
    key_text = "; ".join(key_texts) if key_texts else "(unknown)"
    for v in violations:
        kind = v["kind"]
        det = ", ".join(v.get("determinant", []))
        deps = ", ".join(v.get("dependents", []))
        if kind == "partial":
            v["reason"] = ("%s depends on %s, which is a proper subset of a "
                            "composite candidate key (%s)." % (deps, det, key_text))
        elif kind == "transitive":
            v["reason"] = "%s depends on %s, which is not a superkey; %s is non-prime." % (
                deps, det, deps)
        elif kind == "bcnf":
            v["reason"] = "%s determines %s but is not a superkey." % (det, deps)
        elif kind == "delimited":
            if v.get("evidence_level") == "STRONG_HEURISTIC":
                v["reason"] = ("%s shows a recurring delimiter-separated multi-value "
                               "pattern in %s%% of observed non-null values; this is "
                               "strong heuristic evidence, not a semantic proof." %
                               (deps, v.get("pct", 0)))
            else:
                v["reason"] = ("%s appears to contain delimiter-separated values in %s%% "
                               "of observed non-null values; this is a heuristic suspicion." %
                               (deps, v.get("pct", 0)))
        elif kind == "json-array":
            v["reason"] = ("%s contains JSON-array values in %s%% of observed non-null "
                           "values; this is strong structural evidence of multiple "
                           "values in one cell." % (deps, v.get("pct", 0)))
        elif kind == "repeating":
            v["reason"] = ("Columns %s match a numbered repeating-group pattern; "
                            "this is a heuristic suspicion." % deps)
        elif kind == "no-key":
            v["reason"] = "No declared or data-inferred candidate key was established."
        elif kind == "unknown-key":
            v["reason"] = "Candidate-key search was incomplete, so higher normal forms cannot be established definitively."
    return violations


def classify(table, detail, strong, suspected, keys, col_stats, n_rows,
             complete_data=True, fd_search_complete=True):
    """Classify normal forms using the first failed normal-form boundary.

    Reporting is intentionally hierarchical:
      * suspected/failed 1NF -> show only 1NF findings; do not judge 2NF+.
      * 2NF failure -> show only partial-dependency findings.
      * 3NF failure -> show only transitive-dependency findings.
      * BCNF-only failure -> table is still 3NF; show BCNF as an informational
        note, never as a below-3NF fault.
    """
    # Use schema-guaranteed keys for formal normalization whenever available.
    # Observed alternate keys are evidence, but must not silently change prime
    # attributes or create/remove formal 2NF/3NF/BCNF violations.
    key_list = keys.get("formal_keys") or keys.get("keys", [])
    key_status = keys.get("status", "unknown")
    one_nf = _heuristic_1nf(col_stats, detail.get("columns", []), n_rows)

    key_warning = _key_warning(key_list, key_status, complete_data)
    key_ready = bool(key_list) and key_status == "known"

    # A suspected 1NF heuristic is an explicit boundary: higher normal forms
    # are not reported because they presuppose a 1NF relation.
    if one_nf:
        violations = list(one_nf)
        if key_warning:
            violations.append(key_warning)
        _add_reasons(violations, key_list)
        return {"normal_form": "below 1NF", "current_normal_form": "below 1NF",
                "violated_normal_form": "1NF", "status": "suspected",
                "violations": violations, "analysis_complete": False}

    # Very small relations do not provide enough repeated observations for
    # reliable data-derived FD inference. Keep schema heuristics visible, but
    # do not turn the absence of evidence into a normal-form fault.
    if n_rows < config.MIN_ROWS_FOR_DATA_FDS:
        warnings = []
        if key_warning:
            warnings.append(key_warning)
        _add_reasons(warnings, key_list)
        return {"normal_form": "unknown", "current_normal_form": "unknown",
                "violated_normal_form": None, "status": "insufficient_data",
                "violations": warnings, "analysis_complete": False}

    # Missing/incomplete candidate-key information prevents a definitive
    # 2NF/3NF/BCNF classification. It is not itself a 1NF violation.
    if not key_ready:
        warnings = [key_warning] if key_warning else []
        _add_reasons(warnings, key_list)
        return {"normal_form": "1NF", "current_normal_form": "1NF",
                "violated_normal_form": None, "status": "incomplete",
                "violations": warnings, "analysis_complete": False,
                "analysis_state": "unconfirmed",
                "analysis_limitations": ["Not enough evidence to confirm 2NF, 3NF, or BCNF; candidate key was not established."]}

    # Only strong exact FDs are allowed to affect the normal-form boundary.
    # A merely suspected FD is useful diagnostic evidence, but allowing it to
    # fail 2NF/3NF/BCNF would turn weak data coincidence into a formal result.
    # Keep those observations separately so the UI/API can expose them without
    # corrupting the classification.
    # Do not let exact supersets created by dirty rows turn a near-FD into a
    # hard normalization violation. The exact supersets are still retained in
    # the FD evidence list for inspection.
    formal_fds = _filter_exception_driven_exact_fds(strong, suspected)
    formal_partial, formal_third, formal_bcnf = _exact_violations(formal_fds, key_list)
    suspected_partial, suspected_third, suspected_bcnf = _exact_violations(
        suspected, key_list, min_confidence=config.SUSPECT_CONFIDENCE)

    complete = bool(complete_data and fd_search_complete and key_status == "known")
    possible = suspected_partial + suspected_third + suspected_bcnf
    declared_keys = _keys_are_declared(key_list)

    # First failed boundary: 2NF. Do not report downstream 3NF/BCNF findings.
    if formal_partial:
        violations = _demote_if_inferred(formal_partial, key_list)
        _add_reasons(violations, key_list)
        return {"normal_form": "1NF", "current_normal_form": "1NF",
                "violated_normal_form": "2NF",
                "status": "faulted" if declared_keys else "suspected",
                "violations": violations,
                "possible_violations": _add_reasons(possible, key_list),
                "analysis_complete": complete and declared_keys}

    # First failed boundary: 3NF. Do not report BCNF findings here.
    if formal_third:
        violations = _demote_if_inferred(formal_third, key_list)
        _add_reasons(violations, key_list)
        return {"normal_form": "2NF", "current_normal_form": "2NF",
                "violated_normal_form": "3NF",
                "status": "faulted" if declared_keys else "suspected",
                "violations": violations,
                "possible_violations": _add_reasons(possible, key_list),
                "analysis_complete": complete and declared_keys}

    # No 2NF/3NF failure: the table is at least 3NF. BCNF-only findings are
    # informational and must never make the table a below-3NF fault.
    if formal_bcnf:
        violations = _demote_if_inferred(formal_bcnf, key_list)
        _add_reasons(violations, key_list)
        return {"normal_form": "3NF", "current_normal_form": "3NF",
                "violated_normal_form": "BCNF",
                "status": "bcnf_faulted" if declared_keys and complete else "bcnf_suspected",
                "violations": violations,
                "possible_violations": _add_reasons(possible, key_list),
                "analysis_complete": complete and declared_keys}

    # No strong 2NF/3NF/BCNF violation. Suspected FDs remain diagnostic
    # evidence only and do not lower a confirmed normal form. If FD search is
    # incomplete, however, we cannot prove 2NF (and therefore cannot prove
    # 3NF/BCNF either). Report the last boundary that is actually established:
    # 1NF, and make the higher-form uncertainty explicit.
    if not complete:
        return {
            "normal_form": "1NF", "current_normal_form": "1NF",
            "violated_normal_form": None,
            "status": "incomplete",
            "violations": [],
            "possible_violations": _add_reasons(possible, key_list),
            "analysis_complete": False,
            "analysis_state": "unconfirmed",
            "analysis_limitations": [
                "Not enough evidence to confirm 2NF, 3NF, or BCNF; FD search was incomplete."
            ]}

    return {"normal_form": "BCNF", "current_normal_form": "BCNF",
            "violated_normal_form": None,
            "status": "clean",
            "violations": [],
            "possible_violations": _add_reasons(possible, key_list),
            "analysis_complete": True,
            "analysis_state": "confirmed"}
