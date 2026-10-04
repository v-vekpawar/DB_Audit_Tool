# Design

Status: agreed scope, ready to build. Updated 2026-10-02: FD detection and
scoring rewritten (section 6), table selection, live progress and time estimate
added (sections 9 and 10). Values marked **(tune)** are initial guesses to be
tuned against the test databases (see `TEST_PLAN.md`).

Step 8 tuning record (2026-10-04, M1): recall 100%, precision 100%
(zero false faulted), cross-engine parity on all 29 planted tables, with
every threshold at its default (`STRONG_CONFIDENCE` 75,
`STRONG_CONFIDENCE_CROSS_TABLE` 60, `SUSPECT_CONFIDENCE` 50,
`MIN_REPEATED_ROWS` 5, `NEAR_FD_MAX_ERROR` 3%, `NEAR_FD_MIN_GROUP` 3,
score caps 90/85, `CROSS_TABLE_MATCH_MIN` 97%). No threshold or formula
change was needed. `ETA_DEFAULT_SECONDS_PER_UNIT` set to 2e-07 from
measurement (first estimate 15.1s rough vs 13.4s/12.0s actual).
Benchmarks: full 29-table suite 12-14s; 55 000-row table 16.5s
(`numpy` not needed).
 
---
 
## 1. Scope
 
**In scope**
- MySQL, PostgreSQL, Oracle (non-EOL versions; exact version unknown per target).
- One database per run (Oracle: one schema).
- **Two-step flow:** Connect lists the tables; the reader ticks the tables to
  analyse; only ticked tables are scanned.
- Normal forms up to BCNF.
- Flag a table only if it is **below 3NF**.
- Exact normal form per flagged table, functional dependencies with confidence
  scores and real-value evidence, reason, one-sentence recommendation.
- **Live view:** each table shows "checking" while it is analysed, its result
  appears as soon as it finishes, and a total time estimate is shown.
- Graph-like faulted-table cards, dimmed "suspected" cards, one-line headline.
- On-screen result and backend-generated PDF export (not stored).
- Store only the last scan result.
- Typical size per table: 10-15 columns, 100 to several thousand rows
  (larger tables are sampled, see section 5).
**Phase 2 (built last)**
- EOL check of the DB version against a bundled table.
- "Possibly unused" table detection.
**Out of scope**
- 4NF/5NF (multi-valued and join dependencies).
- Writing to target databases, generating DDL, running migrations.
- Saving credentials or scan history.
- Any OS-level or source-code access to targets.
---
 
## 2. Environment constraints that shape the design
 
- vm1: Windows 64-bit, Python 3.12.10, **no internet** at run time.
- Everything is bundled as `win_amd64` / `cp312` wheels plus source and installed
  with `pip install --no-index`. Dev-only packages are not shipped.
- Targets use self-signed certificates: DB SSL connections skip verification.
- Targets are other VMs reachable over the network; only the DB port is used.
- Therefore: pure-Python DB drivers where possible, no Oracle client, no browser,
  no CDN, system fonts only.
---
 
## 3. Architecture
 
```
Browser (HTML/CSS/JS, SVG cards)
        |  HTTP + SSE
FastAPI app (127.0.0.1 only)
  main.py ---- scan_sessions.py (in-memory state, events, stop)
        |
  Connect:  db_connect.py -> metadata.py (table list only, then closes)
  Scan:     analyzer.py  (one selected table at a time)
              db_connect.py -> metadata.py -> estimator.py (counts, time estimate)
              loader.py -> fd_discovery.py -> fd_scoring.py -> cross_table.py
              keys.py -> normal_forms.py -> recommend.py
        |
  storage.py (SQLite: last scan only)     export_pdf.py (reportlab)
        |
Target DB (MySQL / PostgreSQL / Oracle) -- read-only
 
Cross-cutting: logging_setup.py (every step logs) and errors.py (every failure
becomes a coded error shown in the UI)
```
 
One process, one port. The backend serves the frontend as static files.
 
### Module responsibilities
 
| Module | Responsibility |
|---|---|
| `config.py` | Tunables, ports, thresholds, log settings |
| `logging_setup.py` | Rotating file and console logging, secret-masking filter, per-step helper |
| `errors.py` | Error codes, plain-language messages and hints, driver-exception mapping |
| `schemas.py` | Pydantic request/response models |
| `db_connect.py` | Build SQLAlchemy URL per engine, SSL options, read-only session, server version string |
| `metadata.py` | `sqlalchemy.inspect()`: tables, columns, types, PK, unique, FK, indexes |
| `loader.py` | Load rows per table into memory, NULL policy, LOB/binary/JSON exclusion, row cap, bounded row count |
| `fd_discovery.py` | Exact minimal FD search over a table (pure function, no DB) |
| `fd_scoring.py` | Coincidence probability, correction, near-FD handling, confidence and tier (pure function) |
| `cross_table.py` | Cross-table evidence: compares a table's value pairs with the referenced table's |
| `keys.py` | Candidate keys: declared (certain) and inferred (flagged) |
| `normal_forms.py` | 1NF-BCNF classification, violations list |
| `recommend.py` | One-sentence recommendation per violation |
| `estimator.py` | Work estimate per table, total and remaining time estimate |
| `analyzer.py` | Orchestrates the scan over the selected tables, per-table error isolation, progress events |
| `scan_sessions.py` | Scan lifecycle, SSE events, stop flag |
| `storage.py` | Single-row SQLite store for the last result |
| `export_pdf.py` | Draws the cards and headline into a PDF (reportlab), returns bytes |
| `eol.py`, `unused.py` | Phase 2 |
 
