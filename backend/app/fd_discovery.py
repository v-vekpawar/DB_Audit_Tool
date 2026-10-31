"""Exact minimal FD search over integer-coded columns (TANE-style).

Level-wise search over column sets using stripped partitions (only groups
of 2+ rows are kept; singletons can never split further). Once X -> Y
holds, no superset of X is reported for Y (minimality). Determinants are
limited to MAX_LHS_SIZE (MAX_LHS_SIZE_LARGE on large tables), except every
proper non-empty subset of a declared composite key, which is always tested.

Near-FD candidates (dirty data) are detected here and scored in fd_scoring.
"""

from dataclasses import dataclass, field
from itertools import combinations

from . import config

_MOSTLY_NULL = 0.9  # a column this NULL carries no signal; pruned from search


@dataclass
class Partition:
    groups: dict  # key-tuple -> [row indices], only len >= 2
    examined: int  # rows without NULL in the set's columns


@dataclass
class FoundFD:
    determinant: list
    dependent: str
    kind: str  # "exact" | "near"
    k: int = 0  # exception rows (near only)
    exception_rows: list = field(default_factory=list)
    group_sizes: list = field(default_factory=list)
    examined: int = 0
    equivalent: bool = False


@dataclass
class DiscoverResult:
    fds: list  # FoundFD, minimal only
    m: int  # candidate (determinant, dependent) pairs for the correction
    tests: int = 0
    pruned: int = 0
    algorithm: str = "tane"
    complete: bool = True


def _null_masks(coded, columns, n):
    masks = {}
    for c in columns:
        col = coded[c]
        m = 0
        for j, v in enumerate(col):
            if v is None:
                m |= (1 << j)
        masks[c] = m
    return masks


def _examined(masks, det, n):
    m = 0
    for c in det:
        m |= masks[c]
    return n - m.bit_count()


def _partition_single(col_vals, n):
    groups: dict = {}
    examined = 0
    for j, v in enumerate(col_vals):
        if v is None:
            continue
        examined += 1
        g = groups.get(v)
        if g is None:
            groups[v] = [j]
        else:
            g.append(j)
    stripped = {k: g for k, g in groups.items() if len(g) > 1}
    return Partition(stripped, examined), len(groups)


def _refine(parent: Partition, col_vals):
    groups: dict = {}
    for key, rows in parent.groups.items():
        sub: dict = {}
        for r in rows:
            v = col_vals[r]
            if v is None:
                continue
            g = sub.get(v)
            if g is None:
                sub[v] = [r]
            else:
                g.append(r)
        for v, g in sub.items():
            if len(g) > 1:
                groups[(key, v) if isinstance(key, tuple) else (key, v)] = g
    return groups


def _check_groups(groups: dict, dep_vals, n, max_error):
    """Exact/near test of one determinant against one dependent."""
    if not groups:
        return "unique", None
    sizes = []
    exc_rows: list = []
    k = 0
    for g in groups.values():
        sizes.append(len(g))
        counts: dict = {}
        top = 0
        for r in g:
            v = dep_vals[r]
            c = counts.get(v, 0) + 1
            counts[v] = c
            if c > top:
                top = c
        if top == len(g):
            continue
        if len(g) < config.NEAR_FD_MIN_GROUP or top * 2 <= len(g):
            return "fail", None
        k += len(g) - top
        top_val = max(counts, key=lambda x: counts[x])
        exc_rows.extend(r for r in g if dep_vals[r] != top_val)
    if k == 0:
        return "holds", {"group_sizes": sizes}
    if k / n <= config.NEAR_FD_MAX_ERROR:
        return "near", {"group_sizes": sizes, "k": k, "exception_rows": exc_rows}
    return "fail", None


