# FD Discovery Approach for the Database Normalization Tool

## 1. Purpose

This document describes **only the functional-dependency (FD) discovery
algorithms** used by the normalization tool.

The proposed implementation uses two complementary approaches:

1.  **Brute-force FD discovery with minimization**
2.  **TANE-style FD discovery**

The brute-force approach is intended to be the simple, transparent
implementation and a correctness/reference implementation. TANE is
intended to provide a more scalable partition-based discovery strategy.

The normalization engine can then consume the discovered FDs; this
document deliberately does not cover 1NF/2NF/3NF/BCNF decision logic.

------------------------------------------------------------------------

# 2. Problem Definition

Given a relational table:

``` text
R(A1, A2, ..., An)
```

the FD discovery component attempts to find dependencies of the form:

``` text
X → Y
```

where:

-   `X` is a set of one or more attributes.
-   `Y` is an attribute not contained in `X`.
-   The FD holds on the available relation if every pair of rows
    agreeing on all attributes in `X` also agrees on `Y`.

Example:

``` text
Student_ID → Student_Name
```

means:

> Whenever two rows have the same `Student_ID`, they must have the same
> `Student_Name`.

For composite determinants:

``` text
Student_ID, Course_ID → Marks
```

means:

> Rows having the same `Student_ID` and `Course_ID` must have the same
> `Marks`.

The discovery problem has two separate parts:

``` text
1. Search:
   Which attribute sets X should be considered?

2. Validation:
   Does X → Y actually hold?
```

The validation can be made efficient using partitions. The difficult
part is controlling the enormous search space of possible `X`.

------------------------------------------------------------------------

# 3. Proposed Algorithm Strategy

The tool should support two FD discovery modes.

``` text
                    FD DISCOVERY
                         |
             +-----------+-----------+
             |                       |
             v                       v
      Brute Force +             TANE-style
      Minimization               Discovery
             |                       |
       Simple/reference         Scalable
       implementation           implementation
```

The two algorithms should share the same low-level FD validation
machinery wherever practical.

A recommended high-level flow is:

``` text
Input table
    |
    v
Generate candidate attribute sets
    |
    +---------------------------+
    |                           |
    v                           v
Brute Force                 TANE
    |                           |
    v                           v
FD validation              Partition-based
    |                       validation
    v                           |
Minimal FD                    Pruning
set extraction                  |
    |                           v
    +------------+--------------+
                 |
                 v
          Discovered FDs
```

------------------------------------------------------------------------

# 4. Algorithm 1: Brute Force FD Discovery

## 4.1 Idea

The simplest possible discovery algorithm is:

> Generate possible determinant sets and test every possible FD.

For a table with attributes:

``` text
A, B, C
```

possible determinant sets include:

``` text
{A}
{B}
{C}
{A,B}
{A,C}
{B,C}
{A,B,C}
```

For each determinant `X`, test:

``` text
X → Y
```

for every possible `Y` not already in `X`.

For example:

``` text
A → B
A → C

B → A
B → C

C → A
C → B

AB → C
AC → B
BC → A
```

The algorithm does not need to test:

``` text
A → A
```

because the dependent is already part of the determinant.

------------------------------------------------------------------------

# 5. FD Validation in the Brute-Force Algorithm

The basic FD test is based on grouping rows by the determinant.

For:

``` text
X → Y
```

group rows by `X`.

The FD holds if every group contains only one distinct value of `Y`.

Example:

``` text
Student Course Marks

1       C1     80
1       C2     90
2       C1     70
2       C2     85
```

Test:

``` text
Student → Marks
```

Groups by `Student`:

``` text
Student 1:
    80
    90

Student 2:
    70
    85
```

There are multiple Marks values inside each group, so:

``` text
Student ↛ Marks
```

Now test:

``` text
(Student, Course) → Marks
```

Groups:

``` text
(1,C1) → 80
(1,C2) → 90
(2,C1) → 70
(2,C2) → 85
```

Each group has exactly one Marks value.

Therefore:

``` text
(Student, Course) → Marks
```

holds.

------------------------------------------------------------------------

# 6. SQL-Based Brute-Force Validation

A straightforward implementation can validate:

``` text
X → Y
```

using:

``` sql
SELECT X
FROM table
GROUP BY X
HAVING COUNT(DISTINCT Y) > 1;
```

Interpretation:

``` text
Result contains rows
    → FD fails

Result is empty
    → FD holds on the current data
```