`fd_discovery`, `fd_scoring`, `keys`, `normal_forms`, `recommend` and the
estimator's formulas are pure functions over in-memory data. They can be
unit-tested with no database.
 
---
 
## 4. Database access
 
### Connection URLs (SQLAlchemy)
 
| Engine | URL |
|---|---|
| MySQL | `mysql+pymysql://user:pass@host:port/database` |
| PostgreSQL | `postgresql+pg8000://user:pass@host:port/database` |
| Oracle | `oracle+oracledb://user:pass@host:port/?service_name=SVC` |
 
Passwords are URL-escaped. SSL: when enabled, verification is disabled
(self-signed certificates).
 
### Two connections per run, nothing kept between them
 
- **Connect step:** opens a connection, applies read-only mode, reads the version
  string and the table list (name, column count, has primary key, foreign key
  count), then closes. No rows are read.
- **Scan step:** opens a new connection with the credentials the browser form
  sends again. The server holds the password in memory only for the duration of
  the scan, never on disk.
### Read-only enforcement
 
- Session level: MySQL `SET SESSION TRANSACTION READ ONLY`; PostgreSQL
  `default_transaction_read_only = on`; Oracle `SET TRANSACTION READ ONLY`.
- Code level: only `SELECT` and dictionary queries are issued (a statement guard
  rejects anything else).
- A test (`test_readonly_guard`) fails the build if any other statement type is sent.
### Metadata
 
`sqlalchemy.inspect()` provides tables, columns, primary keys, unique
constraints, foreign keys and indexes identically across engines. Engine-specific
queries are limited to: server version string, PostgreSQL schema selection,
Oracle schema (owner) selection.
 
Views, system schemas and internal tables are excluded.
 
---
 
## 5. Data loading
 
- Tables are processed **one at a time**, in the order of the selected list.
- **Bounded row count first** (step `count`, used for the time estimate): for each
  selected table, a `SELECT COUNT(*)` over a subquery limited to
  `MAX_ROWS_PER_TABLE + 1` rows (built by SQLAlchemy for each engine). It never
  scans a huge table fully. If it fails or times out, the size is treated as
  unknown, the estimate is marked rough, and a `WARNING` is logged.
- Then **one `SELECT` per table** loads its rows into memory as column-oriented
  lists. All FD work happens in memory; the tool does not query per candidate FD.
- Values are converted to integer codes per column (equal values get equal
  codes) so that grouping and comparing are fast.
- Row cap `MAX_ROWS_PER_TABLE` (50 000): above it, the first N rows in primary-key
  order are analysed and the table is marked **sampled** (confidence is capped).
- LOB, binary and very wide text columns are excluded from FD search (decided from
  the column types in the metadata) but still listed on the card and checked for
  the 1NF heuristics where relevant.
- NULL policy for `X -> Y`:
  - rows with a NULL in any column of X are excluded from the check (they cannot be grouped);
  - NULL in Y counts as a value, and NULL equals NULL.
  - NULL ratios are reported in the evidence.
- Per-table timeouts; a failing table is recorded as an error and the scan continues.
- Cross-table evidence (section 6.8) may read two columns of a referenced table
  with a small `SELECT`, even if that table is not ticked. It is not analysed or
  shown.
---
 
## 6. Functional dependency detection
 
### 6.1 Principle
 
An FD `X -> Y` holds in a table instance if no two rows agree on X and differ on
Y. Data can **disprove** an FD but never **prove** it, so a dependency that holds
in the data is only a *candidate*. The tool therefore asks a second question for
every candidate: *how likely is it that the data looks like this by pure chance
if no dependency exists?* Only candidates for which that chance is tiny are
reported.
 
### 6.2 Pipeline
 
1. Prepare columns (integer codes, exclusions, NULL policy).
2. Choose determinant sizes (6.3).
3. Discover exact FDs level by level (6.4).
4. Remove meaningless FDs (6.5).
5. Find near-FDs for dirty data (6.6).
6. Measure the chance of coincidence and correct it for the number of candidates (6.7).
7. Add cross-table evidence (6.8).
8. Compute the confidence score and the tier (6.9).
### 6.3 Determinant size
 
- Default `MAX_LHS_SIZE` = 2.
- Tables with at least `LARGE_TABLE_ROWS` (1 000) rows use `MAX_LHS_SIZE_LARGE` = 3.
  Wide determinants on small tables are almost unique, so they "determine"
  everything by luck and prove nothing.
- **Key parts are always tested.** For a declared composite key, every proper
  non-empty subset of the key is tested as a determinant, whatever its size, so
  partial dependencies (2NF) are never missed.
- Known gap: a 3-column non-key determinant on a table below `LARGE_TABLE_ROWS` is
  not searched. This is stated in the README.
**Number of candidates `m`** (used by the correction in 6.7): the number of
possible (determinant, dependent) pairs within the size limit for that table, plus
the key-part pairs beyond it.
 
```
m = sum over k = 1..L of  C(n, k) * (n - k)       n = searchable columns, L = size limit
```
 