class _Searcher:
    """Correct level-wise partition FD search.

    This is deliberately close to the ideas used by TANE: candidates are
    processed by determinant size, partitions are refined by adding one
    attribute, and known minimal determinants/superkeys prune supersets.
    """
    def __init__(self, coded, columns, n, declared_keys, limit):
        self.coded = coded
        self.columns = sorted(columns)
        self.n = n
        self.limit = limit
        self.masks = _null_masks(coded, columns, n)
        self.distinct = {c: len({v for v in coded[c] if v is not None}) for c in columns}
        # Determinants need more than one observed value to be useful as a
        # minimal determinant, but dependent attributes must include constant
        # columns: a non-key determinant can legitimately determine a constant
        # attribute.  Pruning constant dependents was causing TANE to miss
        # valid FDs (especially in NULL/fixture datasets).
        self.live = [c for c in self.columns
                     if self.distinct[c] > 1 and
                     ((sum(v is None for v in coded[c])) / n if n else 1.0) <= _MOSTLY_NULL]
        self.dependents = [c for c in self.columns
                           if ((sum(v is None for v in coded[c])) / n if n else 1.0) <= _MOSTLY_NULL]
        self.single_keys = {k[0] for k in (declared_keys or []) if len(k) == 1}
        self.minimal = {}
        self.fds = []
        self.tests = 0
        self.pruned = 0
        self.unique_sets = set()
        self.parts = {}

    def _dominated(self, det, dep):
        s = set(det)
        return any(k <= s for k in self.minimal.get(dep, ()))

    def _partition(self, attrs):
        """Build/refine a complete equivalence partition for attrs."""
        key = frozenset(attrs)
        if key in self.parts:
            return self.parts[key]
        attrs = tuple(sorted(attrs))
        if len(attrs) == 1:
            part, _ = _partition_single_full(self.coded[attrs[0]], self.n)
        else:
            parent_attrs = attrs[:-1]
            parent = self._partition(parent_attrs)
            groups = _refine_full(parent.groups, self.coded[attrs[-1]])
            examined = _examined(self.masks, attrs, self.n)
            part = Partition(groups, examined)
        self.parts[key] = part
        if part.groups and all(len(g) == 1 for g in part.groups.values()):
            # Every comparable row is unique for this determinant.
            self.unique_sets.add(key)
        return part

    def _try(self, det, dep, part=None):
        if dep in det or self._dominated(det, dep):
            self.pruned += 1
            return False
        if any(c in self.single_keys for c in det):
            self.pruned += 1
            return False
        part = part or self._partition(det)
        if not part.groups:
            self.pruned += 1
            return False
        # A superkey determinant is not a useful minimal FD determinant.
        if all(len(g) == 1 for g in part.groups.values()):
            self.pruned += 1
            return False
        self.tests += 1
        status, info = _check_groups(part.groups, self.coded[dep], self.n,
                                     config.NEAR_FD_MAX_ERROR)
        if status == "fail":
            return False
        det_names = list(sorted(det))
        # Only an exact FD may establish minimality for pruning.  A near-FD
        # is an approximate observation and must never suppress a larger
        # determinant that may hold exactly.
        if status == "holds":
            self.minimal.setdefault(dep, []).append(set(det_names))
            self.fds.append(FoundFD(det_names, dep, "exact",
                                    group_sizes=info["group_sizes"],
                                    examined=part.examined))
        else:
            self.fds.append(FoundFD(det_names, dep, "near", k=info["k"],
                                    exception_rows=info["exception_rows"],
                                    group_sizes=info["group_sizes"],
                                    examined=part.examined))
        return True

    def run_levels(self):
        cols = self.live
        if not cols:
            return
        # Level-wise candidate determinants. We retain all non-unique
        # partitions; unique determinants prune every superset.
        prev = [tuple([c]) for c in cols]
        self._level_partitions(prev)
        size = 1
        while prev and size <= self.limit:
            if size > 1:
                prev = self._generate_level(prev, size)
                if not prev:
                    break
                self._level_partitions(prev)
            for det in prev:
                part = self.parts.get(frozenset(det))
                if part is None or not part.groups:
                    continue
                if all(len(g) == 1 for g in part.groups.values()):
                    continue
                for dep in self.dependents:
                    self._try(det, dep, part)
            size += 1

    def _generate_level(self, prev, size):
        prev_sets = {frozenset(x) for x in prev}
        out = []
        for combo in combinations(self.live, size):
            s = frozenset(combo)
            if any(u <= s for u in self.unique_sets):
                self.pruned += 1
                continue
            # Apriori/TANE candidate rule: all immediate subsets should have
            # survived the previous level. If a subset is a superkey or a
            # missing partition, the candidate cannot produce a new minimal FD.
            if not all(frozenset(x) in prev_sets for x in combinations(combo, size - 1)):
                self.pruned += 1
                continue
            out.append(combo)
        return out

    def _level_partitions(self, level_sets):
        for s in level_sets:
            self._partition(s)

    def run_key_parts(self, declared_keys):
        # Composite declared keys are especially important for 2NF: test every
        # proper subset even when the general search was pruned by a key column.
        for key in declared_keys or []:
            if len(key) < 2:
                continue
            for r in range(1, len(key)):
                for det in combinations(key, r):
                    part = self._partition(det)
                    for dep in self.columns:
                        self._try(det, dep, part)


