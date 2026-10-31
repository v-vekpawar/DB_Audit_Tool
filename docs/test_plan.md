
# Test Plan
 
Accuracy is the main requirement for functional dependency (FD) detection, and it
can only be shown by testing against databases whose defects we planted
ourselves. This plan defines those databases, the measurements, and the setup.
 
---
 
## 1. Test environments
 
| Engine | Version | How |
|---|---|---|
| MySQL | 8.0.41 | Local server (MySQL Workbench is the client) |
| PostgreSQL | 17.4 | Local server (pgAdmin4 is the client) |
| Oracle | Free edition | Docker: `gvenzl/oracle-free` |
 
Oracle in Docker:
 
```powershell
docker run -d --name oracle-test -p 1521:1521 -e ORACLE_PASSWORD=<pwd> gvenzl/oracle-free:slim
docker logs -f oracle-test        # wait for "DATABASE IS READY TO USE!"
```
 
Connection: `localhost:1521`, service `FREEPDB1`. Create a test user (do not use
`sys`):
 
```sql
ALTER SESSION SET CONTAINER = FREEPDB1;
CREATE USER tester IDENTIFIED BY "<pwd>";
GRANT CREATE SESSION, CREATE TABLE, UNLIMITED TABLESPACE TO tester;
```
 
Tool-side accounts on real targets only need `SELECT` on the tables and access
to the catalog views; the tool never writes.
 
---
 
## 2. Layers of testing
 
| Layer | What | Needs DB |
|---|---|---|
| Unit: FD discovery | Hand-made in-memory tables with known minimal FDs, incl. dirty rows; TANE-style result must equal the brute-force reference | No |
| Unit: scoring | Closed-form probability vs a seeded simulation; candidate correction; near-FD handling; coincidence-prone vs genuine dependencies | No |
| Unit: keys and normal forms | Every rule, incl. 3NF-but-not-BCNF, 2NF-vs-3NF, equivalent columns | No |
| Unit: estimator | Work units, speed blending, `rough`/`measured` switch, unknown table sizes | No |
| Unit: read-only guard | Rejects any non-SELECT statement | No |
| Integration: planted DBs | Full analysis against `expected_verdicts.json` | Yes |
| Integration: synthetic generator | Planted FDs at varied rows, cardinalities, noise: precision/recall curves | Yes |
| Integration: public sample DBs | False-positive stress test on well-normalized databases | Yes |
| API | Connect, start with chosen tables, progress, stop, current, results, last, export | Yes |
| Frontend | Table picker, live status list, card rendering for each violation kind from a fixture result | No |
| Packaging | Clean venv install with `--no-index`, then a scan | Yes |
 
---
 
## 3. Planted-defect database
 
Same logical schema, written once per engine (engine-specific types and syntax).
Tables have 10-15 columns. Sizes are mixed on purpose: most tables 100 to 300 rows
(the hard case), several tables of 1 000 to 5 000 rows (size-3 determinants,
speed), and one table above `MAX_ROWS_PER_TABLE` (sampling). All data is synthetic.
 
### Planted defects (must be found)
 
| Table | Planted defect | Expected status | Expected normal form |
|---|---|---|---|
| `t_1nf_nokey` | No declared/inferred key; 1NF itself is not defined by key existence | suspected/incomplete | 1NF |
| `t_1nf_delimited` | `tags` column holds comma-separated lists | faulted | below 1NF |
| `t_1nf_repeating` | `phone1`, `phone2`, `phone3` | faulted | below 1NF |
| `t_2nf_partial` | `order_items(order_id, product_id, qty, product_name)`; `product_name` depends on `product_id` only | faulted | 1NF |
| `t_partial_key3` | Three-column key; a non-key column depends on two of the three | faulted | 1NF |
| `t_3nf_transitive` | `orders` with `customer_id -> customer_name, customer_email` | faulted | 2NF |
| `t_3nf_multi` | Two separate transitive dependencies in one table | faulted | 2NF |
| `t_3nf_wide` | 1 000+ rows; a transitive dependency with a 3-column determinant | faulted | 2NF |
| `t_cross_copy` | Declared FK to a customers table and a copied `customer_name`; weak coincidence evidence alone, strong through the cross-table match | faulted | 2NF |
| `t_nokey_inferred` | No declared key, but data has a verified data-derived key and a transitive dependency | suspected, lower confidence | 2NF |
 
### Must stay clean
 