For a composite determinant:

``` text
(A, B) → C
```

the query becomes conceptually:

``` sql
SELECT A, B
FROM table
GROUP BY A, B
HAVING COUNT(DISTINCT C) > 1;
```

This is easy to implement, but repeatedly executing SQL queries for
every candidate FD can become expensive.

Therefore, for a larger implementation, the algorithm should preferably
load the required table data once and reuse in-memory structures, or use
partition-based validation.

------------------------------------------------------------------------

# 7. Brute-Force Search Space

For `n` attributes there are:

``` text
2^n - 1
```

non-empty attribute subsets.

For example:

    Number of attributes   Non-empty subsets
  ---------------------- -------------------
                       5                  31
                      10               1,023
                      15              32,767
                      20           1,048,575
                      25          33,554,431
                      30       1,073,741,823

This is why pure brute force does not scale well.

The important point is:

> The FD test itself is not the main problem. The number of candidate
> determinants is the problem.

For this reason, the brute-force implementation should be combined with
minimization and pruning.

------------------------------------------------------------------------

# 8. Minimization of Determinants

## 8.1 Why Minimization Is Necessary

Suppose the algorithm discovers:

``` text
(A, B, C) → D
```

It may also discover:

``` text
(A, B) → D
```

If `(A,B) → D` already holds, then `(A,B,C) → D` is not a minimal
determinant.

In fact, by the augmentation property:

``` text
(A,B) → D
```

implies:

``` text
(A,B,C) → D
```

Therefore, storing every valid FD produces a huge amount of redundant
information.

The tool should prefer minimal determinants.

------------------------------------------------------------------------

# 9. Minimal Determinant Test

Given:

``` text
X → Y
```

where:

``` text
X = {A, B, C}
```

try removing each attribute.

Test:

``` text
(B,C) → Y
(A,C) → Y
(A,B) → Y
```

If one still holds, that removed attribute was unnecessary.

Example:

``` text
A,B,C → D
```

Suppose:

``` text
B,C → D
```

also holds.

Then:

``` text
A
```

is unnecessary.

Reduce:

``` text
A,B,C
```

to:

``` text
B,C
```

Repeat until no attribute can be removed.

The result is a minimal determinant for that dependent.

------------------------------------------------------------------------

# 10. Minimization Algorithm

Conceptual pseudocode:

``` text
minimize(X, Y):

    current = X

    for each attribute a in current:

        candidate = current - {a}

        if candidate → Y holds:
            current = candidate

    return current
```

Example:

``` text
Input:
    ABC → D

Try removing A:
    BC → D ?
    YES

Current:
    BC → D

Try removing B:
    C → D ?
    NO

Try removing C:
    B → D ?
    NO

Result:
    BC → D
```

The order can affect the particular minimal determinant found when
multiple minimal determinants exist, so the implementation should not
assume that the result is the only possible minimal determinant.

For complete FD discovery, the search algorithm should be capable of
retaining multiple distinct minimal determinants.

Example:

``` text
A,B → D
A,C → D
```

Both can be minimal and both should be preserved.

------------------------------------------------------------------------

# 11. Important Distinction: Minimal FD vs All Valid FDs

Suppose:

``` text
A → D
```

holds.

Then all of these also hold:

``` text
A,B → D
A,C → D
A,B,C → D
...
```

These are valid FDs but are not minimal.

The discovery output should therefore normally focus on:

``` text
A → D
```

rather than listing every augmented version.

This greatly reduces output size and gives the normalization engine the
meaningful dependencies it needs.

------------------------------------------------------------------------

# 12. Brute-Force Algorithm with Pruning

The brute-force algorithm can be improved substantially without becoming
TANE.

Recommended strategy:

``` text
1. Test single-column determinants.
2. Store discovered minimal FDs.
3. Before testing X → Y, check whether a known
   smaller determinant already determines Y.
4. If yes, skip X → Y.
5. Generate larger candidate determinants.
6. Minimize newly discovered determinants.
7. Continue until no useful candidates remain.
```

Example:

If:

``` text
A → D
```

has already been discovered, then:

``` text
A,B → D
A,C → D
A,B,C → D
```

do not need to be separately reported.

This is still fundamentally a brute-force search, but it is a **pruned
brute-force algorithm** rather than naïve enumeration.

------------------------------------------------------------------------

# 13. Candidate Keys and Superkeys

A useful optimization is to detect when an attribute set uniquely
identifies rows.