Example: n = 15, L = 2 gives 15*14 + 105*13 = 1 575. With L = 3 it is 7 035.
 
### 6.4 Exact discovery (`fd_discovery.py`)
 
- TANE-style, level-wise search over column sets using stripped partitions
  (groups of rows sharing the same X values; single-row groups are dropped).
- **Minimality:** once `X -> Y` holds, no superset of X is reported for that Y.
- **Cheap prefilter:** `X -> Y` can only hold if Y has no more distinct values than
  X, so the full check is skipped otherwise.
- **Key pruning:** once X is unique, supersets of X are not explored.
- Output: exact FDs and near-FD candidates (6.6).
- A plain brute-force version (every determinant, every dependent, then minimise)
  exists **in the tests only**, as a reference. TANE results must equal it on every
  test table.
### 6.5 Meaningless FDs removed
 
Skipped, because they carry no information:
- determinant X is unique (no repeated groups): it is a key, not a design signal;
- dependent Y is constant (single value);
- Y or any X column is almost all NULL;
- X contains a column that is already a declared key (trivially determines everything);
- the empty determinant (`nothing -> Y`) is not searched; constant columns are
  already covered above.
**Equivalent columns:** if `X -> Y` and `Y -> X` both hold for single columns
(for example `country_code` and `country_name`), neither is "the dependent". They
are marked `equivalent`, shown once with a double-headed arrow, and the
recommendation says "equivalent columns".
 
### 6.6 Near-FDs (dirty data)
 
- A group of rows with the same X but different Y values counts as **dirty data**
  only if the group has at least `NEAR_FD_MIN_GROUP` (3) rows **and** one Y value
  is clearly the most common. The other rows in that group are the exceptions.
  In a group of 2 rows nobody can tell which row is wrong, so the FD fails.
- If the total number of exceptions `k` is at most `NEAR_FD_MAX_ERROR` (3%) of the
  rows, the FD is kept and marked **holds with exceptions**. The conflicting rows
  are shown as evidence.
- The coincidence probability is computed on the data **without** the exception
  rows (6.7), then multiplied by `C(N, k)`, the number of ways the `k` dirty rows
  could have been chosen. This is a conservative union bound: fewer exceptions
  give a stronger result.
- Near-FD scores are capped at `NEAR_FD_SCORE_CAP` (90).
### 6.7 Chance of coincidence (the core measure)
 
For `X -> Y` over `N` rows (rows with NULL in X removed; NULL in Y is a value),
let the groups of rows sharing X have sizes `g_1 ... g_r` (only groups of size 2
or more matter) and let `c_v` be how many rows have each Y value `v`.
 
If the Y values were randomly assigned to rows, the chance that all `g` rows of
one group get the same value is
 
```
q(g) = sum over v of  [c_v]_g / [N]_g          where [a]_g = a * (a-1) * ... * (a-g+1)
```
 
(terms with `c_v < g` are zero). The chance that **every** group is uniform by
luck is the product over groups:
 
```
p_raw = product over groups of q(g_i)
```
 
- It is computed in logarithms (log-gamma), and groups of equal size are combined,
  so it is fast and never underflows. The tool stores the exponent
  `e_raw = -log10(p_raw)`.
- Groups are treated as independent, which is an approximation. A unit test
  compares it with a seeded shuffle simulation on small tables.
- **Correction for many guesses:** the tool tests `m` candidates, so some will look
  real by luck. `p_adj = min(1, m * p_raw)`, so `e_adj = e_raw - log10(m)`
  (not below 0). Real FDs have exponents of 20 or more and survive easily;
  chance FDs do not.
### 6.8 Cross-table evidence
 