| Table | Why | Expected status |
|---|---|---|
| `t_clean_3nf` | Properly normalized orders/customers/products set (several tables) | clean |
| `t_bcnf_only` | In 3NF but not BCNF (classic course/teacher/student) | clean (exact form recorded as 3NF) |
| `t_lookup_codes` | `code -> description`, where `code` is the key | clean |
| `t_composite_ok` | Composite key, all non-key columns depend on the whole key | clean |
 
### Decoys (must not become false faulted)
 
| Table | Trap | Expected status |
|---|---|---|
| `d_tiny` | 5 rows where some column pairs happen to look like an FD | insufficient_data |
| `d_unique_everything` | Every value unique; many trivial FDs, zero redundancy | clean |
| `d_coincidence` | About 100 rows, random categorical columns; an FD that holds by chance | suspected at most, ideally clean |
| `d_many_columns` | 15 random categorical columns, 1 000 rows; thousands of candidates, so some hold by luck unless the candidate correction works | clean |
| `d_nulls` | Columns with many NULLs | clean, NULL handling visible in evidence |
| `d_near_fd` | A real dependency with 1 or 2 dirty rows in groups of 3+ | faulted or suspected, flagged "holds with exceptions", exceptions shown; exact supersets induced by the dirty rows must not create a hard violation |
| `d_dirty_pairs` | Groups of only 2 rows that disagree | not reported as an FD |
| `d_equiv_cols` | `country_code` and `country_name` in a lookup table | clean; equivalent columns shown once if reported |
| `d_wide_text` | Long text and LOB columns | clean, excluded columns noted |
| `d_name_trap` | Names suggest a dependency (`user_id`, `user_name`) that the data does not support | clean |
 
Phase 2 additions: `u_empty`, `u_orphan` (zero rows or no foreign keys, expected
"possibly unused"), and used tables that must not be flagged.
 
`expected_verdicts.json` holds, per table: `status`, `normal_form`, and for faulted
tables the expected determinant/dependents.
 
### Synthetic generator and public sample databases
 
- `generate_synthetic.py` plants FDs while varying row count (100 to 5 000),
  cardinality, number of columns (10 to 15) and noise (0 to 3%). Output: recall and
  false-positive rate per setting, so thresholds are tuned on curves instead of
  seven pass/fail tables.
- Public well-normalized sample databases (for example Sakila/Pagila, Chinook,
  Northwind) are loaded on the build machine and scanned as a false-positive
  stress test: any faulted card on a table that is genuinely in 3NF is reviewed.
  They must be obtained and loaded where there is internet access; vm1 does not
  need them.
---
 
## 4. Measurements
 
Computed on every run of the planted-defect suite, per engine.
 
| Metric | Definition | Target |
|---|---|---|
| Recall | Planted defects reported with the correct violation kind / total planted | 100% |
| Precision | Correct faulted reports / all faulted reports | 100% on this suite (zero false faulted) |
| Normal-form accuracy | Tables with correct exact normal form / flagged tables | 100% |
| Decoy handling | Decoys ending as clean, insufficient_data, or suspected (never faulted) | 100% |
| Cross-engine parity | Same status and normal form for the same table on all engines | 100% |
| Search parity | TANE-style FD set equals the brute-force reference | 100% on every test table |
| Probability check | Closed-form coincidence vs seeded simulation on small tables | Within the agreed tolerance |
| Score separation | Lowest score of true FDs minus highest of decoy FDs | Positive, with a margin |
| Runtime | Full scan of about 50 tables, 10-15 columns, 100 to a few thousand rows | Recorded; target about a minute or less (to be measured) |
| Large table | One 50 000-row table | Recorded; target tens of seconds at most (to be measured) |
| Estimate accuracy | First estimate vs actual; estimate at 30% of the work vs actual | Within a factor of 2 at the start, about 30% at 30% done (targets) |
 
Failures are fixed by changing the algorithm or tuning thresholds, never by
special-casing a test table. Every real-target miss or false alarm found later
becomes a new planted-defect or decoy table.
 
---
 
## 5. Tuning procedure
 
1. Run the suite and the synthetic generator with the default thresholds and
   record the metrics.