For a determinant `X`, if every `X` group contains exactly one row,
then:

``` text
X
```

is a superkey for the current relation.

Therefore:

``` text
X → every other attribute
```

holds.

Once a candidate key or superkey is known, larger supersets do not need
to be explored for the purpose of discovering minimal determinants.

Example:

``` text
(A,B)
```

uniquely identifies every row.

Then:

``` text
A,B → C
A,B → D
A,B → E
...
```

all hold.

And:

``` text
A,B,C → D
A,B,D → E
...
```

are augmented/redundant determinants.

This provides an important pruning rule.

------------------------------------------------------------------------

# 14. Algorithm 2: TANE

## 14.1 Overview

TANE is a classic FD discovery algorithm designed to avoid the cost of
naïve brute-force enumeration.

Its core ideas include:

-   level-wise candidate generation
-   partitions
-   equivalence classes
-   FD testing using partitions
-   pruning
-   minimality

The central difference is:

``` text
Brute Force:
    Generate many candidates
    Test them individually

TANE:
    Organize candidates by level
    Reuse partitions
    Prune candidates aggressively
```

------------------------------------------------------------------------

# 15. TANE's Level-Wise Search

TANE processes attribute sets by size.

For attributes:

``` text
A, B, C, D
```

the search proceeds approximately as:

``` text
Level 1:
A
B
C
D

Level 2:
AB
AC
AD
BC
BD
CD

Level 3:
ABC
ABD
ACD
BCD

Level 4:
ABCD
```

But TANE does not blindly test every candidate at every level.

It uses previously discovered information to eliminate candidates that
cannot produce useful minimal FDs.

------------------------------------------------------------------------

# 16. Partitions in TANE

For an attribute set `X`, define a partition:

``` text
P(X)
```

where rows are grouped according to identical values of `X`.

Example:

``` text
Row   A   B

1     X   P
2     X   P
3     X   Q
4     Y   P
5     Y   P
```

Partition by `A`:

``` text
P(A):

{1,2,3}
{4,5}
```

Partition by `(A,B)`:

``` text
P(A,B):

{1,2}
{3}
{4,5}
```

Adding attributes can split groups but cannot merge previously distinct
groups.

Therefore:

``` text
P(A)
   ↓ add B
P(A,B)
   ↓ add C
P(A,B,C)
```

This property is extremely useful for efficient FD discovery.

------------------------------------------------------------------------

# 17. Testing an FD Using Partitions

For:

``` text
X → Y
```

compare the partition of:

``` text
P(X)
```

with:

``` text
P(X,Y)
```

The FD holds when adding `Y` does not further split any group of `P(X)`.

In other words:

``` text
X → Y
```

iff:

``` text
P(X) = P(X,Y)
```

Example:

``` text
P(A):

{1,2}
{3,4}
```

and:

``` text
P(A,B):

{1,2}
{3,4}
```

No group was split.

Therefore:

``` text
A → B
```

holds.

If instead:

``` text
P(A,B):

{1}
{2}
{3,4}
```

then the first group from `P(A)` was split by B.

Therefore:

``` text
A ↛ B
```

------------------------------------------------------------------------

# 18. Partition Refinement

A major operation in a partition-based implementation is refinement.

Suppose:

``` text
P(A)
```

is already known.

To obtain:

``` text
P(A,B)
```

take every group from `P(A)` and split it according to the value of `B`.

Example:

``` text
P(A):

{1,2,3}
{4,5}
```

Suppose B values are:

``` text
Row  B

1    P
2    P
3    Q
4    P
5    P
```

Then:

``` text
P(A,B):

{1,2}
{3}
{4,5}
```

This allows the implementation to reuse previously computed partitions
rather than rebuilding every grouping from the entire table.

------------------------------------------------------------------------

# 19. TANE Pruning

Pruning is what makes TANE much more useful than naïve brute force.

There are several forms of pruning, but the key intuition is:

> If a smaller determinant already explains a dependency, larger
> supersets do not need to be considered for that same dependent.

Example:

``` text
A → D
```

means:

``` text
A,B → D
A,C → D
A,B,C → D
```

are not minimal.

Therefore, once:

``` text
A → D
```

is known, the algorithm can avoid treating those larger determinants as
new minimal dependencies.

TANE also uses structural properties of candidate sets and partitions to
eliminate candidates that cannot produce new minimal FDs.

------------------------------------------------------------------------

# 20. Brute Force vs TANE

