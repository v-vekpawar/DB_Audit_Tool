"""Compare a scan result against expected_verdicts.json (M1 acceptance).

Usage:
  python scripts/testdb/check_verdicts.py result-mysql.json result-pg.json

Exit 0 when recall is 100%, precision is 100% (zero false faulted) and
both engines agree table by table.
Stdlib only.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_result(path):
    return json.load(open(path, encoding="utf-8"))


def verdicts_of(result):
    out = {}
    for s in result.get("table_summary", []):
        out[s["name"]] = {"status": s["status"],
                          "normal_form": s.get("normal_form")}
    detail = {t["name"]: t for t in result.get("tables", [])}
    for name, d in detail.items():
        out[name]["violations"] = [
            (v["form"], v["kind"], tuple(v["determinant"]),
             tuple(v["dependents"])) for v in d.get("violations", [])]
    return out


def check_one(result, expected):
    got = verdicts_of(result)
    rows = []
    for name, exp in sorted(expected["tables"].items()):
        g = got.get(name, {"status": "MISSING"})
        ok, note = True, ""
        accepts = exp.get("accept", [exp["status"]])
        if g["status"] not in accepts:
            ok, note = False, "status %s not in %s" % (g["status"], accepts)
        elif exp.get("normal_form") == "3NF-or-better":
            if g["status"] == "clean" and g.get("normal_form") not in ("3NF", "BCNF"):
                ok, note = False, "exact form %s" % g.get("normal_form")
            elif g["status"] != "clean":
                ok, note = False, "expected clean, got %s" % g["status"]
        elif exp.get("normal_form") and g.get("normal_form") != exp["normal_form"]:
            ok, note = False, "form %s != %s" % (g.get("normal_form"), exp["normal_form"])
        if ok and exp.get("fds"):
            have = set()
            near_have = set()
            for v in g.get("violations", []):
                for dep in v[3]:
                    have.add((tuple(sorted(v[2])), dep, v[1]))
                    have.add((tuple(sorted(v[2])), dep, "*"))
            det = {t["name"]: t for t in result.get("tables", [])}.get(name, {})
            for f in det.get("fds", []):
                if f.get("kind") == "near":
                    near_have.add((tuple(sorted(f["determinant"])), f["dependent"]))
            for f in exp["fds"]:
                key = (tuple(sorted(f["determinant"])), f["dependent"], f["kind"])
                if f["kind"] == "near":
                    if key[:2] not in near_have or (key[0], key[1], "*") not in have:
                        ok, note = False, "missing near FD %s" % (key,)
                elif key not in have:
                    ok, note = False, "missing FD %s" % (key,)
        if ok and exp.get("sampled"):
            det = {t["name"]: t for t in result.get("tables", [])}.get(name, {})
            if not det.get("sampled"):
                ok, note = False, "not marked sampled"
        if ok and exp.get("cross_table"):
            det = {t["name"]: t for t in result.get("tables", [])}.get(name, {})
            fds = det.get("fds", [])
            if not any(((f.get("evidence") or {}).get("cross_table") or {}).get("supported")
                       for f in fds):
                ok, note = False, "no cross-table support"
        if ok and exp.get("report_fd") is False:
            det = {t["name"]: t for t in result.get("tables", [])}.get(name)
            fds = (det or {}).get("fds", [])
            if fds:
                ok, note = False, "unexpected FDs reported"
        rows.append((name, exp["status"], g["status"], ok, note))
    return rows


def metrics(rows, expected):
    planted = {n for n, e in expected["tables"].items() if e["status"] == "faulted"}
    by_name = {r[0]: r for r in rows}
    found = {n for n in planted if by_name[n][3]
             and by_name[n][2] in expected["tables"][n].get("accept", ["faulted"])}
    recall = len(found) / len(planted) if planted else 1.0
    false_faulted = [r[0] for r in rows if r[2] == "faulted"
                     and expected["tables"][r[0]]["status"] != "faulted"]
    precision = 1.0 if not false_faulted else (
        len(planted) - len(false_faulted)) / len(planted)
    return recall, precision, sorted(found ^ planted), false_faulted


def main(paths):
    expected = json.load(open(HERE / "expected_verdicts.json", encoding="utf-8"))
    results = [(p, load_result(p)) for p in paths]
    all_ok = True
    summaries = []
    for path, res in results:
        print("=" * 70)
        print(path, "status:", res["scan"].get("status"),
              "duration: %ss" % res["scan"].get("duration_seconds"))
        rows = check_one(res, expected)
        for name, exp_s, got_s, ok, note in rows:
            print(("PASS " if ok else "FAIL ") + "%-22s exp=%-17s got=%-17s %s"
                  % (name, exp_s, got_s, note))
            all_ok &= ok
        recall, precision, missed, false_f = metrics(rows, expected)
        print("recall=%.3f precision=%.3f missed=%s false_faulted=%s"
              % (recall, precision, missed, false_f))
        summaries.append((path, rows))
        all_ok &= (recall == 1.0 and precision == 1.0)
    if len(summaries) == 2:
        ra = verdicts_of(load_result(summaries[0][0]))
        rb = verdicts_of(load_result(summaries[1][0]))
        diff = [n for n in ra if (ra[n].get("status"), ra[n].get("normal_form"))
                != (rb.get(n, {}).get("status"), rb.get(n, {}).get("normal_form"))]
        print("cross-engine parity:", "OK" if not diff else "DIFF %s" % diff)
        all_ok &= not diff
    print("RESULT:", "ALL OK" if all_ok else "FAILURES PRESENT")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
