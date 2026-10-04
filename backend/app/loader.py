"""Row loading: bounded counts, integer-coded columns, NULL policy.

One SELECT per table; all FD work happens in memory afterwards.
LOB/binary/JSON columns are excluded from the FD search (listed with a
reason) and their values are never loaded.
"""

from dataclasses import dataclass, field

from . import config
from .db_connect import ConnectError, assert_select, map_exception
from .logging_setup import StepTimer, get_logger

_BINARY_MARKERS = ("BLOB", "BYTEA", "BINARY", "IMAGE", "RAW", "BFILE")
_TEXT_MARKERS = ("CLOB", "NCLOB", "NTEXT", "MEDIUMTEXT", "LONGTEXT", "TINYTEXT", "TEXT")


def exclusion_reason(type_str: str) -> str | None:
    """Why a column is excluded from FD search, or None if searchable."""
    u = (type_str or "").upper()
    if "JSON" in u:
        return "json"
    if any(k in u for k in _BINARY_MARKERS):
        return "binary"
    if any(k in u for k in _TEXT_MARKERS):
        return "wide-text"
    return None


def split_columns(detail: dict) -> tuple:
    """(searchable [names], excluded [{name, type, reason}]) from a detail dict."""
    searchable, excluded = [], []
    for c in detail.get("columns", []):
        reason = exclusion_reason(str(c.get("type", "")))
        if reason is None:
            searchable.append(c["name"])
        else:
            excluded.append({"name": c["name"], "type": str(c.get("type", "")),
                             "reason": reason})
    return searchable, excluded


def code_columns(raw: dict) -> tuple:
    """Integer-code each column: equal values -> equal codes, NULL stays None.

    Returns (coded {name: [int|None]}, null_ratio {name: float},
    labels {name: [original value per code]}).
    NULL policy: rows with NULL in X are excluded from checks later;
    NULL in Y counts as a value (None groups with None).
    """
    coded, ratios, labels = {}, {}, {}
    for name, vals in raw.items():
        mapping: dict = {}
        inv: list = []
        col = []
        nulls = 0
        for v in vals:
            if v is None:
                col.append(None)
                nulls += 1
                continue
            try:
                hash(v)
            except TypeError:
                v = str(v)
            if v not in mapping:
                mapping[v] = len(inv)
                inv.append(v)
            col.append(mapping[v])
        coded[name] = col
        ratios[name] = (nulls / len(vals)) if vals else 0.0
        labels[name] = inv
    return coded, ratios, labels


@dataclass
class LoadedTable:
    name: str
    row_count: int  # rows actually loaded
    bounded_count: int | None  # min(actual, max_rows+1); None if count failed
    sampled: bool
    columns: list  # searchable column names
    coded: dict  # name -> [int | None]
    null_ratio: dict
    labels: dict = field(default_factory=dict)  # name -> [value per code]
    excluded: list = field(default_factory=list)
    order_columns: list = field(default_factory=list)


def _table_ref(detail_name: str, searchable: list, schema: str | None):
    from sqlalchemy import column as sa_column
    from sqlalchemy import table as sa_table
    return sa_table(detail_name, *[sa_column(c) for c in searchable],
                    schema=schema)


def bounded_count_query(table: str, db_type: str, schema: str | None,
                        max_rows: int):
    from sqlalchemy import func, literal, select
    from sqlalchemy import table as sa_table
    kw_schema = None if (db_type or "").lower() == "mysql" else schema
    t = sa_table(table, schema=kw_schema)
    inner = select(literal(1)).select_from(t).limit(max_rows + 1).subquery()
    return select(func.count()).select_from(inner)


