# Accuracy Policy

This is the final accuracy contract of the normalization analyzer.

## Evidence levels

- **GUARANTEED** — directly supported by database schema constraints.
- **DATA_OBSERVED** — verified against the analysed relation, but not guaranteed by schema/business rules.
- **SUSPECTED** — plausible from data/heuristics but insufficient for a definitive normalization claim.
- **UNKNOWN** — the required evidence was not available or the search was incomplete.

## Keys

1. Primary keys are declared candidate-key evidence.
2. UNIQUE constraints/indexes are candidate-key evidence only when participating columns are NOT NULL.
3. Data-inferred candidate keys may be single-column or composite.
4. Inferred keys are never treated as schema guarantees.
5. A sampled inferred key is promoted only after full-table uniqueness and NULL checks.
6. If key search is incomplete, 2NF/3NF/BCNF are not presented as definitive.

## FD discovery

1. Small schemas use exhaustive brute force with minimization as the reference implementation.
2. Larger schemas use a partition-based TANE-style level-wise search.
3. Small-schema TANE results are compared against brute force.
4. Determinant-size limits are explicit; incomplete search is never reported as exhaustive.
5. Exact candidates discovered on samples are validated against the full table where possible.
6. Near-FDs never count as formal FDs for normal-form proof.

## Normal forms

### 1NF

Delimited-cell and repeating-group checks are heuristics. They produce suspected findings, not mathematical proof. A missing key is **not** itself a 1NF violation.

### 2NF

A 2NF violation requires an exact FD `X -> A` where `X` is a proper subset of a **composite candidate key** and `A` is non-prime. A single-column key can never produce a partial dependency.

### 3NF

For every non-trivial exact FD `X -> A`, 3NF requires either `X` to be a superkey or `A` to be prime.

### BCNF

For every non-trivial exact FD `X -> A`, BCNF requires `X` to be a superkey.

## Large tables

Sampling is used for candidate discovery, not proof. Full-table validation is performed for exact candidates where possible. If discovery itself was bounded or sample-dependent, the result is explicitly marked incomplete/suspected.