def _partition_single_full(col_vals, n):
    groups = {}
    examined = 0
    for j, v in enumerate(col_vals):
        if v is None:
            continue
        examined += 1
        groups.setdefault(v, []).append(j)
    return Partition(groups, examined), len(groups)


def _refine_full(parent_groups, col_vals):
    groups = {}
    for rows in parent_groups.values():
        sub = {}
        for r in rows:
            v = col_vals[r]
            if v is None:
                continue
            sub.setdefault(v, []).append(r)
        for v, g in sub.items():
            groups[tuple(g)] = g
    return groups


def parts_parent(parts: dict, s):
    return parts.get(frozenset(s[:-1]))


def _join(prev_tuples):
    out = []
    seen = set()
    prev = sorted(prev_tuples)
    for i in range(len(prev)):
        for j in range(i + 1, len(prev)):
            a, b = prev[i], prev[j]
            if a[:-1] == b[:-1] and a[-1] < b[-1]:
                c = a + (b[-1],)
                if c not in seen:
                    seen.add(c)
                    out.append(c)
    return out


def count_candidates(n_cols, limit, declared_keys=None):
    from math import comb
    m = 0
    for k in range(1, limit + 1):
        if k <= n_cols:
            m += comb(n_cols, k) * (n_cols - k)
    if declared_keys:
        for key in declared_keys:
            for r in range(1, len(key)):
                if r > limit:
                    for _ in combinations(key, r):
                        m += max(0, n_cols - r)
    return m


def _fd_holds_exact(coded, det, dep, n):
    """Exact FD test with the project's NULL policy: NULL in X excludes the row;
    NULL in Y is an ordinary value and therefore can disagree with a non-NULL.
    """
    groups = {}
    cols = [coded[c] for c in det]
    y = coded[dep]
    examined = 0
    for r in range(n):
        vals = []
        valid = True
        for col in cols:
            v = col[r]
            if v is None:
                valid = False
                break
            vals.append(v)
        if not valid:
            continue
        examined += 1
        groups.setdefault(tuple(vals), set()).add(y[r])
        if len(groups[tuple(vals)]) > 1:
            return False
    return examined > 0


def _is_unique_values(coded, cols, n):
    """Whether X is a superkey under the FD NULL policy.

    NULL in a determinant excludes that row from FD validation, so uniqueness
    for FD-discovery pruning is checked only over rows where every determinant
    attribute is non-NULL.  This is deliberately different from candidate-key
    inference, where NULL-containing keys are never accepted.
    """
    if not cols or n <= 0:
        return False
    seen = set()
    examined = 0
    for r in range(n):
        t = tuple(coded[c][r] for c in cols)
        if any(v is None for v in t):
            continue
        examined += 1
        if t in seen:
            return False
        seen.add(t)
    return examined > 0


