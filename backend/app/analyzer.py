"""Scan orchestration: per-table pipeline, estimates, progress events.

Pipeline per table: bounded count -> load -> FD discovery -> scoring ->
cross-table evidence -> keys -> classification -> recommendation.
Per-table failures are isolated (recorded as error, never clean); a stop
leaves unreached tables as not_run. Events go to an optional callback
(wired to SSE in build step 9).
"""

import argparse
import datetime
import getpass
import json
import time
import uuid

from . import config
from .cross_table import check_support, load_ref_pairs
from .db_connect import (
    ConnectError,
    ConnectParams,
    close,
    connect,
    map_exception,
)
from .estimator import Estimator, work_units
from .fd_discovery import count_candidates, discover, brute_force_discover
from .fd_scoring import score_fd
from .keys import candidate_keys
from .loader import bounded_count, load_table, split_columns, _table_ref
from .logging_setup import StepTimer, get_logger, setup_logging
from .metadata import database_structure, list_tables, table_detail
from .normal_forms import classify
from .recommend import recommend

TABLE_STEPS = ("loading", "finding dependencies", "scoring", "classifying")


def headline(counts, selected, not_selected):
    finished = sum(counts.get(k, 0) for k in ("faulted", "suspected", "bcnf_faulted",
                                              "bcnf_suspected", "incomplete", "insufficient_data",
                                              "clean", "error"))
    parts = "%d of %d tables scanned" % (finished, selected + not_selected)
    tail = []
    for key, label in (("faulted", "faulted"), ("suspected", "suspected"),
                       ("bcnf_faulted", "BCNF violations"),
                       ("bcnf_suspected", "suspected BCNF"),
                       ("incomplete", "incomplete"),
                       ("insufficient_data", "insufficient data"),
                       ("clean", "clean"), ("error", "error"),
                       ("not_run", "not run")):
        if counts.get(key):
            tail.append("%d %s" % (counts[key], label))
    if not_selected:
        parts += " (%d not selected)" % not_selected
    return (parts + ": " + ", ".join(tail)) if tail else parts


def _per_table_code(code):
    if code == "DB_TIMEOUT":
        return "TABLE_TIMEOUT"
    if code == "INTERNAL_ERROR":
        return "TABLE_ANALYSIS_FAILED"
    return code


def _fk_targets(detail):
    """single-column FKs: {constrained_col: (ref_table, ref_col)}."""
    out = {}
    for fk in detail.get("fks") or []:
        cols = fk.get("columns") or []
        refs = fk.get("referred_columns") or []
        if len(cols) == 1 and len(refs) == 1:
            out.setdefault(cols[0], (fk.get("referred_table"), refs[0]))
    return out


def _raw_values(loaded, col):
    lab = loaded.labels.get(col, [])
    return [lab[c] if c is not None and 0 <= c < len(lab) else None
            for c in loaded.coded.get(col, [])]



def _fd_signature(fds):
    """Return a stable FD signature for FoundFD/ScoredFD-like objects.

    FD discovery returns dataclass objects (FoundFD), not dictionaries.  The
    analyzer previously indexed them with f[...], so any table that produced
    at least one exact FD crashed during the brute-force/TANE parity check.
    Keep this helper object-based and deliberately independent of scoring.
    """
    return sorted(
        (tuple(sorted(f.determinant)), f.dependent)
        for f in fds
        if getattr(f, "kind", None) == "exact"
    )


