# Build Order
 
The exact sequence to build in. Each step has a **checkpoint**: do not move on
until it passes. Dependencies between steps are shown, so work that can run in
parallel is clear.
 
Legend: `->` means "requires".
 
**Decisions that must be settled before step 7** (they shape the key and
normal-form rules): inferred keys for tables without a primary key, and the 1NF
heuristics. Steps 0 to 6 do not depend on them.
 
---
 
## Step 0. Repository and environment
 
- Create the layout from `README.md`.
- `python -m venv venv`; `requirements.txt` (runtime) and `requirements-dev.txt`
  (pytest, httpx, ruff or black; **never shipped offline**).
- `config.py` with every tunable from the README table, including the log and
  stream settings.
- `logging_setup.py`: rotating file and console handlers, secret-masking filter,
  a helper that logs a step's start, outcome and duration.
- `errors.py`: error codes, plain messages and hints (catalogue in `DESIGN.md`
  section 13), and the structured error object.
Checkpoint: `pytest` runs; `python -c "import fastapi, sqlalchemy"` works; a test
shows the log filter masks a password and an error object carries code, message and hint.
 
## Step 1. Test databases  (-> 0)
 
- Local MySQL 8.0.41 and PostgreSQL 17.4 running.
- Write `scripts/testdb/mysql_planted.sql` and `postgres_planted.sql` with the
  planted defects and decoys from `TEST_PLAN.md`: 10-15 columns, mostly 100 to a
  few thousand rows, one table above `MAX_ROWS_PER_TABLE`.
- `generate_synthetic.py`: plants FDs at varied row counts, cardinalities and noise.
- Keep an `expected_verdicts.json` listing each table and its expected status and
  normal form.
Checkpoint: both databases loaded; `expected_verdicts.json` covers every table.
 
## Step 2. Connection layer  (-> 0, 1)
 
- `db_connect.py`: URL builder per engine, SSL option (verification off), read-only
  session setup, server version string.
- Statement guard: reject anything that is not `SELECT` or a dictionary query.
- Test: guard rejects `INSERT`, `UPDATE`, `DELETE`, `DROP`, `CREATE`, `ALTER`.
- Map driver exceptions to error codes (unreachable, timeout, auth failed,
  database not found, SSL failed, driver missing) and log each connect step.
- Tests: wrong host, closed port, wrong password and wrong database name each give
  the right code and message.
Checkpoint: connects to both local DBs, prints the version, guard test passes,
and each connection failure shows "Database connection not established" with its reason.
 
## Step 3. Metadata and table list  (-> 2)
 
- `metadata.py` using `sqlalchemy.inspect()`: tables, columns and types, primary
  keys, unique constraints, foreign keys, indexes.
- The lightweight **table list** used by Connect: name, column count, has primary
  key, foreign key count. Logged as step `list_tables`.
- Exclude views and system schemas.
Checkpoint: a CLI prints the table list and full structure of both test DBs;
matches the SQL scripts.
 
## Step 4. Row loader  (-> 3)
 
- `loader.py`: **bounded row count** per table (`SELECT COUNT(*)` over a subquery
  limited to `MAX_ROWS_PER_TABLE + 1`), load a table into column lists, integer
  coding, NULL policy, exclude LOB/binary/JSON from FD search, row cap and
  `sampled` flag, per-table timeout.
- A failed count is a `WARNING`, not an error.
Checkpoint: counts and row counts match; a capped table is marked sampled.
 
## Step 5. FD discovery  (-> 0; no database needed)
 
- `fd_discovery.py`: stripped partitions, level-wise search, minimality, cheap
  prefilter, key and constant pruning, determinant size rule (`MAX_LHS_SIZE`,
  `MAX_LHS_SIZE_LARGE` from `LARGE_TABLE_ROWS`), key parts always tested, near-FD
  candidates, equivalent columns.
- In the tests: a plain **brute-force reference** (every determinant, every
  dependent, then minimise).
- Unit tests on small hand-made tables with known FDs, including dirty rows.
Checkpoint: all hand-made cases give exactly the expected minimal FDs, and the
TANE-style result equals brute force on every test table.
 