If table T has a foreign key `A -> R.K`, and T also holds a column `B` while R has
a non-key column `B'`: compare T's (A, B) pairs with R's (K, B') pairs. If at least
`CROSS_TABLE_MATCH_MIN` (97%) of T's rows match, with at least 5 distinct A values,
then T's `B` is a stored copy of R's `B'`: a near-certain redundancy. The finding
`A -> B` is marked **supported by another table** and may reach the strong tier
with a lower score (6.9).
 
- Only declared foreign keys are used. Inferred foreign keys (no constraint, same
  name and value inclusion) are an open item.
- Naming affinity (`customer_id` with `customer_name`) is shown as an evidence
  line only. It is **not** part of the score, so the score stays a pure
  coincidence figure.
### 6.9 Confidence score and tiers
 
**Materiality gate:** `repeated_rows >= MIN_REPEATED_ROWS` (5), where
`repeated_rows` is the sum over groups of (group size - 1): how many rows carry
duplicated information. Below the gate there is no evidence at all and the FD is
discarded.
 
**Score** (from the corrected exponent, for display):
 
```
score = round(100 * e_adj / (e_adj + 2)),  at most 99
```
 
| e_adj (chance of coincidence) | score |
|---|---|
| 2 (1 in 100) | 50 |
| 6 (1 in 1 000 000) | 75 |
| 10 | 83 |
| 30 | 94 |
| 48 | 96 |
 
Declared primary/unique keys score 100 ("declared in schema"). Sampled tables are
capped at `SAMPLED_SCORE_CAP` (85); near-FDs at 90.
 
**Tiers**
 
| Tier | Rule | Effect |
|---|---|---|
| Strong | score >= `STRONG_CONFIDENCE` (75), or score >= `STRONG_CONFIDENCE_CROSS_TABLE` (60) with cross-table support | Can make a table **faulted** |
| Suspected | score >= `SUSPECT_CONFIDENCE` (50) | Shown **dimmed**; table status `suspected` |
| Discarded | below 50, or fails the materiality gate | Not reported |
 
**On the card** both are shown:
`Confidence Score: 96 (chance of coincidence about 1 in 10^48)`. For small
exponents the plain number is written ("1 in 1,000,000").
 
This is **not** the probability that the dependency is a real business rule. It
says how unlikely the data would look like this if no dependency existed. The
README says so in one line.
 
### 6.10 Evidence shown per FD
 
| Item | Meaning |
|---|---|
| `distinct_det` | Number of distinct X values |
| `repeated_rows` | Rows that carry duplicated information (materiality) |
| `rows_in_repeated_groups` | Rows in groups of size 2 or more |
| `exceptions` | Number of exception rows (0 for exact FDs) |
| `candidates_tested` | `m`, the number used for the correction |
| `exponent` | `e_adj`, the basis of the score |
| `cross_table` | Match rate and the other table, if any |
| `naming` | Naming affinity, display only |
| `null_ratio` | NULL share in X and Y |
| `examples` | Up to 3 real value examples, e.g. `customer_id = 17 -> 'Acme' in 14 rows`; for near-FDs the conflicting rows |
 
### 6.11 Honest limits
 
- Business semantics are invisible to the tool.
- Tables below `MIN_ROWS_FOR_DATA_FDS` (10) get no data-derived FDs; they are
  `insufficient data` unless a schema-certain violation exists.
- Tables with unique values everywhere give no redundancy, therefore no evidence.
- On tables of about 100 rows, some true violations simply cannot be proven and
  will appear as suspected or not at all. This is a limit of the data, not a bug.
- FDs in the data that are true by accident of the current data but meaningless
  to the designers can still pass at very low chance levels; the evidence lets the
  reader judge.
### 6.12 Speed
 
- Pure Python on integer-coded columns, stripped partitions, prefilters and key
  pruning. Expected: milliseconds for tables of a few hundred rows, seconds for
  a few thousand rows and 15 columns, and **tens of seconds** in the worst case
  for a 50 000-row table (to be measured, see BUILD_ORDER step 8).
- If measurements show a problem, options in this order: lower
  `MAX_ROWS_PER_TABLE`, lower `MAX_LHS_SIZE_LARGE`, then add `numpy` as a wheel for
  the partition work. `numpy` is not in the plan until a benchmark needs it.
---
 
## 7. Keys and normal-form classification
 
### Candidate keys (`keys.py`)
 
1. Declared primary key and unique constraints: **certain**.
2. If none exists: minimal column sets (size <= 3) whose values are unique in the
   data. These are **inferred** keys, reported with lower confidence.
3. If no unique set exists, the table has duplicate rows or no usable key: 1NF finding.
**Prime attribute** = a column that belongs to at least one candidate key.
 
### Rules (`normal_forms.py`)
 
| Form | Violation | Condition |
|---|---|---|
| 1NF | No key | No declared key and no inferred key |
| 1NF | Multi-valued cell | A column where a large share of non-null values hold delimiter-separated lists (comma, semicolon, pipe), JSON arrays, or a native array type |
| 1NF | Repeating group | Two or more columns named like `stem` + number (`phone1`, `phone2`, `phone3`) |
| 2NF | Partial dependency | `X -> Y`, X a **proper subset** of a composite candidate key, Y non-prime |
| 3NF | Transitive dependency | `X -> Y`, X not a superkey, X not a subset of a key (else it is 2NF), Y non-prime |
| BCNF | Non-superkey determinant | `X -> Y`, X not a superkey (Y prime allowed) |
 
Exact normal form of a table = highest form with no violation, evaluated using
**strong** FDs only. Violations that exist only through suspected FDs set the
table status to `suspected` and the card says "possibly <form>".
 
Equivalent columns (6.5) are treated as one attribute: if one is a superkey, so
is the other; a violation through them is reported once.
 
### Table status
 
| Status | Meaning | Shown |
|---|---|---|
| `faulted` | Below 3NF with strong evidence or schema-certain | Full card |
| `suspected` | Below 3NF only through suspected FDs | Dimmed card |
| `clean` | 3NF or BCNF (3NF-but-not-BCNF is clean, its exact form is recorded) | Counted only |
| `insufficient_data` | Too few rows to judge and no schema-certain violation | Counted only |
| `error` | Could not be analysed (permissions, timeout, table dropped) | Listed in a small notice |
| `not_run` | Ticked, but the scan was stopped before it was reached | Listed, counted separately |
 
Tables that were not ticked are not analysed and are counted as `not_selected`,
never as clean.
 
### Recommendation (`recommend.py`)
 
One plain sentence from a template, for example:
 
- Transitive: `Move customer_name, customer_email into a separate table keyed by customer_id and keep customer_id as a foreign key in orders.`
- Partial: `Move product_name into a table keyed by product_id; it depends on only part of the key (order_id, product_id).`
- Equivalent: `country_code and country_name are equivalent columns; keep one as the key of a lookup table.`
- 1NF delimited: `Store each value of tags in its own row of a child table instead of a comma-separated list.`
- 1NF repeating: `Replace phone1, phone2, phone3 with a child table holding one phone per row.`
- 1NF no key: `Add a primary key to this table.`
No DDL is generated.
 
---
 
## 8. Result data model
 
Stored as JSON in SQLite (one row, overwritten each scan).
 
```json
{
  "scan": {
    "id": "uuid", "db_type": "postgresql", "db_version": "17.4",
    "host": "10.0.0.5", "database": "shop", "schema": "public",
    "started_at": "...", "duration_seconds": 83, "status": "completed",
    "selected_tables": ["orders", "customers"],
    "counts": {"total_in_db": 42, "selected": 12, "not_selected": 30,
               "scanned": 12, "faulted": 3, "suspected": 1,
               "insufficient_data": 1, "clean": 7, "error": 0, "not_run": 0}
  },
  "table_summary": [
    {"name": "orders", "status": "faulted", "normal_form": "2NF", "duration_seconds": 4.2}
  ],
  "tables": [
    {
      "name": "orders", "row_count": 1200, "sampled": false,
      "status": "faulted", "normal_form": "2NF",
      "columns": [{"name": "order_id", "type": "int", "pk": true,
                   "fk": null, "nullable": false}],
      "violations": [
        {"form": "3NF", "kind": "transitive",
         "determinant": ["customer_id"], "dependents": ["customer_name", "customer_email"],
         "reason": "customer_name depends on customer_id, not on the key order_id."}
      ],
      "fds": [
        {"determinant": ["customer_id"], "dependent": "customer_name",
         "kind": "exact", "equivalent": false,
         "confidence": 96, "tier": "strong", "exceptions": 0,
         "coincidence": {"exponent": 48.2, "text": "1 in 10^48"},
         "evidence": {"distinct_det": 34, "repeated_rows": 1166,
                      "rows_in_repeated_groups": 1200, "candidates_tested": 1575,
                      "cross_table": null, "naming": true,
                      "examples": [{"det": [17], "dep": "Acme", "rows": 14}]}}
      ],
      "recommendation": "Move customer_name, customer_email ..."
    }
  ],
  "notices": []
}
```
 
`table_summary` lists every scanned table (so the Last scan panel can show what was
checked); `tables` holds full detail for faulted and suspected tables only. Clean
tables are not listed in `tables`, only counted. Credentials are never part of the
result.
 
---
 
## 9. API
 
### 9.1 Endpoints
 
| Method | Path | Purpose |
|---|---|---|
| POST | `/api/connect` | Connect, return the database list, close the connection |
| POST | `/api/connect/tables` | Table list for the picked database, close the connection |
| POST | `/api/scan/start` | Start a scan of the ticked tables, returns `{scan_id}` |
| GET | `/api/scan/{id}/progress` | SSE: live events (9.3) |
| GET | `/api/scan/{id}/results` | Final or partial result JSON |
| POST | `/api/scan/{id}/stop` | Stop; keeps partial results with status `stopped` |
| GET | `/api/scan/current` | The scan that is running now, or 404 (so a page reload can re-attach) |
| GET | `/api/scan/last` | Last stored result |
| GET | `/api/scan/{id}/export?format=pdf` | PDF bytes, not stored |
 
### 9.2 Bodies
 
`POST /api/connect` body (no database yet; PostgreSQL lists via the
`postgres` maintenance database):
 
```json
{"db_type": "mysql|postgresql|oracle", "host": "", "port": 3306,
 "username": "", "password": "", "database": "", "schema": null,
 "service_name": null, "ssl": false}
```
 
Response:

```json
{"scan_id": "uuid", "db_type": "postgresql", "db_version": "17.4",
 "databases": ["normtest", "shop"]}
```

`POST /api/connect/tables` body: the connection fields plus the picked
`database` (Oracle: `service_name` plus the picked `schema`):

Response:

```json
{"scan_id": "uuid", "db_type": "postgresql", "db_version": "17.4", "schema": "public",
 "tables": [{"name": "orders", "columns": 8, "has_pk": true, "fks": 1}]}
```
 
(Connect requests also get an id, logged and shown in errors in the same
`scan_id` field.)
 
`POST /api/scan/start` body: the connection fields plus
 
```json
{"tables": ["orders", "customers"]}
```
 
`tables` must be non-empty (`VALIDATION_ERROR` otherwise). A name that no longer
exists gets the per-table error `TABLE_NOT_FOUND`; the scan continues.
 
Only one scan runs at a time. Errors use the structured format in section 13
(code, plain message, hint, step, scan id) with no raw exception text and no
password; the technical detail is in the log under the same code and scan id.
 
### 9.3 Live events (SSE)
 
Every event has a sequence number. The stream sends a heartbeat comment every
`SSE_HEARTBEAT_SECONDS` (15).
 
| Event | Carries | UI effect |
|---|---|---|
| `scan_started` | selected table names, total count | Builds the table status list (all "queued") |
| `estimate` | `total_seconds`, `elapsed_seconds`, `remaining_seconds`, `basis` (`rough` or `measured`) | Updates the time line |
| `table_started` | table, index, total | Row turns to "checking" |
| `table_step` | table, step (`loading`, `finding dependencies`, `scoring`, `classifying`) | Row shows the current step |
| `table_done` | table, status, normal form, duration, and for faulted/suspected tables the full table result | Row shows its verdict; the card appears at once; headline counts update |
| `table_error` | table, error object | Row shows the error code |
| `scan_done` | status (`completed`, `stopped`, `failed`), counts | Final headline, Export enabled |
| `error` | error object | Error panel (whole-scan failure) |
 
If no event or heartbeat arrives for `STREAM_TIMEOUT_SECONDS` (45), or the stream
drops, the UI shows the banner `PROGRESS_STREAM_LOST`, fetches
`/api/scan/{id}/results` (partial results) to resynchronise, and reconnects. The
scan itself keeps running on the server.
 
### 9.4 Time estimate (`estimator.py`)
 
The estimate is honest but approximate.
 
1. After the bounded row counts (section 5), each selected table gets a work
   amount: `units = rows * (columns + m)`, with `rows` capped at
   `MAX_ROWS_PER_TABLE`, `columns` the searchable columns, and `m` from 6.3.
2. Speed is seconds per unit. Before any table has finished it is the default
   `ETA_DEFAULT_SECONDS_PER_UNIT` **(tune)**, treated as if it had been observed on
   `ETA_PRIOR_UNITS` units. After each finished table it blends in the measured time:
   `speed = (default * prior_units + time_done) / (prior_units + units_done)`.
3. Remaining time = speed * (units of unfinished tables). Total = elapsed + remaining.
4. The estimate is sent at scan start, after every table, and every
   `ETA_TICK_SECONDS` (5). It is labelled `rough` until at least 3 tables or about
   20% of the work are done, then `measured`.
5. Tables of unknown size use the median of the known sizes and keep the estimate
   `rough`.
The log records the first estimate and the actual duration so the constants can be
tuned. A very slow network or a locked table can still make the estimate drift;
this is why the UI says "about" and shows `rough` at first.
 
---
 
## 10. UI
 
Same look as the audit tool: neutral light background, white surfaces, one blue
accent, badge pills, system fonts, monospace only for technical text. No CDN, no
external assets.
 
### Page flow
 
1. **Form -> Connect.** Connection fields and a Connect button. Errors appear
   under the form (section 13).
2. **Table picker.** After a successful Connect the form is locked (with a
   "Change connection" link) and the tables are listed.
3. **Scan, live.** The progress view and result area fill in while the scan runs.
4. **Result.** Headline, cards, Export PDF, Last scan panel.
### Table picker
 
- One row per table: checkbox, name, column count, primary key yes/no, foreign key
  count. Row counts are not shown (databases give only rough estimates).
- All ticked by default; **Select all**, **Select none**, a filter box, and a
  counter ("12 of 42 selected").
- **Scan selected tables** is disabled when nothing is ticked.
### Live scan view
 
- Top line: `Checking 12 of 42 · elapsed 0:45 · about 2 min remaining · estimated
  total 2 min 45 s (rough)`. The word `rough` disappears when the estimate is based on
  measured speed. **Stop scan** button.
- A compact **status list**, one row per ticked table:
  - queued (grey), **checking** (blue, with the current step and a seconds counter),
  - done: clean (green tick), faulted (red), suspected (amber), insufficient data
    (grey), error (red, with the code), not run (grey, after a stop).
  - Clicking a finished row scrolls to its card.
- The headline updates live:
  `12 of 42 tables scanned (30 not selected): 3 faulted, 1 suspected, 1 insufficient data, 7 clean`.
- A card appears in the result area **the moment its table finishes** (faulted and
  suspected only), in the order tables finish. Nothing is reordered afterwards.
- Reloading the page during a scan re-attaches through `/api/scan/current`.
- Stop is not an error: the result shows status `stopped`; ticked tables never
  reached are listed as `not run`.
### Faulted-table card (accepted mockup, to be refined later)
 
- Header: table name and a badge with the exact normal form.
- Body: **all columns**, primary key marked, foreign keys marked.
- Dependent columns tinted red; determinant columns bold.
- Arrows in a gutter beside the card from each determinant to its dependents;
  a double-headed arrow for equivalent columns.
- Evidence boxes beside the card, per FD:
  `Confidence Score: 96 (chance of coincidence about 1 in 10^48)`, then the counts,
  the exceptions, cross-table support if any, and real example values.
- Reason box and one-sentence recommendation box.
- Suspected tables use the same card, dimmed, with "possibly <form>".
- The cards are plain SVG generated by `app.js` (no graph library).
### PDF
 
Drawn by `export_pdf.py` with reportlab drawing primitives to match the card
layout, generated on request and returned directly. Nothing is written to disk.
 
---
 
## 11. Storage
 
SQLite file with one table:
 
```sql
CREATE TABLE IF NOT EXISTS last_scan (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  saved_at TEXT NOT NULL,
  result_json TEXT NOT NULL
);
```
 
Each completed or stopped scan replaces the row. No credentials, no history.
 
---
 
## 12. Phase 2
 
### EOL check (`eol.py`)
 
- Bundled `data/eol_versions.json` maps engine and major.minor to an EOL date.
- Result states `supported`, `EOL`, or `unknown version`, and shows the table's
  "as of" date because the file goes stale and must be updated by hand.
### Possibly unused tables (`unused.py`)
 
Heuristic signals, never a fact:
- zero rows;
- no foreign keys in or out;
- usage statistics where the engine keeps them (PostgreSQL `pg_stat_user_tables`,
  Oracle `ALL_TAB_MODIFICATIONS`, MySQL weaker, via `information_schema` update
  time where available). These reset on server restart.
Labelled "possibly unused" with the signals listed. Works on the table list, so it
is independent of which tables were ticked for the normal-form scan.
 
---
 
## 13. Logging and error handling
 
Rule: **nothing fails silently.** Every failure is logged with enough context to
find the cause, and every failure that matters to the reader is shown in the UI
in plain words.
 
### Logging (`logging_setup.py`)
 
- Python `logging`, configured once at startup. Two handlers: console and a
  rotating file `backend/logs/dbnorm.log` (`LOG_MAX_BYTES` 5 MB, `LOG_BACKUP_COUNT` 5).
- One line per event: `time | level | scan_id | step | table | message`.
- Every scan step logs start, outcome and duration:
| Step | Logged |
|---|---|
| `connect` | engine, host, port, database/service, SSL on/off, result, duration |
| `readonly` | which read-only mechanism was applied, or why it failed |
| `version` | server version string |
| `list_tables` | number of tables listed at Connect |
| `metadata` | number of tables, columns, keys, foreign keys found |
| `count` (per table) | bounded row count, or why it failed |
| `estimate` | first estimate, each update's basis, final actual vs first estimate |
| `load` (per table) | row count, sampled yes/no, excluded columns, duration |
| `fd_discovery` (per table) | candidate FDs found, pruned, near-FDs, candidates tested `m` |
| `fd_scoring` (per table) | FDs kept per tier |
| `cross_table` (per table) | pairs compared, match rate, or why skipped |
| `classify` (per table) | candidate keys, exact normal form, status |
| `save` | last-scan stored or failed |
| `export` | PDF generated, size, duration or failure |
| `scan` | final status, counts, total duration |
 
- Levels: `INFO` normal steps, `WARNING` degraded results (sampled table, excluded
  column, failed count, per-table failure), `ERROR` a step failed, `EXCEPTION`
  unexpected failure with a stack trace.
- **Never logged:** passwords, full connection URLs, row values. Logs hold
  names, counts and error codes only. A log filter masks any string matching the
  password as a last line of defence.
- No bare `except:` and no swallowed exceptions anywhere: every `except` block
  either logs and re-raises, or logs and records a per-table error.
### Error model (`errors.py`)
 
All failures become one structured error with a stable code:
 
```json
{"error": {"code": "DB_AUTH_FAILED",
           "message": "Database connection not established: the username or password was rejected.",
           "hint": "Check the credentials and that this user may connect from vm1.",
           "step": "connect", "scan_id": "uuid"}}
```
 
- `message` is plain language and says what happened. `hint` says what to check.
- Raw driver text and stack traces go to the log, tagged with the same `code` and
  `scan_id`, so the cause can be found from what the UI shows. They are not sent
  to the browser.
- HTTP status: 400 validation, 409 scan already running, 404 unknown scan,
  502 target database problems, 500 unexpected.
| Code | Shown message (summary) | Typical cause |
|---|---|---|
| `VALIDATION_ERROR` | Names the missing or invalid field (empty host, port not 1-65535, no table ticked) | Form input |
| `DB_DRIVER_MISSING` | Database driver is not installed in this environment | Wheel missing offline |
| `DB_UNREACHABLE` | Database connection not established: host unreachable or port closed | Wrong IP/port, firewall, DB not listening |
| `DB_TIMEOUT` | Database connection not established: connection timed out | Network or firewall dropping packets |
| `DB_AUTH_FAILED` | Database connection not established: username or password rejected | Wrong credentials, host not allowed |
| `DB_NOT_FOUND` | Connected to the server, but the database / service name was not found | Wrong database, Oracle service vs SID |
| `DB_SSL_FAILED` | SSL connection could not be established | Server does not offer SSL, or SSL required |
| `DB_READONLY_FAILED` | Could not put the session in read-only mode; scan not started | Engine or permission issue. The tool refuses to scan without it |
| `NO_TABLES` | Connected, but no tables were found in this database / schema | Wrong schema, no privileges |
| `TABLE_PERMISSION_DENIED` | Table skipped: this user cannot read it | Missing `SELECT` (per table, scan continues) |
| `TABLE_TIMEOUT` | Table skipped: query exceeded the time limit | Very large or locked table (per table) |
| `TABLE_NOT_FOUND` | Table skipped: it no longer exists in the database | Dropped or renamed between Connect and Scan (per table) |
| `TABLE_ANALYSIS_FAILED` | Table skipped: analysis failed unexpectedly | Bug or odd data (per table) |
| `SCAN_ALREADY_RUNNING` | A scan is already in progress | Second start request |
| `SCAN_NOT_FOUND` | No such scan | Old link or restart |
| `STORAGE_FAILED` | Result shown, but could not be saved as the last scan | Disk or permission on the SQLite file |
| `EXPORT_FAILED` | PDF could not be generated | Rendering error |
| `BACKEND_UNREACHABLE` | (UI only) Cannot reach the tool's backend | Backend stopped |
| `PROGRESS_STREAM_LOST` | (UI only) Live progress connection was lost; the scan may still be running | Network hiccup, backend restart |
| `INTERNAL_ERROR` | Unexpected error; reference the scan id and see the log | Bug |
 
Driver exceptions are mapped to these codes in one place (`errors.py`), using the
engine's error numbers (for example MySQL 2003/1045/1049, PostgreSQL SQLSTATE
28P01/3D000, Oracle `ORA-01017`/`ORA-12514`/`DPY-` codes) with a safe fallback
to `INTERNAL_ERROR`.
 
