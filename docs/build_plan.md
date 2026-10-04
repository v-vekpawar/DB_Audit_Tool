# Build Plan
 
What gets built, in which phases, how we know each phase is done, and what can
go wrong. The step-by-step order is in `BUILD_ORDER.md`; the design is in
`DESIGN.md`.
 
Sizes are relative effort: **S** small, **M** medium, **L** large.
 
---
 
## Guiding rules
 
1. **Accuracy first.** The analysis core is built and tested before any UI.
2. **Test early on planted defects.** Test databases exist before the analysis code.
3. **MySQL and PostgreSQL first, Oracle after.** Oracle is added once the core is proven.
4. **Offline from day one.** Every dependency is checked against the offline wheel
   flow early, not at the end.
5. **Read-only always.** A statement guard and a test enforce it from the first query.
6. **Pure functions for the core.** FD discovery, scoring, keys and normal forms
   take plain in-memory tables, so they are unit-testable without a database.
7. **Nothing fails silently.** Logging and the error model exist from phase 0, and
   every later phase logs its steps and maps its failures to coded errors shown
   in the UI. They are not left to the hardening phase.
8. **Check the fast version against the slow one.** The TANE-style search is
   verified against a plain brute-force search on every test table.
---
 
## Phases
 
### Phase 0: Foundation (S)
 
Deliverables
- Repository skeleton matching the layout in `README.md`.
- `requirements.txt` and `requirements-dev.txt`.
- venv, test runner, formatter.
- `config.py` with all tunables.
- `logging_setup.py` (rotating file, console, secret-masking filter) and
  `errors.py` (error codes, messages, hints).
Done when
- `pytest` runs (even with one placeholder test).
- Dependencies install from a local wheels folder with `--no-index` (dry run).
- A test proves the log filter masks a password and that an error object has
  code, message and hint.
### Phase 1: Test databases (M)
 
Deliverables
- `mysql_planted.sql`, `postgres_planted.sql` (same logical schema, engine-specific syntax).
- Tables of realistic size: 10-15 columns, mostly 100 to a few thousand rows, one
  table above the row cap.
- `generate_synthetic.py`: plants FDs at varied row counts, cardinalities and
  noise levels, for precision/recall curves.
- Local MySQL 8.0.41 and PostgreSQL 17.4 loaded with the schema.
- Catalog of planted defects and decoys (see `TEST_PLAN.md`).
Done when
- Every planted defect and decoy has a row in the catalog with its expected verdict.
- Both engines hold identical data.
### Phase 2: Connection, table list and metadata (M)
 
Deliverables
- `db_connect.py` (URL building, SSL, read-only sessions, version string).
- `metadata.py` (tables, columns, keys, foreign keys through `inspect()`), including
  the lightweight table list for Connect (name, column count, has PK, FK count).
- `loader.py` (bounded row count, row loading, NULL policy, exclusions, row cap).
- Statement guard plus its test.
- Driver-exception mapping to error codes (unreachable, timeout, auth, database
  not found, SSL, driver missing) and per-step logging for connect, version,
  list_tables, metadata, count and load.
Done when
- A CLI smoke script prints the table list, keys and bounded row counts for both test DBs.
- The guard test proves no non-`SELECT` statement is ever sent.
- Wrong host, closed port, wrong password and wrong database name each produce
  the right error code and a clear message, and a matching log line.
### Phase 3: Analysis core (L)
 
Deliverables
- `fd_discovery.py` (TANE-style, determinant size rule, key-part rule, prefilters)
  and the brute-force reference in the tests.
- `fd_scoring.py` (closed-form coincidence probability, correction for candidates
  tested, near-FDs, materiality gate, score and tiers), `cross_table.py`.
- `keys.py`, `normal_forms.py`, `recommend.py`.
- `estimator.py` (work per table, speed, total and remaining time).
- `analyzer.py` that runs the whole pipeline per selected table, emits progress
  events as each table starts and finishes, and produces the result JSON.
- First tuning pass of thresholds and the score formula; benchmark on 1 000,
  5 000 and 50 000 row tables.
Done when (acceptance, measured on the planted databases)
- TANE-style discovery equals the brute-force reference on every test table.
- The closed-form probability agrees with a seeded simulation on small tables.
- Every planted 1NF, 2NF and 3NF defect is reported with the correct exact normal form.
- Zero false **faulted** results on decoys; coincidence-prone decoys end up
  `suspected` or `insufficient_data`.
- MySQL and PostgreSQL give the same verdict for the same schema.
- Benchmark recorded: scan time for typical tables, and for a 50 000-row table
  (target: tens of seconds at most; otherwise apply the speed options in `DESIGN.md` 6.12).
- The time estimate is within a factor of 2 at the start and within about 30%
  once 30% of the work is done (targets, measured in the test runs).
### Phase 4: API and storage (M)
 
Deliverables
- FastAPI app, `POST /api/connect`, scan sessions, `tables` in the start body, SSE
  progress with per-table events and estimate events, stop, current scan,
  last-scan SQLite storage.
- Structured error responses (code, message, hint, step, scan id) that never leak
  passwords or raw exceptions; a global exception handler and a wrapper around the
  background scan thread so no failure goes unlogged.
Done when
- Connect returns the table list; a scan of a chosen subset can be started,
  watched table by table, stopped and re-read through the API with `curl`/tests.
- Restarting the backend keeps the last scan.
- A crash inside the scan thread still ends the scan as `failed` with an error and a log entry.
### Phase 5: Frontend (M)
 
Deliverables
- Form, Connect, table picker (checkboxes, select all/none, filter, counter).
- Live scan view: per-table status list (queued, checking with step, verdict),
  time line with elapsed, remaining and total estimate, Stop, live headline.