The two approaches can be compared as follows:

  -----------------------------------------------------------------------
  Property                Brute Force +           TANE
                          Minimization            
  ----------------------- ----------------------- -----------------------
  Basic search            Enumerate candidate     Level-wise search
                          sets                    

  FD checking             GROUP BY / partition    Partition-based

  Partition reuse         Optional                Central idea

  Pruning                 Basic                   Extensive

  Implementation          Easier                  More complex

  Transparency            Very high               Moderate

  Small tables            Excellent               Good

  Few columns             Excellent               Good

  Many columns            Can become expensive    Generally more scalable

  Minimal FDs             Yes, with minimization  Yes

  Good reference          Yes                     Yes
  implementation                                  

  Good production         For small/moderate      For larger cases
  algorithm               cases                   
  -----------------------------------------------------------------------

------------------------------------------------------------------------

# 21. Recommended Architecture for This Tool

The recommended design is to implement both algorithms behind a common
interface.

``` text
FDDiscoveryEngine
        |
        +----------------------+
        |                      |
        v                      v
BruteForceFDDiscovery      TANEFDDiscovery
        |                      |
        +----------+-----------+
                   |
                   v
             FD collection
```

Both implementations should return the same conceptual structure:

``` text
FD {
    determinant: Set<Attribute>,
    dependent: Attribute,
    source: algorithm/schema/data,
    confidence/type: ...
}
```

For the algorithm-only portion, the important fields are:

``` text
determinant
dependent
```

------------------------------------------------------------------------

# 22. Shared FD Validation Layer

Both algorithms should ideally share a common FD validation layer.

Possible interface:

``` text
holds(X, Y) -> boolean
```

Example:

``` text
holds({Student_ID}, Student_Name)
```

returns:

``` text
true
```

while:

``` text
holds({Student_ID}, Marks)
```

may return:

``` text
false
```

The underlying implementation can use:

``` text
Option A:
    SQL GROUP BY

Option B:
    in-memory grouping

Option C:
    partition objects
```

For TANE, partition objects become the preferred mechanism.

------------------------------------------------------------------------

# 23. Recommended Implementation Stages

## Stage 1: Naïve brute force

Implement:

``` text
for every determinant X:
    for every dependent Y not in X:
        test X → Y
```

This provides the simplest correctness baseline.

------------------------------------------------------------------------

## Stage 2: Add minimization

Whenever:

``` text
X → Y
```

is found:

``` text
minimize(X, Y)
```

Store only the minimal determinant.

------------------------------------------------------------------------

## Stage 3: Add pruning

Use already discovered dependencies to avoid testing obviously redundant
supersets.

Also identify superkeys/candidate keys early.

------------------------------------------------------------------------

## Stage 4: Add partitions

Replace repeated database-level grouping with reusable in-memory
partitions.

Introduce:

``` text
Partition(X)
```

and:

``` text
refine(Partition(X), attribute)
```

------------------------------------------------------------------------

## Stage 5: Implement TANE

Build the level-wise candidate-generation process around the partition
infrastructure.

``` text
Level 1
   ↓
FD checks
   ↓
Prune
   ↓
Generate Level 2
   ↓
FD checks
   ↓
Prune
   ↓
...
```

------------------------------------------------------------------------

# 24. Recommended Execution Strategy

The tool can select the algorithm based on table characteristics.

A simple initial policy:

``` text
Small number of attributes
        ↓
Brute Force + Minimization

Larger number of attributes
        ↓
TANE
```

For example, the initial implementation could expose:

``` text
Algorithm:
    AUTO
    BRUTE_FORCE
    TANE
```

`BRUTE_FORCE` is useful for:

-   debugging
-   correctness verification
-   small tables
-   development/testing

`TANE` is useful for:

-   larger schemas
-   repeated analysis
-   production workloads
-   cases where brute-force candidate enumeration becomes expensive

The exact threshold should be determined through benchmarking rather
than hard-coded based only on the number of columns.

------------------------------------------------------------------------

# 25. Correctness Testing Strategy

The brute-force implementation should be treated as the **reference
implementation**.

For test datasets small enough that brute force is practical:

``` text
Brute Force results
        vs
TANE results
```

should produce equivalent minimal FD sets.

Example:

``` text
Dataset
   |
   +----> Brute Force
   |
   +----> TANE
              |
              v
       Normalize result
              |
              v
        Compare FD sets
```

This is particularly valuable when implementing TANE because partition
refinement and pruning are easy places to introduce subtle bugs.