2. Tabulate the score of true FDs against decoy FDs.
3. Adjust `STRONG_CONFIDENCE`, `STRONG_CONFIDENCE_CROSS_TABLE`,
   `SUSPECT_CONFIDENCE`, `MIN_REPEATED_ROWS`, `NEAR_FD_MAX_ERROR`,
   `NEAR_FD_MIN_GROUP`, `LARGE_TABLE_ROWS`, `MIN_ROWS_FOR_DATA_FDS`, and the
   `ETA_*` constants.
4. Re-run; keep a short log of each change and its effect.
5. Freeze the values in `config.py` and record them in `DESIGN.md`.
The random seed for the simulation used in tests is fixed so results are
reproducible. The production scoring is deterministic (closed form, no random
numbers).
 
---
 
## 6. Specific checks
 
- **Read-only**: the guard test sends every non-SELECT statement type and expects rejection;
  an integration test confirms the connection is read-only on all three engines.
- **Secrets**: a test scans the log output and API errors for the test password; it
  must never appear.
- **Table selection**: Connect returns the table list with column count, primary
  key flag and foreign key count and reads no rows; a scan of 3 ticked tables
  analyses exactly those 3; the others are counted as not selected and never as
  clean; zero ticked tables is rejected with `VALIDATION_ERROR`; a table dropped
  between Connect and Scan gives `TABLE_NOT_FOUND` for that table and the scan continues.
- **Cross-table read**: with a selected table pointing to an unselected one, only
  the needed columns of the unselected table are read, and it is not analysed or shown.
- **Live progress**: events arrive in order; each table goes queued, checking, then
  its verdict; a card for a faulted table is delivered with its `table_done`
  event; the headline counts match the final result.
- **Stream loss**: killing the stream mid-scan shows the loss banner, the UI
  resyncs from partial results, and the scan finishes on the server. Reloading the
  page during a scan re-attaches through `/api/scan/current`.
- **Time estimate**: the first estimate is sent at scan start and marked `rough`;
  it switches to `measured`; the final log line holds first estimate vs actual.
- **Large table**: a table above `MAX_ROWS_PER_TABLE` is marked `sampled` and
  confidence is capped.
- **Failure isolation**: revoke `SELECT` on one table; the scan finishes, that table
  is `error`, the rest are analysed.
- **Stop**: stopping mid-scan keeps partial results with status `stopped`; ticked
  tables not reached are `not_run`.
- **Connection errors**: each of these must show "Database connection not
  established" (or the matching message) with the right error code, a hint, and a
  log line carrying the same code and scan id, with no raw exception text in the UI.
  They apply to Connect and to Scan:
  | Scenario | Expected code |
  |---|---|
  | Wrong IP or host name | `DB_UNREACHABLE` |
  | Right host, closed port | `DB_UNREACHABLE` |
  | Port filtered (no reply) | `DB_TIMEOUT` |
  | Wrong password or user | `DB_AUTH_FAILED` |
  | Wrong database / Oracle service name | `DB_NOT_FOUND` |
  | SSL requested but server has none | `DB_SSL_FAILED` |
  | Driver package missing from the venv | `DB_DRIVER_MISSING` |
  | Empty host, port 0 or 70000, no table ticked | `VALIDATION_ERROR` (before any request where possible) |
  | Empty schema or no privileges | `NO_TABLES` |
  | Second scan started during a scan | `SCAN_ALREADY_RUNNING` |
  Run each on all three engines where the scenario applies.
- **No silent failures**: force an exception inside each step (connect, list
  tables, metadata, count, load, discovery, scoring, classification, save, export).
  Each must produce a log entry at `ERROR` or `EXCEPTION`, and a UI message or a
  table error, never a hang or a blank screen.
- **Scan thread crash**: raise inside the background scan; the scan ends as `failed`
  with an error object and the progress stream reports it.
- **Stopped backend**: the UI shows an error banner, not a frozen progress line.
- **Table error is never clean**: a table that cannot be read is listed as not
  analysed and is excluded from the "clean" count.
- **Log content**: after a full scan with a known password and known row values,
  the log file contains neither.
- **MySQL 8 auth**: connect with `caching_sha2_password` users.
- **PDF**: generated from a fixture with all violation kinds; nothing remains on disk.
- **Offline**: install in a clean venv with `--no-index`, disconnect the network,
  run a scan.
---
 
## 7. Real-target validation (after the suite passes)
 
On a few real target databases, manually review the faulted and suspected cards
against what the schema designers intended. Record disagreements, add matching
tables to the planted suite, and adjust. Oracle on older versions stays marked
unverified until tried on a real target.