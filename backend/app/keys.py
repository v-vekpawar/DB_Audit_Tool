"""Candidate-key discovery.

Declared keys/unique constraints are schema evidence. Data-derived keys are
only observations over the analysed relation and are never treated as schema
guarantees. Composite inferred keys are supported because they are essential
for correct 2NF analysis.
"""

from itertools import combinations

from . import config


def _is_unique(coded, cols, n):
    """Return True only when every row has a non-NULL key and tuples are unique."""
    if not cols or n <= 0:
        return False
    data = [coded[c] for c in cols]
    seen = set()
    for j in range(n):
        t = tuple(col[j] for col in data)
        if any(v is None for v in t):
            return False
        if t in seen:
            return False
        seen.add(t)
    return True


def _declared_key_candidates(detail):
    columns = {c["name"]: c for c in detail.get("columns", [])}
    out = []
    pk = list(detail.get("pk") or [])
    if pk:
        out.append({"columns": pk, "source": "declared", "guaranteed": True})

    # A UNIQUE constraint is a candidate key only if all participating columns
    # are NOT NULL. SQL UNIQUE permits multiple NULLs on the supported engines.
    for u in detail.get("uniques") or []:
        u = list(u)
        if not u or any(columns.get(c, {}).get("nullable", True) for c in u):
            continue
        out.append({"columns": u, "source": "declared", "guaranteed": True})

    # Unique indexes can also enforce uniqueness when no UNIQUE constraint was
    # exposed by the inspector. Again, nullable columns are not sufficient.
    for idx in detail.get("indexes") or []:
        if not idx.get("unique"):
            continue
        u = list(idx.get("columns") or [])
        if not u or any(columns.get(c, {}).get("nullable", True) for c in u):
            continue
        out.append({"columns": u, "source": "declared", "guaranteed": True})

    # A candidate key must be minimal. If a shorter declared key is already
    # known, a longer UNIQUE/unique-index set containing it is only a superkey,
    # not another candidate key.
    seen = set()
    result = []
    for k in sorted(out, key=lambda x: (len(x["columns"]), tuple(x["columns"]))):
        t = tuple(k["columns"])
        if t in seen:
            continue
        ks = set(t)
        if any(set(prev["columns"]) < ks for prev in result):
            continue
        seen.add(t)
        result.append(k)
    return result


def _infer_candidate_keys(coded, columns, n_rows, existing, source="inferred"):
    """Find all minimal unique sets, level by level.

    The search stops after the first level containing candidate keys because a
    larger unique set cannot be minimal once a smaller unique set exists.
    A safety cap prevents pathological schemas from exhausting memory; the
    caller receives an explicit incomplete flag in that case.
    """
    if n_rows < config.MIN_ROWS_FOR_INFERRED_KEYS:
        return [], False

    cols = sorted(c for c in columns if c in coded)
    existing_sets = [set(k["columns"]) for k in existing]
    found = []
    tested = 0
    complete = True

    for size in range(1, len(cols) + 1):
        level_found = []
        for combo in combinations(cols, size):
            tested += 1
            if tested > config.MAX_INFERRED_KEY_COMBINATIONS:
                complete = False
                break
            s = set(combo)
            if any(k <= s for k in existing_sets + [set(x["columns"]) for x in found]):
                continue
            if _is_unique(coded, combo, n_rows):
                level_found.append({
                    "columns": list(combo),
                    "source": source,
                    "guaranteed": False,
                })
        if not complete:
            break
        if level_found:
            found.extend(level_found)
            # All candidate keys are minimal; no larger key can be minimal.
            break
    return found, complete


def candidate_keys(detail, coded, columns, n_rows, complete_data=True):
    """Return declared and data-inferred candidate keys plus evidence status."""
    declared = _declared_key_candidates(detail)
    inferred = []
    complete = True
    if n_rows >= config.MIN_ROWS_FOR_INFERRED_KEYS:
        inferred, search_complete = _infer_candidate_keys(
            coded, columns, n_rows, declared,
            source="inferred" if complete_data else "inferred_sample")
        complete = bool(complete_data and search_complete)
    else:
        complete = bool(complete_data)

    # A declared key can be non-minimal in malformed/legacy schemas. Do not
    # silently remove it; it remains schema evidence. Inferred keys that are
    # supersets of any declared key are not candidate keys.
    all_keys = declared[:]
    for k in inferred:
        ks = set(k["columns"])
        if any(set(d["columns"]) <= ks for d in declared):
            continue
        all_keys.append(k)

    # Formal normalization analysis must not mix data-inferred alternate keys
    # into schema-guaranteed candidate-key semantics when declared keys exist.
    # Such observed keys remain useful evidence and are returned separately.
    # If the schema declares no candidate key, inferred keys may be used as
    # observed candidate-key evidence, but never as guaranteed schema facts.
    formal_keys = declared[:] if declared else inferred[:]

    # If no key was found, distinguish "no evidence" from "search incomplete".
    status = "known" if all_keys and complete else ("incomplete" if not complete else "unknown")
    prime = set()
    for k in formal_keys:
        prime.update(k["columns"])

    return {
        "keys": all_keys,
        "formal_keys": formal_keys,
        "prime": prime,
        "status": status,
        "complete": complete,
        "declared": [k for k in all_keys if k.get("source") == "declared"],
        "inferred": [k for k in all_keys if k.get("source") in ("inferred", "inferred_sample", "inferred_verified")],
    }


def canonical_map(equiv_pairs):
    """col -> canonical representative over 1-1 equivalent column pairs."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for a, b in equiv_pairs:
        union(a, b)
    return {c: find(c) for c in parent}


def is_superkey(cols, keys, canon=None):
    canon = canon or {}
    rep = lambda c: canon.get(c, c)  # noqa: E731
    want = {rep(c) for c in cols}
    for k in keys:
        if {rep(c) for c in k["columns"]} <= want:
            return True
    return False