## Step 6. FD scoring and cross-table evidence  (-> 5; cross-table also -> 4)
 
- `fd_scoring.py`: `distinct_det`, `repeated_rows` (materiality gate),
  closed-form coincidence probability in logarithms, correction for the number of
  candidates `m`, near-FD handling (cleaned data and the `C(N,k)` factor), score
  from the exponent, caps, tier.
- `cross_table.py`: pairs of a declared foreign key and a copied column compared
  with the referenced table (reads the 2 columns it needs).
- Unit tests: coincidence-prone small tables score low; true dependencies score
  high; the closed form agrees with a seeded simulation on small tables;
  deterministic output.
Checkpoint: score ordering matches expectations on the unit-test tables and the
closed form matches the simulation within the agreed tolerance.
 
## Step 7. Keys and normal forms  (-> 3, 5, 6; open design items settled)
 
- `keys.py`: declared keys (certain), inferred keys (flagged), prime attributes.
- `normal_forms.py`: 1NF checks (no key, delimited values, repeating groups),
  2NF, 3NF, BCNF, exact form and table status.
- `recommend.py`: one-sentence templates.
- Unit tests for every rule, including the 3NF-but-not-BCNF case (must be `clean`),
  the 2NF-vs-3NF split, and equivalent columns.
Checkpoint: rule tests pass on in-memory tables.
 
## Step 8. Analyzer and estimator  (-> 4, 6, 7)
 
- `analyzer.py`: for each selected table, load rows, discover and score FDs,
  classify, build the result JSON, count statuses, isolate per-table errors, emit
  progress events (`table_started`, `table_step`, `table_done`, `table_error`).
- `estimator.py`: work units per table, speed blending, total and remaining time,
  `rough` / `measured` label; the first estimate and the actual time are logged.
- Log every step per table (count, load, discovery, scoring, classification) with
  counts and duration; a per-table failure is recorded as status `error` with its
  code and never becomes `clean`; a stop leaves the rest as `not_run`.
- CLI: `python -m app.analyzer --url ... --tables a,b > result.json`.
Checkpoint (**M1**): run on both test DBs; compare with `expected_verdicts.json`.
Record precision and recall. Tune thresholds and the score formula; rerun until
the acceptance criteria in `BUILD_PLAN.md` hold. **Benchmark** typical tables and
a 50 000-row table; decide whether `numpy` is needed. Compare the estimate with
the actual time and set the `ETA_*` constants. Write the tuned values into
`config.py` and `DESIGN.md`.
 
## Step 9. Storage and API  (-> 8)
 
- `storage.py` (single-row SQLite), `scan_sessions.py` (state, SSE, stop),
  `main.py` routes (`/api/connect`, `/api/scan/start` with `tables`, progress,
  results, stop, current, last), `schemas.py`.
- Structured error responses (code, message, hint, step, scan id) without
  passwords or raw exceptions; a global exception handler; a wrapper around the
  background scan thread so any crash ends the scan as `failed` and is logged.
- Progress events carry the current step, the estimate, per-table results as
  tables finish, and on failure the error object; heartbeat every 15 s.
Checkpoint: connect, start with a chosen subset, progress stream, stop, results,
current and last scan all work through the API tests; last scan survives a
restart; a forced crash inside a scan is logged and reported as a failed scan.
 
## Step 10. Frontend shell  (-> 9)
 
- `index.html`, `styles.css` (reuse tokens and patterns from the audit tool),
  `app.js`: form with per-engine fields, validation, Connect, **table picker**
  (checkboxes, select all/none, filter, counter, disabled button when nothing is
  ticked), **live status list** (queued, checking with step, verdict), time line
  (elapsed, remaining, total, `rough` label), Stop, Last scan panel, live headline.
- Error handling: field validation, an error panel (message, hint, step, code,
  scan id), a wrapper for every `fetch`, handling of a dropped progress stream
  (banner, resync from partial results, reconnect), `window.onerror` and
  unhandled promise rejections, re-attach on reload through `/api/scan/current`,
  and the "tables that could not be analysed" notice.