### Failure scope
 
- **Whole-scan failures** (connect, auth, read-only setup, no tables, metadata
  read) stop the scan with status `failed` and a single error. The same errors
  at the Connect step appear under the form and no table list is shown.
- **Per-table failures** never stop the scan: the table gets status `error`
  with its code, the scan continues, and the result is marked partial.
- A table error never turns into a "clean" verdict. Unanalysed tables are listed.
- A failed bounded count is not an error: it only makes the estimate rough.
### UI behaviour
 
- The progress line shows the current step (`Connecting`, `Reading schema`,
  `Checking table 12 of 42: orders`, `Saving`).
- On failure the progress area becomes an error panel: the message, the hint,
  the failed step, the error code and the scan id. Example: **"Database
  connection not established: host unreachable or port closed."**
- Field-level validation errors appear next to the field before any request is made.
- Per-table errors appear on the table's status row at once and in a "Tables that
  could not be analysed" notice under the cards, with the code for each.
- A global wrapper around every `fetch`, the progress stream (including a dropped
  connection), and `window.onerror` / unhandled promise rejections shows an error
  banner instead of failing silently.
---
 
## 14. Security and robustness
 
- Bind to `127.0.0.1`; no CORS.
- Passwords held in memory per scan, never logged, never stored. The browser form
  sends them again for the scan step; the Connect step keeps nothing.
