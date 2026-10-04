"""Work-based time estimation (pure functions + small state object).

units(table) = rows * (columns + m): rows drive grouping passes, m (the
candidate count) drives per-candidate checks. Speed (seconds per unit)
starts at ETA_DEFAULT_SECONDS_PER_UNIT weighted as ETA_PRIOR_UNITS, then
blends in measured tables. `rough` until measured speed exists (3 tables
or ~20% of the work) and while any unfinished table has unknown size.
"""

import time

from . import config


def work_units(n_rows, n_columns, m):
    return max(0, n_rows) * (max(0, n_columns) + max(0, m))


class Estimator:
    def __init__(self, units, default_speed=config.ETA_DEFAULT_SECONDS_PER_UNIT,
                 prior_units=config.ETA_PRIOR_UNITS):
        self.units = dict(units)  # table -> units or None (unknown size)
        self.default_speed = default_speed
        self.prior_units = prior_units
        self.t0 = time.monotonic()
        self.done_units = 0.0
        self.done_time = 0.0
        self.finished = 0
        known = [u for u in units.values() if u is not None]
        self.median = sorted(known)[len(known) // 2] if known else 0.0

    def speed(self):
        return ((self.default_speed * self.prior_units + self.done_time)
                / (self.prior_units + self.done_units))

    def _remaining_units(self, unfinished):
        total = 0.0
        for t in unfinished:
            u = self.units.get(t)
            total += self.median if u is None else u
        return total

    def observe(self, table, seconds):
        u = self.units.get(table)
        if u is not None:
            self.done_units += u
            self.done_time += seconds
        self.finished += 1

    def estimate(self, unfinished):
        elapsed = time.monotonic() - self.t0
        remaining = self.speed() * self._remaining_units(unfinished)
        total = elapsed + remaining
        total_units = self.done_units + self._remaining_units(unfinished)
        measured = ((self.finished >= 3 or
                     (total_units > 0 and self.done_units >= 0.2 * total_units))
                    and not any(self.units.get(t) is None for t in unfinished))
        return {"total_seconds": total, "elapsed_seconds": elapsed,
                "remaining_seconds": remaining,
                "basis": "measured" if measured else "rough"}
