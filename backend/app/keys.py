"""Candidate keys: declared keys are certain, the rest is inferred."""

from itertools import combinations


def _is_unique(coded, cols, n):
    seen = set()
    data = [coded[c] for c in cols]
    for j in range(n):
        t = tuple(col[j] for col in data)
        if t in seen:
            return False
        seen.add(t)
    return True


def candidate_keys(detail, coded, columns, n_rows):
    """Returns {"keys": [{"columns": [...], "source": ...}], "prime": set}."""
    keys = []
    pk = list(detail.get("pk") or [])
    if pk:
        keys.append({"columns": pk, "source": "declared"})
    for u in detail.get("uniques") or []:
        u = list(u)
        if u and not any(set(u) >= set(k["columns"]) for k in keys):
            keys.append({"columns": u, "source": "declared"})
    if not keys:
        live = [c for c in columns if c in coded]
        for size in (1, 2, 3):
            found_here = []
            for combo in combinations(sorted(live), size):
                if any(set(k["columns"]) <= set(combo) for k in keys):
                    continue
                if _is_unique(coded, combo, n_rows):
                    found_here.append({"columns": list(combo),
                                       "source": "inferred"})
            keys.extend(found_here)
            if keys:
                break
    prime = set()
    for k in keys:
        prime.update(k["columns"])
    return {"keys": keys, "prime": prime}


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
