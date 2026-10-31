"""Normalization recommendations derived from the complete violation set.

Recommendations are deliberately dependency-oriented rather than one-sentence
per FD. Equivalent determinants are consolidated so the same fact is not
suggested as multiple lookup tables.
"""

_ORDER = {"1NF": 0, "2NF": 1, "3NF": 2, "BCNF": 3}


def _det(v):
    return tuple(v.get("determinant") or ())


def _equivalence_classes(violations):
    """Union determinant columns that are explicitly marked equivalent."""
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
            parent[rb] = ra

    for v in violations:
        fd = v.get("evidence_fd") or {}
        if not fd.get("equivalent"):
            continue
        det = list(v.get("determinant") or ())
        deps = list(v.get("dependents") or ())
        if len(det) == 1 and len(deps) == 1:
            union(det[0], deps[0])
    return parent, find


def _canonical_determinants(violations):
    """Map related singleton determinants to one representative.

    If A -> B and B is itself a determinant, A and B belong to the same
    dependency group for remediation. Reciprocal A <-> B dependencies are
    likewise one group. The representative is the column with the strongest
    explanatory coverage, with a deterministic name tie-break.
    """
    ds = {v.get("determinant", [None])[0] for v in violations
          if len(v.get("determinant") or []) == 1}
    score = {d: 0 for d in ds}
    adj = {d: set() for d in ds}
    for v in violations:
        det = v.get("determinant") or []
        deps = v.get("dependents") or []
        if len(det) == 1:
            score[det[0]] = score.get(det[0], 0) + len(deps)
            if len(deps) == 1 and deps[0] in ds:
                adj[det[0]].add(deps[0])
                adj[deps[0]].add(det[0])

    comp_root = {}
    seen = set()
    for start in ds:
        if start in seen:
            continue
        stack = [start]
        comp = []
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            comp.append(cur)
            stack.extend(adj.get(cur, ()))
        root = sorted(comp, key=lambda x: (-score.get(x, 0), x))[0]
        for col in comp:
            comp_root[col] = root

    out = {}
    for v in violations:
        d = tuple(v.get("determinant") or ())
        if len(d) == 1:
            out[d] = (comp_root.get(d[0], d[0]),)
        else:
            out[d] = d
    return out


def recommend(table, violations, keys=None):
    if not violations:
        return ""

    kind = min(violations, key=lambda x: (_ORDER.get(x.get("form"), 9), x.get("kind", ""))).get("kind")

    if kind == "no-key":
        return "Define a declared candidate key before relying on 2NF/3NF/BCNF results."
    if kind == "unknown-key":
        return "Complete candidate-key discovery or declare a key before making a higher-normal-form judgment."
    if kind == "delimited":
        deps = sorted({d for v in violations if v.get("kind") == "delimited"
                       for d in (v.get("dependents") or [])})
        return "Store each value of %s in its own row of a child table instead of delimiter-separated lists." % ", ".join(deps) if deps else "Store multi-valued cells in a child table instead of delimiter-separated lists."
    if kind == "repeating":
        deps = sorted({d for v in violations if v.get("kind") == "repeating"
                       for d in (v.get("dependents") or [])})
        stems = sorted({v.get("detail", "value") for v in violations if v.get("kind") == "repeating"})
        stem = ", ".join(stems) if stems else "value"
        return "Replace %s with a child table holding one %s per row." % (", ".join(deps), stem)

    relevant = [v for v in violations if v.get("kind") == kind]
    canonical = _canonical_determinants(relevant)
    groups = {}
    for v in relevant:
        det = canonical.get(_det(v), _det(v))
        deps = groups.setdefault(det, [])
        for dep in v.get("dependents") or []:
            if dep not in deps and dep not in det:
                deps.append(dep)

    if kind == "partial":
        chunks = [
            "move %s into a table keyed by %s; it depends on only part of the key" % (
                ", ".join(deps), ", ".join(det))
            for det, deps in groups.items()
        ]
        return "Fix the partial dependencies: " + "; ".join(chunks) + "."

    if kind == "transitive":
        chunks = [
            "move %s into a separate table keyed by %s and keep %s as a foreign key in %s" % (
                ", ".join(deps), ", ".join(det), ", ".join(det), table)
            for det, deps in groups.items()
        ]
        return "Fix the transitive dependencies: " + "; ".join(chunks) + "."

    if kind == "bcnf":
        chunks = [
            "move %s into a table keyed by %s; %s is not a key of %s" % (
                ", ".join(deps), ", ".join(det), ", ".join(det), table)
            for det, deps in groups.items()
        ]
        return "Fix the BCNF dependencies: " + "; ".join(chunks) + "."

    return ""
