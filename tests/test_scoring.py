"""Step 6 checkpoint: score ordering, closed form vs seeded simulation,
near-FD handling, caps, tiers, cross-table support. No database needed."""

import math
import random

from app.fd_discovery import FoundFD
from app.fd_scoring import (
    adjust_exponent,
    coincidence_exponent,
    coincidence_text,
    naming_affinity,
    score_fd,
    score_from_exponent,
)
from app.cross_table import build_ref_map, check_support


def coded_of(raw):
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


def labels_of(coded):
    return {c: sorted(set(v for v in vals if v is not None)) for c, vals in coded.items()}


def FD(det, dep, kind="exact", k=0, exc=()):
    return FoundFD(list(det), dep, kind, k=k, exception_rows=list(exc),
                   group_sizes=[], examined=0)


def test_closed_form_matches_simulation():
    # Regime where the independence approximation holds: many rows,
    # small groups. 10 fixed pairs among 200 rows, Y at 100/100.
    # Exact value by direct counting (pairs uniform AND margins met).
    from math import comb
    sizes = [2] * 10
    e_closed = coincidence_exponent(sizes, [100, 100], 200)
    num = sum(comb(10, j) * comb(180, 100 - 2 * j)
              for j in range(11) if 100 - 2 * j >= 0)
    e_exact = -math.log10(num / comb(200, 100))
    assert abs(e_closed - e_exact) <= 0.05, (e_closed, e_exact)
    # Seeded shuffle agrees with both.
    rng = random.Random(12345)
    y = [0] * 100 + [1] * 100
    pairs = [(2 * i, 2 * i + 1) for i in range(10)]
    trials, hits = 30000, 0
    for _ in range(trials):
        vals = y[:]
        rng.shuffle(vals)
        if all(vals[a] == vals[b] for a, b in pairs):
            hits += 1
    assert hits > 10, "simulation setup too rare to measure"
    e_sim = -math.log10(hits / trials)
    assert abs(e_sim - e_closed) <= 0.3, (e_sim, e_closed)


def test_score_formula_mapping():
    assert score_from_exponent(2) == 50
    assert score_from_exponent(6) == 75
    assert score_from_exponent(10) == 83
    assert score_from_exponent(30) == 94
    assert score_from_exponent(48) == 96


def test_coincidence_text():
    assert coincidence_text(48.2) == "1 in 10^48"
    assert coincidence_text(6) == "1 in 1,000,000"


def test_strong_true_fd():
    n = 300
    cid = [(i * 7) % 40 + 1 for i in range(n)]
    raw = {"cid": cid, "cn": list(cid)}
    coded = coded_of(raw)
    s = score_fd(coded, labels_of(coded), FD(["cid"], "cn"), m=1575)
    assert s.confidence == 99 and s.tier == "strong"
    assert s.evidence["repeated_rows"] == n - 40
    assert s.evidence["distinct_det"] == 40
    assert s.evidence["candidates_tested"] == 1575
    assert s.evidence["examples"][0]["rows"] > 0


def test_skewed_decoy_discarded():
    n = 100
    det = [i % 25 for i in range(n)]
    dep = [0 if (i % 25) < 24 else 1 for i in range(n)]
    coded = coded_of({"d": det, "y": dep})
    s = score_fd(coded, labels_of(coded), FD(["d"], "y"), m=500)
    assert s.tier == "discarded"


def test_tiny_table_discarded_by_materiality():
    coded = coded_of({"a": [1, 1, 2, 2, 3], "b": list("xxyyz")})
    s = score_fd(coded, labels_of(coded), FD(["a"], "b"), m=50)
    assert s.tier == "discarded" and s.confidence == 0


def test_near_fd_capped_at_90():
    n = 300
    cid = [(i * 7) % 40 + 1 for i in range(n)]
    cn = list(cid)
    cn[10] = 999  # one dirty row in a group of 7-8
    coded = coded_of({"cid": cid, "cn": cn})
    s = score_fd(coded, labels_of(coded), FD(["cid"], "cn", kind="near", k=1, exc=[10]), m=1575)
    assert s.confidence == 90 and s.tier == "strong"
    assert s.exceptions == 1


