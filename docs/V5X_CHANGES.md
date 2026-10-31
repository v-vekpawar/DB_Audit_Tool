# v5.x changes

## Normal-form boundary reporting

Table results now separate:

- `current_normal_form`: highest normal form established by the evidence.
- `violated_normal_form`: first formal normal-form boundary that fails, when one exists.
- `status`: whether the result is faulted, suspected, BCNF-only faulted/suspected, incomplete, or clean.

Examples:

- partial dependency: current `1NF`, violated `2NF`;
- transitive dependency: current `2NF`, violated `3NF`;
- BCNF-only defect: current `3NF`, violated `BCNF`;
- no formal violation but incomplete FD search: current `3NF`, no violated form, status `incomplete`;
- confirmed clean: current `BCNF`, no violated form, status `clean`.

A missing candidate key is an analysis limitation, not a 1NF violation.

## FD evidence tiers

Exact and near FDs with insufficient evidence are now assigned `discarded` for the main report. They remain counted in `fd_summary` but do not:

- affect normal-form classification;
- appear as individual FD cards in the main report.

Material suspected FDs require both a minimum repetition level and minimum confidence. Strong FDs continue to require the stronger evidence thresholds.

The result includes:

```json
"fd_summary": {
  "total_discovered": 100,
  "strong": 2,
  "suspected": 3,
  "near": 1,
  "weak_suppressed": 95
}
```

## Incomplete FD search

An incomplete bounded search is no longer presented as `possibly BCNF` or as a table below 3NF. If no formal 2NF/3NF/BCNF violation was established, the table is reported as `3NF` with status `incomplete` and an explicit limitation that BCNF was not confirmed.

## Recommendations

Transitive recommendations consolidate dependency chains and equivalent/related determinants into one remediation group. For example:

`carrier_id -> carrier_name` and `carrier_id -> carrier_mode`, together with `carrier_name -> carrier_mode`, produce one Carrier relation keyed by `carrier_id` rather than two redundant lookup tables.

## Compatibility

The legacy `normal_form` field is retained and mirrors `current_normal_form`. Consumers that need the exact failed boundary should use `violated_normal_form`.

## v5.x.1 reporting refinements

- Dependency graphs now label 2NF partial-dependency arcs as `partial dependency`, matching the existing `transitive` label for 3NF.
- Partial-dependency graph arrows show the composite key -> proper subset determinant -> non-prime dependent relationship, with a dashed arc labelled `partial dependency`.
- Candidate-key provenance is surfaced as `schema-declared`, `data-inferred (full-table verified)`, `data-inferred (sample)`, or `data-inferred` in the report/UI.
- Suggested decomposition sketches are generated from all material violations of the failed boundary, not only the first/worst violation, so multiple determinant groups are represented.
- Recommendation sketches keep all dependents named by the recommendation text, preventing text/diagram drift.

## v5.x.2 reporting/graph refinement

- PDF FD evidence is now presentation-limited to material violation-linked rules: up to 2 for partial/BCNF findings and up to 3 for transitive findings, because a transitive explanation may require three relevant rules. The underlying FD set and result-card FD list are unchanged.
- PDF evidence tables are explicitly labelled `Functional Dependency 1`, `Functional Dependency 2`, etc.
- Dependency graphs no longer invent an `emp_id → determinant`/`ship_no → carrier_id` edge when that FD is absent from explicit FD evidence. Where an actual two-FD chain exists, the graph uses that chain. Otherwise the transitive graph shows the actual violating FD directly rather than fabricating a key edge.
- Added regression tests for PDF FD selection and graph evidence grounding.

## v5.x.3 transitive graph/PDF fix

- Transitive dependency graphs now use candidate-key closure when the key -> determinant edge is not present in the discovered-FD list. The edge is labeled `key-implied` so it is not presented as an independently discovered FD.
- Explicit discovered chains remain preferred, including the existing `X -> Y -> Z` case used by `t_nokey_inferred`.
- The compact transitive fallback is hardened so missing labels cannot reach ReportLab as `None` text.
- Added regression coverage for the key-implied transitive graph and `t_3nf_multi` PDF generation.


## v5.x.8 planned/current implementation additions

### 1NF evidence improvement
- 1NF detection now inspects observed values in addition to schema/name heuristics.
- JSON-array-like observed values are reported separately from delimiter heuristics.
- Recurring delimiter tokens are marked `STRONG_HEURISTIC`; delimiter-only patterns remain `SUSPECTED`.
- Numbered repeating-column patterns remain explicitly heuristic.
- Heuristic 1NF findings never claim semantic atomicity as mathematically proven; they remain an evidence-aware boundary.

### MSSQL support
- Added Microsoft SQL Server (`mssql`) support through SQLAlchemy + pyodbc.
- Default port: 1433.
- Default ODBC driver: ODBC Driver 18 for SQL Server; UI permits overriding the installed driver name.
- Database picker connects through `master` when no database is selected, then lists online user databases.
- MSSQL uses SQLAlchemy identifier quoting (`[name]`) for unusual identifiers.
- `ApplicationIntent=ReadOnly` is included in the connection string; the application's SQL statement guard still permits only SELECT-family statements.
- MSSQL schema selection is supported; when omitted, the login's default schema is used.
- The pyodbc Python package is required, plus a Microsoft SQL Server ODBC driver installed on the machine.