def _validate_exact_fds_full(conn, db_type, detail, schema, fds):
    """Validate sampled exact-FD candidates against the complete table.

    This confirms candidates but cannot discover a dependency that the sample
    search never generated; callers therefore keep the search-complete flag.
    """
    if not fds:
        return []
    from sqlalchemy import case, func, select
    searchable = [c["name"] for c in detail.get("columns", [])]
    t = _table_ref(detail["name"], searchable,
                   None if (db_type or "").lower() == "mysql" else schema,
                   db_type)
    out = []
    for fd in fds:
        if getattr(fd, "kind", None) != "exact":
            out.append(fd)
            continue
        det = list(fd.determinant)
        dep = fd.dependent
        dcols = [t.c[c] for c in det]
        y = t.c[dep]
        q = select(*dcols).select_from(t)
        if dcols:
            q = q.where(*[c.is_not(None) for c in dcols]).group_by(*dcols)
            null_count = func.coalesce(func.sum(case((y.is_(None), 1), else_=0)), 0)
            distinct_count = func.count(y.distinct()) + case((null_count > 0, 1), else_=0)
            q = q.having(distinct_count > 1).limit(1)
        else:
            # Empty determinant is not currently a discovered determinant.
            q = q.limit(1)
        row = conn.execute(q).first()
        if row is None:
            fd.equivalent = bool(fd.equivalent)
            out.append(fd)
    return out


def _validate_inferred_keys_full(conn, db_type, detail, schema, keys):
    """Promote sample-inferred keys only after full-table uniqueness validation."""
    from sqlalchemy import func, select
    searchable = [c["name"] for c in detail.get("columns", [])]
    t = _table_ref(detail["name"], searchable,
                   None if (db_type or "").lower() == "mysql" else schema,
                   db_type)
    out = []
    for k in keys:
        if k.get("source") != "inferred_sample":
            out.append(k)
            continue
        cols = list(k["columns"])
        qnull = select(1).select_from(t).where(*[t.c[c].is_(None) for c in cols]).limit(1)
        if conn.execute(qnull).first() is not None:
            continue
        qdup = select(*[t.c[c] for c in cols]).select_from(t).group_by(
            *[t.c[c] for c in cols]).having(func.count() > 1).limit(1)
        if conn.execute(qdup).first() is None:
            k = dict(k)
            k["source"] = "inferred_verified"
            k["guaranteed"] = False
            out.append(k)
    return out