- Logs contain table and column names and counts, never row values or credentials.
- Statement guard keeps the connection read-only.
- Timeouts per query and per table; per-table error isolation.
- Evidence example values appear in the live events (localhost only), the stored
  last result, and the PDF, as requested.
---
 
## 15. Decisions log
 
| Decision | Choice |
|---|---|
| Web framework | FastAPI + uvicorn (user preference) |
| Frontend | Plain HTML/CSS/JS, SVG cards, no libraries |
| DB layer | SQLAlchemy `inspect()` |
| Drivers | PyMySQL (+`cryptography`), pg8000, python-oracledb thin |
| PDF | reportlab |
| Storage | SQLite, last scan only |
| Credentials | Not saved |
| Flag threshold | Below 3NF (3NF-but-not-BCNF not flagged) |
| Normal-form scope | Up to BCNF |
| Low confidence | Shown dimmed as "suspected" |
| Evidence | Real example values shown |
| Recommendation | One simple statement |
| Headline | One-line counts, no table |
| Logging | Rotating log file plus console, one line per step, no secrets or values |
| Errors | Structured error with stable code, plain message and hint; raw detail only in the log |
| Failure scope | Whole-scan failures stop the scan; per-table failures are recorded and the scan continues |
| Table sizes | 10-15 columns, 100 to several thousand rows (corrected 2026-10-02) |
| Coincidence measure (D1) | Closed-form probability, not shuffle simulation (simulation only in tests) |
| Many-guesses correction (D2) | Multiply by the number of candidates `m` |
| Cross-table evidence (D3) | Declared foreign keys, 97% pair match; may promote to strong |
| Tiers (D4) | Materiality gate plus score tiers; score derived from the coincidence exponent |
| Near-FDs (D5) | Dirty only in groups of 3+ rows with a clear majority; probability on cleaned data times `C(N,k)`; score cap 90 |
| Determinant size (D6) | 2 by default, 3 from 1 000 rows, key parts always tested |
| Card score | Show both: `Confidence Score: 96 (chance of coincidence about 1 in 10^48)` |
| Naming affinity | Evidence line only, not in the score |
| FD search | TANE-style in production; brute force as a test reference only |
| Table selection | Connect lists tables; only ticked tables are scanned |
| Between Connect and Scan | Connection closed; credentials re-sent from the form; nothing kept on the server |
| Live view | Per-table status, results appear as tables finish, time estimate with `rough`/`measured` label |
 
## 16. Open items
 
- **Inferred keys** for tables with no declared primary key (next topic): how they
  affect prime attributes and the wording on the card.
- **Inferred foreign keys** (no constraint, same name, value inclusion) for the
  cross-table evidence.
- 1NF heuristics (delimited values, repeating groups) and false-positive control.
- Large tables above `MAX_ROWS_PER_TABLE`: simple first-N sampling for now.
- Whether `numpy` is needed: decided by the benchmark in BUILD_ORDER step 8.
- All thresholds, `ETA_*` constants and the score formula: tune against test
  databases and the synthetic generator (`TEST_PLAN.md`).
- Card layout refinements and the PDF layout (user to discuss after the first
  working version).
- Real example values in stored result and PDF: privacy handling to confirm.
- Whether to let the reader mark an FD as "not real" and re-evaluate: deferred.
- Phase 2: unused-table signals per engine and the EOL data table contents.
- Oracle: tested on the free edition in Docker only; older versions unverified.