def bounded_count(engine_or_conn, db_type: str, table: str,
                  schema: str | None = None, scan_id: str = "-",
                  max_rows: int = config.MAX_ROWS_PER_TABLE) -> int | None:
    """SELECT COUNT(*) over a LIMITed subquery. WARNING + None on failure."""
    log = get_logger()
    with StepTimer("count", scan_id, table):
        try:
            q = bounded_count_query(table, db_type, schema, max_rows)
            sql = str(q.compile(compile_kwargs={"literal_binds": True}))
            assert_select(sql)
            val = engine_or_conn.execute(q).scalar()
            count = int(val)
        except Exception as e:
            log.warning("bounded count failed for %s: %s: %s", table,
                        type(e).__name__, e,
                        extra={"scan_id": scan_id, "step": "count", "table": table})
            return None
    log.info("bounded_count=%s", count,
             extra={"scan_id": scan_id, "step": "count", "table": table})
    return count


def load_table(engine_or_conn, db_type: str, detail: dict,
               schema: str | None = None, scan_id: str = "-",
               max_rows: int = config.MAX_ROWS_PER_TABLE,
               known_count: int | None = None) -> LoadedTable:
    """Load one table's searchable columns, honouring the row cap."""
    from sqlalchemy import select
    log = get_logger()
    name = detail["name"]
    searchable, excluded = split_columns(detail)
    order = [c for c in (detail.get("pk") or []) if c in searchable]
    if not searchable:
        log.info("rows=0 sampled=False excluded=%d (nothing searchable)", len(excluded),
                 extra={"scan_id": scan_id, "step": "load", "table": name})
        return LoadedTable(name=name, row_count=0, bounded_count=known_count,
                           sampled=False, columns=[], coded={}, null_ratio={},
                           labels={}, excluded=excluded, order_columns=[])
    with StepTimer("load", scan_id, name):
        try:
            kw_schema = None if (db_type or "").lower() == "mysql" else schema
            t = _table_ref(name, searchable, kw_schema)
            q = select(*[t.c[c] for c in searchable])
            for c in order:
                q = q.order_by(t.c[c])
            if known_count is not None and known_count > max_rows:
                q = q.limit(max_rows)
            sql = str(q.compile(compile_kwargs={"literal_binds": True}))
            assert_select(sql)
            result = engine_or_conn.execute(q)
            raw_cols = {c: [] for c in searchable}
            for row in result:
                for c, v in zip(searchable, row):
                    raw_cols[c].append(v)
                if len(raw_cols[searchable[0]]) >= max_rows:
                    break
            n = len(next(iter(raw_cols.values()), []))
            sampled = known_count is not None and known_count > max_rows
            coded, ratios, labels = code_columns(raw_cols)
        except Exception as e:
            log.error("load failed for %s: %s: %s", name, type(e).__name__, e,
                      extra={"scan_id": scan_id, "step": "load", "table": name})
            raise ConnectError(map_exception(e, step="load", scan_id=scan_id)) from e
    log.info("rows=%d sampled=%s excluded=%d", n, sampled, len(excluded),
             extra={"scan_id": scan_id, "step": "load", "table": name})
    return LoadedTable(name=name, row_count=n, bounded_count=known_count,
                       sampled=sampled, columns=searchable, coded=coded,
                       null_ratio=ratios, labels=labels,
                       excluded=excluded, order_columns=order)


def load_columns(engine_or_conn, db_type: str, table: str, columns: list,
                 schema: str | None = None, scan_id: str = "-",
                 limit: int | None = None) -> list:
    """Load raw values for a few columns (cross-table evidence)."""
    from sqlalchemy import select
    kw_schema = None if (db_type or "").lower() == "mysql" else schema
    t = _table_ref(table, columns, kw_schema)
    q = select(*[t.c[c] for c in columns])
    if limit is not None:
        q = q.limit(limit)
    assert_select(str(q.compile(compile_kwargs={"literal_binds": True})))
    try:
        return [tuple(row) for row in engine_or_conn.execute(q)]
    except Exception as e:
        get_logger().error("column load failed for %s: %s: %s", table,
                           type(e).__name__, e,
                           extra={"scan_id": scan_id, "step": "load", "table": table})
        raise ConnectError(map_exception(e, step="load", scan_id=scan_id)) from e