def analyze_table(conn, db_type, detail, schema, scan_id, ref_cache,
                  on_event=None, max_rows=config.MAX_ROWS_PER_TABLE,
                  known_count=None):
    """Run the full pipeline for one table. Returns (result, units, secs).

    Raises ConnectError (mapped per-table code) on failure; the caller
    isolates it. ref_cache maps table name -> detail for FK targets.
    """
    log = get_logger()
    name = detail["name"]
    t0 = time.monotonic()

    def step(s):
        if on_event:
            on_event({"event": "table_step", "table": name, "step": s})

    searchable, _ = split_columns(detail)
    n_actual = known_count if known_count is not None else bounded_count(
        conn, db_type, name, schema=schema, scan_id=scan_id)
    rows_for_l = n_actual if n_actual is not None else max_rows
    keys_decl = [list(detail.get("pk") or [])] + [list(u) for u in (detail.get("uniques") or [])]
    keys_decl = [k for k in keys_decl if k]
    limit = (config.MAX_LHS_SIZE_LARGE if rows_for_l >= config.LARGE_TABLE_ROWS
             else config.MAX_LHS_SIZE)
    m = count_candidates(len(searchable), limit, keys_decl)
    units = work_units(min(n_actual, max_rows) if n_actual is not None else max_rows,
                       len(searchable), m)

    step("loading")
    loaded = load_table(conn, db_type, detail, schema=schema,
                        scan_id=scan_id, max_rows=max_rows,
                        known_count=n_actual)

    step("finding dependencies")
    with StepTimer("fd_discovery", scan_id, name):
        res = discover(loaded.coded, loaded.columns, loaded.row_count,
                       keys_decl, algorithm="auto")
        parity = None
        if (config.RUN_BRUTE_FORCE_PARITY and len(loaded.columns) <= config.BRUTE_FORCE_MAX_COLUMNS
                and res.algorithm == "bruteforce"):
            # Run the partition search independently as a regression oracle.
            tane = discover(loaded.coded, loaded.columns, loaded.row_count,
                            keys_decl, limit=max(1, len(loaded.columns) - 1),
                            algorithm="tane")
            parity = (_fd_signature(res.fds) == _fd_signature(tane.fds))
            if not parity:
                log.error("FD search parity mismatch: brute_force=%s tane=%s",
                          _fd_signature(res.fds), _fd_signature(tane.fds),
                          extra={"scan_id": scan_id, "step": "fd_discovery", "table": name})
        log.info("algorithm=%s complete=%s fds=%d near=%d tested=%d pruned=%d m=%d parity=%s",
                 res.algorithm, res.complete, len(res.fds),
                 sum(1 for f in res.fds if f.kind == "near"),
                 res.tests, res.pruned, res.m, parity,
                 extra={"scan_id": scan_id, "step": "fd_discovery", "table": name})

    # A row cap is a discovery aid, not proof. Validate exact candidates on the
    # full table before they can become strong evidence.
    if loaded.sampled:
        res.fds = _validate_exact_fds_full(conn, db_type, detail, schema, res.fds)

    step("scoring")
    with StepTimer("fd_scoring", scan_id, name):
        scored = [score_fd(loaded.coded, loaded.labels, f, res.m,
                           sampled=False if loaded.sampled and f.kind == "exact" else loaded.sampled)
                  for f in res.fds]
        for s in scored:
            if loaded.sampled and s.kind == "exact":
                s.evidence["full_table_validated"] = True
                s.evidence["evidence_level"] = "DATA_OBSERVED"
        by_tier = {}
        for s in scored:
            by_tier[s.tier] = by_tier.get(s.tier, 0) + 1
        log.info("tiers=%s", by_tier,
                 extra={"scan_id": scan_id, "step": "fd_scoring", "table": name})

    with StepTimer("cross_table", scan_id, name):
        _promote_with_cross_table(conn, db_type, detail, schema, scan_id,
                                  loaded, scored, ref_cache)

    step("classifying")
    with StepTimer("classify", scan_id, name):
        keys = candidate_keys(detail, loaded.coded, loaded.columns,
                              loaded.row_count, complete_data=not loaded.sampled)
        if loaded.sampled:
            keys["keys"] = _validate_inferred_keys_full(
                conn, db_type, detail, schema, keys["keys"])
            keys["inferred"] = [k for k in keys["keys"]
                                 if k.get("source") in ("inferred", "inferred_verified", "inferred_sample")]
            keys["prime"] = {c for k in keys.get("formal_keys", keys["keys"]) for c in k["columns"]}
            if "formal_keys" in keys:
                keys["formal_keys"] = _filter_formal_keys_after_validation(keys["formal_keys"], keys["keys"])
            keys["status"] = "incomplete"
        stats = _col_stats(detail, loaded)
        strong = [s for s in scored if s.tier == "strong"]
        susp = [s for s in scored if s.tier == "suspected"]
        F = lambda s: {"determinant": s.determinant, "dependent": s.dependent,
                       "kind": s.kind, "equivalent": s.equivalent,
                       "confidence": s.confidence, "exceptions": s.exceptions,
                       "exception_rows": list(s.exception_rows)}
        verdict = classify(name, detail, [F(s) for s in strong],
                           [F(s) for s in susp], keys, stats,
                           loaded.row_count,
                           complete_data=not loaded.sampled,
                           fd_search_complete=res.complete and (parity is not False))
        log.info("keys=%d exact=%s status=%s", len(keys["keys"]),
                 verdict["normal_form"], verdict["status"],
                 extra={"scan_id": scan_id, "step": "classify", "table": name})

    result = _assemble(name, detail, loaded, verdict, scored, keys, res)
    secs = time.monotonic() - t0
    return result, units, secs


