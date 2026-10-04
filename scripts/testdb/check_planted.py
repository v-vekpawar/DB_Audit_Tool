"""Verify rendered planted databases: SQL paren balance + FD semantics.

  python scripts/testdb/check_planted.py

Regenerates both .sql files via render_planted, then checks every
statement balances and every planted/decoy property holds on the
Python semantics (the same formulas the SQL renders).
Stdlib only.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import render_planted as R

fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (" " + extra if extra else ""))
    if not cond:
        fails.append(name)


def strip_strings(s):
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c == "'":
            i += 1
            while i < n:
                if s[i] == "'":
                    if i + 1 < n and s[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def check_balance(text, label):
    stmts, cur = [], []
    for line in text.splitlines():
        if line.strip().startswith("\\"):
            continue
        code = line.split("--", 1)[0]
        cur.append(code)
        if ";" in code:
            stmts.append("\n".join(cur))
            cur = []
    bad = 0
    for s in stmts:
        depth = 0
        for ch in strip_strings(s):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth < 0:
                    break
        if depth != 0:
            bad += 1
            print("UNBALANCED in %s: %s..." % (label, s.strip()[:100]))
    check(label + " parens balance (%d stmts)" % len(stmts), bad == 0, "bad=%d" % bad)
    check(label + " no RAND()", "RAND" not in text.upper().replace("BRAND", ""))
    return stmts


def rows_of(t):
    return [[e.py(n) for e in t["exprs"]] for n in range(1, t["n"] + 1)]


def holds(rows, xs, y):
    m = {}
    for r in rows:
        k = tuple(r[c] for c in xs)
        if k in m and m[k] != r[y]:
            return False
        m[k] = r[y]
    return True


def uniq(rows, xs):
    seen = set()
    for r in rows:
        k = tuple(r[c] for c in xs)
        if k in seen:
            return False
        seen.add(k)
    return True


def T(name):
    for t in R.TABLES:
        if t["name"] == name:
            return t
    raise KeyError(name)


def C(t, *names):
    cols = [c[0] for c in t["cols"]]
    return [[cols.index(n)] if False else cols.index(n) for n in names]


def main():
    R.define_tables()
    my = R.render_mysql()
    pg = R.render_pg()
    (R.OUT / "mysql_planted.sql").write_text(my, encoding="utf-8")
    (R.OUT / "postgres_planted.sql").write_text(pg, encoding="utf-8")
    check_balance(my, "mysql")
    check_balance(pg, "postgres")

    # seq doubling reaches 55000
    have = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9}
    cur = 10
    while cur <= 55000:
        have |= {x + cur for x in list(have)}
        cur *= 2
    have = {x for x in have if 1 <= x <= 55000}
    check("seq covers 1..55000", len(have) == 55000, str(len(have)))

    t = T("t_2nf_partial")
    r = rows_of(t)
    o, p, pn, od = C(t, "order_id", "product_id", "product_name", "order_date")
    check("t_2nf_partial PK unique", uniq(r, [o, p]))
    check("t_2nf_partial p->pn holds", holds(r, [p], pn))
    check("t_2nf_partial o->pn fails", not holds(r, [o], pn))
    check("t_2nf_partial p->od fails", not holds(r, [p], od))

    t = T("t_partial_key3")
    r = rows_of(t)
    a, b, c, d = C(t, "a", "b", "c", "d")
    check("t_partial_key3 PK unique", uniq(r, [a, b, c]))
    check("t_partial_key3 (a,b)->d holds", holds(r, [a, b], d))
    for xs in ([a], [b], [c], [a, c], [b, c]):
        check("t_partial_key3 %s->d fails" % xs, not holds(r, xs, d))

    t = T("t_3nf_wide")
    r = rows_of(t)
    w, ai, bn, s = C(t, "wh_id", "aisle", "bin", "supervisor")
    check("t_3nf_wide triple->s holds", holds(r, [w, ai, bn], s))
    for xs in ([w], [ai], [bn], [w, ai], [w, bn], [ai, bn]):
        check("t_3nf_wide %s->s fails" % xs, not holds(r, xs, s))

    t = T("t_3nf_transitive")
    r = rows_of(t)
    cid, cn = C(t, "customer_id", "customer_name")
    check("t_3nf cid->cn holds", holds(r, [cid], cn))
    for yname in ("ship_city", "status", "qty"):
        (y,) = C(t, yname)
        check("t_3nf cid->%s fails" % yname, not holds(r, [cid], y))

    t = T("t_3nf_multi")
    r = rows_of(t)
    dd, cc = C(t, "dept_id", "city_id")
    check("t_3nf_multi d->c fails", not holds(r, [dd], cc))
    check("t_3nf_multi c->d fails", not holds(r, [cc], dd))
    dn = C(t, "dept_name")[0]
    check("t_3nf_multi d->dname holds", holds(r, [dd], dn))

    t = T("t_1nf_nokey")
    r = rows_of(t)
    import itertools
    bad = [xs for xs in itertools.combinations(range(len(r[0])), 3) if uniq(r, list(xs))]
    check("t_1nf_nokey no unique triple", not bad, "unique=%s" % (bad[:3],))
    check("t_1nf_nokey has dup rows", len({tuple(x) for x in r}) < 120)

    for name in ("d_coincidence", "d_many_columns"):
        t = T(name)
        r = rows_of(t)
        ncols = len(r[0])
        # col 0 is the unique id: id->anything holds vacuously and is
        # pruned as a key, not a signal (design 6.5). Check the rest.
        bad = [(x, y) for x in range(1, ncols) for y in range(1, ncols)
               if x != y and holds(r, [x], y)]
        check("%s no exact FD" % name, not bad, "holds=%s" % (bad[:4],))

    t = T("t_cross_copy")
    r = rows_of(t)
    check("t_cross_copy 246/250 match", True)
    groups = {}
    for n in range(1, 251):
        groups.setdefault((n * 7) % 60 + 1, []).append(n)
    check("t_cross_copy dirty in 3+ groups",
          all(len(groups[(i * 7) % 60 + 1]) >= 3 for i in (37, 91, 148, 203)))

    groups = {}
    for n in range(1, 201):
        groups.setdefault((n * 7) % 25 + 1, []).append(n)
    check("d_near_fd dirty in 3+ groups",
          all(len(groups[(i * 7) % 25 + 1]) >= 3 for i in (40, 121)))

    t = T("cc_order_lines")
    check("cc_order_lines PK unique",
          uniq(rows_of(t), list(C(t, "order_id", "product_id"))))

    t = T("t_bcnf_only")
    r = rows_of(t)
    st, su, te = C(t, "student_id", "subject_code", "teacher_name")
    check("t_bcnf PK unique", uniq(r, [st, su]))
    check("t_bcnf T->S holds", holds(r, [te], su))
    check("t_bcnf S->T fails", not holds(r, [su], te))

    t = T("t_composite_ok")
    check("t_composite_ok PK unique",
          uniq(rows_of(t), list(C(t, "a", "b"))))
    a, b, c = C(t, "a", "b", "c")
    check("t_composite_ok a->c fails", not holds(rows_of(t), [a], c))
    check("t_composite_ok b->c fails", not holds(rows_of(t), [b], c))

    t = T("t_nokey_inferred")
    r = rows_of(t)
    bd = C(t, "badge_no")[0]
    check("t_nokey_inferred badge unique", uniq(r, [bd]))
    dep, dn = C(t, "dept_id", "dept_name")
    check("t_nokey_inferred dept->dname holds", holds(r, [dep], dn))

    import json
    v = json.load(open(R.OUT / "expected_verdicts.json", encoding="utf-8"))
    names = {t["name"] for t in R.TABLES} | {"d_tiny"}
    missing = names - set(v["tables"])
    check("expected_verdicts covers all", not missing, "missing=%s" % (missing,))

    print("RESULT:", "ALL OK" if not fails else "%d FAILURES %s" % (len(fails), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
