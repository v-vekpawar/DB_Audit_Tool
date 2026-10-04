"""One-sentence recommendations per violation (templates, no DDL)."""

_ORDER = {"1NF": 0, "2NF": 1, "3NF": 2, "BCNF": 3}


def recommend(table, violations):
    if not violations:
        return ""
    v = min(violations, key=lambda x: (_ORDER.get(x["form"], 9), x["kind"]))
    kind = v["kind"]
    det = list(v.get("determinant") or [])
    deps = list(v.get("dependents") or [])
    if kind == "no-key":
        return "Add a primary key to this table."
    if kind == "delimited":
        return "Store each value of %s in its own row of a child table instead of a %s-separated list." % (
            deps[0], v.get("detail", "comma"))
    if kind == "repeating":
        stem = v.get("detail", "value")
        return "Replace %s with a child table holding one %s per row." % (
            ", ".join(deps), stem)
    if kind == "partial":
        return "Move %s into a table keyed by %s; it depends on only part of the key." % (
            ", ".join(deps), ", ".join(det))
    if kind == "transitive":
        if v.get("equivalent"):
            a, b = det[0] if det else "", deps[0] if deps else ""
            return "%s and %s are equivalent columns; keep one as the key of a lookup table." % (a, b)
        return "Move %s into a separate table keyed by %s and keep %s as a foreign key in %s." % (
            ", ".join(deps), ", ".join(det), ", ".join(det), table)
    if kind == "bcnf":
        return "Move %s into a table keyed by %s; %s is not a key of %s." % (
            ", ".join(deps), ", ".join(det), ", ".join(det), table)
    return ""