def _col_stats(detail, loaded):
    excl = {e["name"]: e["reason"] for e in loaded.excluded}
    types = {c["name"]: str(c.get("type", "")) for c in detail.get("columns", [])}
    stats = {}
    for c in detail.get("columns", []):
        n = c["name"]
        sample = loaded.heuristic_samples.get(n)
        stats[n] = {"labels": (sample.labels.get(n, []) if sample else loaded.labels.get(n, [])),
                    "counts": (sample.counts.get(n, []) if sample else loaded.counts.get(n, [])),
                    "type": types.get(n, ""),
                    "null_ratio": loaded.null_ratio.get(n, 0.0),
                    "excluded_in": excl.get(n)}
    return stats


def _promote_with_cross_table(conn, db_type, detail, schema, scan_id,
                              loaded, scored, ref_cache):
    """Cross-table evidence for reported FDs with single-column FK dets.

    Suspected FDs at score 60+ with support become strong; strong FDs
    keep their tier and gain the support as corroborating evidence.
    """
    from .metadata import table_detail as _detail
    from . import config as _config
    log = get_logger()
    fks = _fk_targets(detail)
    if not fks:
        log.info("skipped: no single-column foreign keys",
                 extra={"scan_id": scan_id, "step": "cross_table",
                        "table": detail["name"]})
        return
    cands = [s for s in scored
             if s.kind == "exact" and s.tier in ("strong", "suspected") and len(s.determinant) == 1
             and s.determinant[0] in fks]
    if not cands:
        log.info("skipped: no promotable findings",
                 extra={"scan_id": scan_id, "step": "cross_table",
                        "table": detail["name"]})
        return
    for s in cands:
        a = s.determinant[0]
        ref_table, ref_k = fks[a]
        try:
            if ref_table not in ref_cache:
                ref_cache[ref_table] = _detail(conn, db_type, ref_table,
                                               schema=schema, scan_id=scan_id)
            ref_det = ref_cache[ref_table]
            ref_keys = {c for k in ([ref_det.get("pk") or []]
                                    + [list(u) for u in (ref_det.get("uniques") or [])])
                        for c in k}
            for col in ref_det.get("columns", []):
                bcol = col["name"]
                if bcol in ref_keys or bcol == ref_k:
                    continue
                pairs = load_ref_pairs(conn, db_type, ref_table, ref_k,
                                       bcol, schema=schema, scan_id=scan_id)
                out = check_support(_raw_values(loaded, a),
                                    _raw_values(loaded, s.dependent), pairs)
                log.info("compare %s.%s vs %s.%s: match=%.3f n=%d %s",
                         detail["name"], s.dependent, ref_table, bcol,
                         out["match_rate"], out["comparable"],
                         "SUPPORTED" if out["supported"] else "no",
                         extra={"scan_id": scan_id, "step": "cross_table",
                                "table": detail["name"]})
                if out["supported"]:
                    s.evidence["cross_table"] = {
                        "supported": True,
                        "ref_table": ref_table, "ref_column": bcol,
                        "match_rate": out["match_rate"],
                        "comparable": out["comparable"],
                        "distinct_a": out["distinct_a"]}
                    if s.tier == "suspected" and s.confidence >= _config.STRONG_CONFIDENCE_CROSS_TABLE:
                        s.tier = "strong"
                    break
        except Exception as e:
            log.warning("cross-table check skipped for %s: %s: %s", a,
                        type(e).__name__, e,
                        extra={"scan_id": scan_id, "step": "cross_table",
                               "table": detail["name"]})
            continue


def _filter_formal_keys_after_validation(formal_keys, all_keys):
    """Keep schema keys authoritative; otherwise use validated observed keys."""
    if any(k.get("source") == "declared" for k in formal_keys):
        return [k for k in all_keys if k.get("source") == "declared"]
    return [k for k in all_keys if k.get("source") in ("inferred", "inferred_verified") ]