Checkpoint: connect, pick tables, scan from the browser; statuses change as tables
are checked and the headline updates live; an unreachable database, a wrong
password, a dropped stream and a stopped backend each show a clear message.
 
## Step 11. Card renderer  (-> 10)
 
- SVG cards in `app.js`: header with normal-form badge, all columns, key markers,
  tinted dependents, arrows, evidence boxes (score line with the coincidence
  figure), reason box, recommendation box.
- Cards are appended the moment their table finishes.
- Dimmed variant for suspected tables.
- Test with a result file that contains every violation kind.
Checkpoint (**part of M2**): output matches the accepted mockup for each kind.
 
## Step 12. PDF export  (-> 8, 11)
 
- `export_pdf.py` with reportlab drawing the same cards, headline, scan details;
  endpoint returns bytes directly.
Checkpoint (**M2**): PDF reads correctly for a result with all violation kinds;
no file remains on disk.
 
## Step 13. Oracle  (-> 8)
 
- Start Oracle Free in Docker:
  `docker run -d --name oracle-test -p 1521:1521 -e ORACLE_PASSWORD=<pwd> gvenzl/oracle-free:slim`
- Create the test user, then `oracle_planted.sql` and add rows to
  `expected_verdicts.json`.
- Add `oracle+oracledb` to `db_connect.py`, Oracle metadata handling (owner/schema),
  the table list, and the Oracle form fields.
Checkpoint: same planted schema gives the same verdicts on all three engines.
 
## Step 14. Offline packaging dry run  (-> 12, 13)
 
- `build_wheels.ps1`: `pip download` with `--only-binary=:all:`, `win_amd64`,
  Python 3.12, `cp312`.
- Fresh venv, `pip install --no-index --find-links wheels -r requirements.txt`.
- Run the tool and a scan from that venv.
- `build_iso.ps1`: `oscdimg -m -j1` (the `-j1` flag matters).
Checkpoint (**M3**): the clean-venv install works and a scan succeeds with the
network disabled.
 
## Step 15. EOL check  (-> 8, phase 2)
 
- `eol.py`, `data/eol_versions.json` (with an "as of" date).
- Add to the result model and a small notice on the UI and PDF.
Checkpoint: supported, EOL and unknown versions classify correctly.
 
## Step 16. Unused tables  (-> 8, phase 2)
 
- `unused.py`: zero rows, no foreign keys, usage statistics where available.
- Planted unused tables in the test databases; label "possibly unused" with signals.
Checkpoint: planted unused tables flagged, used ones not.
 
## Step 17. Hardening and docs  (-> all)
 
- Review the full log output and error catalogue against real failures; check that
  no log line contains a secret or a row value; timeouts and UI edge cases
  (invalid port, wrong password, unreachable host, dropped stream).
- Run the false-positive stress test on public sample databases (see `TEST_PLAN.md`).
- Update README, DESIGN, TEST_PLAN, OFFLINE_PACKAGING with tuned values and
  verified behavior; write the short operator guide for vm1.
Checkpoint (**M4**): full test suite green; fresh offline install verified again.
 
---
 
## Dependency overview
 
```
0 -> 1 -> 2 -> 3 -> 4 ----+
0 -> 5 -> 6 -------------+--> 8 -> 9 -> 10 -> 11 -> 12 -> 14
        3,5,6 -> 7 ------+     |                        ^
        (6 also needs 4)       +--> 13 ----------------+
                               +--> 15, 16 (phase 2)
```
 
Steps 1 to 4 (data side) and 5 to 7 (pure logic) can proceed in parallel and meet
at step 8.
 
## Rules while building
 
- Do not start the UI (step 10) before the analyzer passes the planted-defect checks.
- Add every new dependency to `requirements.txt` immediately and re-run the
  `--no-index` dry run when it changes.
- Every step adds its own log lines and error codes as it is built, with a test for
  its main failure cases. A step is not done if a failure inside it can pass silently.
- Never commit credentials or real data; test data is synthetic.
- Every bug found on a real target becomes a new planted-defect or decoy table.
 