------------------------------------------------------------------------

# 26. Important Edge Cases

The FD discovery algorithms should explicitly define behavior for:

### Empty table

Mathematically, many FDs hold vacuously on an empty relation.

However, an application should distinguish:

``` text
"FD holds because there is no contradictory data"
```

from:

``` text
"FD was actually observed from data"
```

A practical implementation should mark data-based inference as having
insufficient evidence when there are no rows.

### Duplicate rows

Duplicate rows do not violate an FD if their determinant and dependent
values agree.

### NULL values

The implementation needs an explicit policy because SQL `NULL` semantics
differ from ordinary equality.

Do not silently assume that SQL `COUNT(DISTINCT ...)` behavior is
identical to the mathematical FD definition.

### Constant columns

A column with one value across the entire dataset technically determines
another constant column.

The implementation should decide whether such data-derived dependencies
are useful or whether they should be treated separately as
trivial/data-specific dependencies.

### Empty determinant

The empty determinant:

``` text
∅ → A
```

means A is constant throughout the relation.

This may be supported explicitly or excluded from the initial
implementation.

------------------------------------------------------------------------

# 27. Complexity Considerations

The fundamental challenge is combinatorial.

With `n` attributes:

``` text
number of subsets = 2^n - 1
```

Therefore, no implementation should assume that every possible
determinant can always be enumerated cheaply.

The goal of optimization is to avoid examining unnecessary candidates.

Brute force improves through:

``` text
minimality
+ pruning
+ early key detection
+ partition reuse
```

TANE improves through:

``` text
level-wise search
+ partition representation
+ partition refinement
+ pruning
+ minimality
```

------------------------------------------------------------------------

# 28. Why Use Both Algorithms?

Using both algorithms provides a strong engineering advantage.

### Brute Force

Acts as:

``` text
simple
transparent
easy to debug
reference implementation
```

### TANE

Acts as:

``` text
optimized
partition-based
pruned
more scalable implementation
```

The project therefore does not have to trust a complex algorithm
immediately.

For small datasets:

``` text
Brute Force → expected answer
TANE        → implementation under test
```

For larger datasets:

``` text
TANE → production discovery
```

This gives the project both **simplicity and scalability**.

------------------------------------------------------------------------

# 29. Final Recommended Design

The final FD discovery subsystem should look like:

``` text
                    FD DISCOVERY
                         |
                Algorithm Selector
                         |
          +--------------+--------------+
          |                             |
          v                             v
 Brute Force +                    TANE
 Minimization
          |                             |
          |                       Level-wise search
          |                             |
          |                       Partitions
          |                             |
          |                       Refinement
          |                             |
          |                       FD checks
          |                             |
          |                       Pruning
          |                             |
          +-------------+---------------+
                        |
                        v
               Minimal FD Set
```

Recommended development order:

``` text
1. Implement FD validation
2. Implement naïve brute-force search
3. Add determinant minimization
4. Add pruning
5. Add reusable partitions
6. Implement TANE
7. Compare TANE against brute force
8. Benchmark both
9. Select algorithm automatically based on measured performance
```

The key design principle is:

> **Brute force is the simple baseline; minimization and pruning make it
> practical for smaller schemas; TANE uses the same FD concepts but
> organizes the search around partitions to handle larger search spaces
> more efficiently.**

---

# Final Prototype Implementation Policy

The production prototype now uses the following policy:

- Up to `BRUTE_FORCE_MAX_COLUMNS`, brute force is the reference and production search because exhaustive enumeration is practical for small schemas.
- The partition-based level-wise search is the TANE-style production search for larger schemas.
- On small schemas, TANE is also run as a parity check. A mismatch is logged as an implementation error and the brute-force result remains authoritative.
- A determinant-size limit on larger schemas is reported through `fd_discovery.complete=false`; the tool never presents an incomplete search as proof that no FD exists.
- Exact candidates found on a sampled table are validated against the full database table before they are treated as full-table observations.
- Near-FDs are never used as formal proof of 2NF/3NF/BCNF.
- Low-evidence exact FDs are retained as `suspected` instead of being silently discarded, improving recall without turning weak evidence into a faulted normalization result.
- Candidate keys support arbitrary composite sizes subject to an explicit search-combination safety cap. Inferred keys are data evidence, not schema guarantees.
- 2NF is checked only against proper subsets of composite candidate keys. A single-column key cannot produce a partial dependency.