def _assemble(name, detail, loaded, verdict, scored, keys, discovery=None):
    fk_of = {}
    for fk in detail.get("fks") or []:
        for c, r in zip(fk.get("columns") or [], fk.get("referred_columns") or []):
            fk_of[c] = {"table": fk.get("referred_table"), "column": r}
    pk = set(detail.get("pk") or [])
    kept = [s for s in scored if s.tier in ("strong", "suspected")]
    strong_count = sum(1 for s in scored if s.tier == "strong")
    suspected_count = sum(1 for s in scored if s.tier == "suspected")
    discarded_count = sum(1 for s in scored if s.tier == "discarded")
    near_count = sum(1 for s in scored if s.kind == "near")
    key_list = keys.get("keys", []) if keys else []
    primary = key_list[0] if key_list else None
    if primary is None:
        key_confidence = None
    elif primary.get("source") == "declared":
        key_confidence = "High"
    elif primary.get("source") == "inferred_verified":
        key_confidence = "Medium"
    elif primary.get("source") == "inferred_sample":
        key_confidence = "Low"
    else:
        key_confidence = "Medium"
    return {
        "name": name, "row_count": loaded.row_count, "sampled": loaded.sampled,
        "status": verdict["status"], "normal_form": verdict["normal_form"],
        "current_normal_form": verdict.get("current_normal_form", verdict["normal_form"]),
        "violated_normal_form": verdict.get("violated_normal_form"),
        "analysis_limitations": verdict.get("analysis_limitations", []),
        "keys": [{"columns": list(k.get("columns", [])),
                  "source": k.get("source", "inferred")} for k in key_list],
        "key_confidence": key_confidence,
        "key_status": keys.get("status", "unknown") if keys else "unknown",
        "analysis_complete": verdict.get("analysis_complete", False),
        "fd_discovery": {"algorithm": getattr(discovery, "algorithm", None),
                         "complete": getattr(discovery, "complete", False),
                         "tests": getattr(discovery, "tests", 0),
                         "pruned": getattr(discovery, "pruned", 0)},
        "fd_summary": {"total_discovered": len(scored), "strong": strong_count,
                       "suspected": suspected_count, "near": near_count,
                       "weak_suppressed": discarded_count},
        "columns": [{"name": c["name"], "type": str(c.get("type", "")),
                     "pk": c["name"] in pk, "fk": fk_of.get(c["name"]),
                     "nullable": bool(c.get("nullable", True))}
                    for c in detail.get("columns", [])],
        "violations": verdict["violations"],
        "fds": [{"determinant": s.determinant, "dependent": s.dependent,
                 "kind": s.kind, "equivalent": s.equivalent,
                 "confidence": s.confidence, "tier": s.tier,
                 "exceptions": s.exceptions,
                 "coincidence": {"exponent": round(s.exponent, 2),
                                 "text": s.coincidence},
                 "evidence": s.evidence} for s in kept],
        "recommendation": recommend(name, verdict["violations"], key_list),
    }


def analyze_db(params, selected, schema=None, scan_id=None, on_event=None,
               stop_event=None, max_rows=config.MAX_ROWS_PER_TABLE):
    """Scan the selected tables. Returns the §8 result JSON (never raises)."""
    scan_id = scan_id or uuid.uuid4().hex[:8]
    try:
        engine, conn, version = connect(params, scan_id=scan_id)
    except ConnectError as e:
        started = datetime.datetime.now(datetime.timezone.utc)
        result = {"scan": {}, "table_summary": [], "tables": [],
                  "notices": []}
        counts = {"faulted": 0, "suspected": 0, "bcnf_faulted": 0, "bcnf_suspected": 0,
                  "incomplete": 0, "insufficient_data": 0, "clean": 0, "error": 0, "not_run": 0}
        result["scan"] = _scan_head(params, schema, scan_id, started, 0.0,
                                    "failed", selected, 0, counts)
        result["error"] = e.error_obj
        if on_event:
            on_event({"event": "scan_done", "status": "failed",
                      "error": e.error_obj, "counts": counts})
        return result
    try:
        return analyze_on_connection(engine, conn, params, selected,
                                     schema=schema, scan_id=scan_id,
                                     on_event=on_event, stop_event=stop_event,
                                     max_rows=max_rows,
                                     version=version)
    finally:
        try:
            close(engine, conn)
        except Exception:
            pass


