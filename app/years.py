"""NBA assessment-year logic.

NBA SAR formats name years relative to the Current Academic Year (CAY):
CAY, CAYm1 (one year before), CAYm2, CAYm3. Graduating batches are LYG, LYGm1, LYGm2.
An academic year is labelled "2026-27" and, by default, runs from 1 July to 30 June.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

YEAR_NAMES = ["CAY", "CAYm1", "CAYm2", "CAYm3", "CAYm4", "CAYm5", "CAYm6"]
# Graduating batches are named by their year of entry (SAR Table 4C): LYG entered in CAYm4.
BATCH_ALIAS = {"CAYm4": "LYG", "CAYm5": "LYGm1", "CAYm6": "LYGm2"}


def display_name(name: str) -> str:
    return f"{name} ({BATCH_ALIAS[name]})" if name in BATCH_ALIAS else name


def ay_label(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def ay_start_year(d: date, start_month: int = 7) -> int:
    """Return the start year of the academic year that contains date d."""
    return d.year if d.month >= start_month else d.year - 1


def ay_bounds(start_year: int, start_month: int = 7) -> tuple[date, date]:
    first = date(start_year, start_month, 1)
    end_month = start_month - 1 or 12
    end_year = start_year + 1 if start_month > 1 else start_year
    last_day = calendar.monthrange(end_year, end_month)[1]
    return first, date(end_year, end_month, last_day)


@dataclass(frozen=True)
class AssessmentYears:
    cay_start: int
    start_month: int = 7

    def start_of(self, name: str) -> int:
        if name not in YEAR_NAMES:
            raise ValueError(f"Unknown assessment year name: {name}")
        return self.cay_start - YEAR_NAMES.index(name)

    def label(self, name: str) -> str:
        return ay_label(self.start_of(name))

    def name_of(self, d: date) -> str | None:
        """Which assessment year name (CAY, CAYm1, ...) a date falls in, if any."""
        y = ay_start_year(d, self.start_month)
        offset = self.cay_start - y
        if 0 <= offset < len(YEAR_NAMES):
            return YEAR_NAMES[offset]
        return None

    def window_bounds(self, names: list[str]) -> tuple[date, date]:
        starts = [self.start_of(n) for n in names]
        first, _ = ay_bounds(min(starts), self.start_month)
        _, last = ay_bounds(max(starts), self.start_month)
        return first, last

    def window_text(self, names: list[str]) -> str:
        starts = sorted(self.start_of(n) for n in names)
        return ", ".join(ay_label(s) for s in starts)

    def table(self) -> list[dict]:
        rows = []
        for n in YEAR_NAMES[:4]:
            s = self.start_of(n)
            a, b = ay_bounds(s, self.start_month)
            rows.append({"name": n, "label": ay_label(s), "from": a, "to": b})
        return rows