def brute_force_discover(coded, columns, n_rows, declared_keys=None) -> DiscoverResult:
    """Reference FD discovery: enumerate determinants by size and test exactly.

    It intentionally favors clarity over speed. It reports minimal exact FDs only;
    schema-declared key determinants are skipped because they are already guaranteed
    to determine every column and are not useful minimal non-key dependencies.
    """
    cols_all = sorted(columns)
    cols = [c for c in cols_all
            if n_rows and (sum(v is None for v in coded[c]) / n_rows) <= _MOSTLY_NULL]
    # With fewer than two rows there is no repeated determinant group from
    # which a data-observed FD can be established. Treat the relation as
    # insufficient evidence rather than reporting vacuous FDs.
    if n_rows < 2:
        m = sum(len(list(combinations(cols, k))) * (len(cols) - k)
                for k in range(1, len(cols)))
        return DiscoverResult(fds=[], m=m, tests=0, pruned=0,
                              algorithm="bruteforce", complete=True)
    fds = []
    minimal = {}
    tests = 0
    pruned = 0
    declared_single = {k[0] for k in (declared_keys or []) if len(k) == 1}

    for size in range(1, len(cols) + 1):
        for det_t in combinations(cols, size):
            det = set(det_t)
            # A declared single-column key already determines everything.
            if any(c in declared_single for c in det):
                pruned += max(0, len(cols) - size)
                continue
            # A unique determinant is a superkey; it determines every other
            # attribute but is not a useful minimal non-key FD.
            if _is_unique_values(coded, det_t, n_rows):
                pruned += len(cols) - size
                continue
            for dep in cols:
                if dep in det:
                    continue
                known = minimal.get(dep, [])
                if any(k <= det for k in known):
                    pruned += 1
                    continue
                tests += 1
                if _fd_holds_exact(coded, det_t, dep, n_rows):
                    minimal.setdefault(dep, []).append(det)
                    fds.append(FoundFD(list(det_t), dep, "exact",
                                       group_sizes=[], examined=n_rows))
    _mark_equivalent(fds)
    m = sum(len(list(combinations(cols, k))) * (len(cols) - k)
            for k in range(1, len(cols)))
    return DiscoverResult(fds=fds, m=m, tests=tests, pruned=pruned,
                          algorithm="bruteforce", complete=True)


def discover(coded, columns, n_rows, declared_keys=None, limit=None,
             algorithm="auto") -> DiscoverResult:
    """Discover minimal FDs. Small schemas use the exact reference search;
    larger schemas use the partition-based TANE-style search with an explicit
    completeness flag when determinant-size limits prevent exhaustive search.
    """
    ncols = len(columns)
    if n_rows < 2:
        m = sum(len(list(combinations(sorted(columns), k))) * (ncols - k)
                for k in range(1, ncols))
        return DiscoverResult(fds=[], m=m, tests=0, pruned=0,
                              algorithm=("bruteforce" if algorithm == "bruteforce" else "tane"),
                              complete=True)
    if algorithm == "bruteforce" or (algorithm == "auto" and
                                      config.RUN_BRUTE_FORCE_PARITY and
                                      ncols <= config.BRUTE_FORCE_MAX_COLUMNS):
        return brute_force_discover(coded, columns, n_rows, declared_keys)
    if limit is None:
        limit = (config.MAX_LHS_SIZE_LARGE if n_rows >= config.LARGE_TABLE_ROWS
                 else config.MAX_LHS_SIZE)
    limit = min(limit, max(1, ncols - 1))
    s = _Searcher(coded, list(columns), n_rows, declared_keys, limit)
    s.run_levels()
    s.run_key_parts(declared_keys)
    _mark_equivalent(s.fds)
    m = count_candidates(len(columns), limit, declared_keys)
    return DiscoverResult(fds=s.fds, m=m, tests=s.tests, pruned=s.pruned,
                          algorithm="tane", complete=(limit >= max(1, ncols - 1)))


def _mark_equivalent(fds):
    singles = {}
    for f in fds:
        if f.kind == "exact" and len(f.determinant) == 1:
            singles.setdefault((f.determinant[0], f.dependent), f)
    for (a, b), f in list(singles.items()):
        if (b, a) in singles:
            f.equivalent = True
            singles[(b, a)].equivalent = True