def analyze_on_connection(engine, conn, params, selected, schema=None,
                         scan_id="scan", on_event=None, stop_event=None,
                         max_rows=config.MAX_ROWS_PER_TABLE, version=""):
    """Scan body given an open read-only connection (never raises)."""
    started = datetime.datetime.now(datetime.timezone.utc)
    t_start = time.monotonic()
    result = {"scan": {}, "table_summary": [], "tables": [], "notices": []}
    counts = {"faulted": 0, "suspected": 0, "bcnf_faulted": 0, "bcnf_suspected": 0,
              "incomplete": 0, "insufficient_data": 0, "clean": 0, "error": 0, "not_run": 0}

    def emit(ev):
        if on_event:
            on_event(ev)

    tick_stop = None
    try:
        briefs = list_tables(conn, params.db_type, schema=schema, scan_id=scan_id)
        total_in_db = len(briefs)
        if not selected:
            err = {"error": {"code": "VALIDATION_ERROR",
                             "message": "Invalid input: no table ticked.",
                             "hint": "Tick at least one table and start the scan again.",
                             "step": "scan", "scan_id": scan_id}}
            result["scan"] = _scan_head(params, schema, scan_id, started,
                                        time.monotonic() - t_start, "failed",
                                        selected, total_in_db, counts)
            result["error"] = err["error"]
            emit({"event": "scan_done", "status": "failed",
                  "error": err["error"], "counts": counts})
            return result

        # Pass A: details + bounded counts for the estimate.
        details, errors, units = {}, {}, {}
        for name in selected:
            try:
                details[name] = table_detail(conn, params.db_type, name,
                                             schema=schema, scan_id=scan_id)
            except ConnectError as e:
                code = _per_table_code(e.error_obj["error"]["code"])
                errors[name] = {"code": code,
                                "message": e.error_obj["error"]["message"],
                                "hint": e.error_obj["error"]["hint"],
                                "step": "metadata", "scan_id": scan_id}
        for name, det in details.items():
            searchable, _ = split_columns(det)
            n = bounded_count(conn, params.db_type, name, schema=schema,
                              scan_id=scan_id)
            keys_decl = [list(det.get("pk") or [])] + [list(u) for u in (det.get("uniques") or [])]
            keys_decl = [k for k in keys_decl if k]
            rows = n if n is not None else max_rows
            lim = (config.MAX_LHS_SIZE_LARGE if rows >= config.LARGE_TABLE_ROWS
                   else config.MAX_LHS_SIZE)
            units[name] = None if n is None else work_units(
                min(n, max_rows), len(searchable),
                count_candidates(len(searchable), lim, keys_decl))
            details[name]["_known_count"] = n

        est = Estimator(units)
        emit({"event": "scan_started", "tables": list(selected),
              "total": len(selected)})
        first_est = est.estimate(list(selected))
        emit({"event": "estimate", **first_est})

        import threading as _threading
        tick_stop = _threading.Event()

        def _unfinished():
            done = {s["name"] for s in result["table_summary"]}
            return [n for n in selected if n not in done and n not in errors]

        def _tick():
            while not tick_stop.wait(config.ETA_TICK_SECONDS):
                try:
                    emit({"event": "estimate", **est.estimate(_unfinished())})
                except Exception:
                    pass

        ticker = None
        if on_event is not None:
            ticker = _threading.Thread(target=_tick, daemon=True,
                                       name="estimate-" + scan_id)
            ticker.start()

        ref_cache = {}
        idx = 0
        stopped = False
        for name in selected:
            idx += 1
            if stop_event is not None and stop_event.is_set():
                stopped = True
                done = {s["name"] for s in result["table_summary"]}
                for rest in selected[idx - 1:]:
                    if rest not in done and rest not in errors:
                        counts["not_run"] += 1
                        result["table_summary"].append(
                            {"name": rest, "status": "not_run",
                             "normal_form": None, "duration_seconds": 0.0})
                        done.add(rest)
                break
            if name in errors:
                counts["error"] += 1
                result["table_summary"].append(
                    {"name": name, "status": "error", "normal_form": None,
                     "duration_seconds": 0.0})
                result["notices"].append({"table": name, **errors[name]})
                emit({"event": "table_error", "table": name,
                      "error": errors[name]})
                continue
            emit({"event": "table_started", "table": name, "index": idx,
                  "total": len(selected)})
            t0 = time.monotonic()
            try:
                det = details[name]
                known = det.pop("_known_count", None)
                res, used_units, secs = _run_one(
                    conn, params, det, schema, scan_id, ref_cache,
                    on_event, max_rows, known)
                est.units[name] = used_units
                est.observe(name, secs)
                counts[res["status"]] = counts.get(res["status"], 0) + 1
                result["table_summary"].append(
                    {"name": name, "status": res["status"],
                     "normal_form": res["normal_form"],
                     "duration_seconds": round(secs, 2)})
                if res["status"] in ("faulted", "suspected", "bcnf_faulted",
                                       "bcnf_suspected", "incomplete", "insufficient_data"):
                    result["tables"].append(res)
                emit({"event": "table_done", "table": name,
                      "status": res["status"],
                      "normal_form": res["normal_form"],
                      "duration_seconds": round(secs, 2),
                      "result": res if res["status"] in ("faulted", "suspected", "bcnf_faulted", "bcnf_suspected", "incomplete") else None})
            except ConnectError as e:
                secs = time.monotonic() - t0
                code = _per_table_code(e.error_obj["error"]["code"])
                err = {"code": code, "message": e.error_obj["error"]["message"],
                       "hint": e.error_obj["error"]["hint"],
                       "step": e.error_obj["error"]["step"], "scan_id": scan_id}
                get_logger().error("table %s failed: %s", name, code,
                                   extra={"scan_id": scan_id, "step": "scan",
                                          "table": name})
                est.observe(name, secs)
                counts["error"] += 1
                result["table_summary"].append(
                    {"name": name, "status": "error", "normal_form": None,
                     "duration_seconds": round(secs, 2)})
                result["notices"].append({"table": name, **err})
                emit({"event": "table_error", "table": name, "error": err})
            except Exception as e:
                secs = time.monotonic() - t0
                get_logger().exception("table %s crashed: %s", name, e,
                                       extra={"scan_id": scan_id, "step": "scan",
                                              "table": name})
                est.observe(name, secs)
                counts["error"] += 1
                err = {"code": "TABLE_ANALYSIS_FAILED",
                       "message": "Table skipped: analysis failed unexpectedly.",
                       "hint": "See the log under this scan id and table name.",
                       "step": "scan", "scan_id": scan_id}
                result["table_summary"].append(
                    {"name": name, "status": "error", "normal_form": None,
                     "duration_seconds": round(secs, 2)})
                result["notices"].append({"table": name, **err})
                emit({"event": "table_error", "table": name, "error": err})
            emit({"event": "estimate",
                  **est.estimate([n for n in selected[idx:]
                                  if n not in errors])})
        # estimate on the whole selected set for the first-estimate log line
        status = "stopped" if stopped else "completed"
        secs_total = time.monotonic() - t_start
        get_logger().info("scan %s: %s (%.1fs)", status, counts, secs_total,
                          extra={"scan_id": scan_id, "step": "scan", "table": "-"})
        get_logger().info(
            "estimate first=%.1fs basis=%s actual=%.1fs", first_est["total_seconds"],
            first_est["basis"], secs_total,
            extra={"scan_id": scan_id, "step": "estimate", "table": "-"})
        result["scan"] = _scan_head(params, schema, scan_id, started,
                                    secs_total, status, selected,
                                    total_in_db, counts)
        emit({"event": "scan_done", "status": status, "counts": counts,
              "headline": headline(counts, len(selected),
                                   total_in_db - len(selected))})
        return result
    except ConnectError as e:
        result["scan"] = _scan_head(params, schema, scan_id, started,
                                    time.monotonic() - t_start, "failed",
                                    selected, 0, counts)
        result["error"] = e.error_obj
        emit({"event": "scan_done", "status": "failed",
              "error": e.error_obj, "counts": counts})
        return result
    except Exception as e:
        get_logger().exception("scan crashed: %s", e,
                               extra={"scan_id": scan_id, "step": "scan",
                                      "table": "-"})
        from .errors import make_error
        err = make_error("INTERNAL_ERROR", step="scan", scan_id=scan_id)["error"]
        result["scan"] = _scan_head(params, schema, scan_id, started,
                                    time.monotonic() - t_start, "failed",
                                    selected, 0, counts)
        result["error"] = err
        emit({"event": "scan_done", "status": "failed",
              "error": err, "counts": counts})
        return result
    finally:
        if tick_stop is not None:
            tick_stop.set()