- SVG card renderer (faulted, and dimmed suspected) that appends each card as its
  table finishes, with evidence and reason boxes.
- Last scan panel; styles reused from the audit tool.
- Error panel, field validation, global fetch/stream/`window.onerror` handling,
  stream-loss resync, and the "tables that could not be analysed" notice.
Done when
- The accepted card mockup is reproduced from live results for all violation kinds.
- Works with no network access (no CDN, no external fonts).
- Stopping the backend mid-scan, a dropped progress stream, a failed connection
  and a table error each show a clear message in the UI.
- Reloading the page during a scan re-attaches to it.
### Phase 6: PDF export (M)
 
Deliverables
- `export_pdf.py` drawing the headline and cards with reportlab.
- Export button wired to the API.
Done when
- The PDF matches the on-screen cards for a result with all violation kinds.
- No file is left on disk after export.
### Phase 7: Oracle (M)
 
Deliverables
- `oracle+oracledb` support in `db_connect.py` and `metadata.py`.
- `oracle_planted.sql`; Oracle Free running in Docker (`gvenzl/oracle-free`).
- Oracle form fields (service name, schema).
Done when
- The same planted schema produces the same verdicts on Oracle as on MySQL and PostgreSQL.
- Oracle is documented as "tested on the Free edition; older versions unverified".
### Phase 8: Offline packaging dry run (S)
 
Deliverables
- `build_wheels.ps1`, `build_iso.ps1`, `requirements.txt` frozen for `win_amd64`/`cp312`.
- Clean venv install with `--no-index`, then the full test pass against the installed tool.
Done when
- A fresh venv built only from the wheels folder runs a scan end to end.
- The ISO is built with `oscdimg -m -j1` and the installed copy matches.
### Phase 9: EOL check and unused tables (M)  (built last)
 
Deliverables
- `eol.py` and `data/eol_versions.json` with an "as of" date.
- `unused.py` heuristics with listed signals.
- Result model and cards extended with a small notice section.
Done when
- Known supported and EOL versions are classified correctly on the test DBs.
- Planted unused tables are flagged as "possibly unused", used ones are not.
### Phase 10: Hardening and documentation (S)
 
Deliverables
- Review of the whole log and error catalogue against real failures, timeouts,
  per-table error isolation, and a check that no log line contains secrets or values.
- Final README, troubleshooting, tuning notes with the tuned values.
- Short operator guide for vm1.
Done when
- Failure modes in the risk table below behave as described.
---
 
## Milestones
 
| Milestone | After phase | What you can do |
|---|---|---|
| M1 | 3 | Run a scan from a CLI and get correct verdicts as JSON on MySQL and PostgreSQL |
| M2 | 6 | Full tool in the browser for MySQL and PostgreSQL: connect, pick tables, watch live, PDF export |
| M3 | 8 | Same, with Oracle, installable offline on vm1 |
| M4 | 10 | EOL and unused-table checks, hardened and documented |
 
---
 
## Risks
 
| Risk | Impact | Mitigation |
|---|---|---|
| FDs from about 100 rows are coincidental | False faulted tables | Closed-form coincidence probability corrected for candidates tested, materiality gate, dimmed tier, `insufficient_data` status |
| Dirty data hides real FDs | Missed violations | Near-FD detection (groups of 3+ rows, 3% limit) with exceptions shown as evidence |
| Score is not a probability that the FD is a real rule | Over-trust | Card and README say what it means; evidence shown; tuned on planted DBs |
| Tables with no declared keys | Inferred keys are guesses | Mark inferred keys, lower confidence, plain note on the card (open design item) |
| 1NF checks are heuristic | False positives on text columns | Require consistent delimiter patterns across many values; examples shown |
| 3-column non-key determinants on small tables are not searched | Rare missed 3NF violations | Searched from 1 000 rows; key parts always tested; stated in README |
| No declared foreign keys in the target | Cross-table evidence unavailable | Evidence is a bonus, not required; inferred foreign keys are an open item |
| Pure-Python speed on large tables | Slow scans | Prefilters, stripped partitions, row cap, benchmark in step 8, then lower caps, then optional `numpy` |
| Time estimate is off | Misleading UI | Labelled "rough" until measured; refreshed after every table; compared with actuals in logs |
| Live stream drops | Frozen-looking UI | Heartbeats, loss banner, resync from partial results, scan continues on the server |
| Oracle verified on one version only | Surprises on older targets | Use standard dictionary views only; document as unverified; test later on a real target |
| Offline wheel missing | Install fails on vm1 | Phase 8 dry run in a clean venv with `--no-index`; keep the wheel list in the repo |
| MySQL 8 auth needs `cryptography` | Cannot connect | Bundle it; test against MySQL 8.0.41 |
| Large tables | Memory and time | Row cap with first-N sampling, flagged `sampled`, confidence capped |
| Target DB overload | Impact on a submission | Read-only, one table at a time, bounded counts, query timeouts, scan only ticked tables |
| Credentials or values leak | Privacy | Never log secrets or values, never store credentials, bind to localhost |
| EOL table goes stale | Wrong EOL verdict | Show "as of" date; document the update procedure |
 
---
 
## Definition of done (whole project)
 
- All phase acceptance checks pass.
- Planted-defect test suite: all planted defects found, zero false faulted on decoys,
  identical verdicts on all three engines.
- Table selection, per-table live status and the time estimate work as specified.
- Installs and runs on a fresh Windows venv from the wheels folder with no network.
- Read-only guarantee covered by a test.
- README, DESIGN, TEST_PLAN and OFFLINE_PACKAGING reflect the tuned values and the
  final behavior.
---
 
## Not in this plan
 
4NF/5NF, DDL generation, scan history, saved credentials, multi-database scans,
automated migrations.
 