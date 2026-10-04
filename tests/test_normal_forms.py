"""Step 7 checkpoint: key rules, every normal-form rule (incl. the
3NF-but-not-BCNF clean case and the 2NF-vs-3NF split), equivalent
columns, suspected/insufficient_data, and recommendation templates."""

from app.keys import candidate_keys
from app.normal_forms import classify, delimited_columns, repeating_groups
from app.recommend import recommend


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


def stats_of(raw, types=None, excluded=None):
    out = {}
    for name, vals in raw.items():
        nn = [v for v in vals if v is not None]
        labels = sorted(set(nn), key=str)
        out[name] = {"labels": labels,
                     "counts": [nn.count(v) for v in labels],
                     "type": (types or {}).get(name, "VARCHAR(20)"),
                     "null_ratio": (len(vals) - len(nn)) / len(vals),
                     "excluded_in": (excluded or {}).get(name)}
    return out


def detail_of(names, pk=(), uniques=()):
    return {"name": "t",
            "columns": [{"name": n, "type": "VARCHAR(20)"} for n in names],
            "pk": list(pk), "uniques": [list(u) for u in uniques], "fks": []}


def FD(det, dep, kind="exact", equiv=False):
    return {"determinant": list(det), "dependent": dep, "kind": kind,
            "equivalent": equiv}


def classify_of(raw, strong=(), suspected=(), pk=(), uniques=(), types=None,
                excluded=None, n=100):
    coded = coded_of(raw)
    keys = candidate_keys(detail_of(list(raw), pk, uniques), coded,
                          list(raw), n)
    return classify("t", detail_of(list(raw), pk, uniques), list(strong),
                    list(suspected), keys, stats_of(raw, types, excluded), n), keys


def test_declared_keys_certain_and_prime():
    raw = {"a": [1, 2, 3], "b": list("xyx")}
    keys = candidate_keys(detail_of(["a", "b"], pk=["a"]), coded_of(raw),
                          ["a", "b"], 3)
    assert keys["keys"] == [{"columns": ["a"], "source": "declared"}]
    assert keys["prime"] == {"a"}


def test_inferred_keys_flagged():
    raw = {"u": [1, 2, 3, 4], "g": [1, 1, 2, 2], "d": [1, 1, 2, 2]}
    keys = candidate_keys(detail_of(["u", "g", "d"]), coded_of(raw),
                          ["u", "g", "d"], 4)
    assert keys["keys"] == [{"columns": ["u"], "source": "inferred"}]
    raw2 = {"a": [1, 1, 2, 2], "b": [1, 2, 1, 2], "c": [1, 1, 1, 1]}
    keys2 = candidate_keys(detail_of(["a", "b", "c"]), coded_of(raw2),
                           ["a", "b", "c"], 4)
    assert {"columns": ["a", "b"], "source": "inferred"} in keys2["keys"]
    assert keys2["prime"] == {"a", "b"}


def test_no_unique_set_no_keys():
    raw = {"a": [1, 1, 2, 2], "b": [1, 1, 2, 2]}
    keys = candidate_keys(detail_of(["a", "b"]), coded_of(raw), ["a", "b"], 4)
    assert keys["keys"] == [] and keys["prime"] == set()


def test_1nf_no_key():
    raw = {"a": [1, 1, 2, 2, 1, 1, 2, 2], "b": [1, 2, 1, 2, 1, 2, 1, 2]}
    out, _ = classify_of({k: v * 15 for k, v in raw.items()}, n=120)
    assert out["status"] == "faulted" and out["normal_form"] == "below 1NF"
    assert out["violations"][0]["kind"] == "no-key"


def test_1nf_delimited_and_repeating():
    tags = ["a,b"] * 105 + ["solo"] * 45
    out, _ = classify_of({"id": list(range(150)), "tags": tags}, pk=["id"], n=150)
    assert out["status"] == "faulted"
    assert any(v["kind"] == "delimited" for v in out["violations"])
    out, _ = classify_of({"id": list(range(150)),
                          "phone1": ["x"] * 150, "phone2": ["y"] * 150,
                          "phone3": ["z"] * 150}, pk=["id"], n=150)
    assert any(v["kind"] == "repeating" for v in out["violations"])
    # c01-style stems are ignored
    assert repeating_groups(["id"] + ["c%02d" % i for i in range(1, 15)]) == {}
    assert set(repeating_groups(["id", "phone1", "phone2", "phone3"])["phone"]) == {
        "phone1", "phone2", "phone3"}
    # binary/json columns are not list-checked
    st = stats_of({"b": ["a;b"] * 50}, excluded={"b": "binary"})
    assert delimited_columns(st, 50) == []


def test_2nf_partial_vs_3nf_transitive():
    n = 120
    o = [i % 6 for i in range(n)]
    p = [(i * 7) % 5 for i in range(n)]
    raw = {"o": o, "p": p, "pn": list(p), "q": [(i * 3) % 4 for i in range(n)]}
    out, _ = classify_of(raw, strong=[FD(["p"], "pn")], pk=["o", "p"], n=n)
    assert (out["status"], out["normal_form"]) == ("faulted", "1NF")
    assert out["violations"][0]["kind"] == "partial"
    assert "only part of the key" in out["violations"][0]["reason"]
    c = [i % 4 for i in range(n)]
    raw2 = {"id": list(range(n)), "c": c, "cn": list(c),
            "q": [(i * 3) % 4 for i in range(n)]}
    out, _ = classify_of(raw2, strong=[FD(["c"], "cn")], pk=["id"], n=n)
    assert (out["status"], out["normal_form"]) == ("faulted", "2NF")
    assert out["violations"][0]["kind"] == "transitive"
    assert "not on the key" in out["violations"][0]["reason"]