def _run_one(conn, params, detail, schema, scan_id, ref_cache, on_event,
             max_rows, known):
    return analyze_table(conn, params.db_type, detail, schema, scan_id,
                         ref_cache, on_event=on_event, max_rows=max_rows,
                         known_count=known)


def _scan_head(params, schema, scan_id, started, duration, status,
               selected, total_in_db, counts):
    return {"id": scan_id, "db_type": params.db_type,
            "host": params.host, "database": params.database,
            "schema": schema, "started_at": started.isoformat(),
            "duration_seconds": round(duration, 1), "status": status,
            "database_count": getattr(params, "database_count", None),
            "selected_tables": list(selected),
            "counts": {"total_in_db": total_in_db, "selected": len(selected),
                       "not_selected": total_in_db - len(selected),
                       **counts}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Scan selected tables; print result JSON.")
    ap.add_argument("--db-type", required=True)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--username", default="root")
    ap.add_argument("--password", default=None)
    ap.add_argument("--database", default="normtest")
    ap.add_argument("--schema", default=None)
    ap.add_argument("--service-name", default=None)
    ap.add_argument("--ssl", action="store_true")
    ap.add_argument("--tables", default="",
                    help="comma-separated table names, or empty with --all")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default=None,
                    help="write result JSON to this file (utf-8) instead of stdout")
    a = ap.parse_args(argv)
    pw = a.password if a.password is not None else getpass.getpass("Password: ")
    setup_logging()
    params = ConnectParams(db_type=a.db_type, host=a.host, port=a.port,
                           username=a.username, password=pw,
                           database=a.database, schema=a.schema,
                           service_name=a.service_name, ssl=a.ssl)
    if a.all:
        try:
            engine, conn, _ = connect(params, scan_id="cli")
            try:
                briefs = list_tables(conn, params.db_type, schema=params.schema,
                                     scan_id="cli")
                selected = [b["name"] for b in briefs]
            finally:
                close(engine, conn)
        except ConnectError as e:
            print(json.dumps({"error": e.error_obj["error"]}, indent=2))
            return 2
    else:
        selected = [t.strip() for t in a.tables.split(",") if t.strip()]
    result = analyze_db(params, selected, schema=params.schema, scan_id="cli")
    text = json.dumps(result, indent=2, default=str)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("wrote %s (%s)" % (a.out, result["scan"].get("status")))
    else:
        print(text)
    return 0 if result["scan"].get("status") in ("completed", "stopped") else 2


if __name__ == "__main__":
    raise SystemExit(main())
