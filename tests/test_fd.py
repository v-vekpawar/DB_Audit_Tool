"""Step 5 checkpoint: handmade FD cases + brute-force reference parity.

The brute-force reference below enumerates every determinant and
minimises afterwards; the production search is level-wise with pruning.
Both must agree on every test table.
"""

from itertools import combinations

from app import fd_discovery as fd
from app.fd_discovery import (
    _check_groups,
    _mark_equivalent,
    _null_masks,
    count_candidates,
    discover,
)


def code_cols(raw):
    coded = {}
    for name, vals in raw.items():
        mapping, col = {}, []
        for v in vals:
            if v is None:
                col.append(None)
                continue
            if v not in mapping:
                mapping[v] = len(mapping)
            col.append(mapping[v])
        coded[name] = col
    return coded


def brute(coded, columns, n, declared_keys=None, limit=2):
    """Reference: enumerate every determinant, test, then minimise."""
    masks = _null_masks(coded, columns, n)
    live = [c for c in columns
            if len(set(v for v in coded[c] if v is not None)) > 1]
    single_keys = set()
    for k in declared_keys or []:
        if len(k) == 1:
            single_keys.update(k)

    def examined(det):
        m = 0
        for c in det:
            m |= masks[c]
        return n - m.bit_count()

    def groups_of(det):
        cols = [coded[c] for c in det]
        groups = {}
        for j in range(n):
            key = []
            for col in cols:
                v = col[j]
                if v is None:
                    break
                key.append(v)
            else:
                t = tuple(key)
                groups.setdefault(t, []).append(j)
        return {k: g for k, g in groups.items() if len(g) > 1}

    dets = []
    for r in range(1, limit + 1):
        dets.extend(combinations(live, r))
    for key in declared_keys or []:
        if len(key) >= 2:
            for r in range(1, len(key)):
                for s in combinations(key, r):
                    if len(s) > limit and s not in dets:
                        dets.append(s)
    found = []  # (det, dep, kind, k)
    for det in dets:
        if any(c in single_keys for c in det):
            continue
        groups = groups_of(det)
        if not groups:
            continue
        ex = examined(det)
        for dep in live:
            if dep in det:
                continue
            if len(set(v for v in coded[dep] if v is not None)) <= 1:
                continue
            status, info = _check_groups(groups, coded[dep], n, 0.03)
            if status == "fail":
                continue
            kind = "exact" if status == "holds" else "near"
            found.append((tuple(sorted(det)), dep, kind,
                          info.get("k", 0), ex))
    # minimise: smaller first; drop supersets of a kept determinant
    found.sort(key=lambda f: (len(f[0]), f[0], f[1]))
    kept = []
    for det, dep, kind, k, ex in found:
        if any(set(kd) <= set(det) for kd, dp, _, _, _ in kept if dp == dep):
            continue
        kept.append((det, dep, kind, k, ex))
    return {(d, dep, kind, k) for d, dep, kind, k, _ in kept}


def sig(res):
    return {(tuple(sorted(f.determinant)), f.dependent, f.kind, f.k) for f in res.fds}


def check_parity(raw, declared_keys=None, limit=2):
    columns = list(raw)
    n = len(next(iter(raw.values())))
    coded = code_cols(raw)
    got = sig(discover(coded, columns, n, declared_keys, limit))
    want = brute(coded, columns, n, declared_keys, limit)
    assert got == want, "TANE %s != brute %s" % (got, want)
    return got


def test_single_fd():
    raw = {"A": [i % 6 for i in range(12)],
           "B": [(i % 6) // 2 for i in range(12)],
           "C": [(i * 7) % 5 for i in range(12)]}
    got = check_parity(raw)
    assert got == {(("A",), "B", "exact", 0)}


def test_near_fd_dirty_rows():
    n = 40
    avals = [i % 4 for i in range(n)]
    bvals = list(avals)
    bvals[7] = (bvals[7] + 1) % 4  # one dirty row in a group of 10
    raw = {"A": avals, "B": bvals, "C": [(i * 7) % 5 for i in range(n)]}
    got = check_parity(raw)
    assert got == {(("A",), "B", "near", 1), (("B",), "A", "near", 1)}


def test_pair_disagreement_not_an_fd():
    raw = {"X": [(i // 2) for i in range(12)],
           "Y": ["L" if i % 2 else "R" for i in range(12)]}
    assert check_parity(raw) == set()


def test_key_parts_beyond_limit():
    n = 60
    k1 = [i % 5 for i in range(n)]
    k2 = [(i // 5) % 4 for i in range(n)]
    k3 = [(i // 20) % 3 for i in range(n)]
    raw = {"K1": k1, "K2": k2, "K3": k3,
           "D": [a + b for a, b in zip(k1, k2)],
           "F": [(i * 7) % 11 for i in range(n)]}
    columns = list(raw)
    coded = code_cols(raw)
    # limit 1: level search cannot reach the size-2 determinant, but the
    # key-part rule must (declared composite key K1,K2,K3).
    res = discover(coded, columns, n, [["K1", "K2", "K3"]], limit=1)
    got = sig(res)
    assert got == {(("K1", "K2"), "D", "exact", 0)}
    assert sig(discover(coded, columns, n, None, limit=1)) == set()


def test_equivalent_columns():
    n = 20
    raw = {"X": [i % 5 for i in range(n)],
           "Y": [i % 5 for i in range(n)],
           "Z": [(i * 7) % 6 for i in range(n)]}
    columns = list(raw)
    coded = code_cols(raw)
    res = discover(coded, columns, n, None, limit=2)
    assert sig(res) == {(("X",), "Y", "exact", 0), (("Y",), "X", "exact", 0)}
    assert all(f.equivalent for f in res.fds)


def test_null_policy():
    # NULL in X excludes the row; NULL in Y counts as a value (None groups
    # with None). Here the single None-Y row makes A->B fail (1/24 = 4.2%
    # above the 3% near-FD allowance) while B->A holds exactly.
    avals, bvals = [], []
    for i in range(24):
        a = None if i % 6 == 5 else i % 3
        b = None if i % 8 == 7 else (i % 3)
        avals.append(a)
        bvals.append(None if a is None else b)
    raw = {"A": avals, "B": bvals}
    got = check_parity(raw)
    assert got == {(("B",), "A", "exact", 0)}


def test_unique_determinant_skipped():
    n = 30
    g = [i % 3 for i in range(n)]
    raw = {"U": list(range(n)),
           "G": g,
           "D": [5 if x < 2 else 7 for x in g]}
    got = check_parity(raw)
    assert got == {(("G",), "D", "exact", 0)}


def test_constant_dependent_skipped():
    raw = {"A": [i % 4 for i in range(16)],
           "B": [7] * 16,
           "C": [(i * 3) % 5 for i in range(16)]}
    assert check_parity(raw) == set()


def test_random_tables_parity():
    import random
    rng = random.Random(7)
    for trial in range(5):
        n = 60
        raw = {}
        for c in range(6):
            mod = rng.choice([3, 4, 5, 8])
            mult = rng.choice([1, 3, 7])
            raw["c%d" % c] = [(i * mult) % mod for i in range(n)]
        check_parity(raw)


def test_candidate_count_matches_design():
    assert count_candidates(15, 2) == 1575
    assert count_candidates(15, 3) == 7035
