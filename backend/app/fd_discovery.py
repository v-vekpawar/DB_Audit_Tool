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
    def __init__(self, coded, columns, n, declared_keys, limit):
        self.coded = coded
        self.columns = columns
        self.n = n
        self.limit = limit
        self.masks = _null_masks(coded, columns, n)
        self.distinct = {}
        self.live = []  # searchable, non-constant columns
        for c in columns:
            vals = coded[c]
            nn = [v for v in vals if v is not None]
            d = len(set(nn))
            self.distinct[c] = d
            null_ratio = (n - len(nn)) / n if n else 1.0
            if d > 1 and null_ratio <= _MOSTLY_NULL:
                self.live.append(c)
        self.single_keys = set()
        for k in declared_keys or []:
            if len(k) == 1:
                self.single_keys.update(k)
        self.minimal = {}  # dep -> [set(det)]
        self.fds: list = []
        self.tests = 0
        self.pruned = 0
        self.unique_sets = set()
        self.parts: dict = {}  # frozenset -> Partition (previous level)

    def _dominated(self, det, dep):
        for known in self.minimal.get(dep, ()):
            if known <= set(det):
                return True
        return False

    def _try(self, det, dep):
        """Test det -> dep. det: list of names. Returns True if recorded."""
        if dep in det or self._dominated(det, dep):
            self.pruned += 1
            return False
        if any(c in self.single_keys for c in det):
            self.pruned += 1
            return False
        if self.distinct.get(dep, 0) <= 1:
            self.pruned += 1
            return False
        key = frozenset(det)
        part = self.parts.get(key)
        if part is None:
            groups = self._direct_groups(det)
            examined = _examined(self.masks, det, self.n)
            if not groups:
                if examined:
                    self.unique_sets.add(key)
                return False
        else:
            groups = part.groups
            examined = part.examined
            if not groups:
                return False
        # cheap prefilter: Y cannot have more distinct values than X
        if self.distinct[dep] > len(groups) + (examined - sum(len(g) for g in groups.values())):
            self.pruned += 1
            return False
        self.tests += 1
        status, info = _check_groups(groups, self.coded[dep], self.n,
                                     config.NEAR_FD_MAX_ERROR)
        if status == "fail":
            return False
        det_names = list(det)
        if status == "holds":
            self.minimal.setdefault(dep, []).append(set(det_names))
            self.fds.append(FoundFD(det_names, dep, "exact",
                                    group_sizes=info["group_sizes"],
                                    examined=examined))
            return True
        # near
        self.minimal.setdefault(dep, []).append(set(det_names))
        self.fds.append(FoundFD(det_names, dep, "near", k=info["k"],
                                exception_rows=info["exception_rows"],
                                group_sizes=info["group_sizes"],
                                examined=examined))
        return True

    def _direct_groups(self, det):
        cols = [self.coded[c] for c in det]
        groups: dict = {}
        for j in range(self.n):
            key = []
            for col in cols:
                v = col[j]
                if v is None:
                    break
                key.append(v)
            else:
                t = tuple(key)
                g = groups.get(t)
                if g is None:
                    groups[t] = [j]
                else:
                    g.append(j)
                continue
        return {k: g for k, g in groups.items() if len(g) > 1}

    def _level_partitions(self, level_sets):
        parts = {}
        for s in level_sets:
            s = tuple(sorted(s))
            if len(s) == 1:
                part, _ = _partition_single(self.coded[s[0]], self.n)
            else:
                parent = parts_parent(self.parts, s)
                if parent is None:
                    continue
                groups = _refine(parent, self.coded[s[-1]])
                examined = _examined(self.masks, s, self.n)
                part = Partition(groups, examined)
            if not part.groups:
                if part.examined:
                    self.unique_sets.add(frozenset(s))
                continue
            parts[frozenset(s)] = part
        self.parts = parts
        return parts

    def run_levels(self):
        cols = sorted(self.live)
        # level 1
        self._level_partitions([(c,) for c in cols])
        prev = [frozenset((c,)) for c in cols if frozenset((c,)) in self.parts]
        prev_tuples = [tuple(sorted(s)) for s in prev]
        size = 2
        while size <= self.limit + 1:
            cands = _join(prev_tuples)
            cands = [c for c in cands if not any(u <= set(c) for u in self.unique_sets)]
            if not cands:
                break
            # The last level's partitions would never be reused as parents,
            # so its candidates are tested by direct grouping instead.
            last = (size == self.limit + 1)
            if not last:
                self._level_partitions(cands)
            # RHS tests: for set X, for A in X: (X - A) -> A.
            # Grouped by determinant so each group-by runs once.
            if last:
                by_det: dict = {}
                for x in cands:
                    if len(x) - 1 > self.limit or len(x) - 1 < 1:
                        continue
                    for a in x:
                        if a not in self.live:
                            continue
                        det = tuple(sorted(c for c in x if c != a))
                        if not all(d in self.live for d in det):
                            continue
                        by_det.setdefault(det, []).append(a)
                for det, deps in by_det.items():
                    self._try_direct_grouped(list(det), deps)
            else:
                for x in cands:
                    if frozenset(x) not in self.parts:
                        continue
                    if len(x) - 1 > self.limit or len(x) - 1 < 1:
                        continue
                    for a in x:
                        if a not in self.live:
                            continue
                        det = [c for c in x if c != a]
                        if all(d in self.live for d in det):
                            self._try(det, a)
            prev_tuples = [tuple(sorted(s)) for s in self.parts]
            size += 1

    def run_key_parts(self, declared_keys):
        for key in declared_keys or []:
            if len(key) < 2:
                continue
            subs = []
            for r in range(1, len(key)):
                subs.extend(combinations(key, r))
            for s in subs:
                det = list(s)
                if not all(d in self.live for d in det):
                    # key parts outside the search space are still tested,
                    # but need direct grouping (no partition objects)
                    pass
                for dep in self.columns:
                    if dep in det or self.distinct.get(dep, 0) <= 1:
                        continue
                    self._try_direct(det, dep)

    def _try_direct(self, det, dep):
        return self._try_direct_grouped(det, [dep])

    def _try_direct_grouped(self, det, deps):
        groups = self._direct_groups(det)
        examined = _examined(self.masks, det, self.n)
        if not groups:
            if examined:
                self.unique_sets.add(frozenset(det))
            return False
        self.tests += len(deps)
        found_any = False
        for dep in deps:
            if dep in det or self._dominated(det, dep):
                self.pruned += 1
                continue
            if any(c in self.single_keys for c in det):
                self.pruned += 1
                continue
            status, info = _check_groups(groups, self.coded[dep], self.n,
                                         config.NEAR_FD_MAX_ERROR)
            if status == "fail":
                continue
            self.minimal.setdefault(dep, []).append(set(det))
            if status == "holds":
                self.fds.append(FoundFD(list(det), dep, "exact",
                                        group_sizes=info["group_sizes"],
                                        examined=examined))
            else:
                self.fds.append(FoundFD(list(det), dep, "near", k=info["k"],
                                        exception_rows=info["exception_rows"],
                                        group_sizes=info["group_sizes"],
                                        examined=examined))
            found_any = True
        return found_any


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


def discover(coded, columns, n_rows, declared_keys=None, limit=None) -> DiscoverResult:
    if limit is None:
        limit = (config.MAX_LHS_SIZE_LARGE if n_rows >= config.LARGE_TABLE_ROWS
                 else config.MAX_LHS_SIZE)
    s = _Searcher(coded, list(columns), n_rows, declared_keys, limit)
    s.run_levels()
    s.run_key_parts(declared_keys)
    _mark_equivalent(s.fds)
    m = count_candidates(len(columns), limit, declared_keys)
    return DiscoverResult(fds=s.fds, m=m, tests=s.tests, pruned=s.pruned)


def _mark_equivalent(fds):
    singles = {}
    for f in fds:
        if f.kind == "exact" and len(f.determinant) == 1:
            singles.setdefault((f.determinant[0], f.dependent), f)
    for (a, b), f in list(singles.items()):
        if (b, a) in singles:
            f.equivalent = True
            singles[(b, a)].equivalent = True
