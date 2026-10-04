"""Cross-table evidence: stored copies through declared foreign keys.

If T has a foreign key A -> R.K and also holds column B while R has a
non-key column B', compare T's (A, B) pairs with R's (K, B') pairs. A
>=97% match over >=5 distinct A values is near-certain redundancy: the
finding A -> B is marked supported and may reach the strong tier sooner.
Pure functions over in-memory rows; one narrow read fetches R's pairs.
"""

from . import config

MIN_DISTINCT_A = 5


def build_ref_map(ref_rows):
    """{K value: B' value} from raw (K, B') pairs.

    K must determine B' inside R; inconsistent K values are dropped.
    NULL keys never match.
    """
    m, bad = {}, set()
    for k, b in ref_rows:
        if k is None or k in bad:
            if k is not None and k in m and m[k] != b:
                bad.add(k)
            continue
        if k in m:
            if m[k] != b:
                bad.add(k)
                del m[k]
        else:
            m[k] = b
    return m


def match_rate(t_det_vals, t_dep_vals, ref_map):
    """(rate, comparable, distinct_a) over T's raw (A, B) values."""
    comp = hit = 0
    seen = set()
    for a, b in zip(t_det_vals, t_dep_vals):
        if a is None:
            continue
        comp += 1
        seen.add(a)
        if b is not None and a in ref_map and ref_map[a] == b:
            hit += 1
    return (hit / comp) if comp else 0.0, comp, len(seen)


def check_support(t_det_vals, t_dep_vals, ref_rows,
                  min_match=config.CROSS_TABLE_MATCH_MIN,
                  min_distinct=MIN_DISTINCT_A):
    """Decide cross-table support for one A -> B finding."""
    ref_map = build_ref_map(ref_rows)
    rate, comp, distinct_a = match_rate(t_det_vals, t_dep_vals, ref_map)
    supported = comp > 0 and distinct_a >= min_distinct and rate >= min_match
    return {"supported": supported, "match_rate": round(rate, 4),
            "comparable": comp, "distinct_a": distinct_a}


def load_ref_pairs(engine_or_conn, db_type, table, k_col, b_col,
                   schema=None, scan_id="-", limit=None):
    """Read the two columns needed from the referenced table."""
    from .loader import load_columns
    rows = load_columns(engine_or_conn, db_type, table, [k_col, b_col],
                        schema=schema, scan_id=scan_id, limit=limit)
    return [(r[0], r[1]) for r in rows]