def test_key_determinant_is_clean():
    n = 60
    raw = {"id": list(range(n)), "v": ["v%d" % (i % 6) for i in range(n)]}
    out, _ = classify_of(raw, strong=[], pk=["id"], n=n)
    assert (out["status"], out["normal_form"]) == ("clean", "BCNF")


def test_3nf_but_not_bcnf_is_clean():
    n = 60
    s = [(i // 2) % 3 + 1 for i in range(n)]
    t = ["T%d" % (((x - 1) * 2 + (i % 2)) + 1) for i, x in
         enumerate([(i // 2) % 3 + 1 for i in range(n)])]
    raw = {"st": [(i // 6) + 1 for i in range(n)], "s": s, "t": t}
    out, _ = classify_of(raw, strong=[FD(["t"], "s")], pk=["st", "s"], n=n)
    assert (out["status"], out["normal_form"]) == ("clean", "3NF")


def test_equivalent_through_key_is_clean():
    n = 40
    raw = {"code": list(range(n)), "name": list(range(n)),
           "city": [(i * 7) % 6 for i in range(n)]}
    out, _ = classify_of(raw, strong=[FD(["code"], "name", equiv=True),
                                      FD(["name"], "code", equiv=True)],
                          pk=["code"], n=n)
    assert (out["status"], out["normal_form"]) == ("clean", "BCNF")


def test_equivalent_pair_reported_once():
    n = 60
    g = [i % 6 for i in range(n)]
    raw = {"a": list(g), "b": list(g), "c": [(i * 7) % 5 for i in range(n)]}
    out, _ = classify_of(raw, strong=[FD(["a"], "b", equiv=True),
                                      FD(["b"], "a", equiv=True)],
                          n=n)
    # no declared key, but (a,b) unique together -> inferred key; the
    # equivalent pair must collapse to a single violation entry
    equiv_v = [v for v in out["violations"] if v.get("equivalent")]
    assert len(equiv_v) == 1
    assert equiv_v[0]["dependents"] == [equiv_v[0]["dependents"][0]]
    assert equiv_v[0]["determinant"] != equiv_v[0]["dependents"]


def test_equivalent_clique_reports_prime_determinant():
    # product_id ~ product_name ~ unit_price clique with product_id in the
    # key: the violation must read product_id -> {others} as partial.
    n = 60
    pid = [(i * 7) % 10 + 1 for i in range(n)]
    raw = {"oid": [i % 6 for i in range(n)], "pid": pid,
           "pname": list(pid), "uprice": list(pid)}
    out, _ = classify_of(raw, strong=[FD(["pid"], "pname", equiv=True),
                                      FD(["pname"], "pid", equiv=True),
                                      FD(["pid"], "uprice", equiv=True),
                                      FD(["uprice"], "pid", equiv=True)],
                          pk=["oid", "pid"], n=n)
    assert out["status"] == "faulted"
    partials = [v for v in out["violations"] if v["kind"] == "partial"]
    assert len(partials) == 1
    assert partials[0]["determinant"] == ["pid"]
    assert sorted(partials[0]["dependents"]) == ["pname", "uprice"]


def test_suspected_only_is_dimmed():
    n = 120
    c = [i % 4 for i in range(n)]
    raw = {"id": list(range(n)), "c": c, "cn": list(c)}
    out, _ = classify_of(raw, suspected=[FD(["c"], "cn")], pk=["id"], n=n)
    assert out["status"] == "suspected" and out["normal_form"] == "2NF"


def test_insufficient_data_and_tiny_pattern():
    out, _ = classify_of({"a": [1, 1, 2], "b": list("xxy")}, n=5)
    assert out["status"] == "insufficient_data" and out["normal_form"] is None
    tags = ["a,b"] * 4 + ["solo"]
    out, _ = classify_of({"id": [1, 2, 3, 4, 5], "tags": tags}, pk=["id"], n=5)
    assert out["status"] == "faulted" and out["normal_form"] == "below 1NF"


def test_recommend_templates():
    assert recommend("t", []) == ""
    assert recommend("t", [{"form": "1NF", "kind": "no-key"}]) == \
        "Add a primary key to this table."
    r = recommend("orders", [{"form": "3NF", "kind": "transitive",
                              "determinant": ["customer_id"],
                              "dependents": ["customer_name"]}])
    assert r == "Move customer_name into a separate table keyed by customer_id and keep customer_id as a foreign key in orders."
    r = recommend("t", [{"form": "2NF", "kind": "partial",
                         "determinant": ["product_id"],
                         "dependents": ["product_name"]}])
    assert "only part of the key" in r
    r = recommend("t", [{"form": "1NF", "kind": "repeating",
                         "determinant": [], "dependents": ["phone1", "phone2"],
                         "detail": "phone"}])
    assert "one phone per row" in r
    r = recommend("t", [{"form": "3NF", "kind": "transitive",
                         "determinant": ["country_code"],
                         "dependents": ["country_name"], "equivalent": True}])
    assert "equivalent columns" in r