def test_sampled_capped_at_85():
    n = 300
    cid = [(i * 7) % 40 + 1 for i in range(n)]
    coded = coded_of({"cid": cid, "cn": list(cid)})
    s = score_fd(coded, labels_of(coded), FD(["cid"], "cn"), m=1575, sampled=True)
    assert s.confidence == 85 and s.tier == "strong"


def test_near_penalty_matches_combinatorial_factor():
    # Unit level: the C(N,k) dirty-row penalty is exactly log10(C(N,k)).
    e = 12.0
    assert adjust_exponent(e, 100, k=1, n=20) == abs(e - 2.0 - math.log10(20))
    got = adjust_exponent(e, 100, k=2, n=50)
    assert abs(got - (e - 2.0 - math.log10(math.comb(50, 2)))) < 1e-9
    # Integration level: a dirty table scores below its clean twin.
    n = 20
    det = [i % 5 for i in range(n)]
    dep = [i % 5 for i in range(n)]
    coded = coded_of({"d": det, "y": list(dep)})
    e_exact = score_fd(coded, labels_of(coded), FD(["d"], "y"), m=100).exponent
    dep2 = list(dep)
    dep2[0] = 99  # 1 dirty row in a group of 4
    coded2 = coded_of({"d": det, "y": dep2})
    s_near = score_fd(coded2, labels_of(coded2),
                      FD(["d"], "y", kind="near", k=1, exc=[0]), m=100)
    assert s_near.kind == "near" and s_near.exceptions == 1
    assert s_near.exponent < e_exact


def test_cross_table_promotes_to_strong():
    det = [g for g in range(4) for _ in range(5)]
    dep = [0] * 10 + [1] * 10
    coded = coded_of({"d": det, "y": dep})
    labels = labels_of(coded)
    plain = score_fd(coded, labels, FD(["d"], "y"), m=200)
    assert 60 <= plain.confidence < 75, plain.confidence
    assert plain.tier == "suspected"
    promoted = score_fd(coded, labels, FD(["d"], "y"), m=200,
                        cross={"supported": True, "match_rate": 0.99,
                               "ref_table": "r"})
    assert promoted.tier == "strong"
    assert promoted.confidence == plain.confidence  # support changes tier, not score


def test_deterministic():
    n = 120
    cid = [(i * 7) % 30 + 1 for i in range(n)]
    coded = coded_of({"cid": cid, "cn": list(cid)})
    labels = labels_of(coded)
    a = score_fd(coded, labels, FD(["cid"], "cn"), m=800)
    b = score_fd(coded, labels, FD(["cid"], "cn"), m=800)
    assert (a.confidence, a.tier, a.exponent) == (b.confidence, b.tier, b.exponent)


def test_naming_affinity_display_only():
    assert naming_affinity(["customer_id"], "customer_name")
    assert not naming_affinity(["order_id"], "ship_city")


def test_cross_table_match():
    ref = [(k, "N%03d" % k) for k in range(1, 61) for _ in range(4)]
    det = [(i * 7) % 60 + 1 for i in range(250)]
    dep = ["N%03d" % a if i not in (37, 91, 148, 203) else "N999"
           for i, a in enumerate(det)]
    out = check_support(det, dep, ref)
    assert out["supported"] and out["match_rate"] >= 0.97
    assert out["distinct_a"] >= 5


def test_cross_table_rejects():
    ref = [(k, "N%03d" % k) for k in range(1, 61)]
    dep = ["OTHER"] * 60
    out = check_support(list(range(1, 61)), dep, ref)
    assert not out["supported"]
    out = check_support([1, 1, 2, 2, 1], ["a"] * 5, [(1, "a"), (2, "a")])
    assert not out["supported"]  # fewer than 5 distinct A values
    # inconsistent K in R drops out of the map
    m = build_ref_map([(1, "a"), (1, "b"), (2, "a")])
    assert m == {2: "a"}


def test_adjust_exponent_correction():
    assert adjust_exponent(10.0, 100) == 8.0
    assert adjust_exponent(1.0, 100) == 0.0  # floored, never negative
