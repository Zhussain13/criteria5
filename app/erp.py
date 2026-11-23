"""Link to the college ERP (Juno Campus at CMRIT): read the faculty list and student strength straight into
the Criterion 5 SAR tables, so the criteria member does not type them.

Modes (environment variable ERP_MODE):
- "demo"      (default) a DEMO connection with sample data shaped like a Juno Campus export. It shows exactly
              how the real link will behave; the screen says clearly that it is sample data.
- "database"  read-only connection to the ERP database (ERP_DB_URL). Needs the table structure and a read-only
              account from CMRIT IT; until then it reports "not connected".
- "off"       no ERP button.

Every connector returns records in the same shape as the faculty list (app/views/faculty.FIELDS), so the import,
the comparison and the proof re-checks are the same whatever the source.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

from flask import current_app


class NotConnected(Exception):
    pass


@dataclass
class ERPStatus:
    mode: str
    connected: bool
    label: str
    detail: str


class DemoERP:
    """Sample ERP data: the demo college's staff records plus a few changes since the last import
    (two new joinees, one person who left, one promotion), as a real ERP would have."""

    mode = "demo"

    def status(self) -> ERPStatus:
        return ERPStatus("demo", True, "Demo ERP connection (sample data)",
                         "Shows how the Juno Campus link works. The real link needs read-only access from CMRIT IT.")

    def faculty(self, program) -> list[dict]:
        from .demo import CURRENT_CAY, _faculty_rows

        rows = _faculty_rows(random.Random(5))
        by_name = {r["name"]: r for r in rows}
        # changes in the ERP since the department last imported (sample)
        if "AIML Faculty 005" in by_name:
            by_name["AIML Faculty 005"]["leaving_date"] = date(CURRENT_CAY, 10, 31).isoformat()
        if "CSE Faculty 010" in by_name and by_name["CSE Faculty 010"]["designation"] == "Assistant Professor":
            r = by_name["CSE Faculty 010"]
            r.update(designation="Associate Professor", designation_at_joining="Assistant Professor",
                     designated_on=date(CURRENT_CAY, 8, 1).isoformat())
        base = dict(rows[-3])
        for name, dept, allied in (("CSE Faculty 065", "CSE", 0), ("DS Faculty 031", "CSE (Data Science)", 1)):
            rows.append({**base, "name": name, "department": dept, "allied": allied, "designation": "Assistant Professor",
                         "designation_at_joining": "Assistant Professor", "designated_on": None, "leaving_date": None,
                         "joining_date": date(CURRENT_CAY, 8, 4).isoformat(), "qualification": "M.Tech", "phd_date": None,
                         "nature": "Regular", "full_time": 1, "first_year": 0, "is_hod": 0, "experience_years": "2"})
        return rows

    def students(self, program) -> dict:
        from .demo import STUDENTS

        return {k: v for k, v in STUDENTS.items() if k[0] in ("S", "DS")}


class DatabaseERP:
    """Read-only link to the ERP database. Filled in once CMRIT IT shares the table structure."""

    mode = "database"

    def __init__(self, url: str):
        self.url = url

    def status(self) -> ERPStatus:
        if not self.url:
            return ERPStatus("database", False, "ERP not connected yet",
                             "Set ERP_DB_URL to a read-only account on the Juno Campus database (from CMRIT IT).")
        return ERPStatus("database", False, "ERP database configured, mapping pending",
                         "The faculty and admission tables still have to be mapped to the SAR fields.")

    def faculty(self, program) -> list[dict]:
        raise NotConnected(self.status().detail)

    def students(self, program) -> dict:
        raise NotConnected(self.status().detail)


def connector():
    mode = current_app.config.get("ERP_MODE", "demo")
    if mode == "off":
        return None
    if mode == "database":
        return DatabaseERP(current_app.config.get("ERP_DB_URL", ""))
    return DemoERP()


COMPARE = ["designation", "designation_at_joining", "designated_on", "joining_date", "leaving_date", "qualification",
           "phd_date", "university", "department", "allied", "nature", "full_time", "first_year", "is_hod"]
LABELS = {"designation": "designation", "designation_at_joining": "designation at joining",
          "designated_on": "date designated", "joining_date": "joining date", "leaving_date": "leaving date",
          "qualification": "highest degree", "phd_date": "Ph.D. date", "university": "university",
          "department": "department", "allied": "allied", "nature": "nature", "full_time": "full-time",
          "first_year": "first-year load", "is_hod": "HoD"}


def compare(db, program, erp_rows: list[dict]) -> dict:
    """What an import would change: new people, changed fields, unchanged, and people not in the ERP."""
    from . import proofs as P
    from .views.faculty import clean_row

    have = {P.normalise_name(f["name"]): dict(f) for f in
            db.execute("SELECT * FROM faculty WHERE program_id = ?", (program["id"],))}
    new, changed, same, errors = [], [], 0, []
    seen = set()
    for raw in erp_rows:
        try:
            r = clean_row({k: ("" if v is None else v) for k, v in raw.items()}, program)
        except ValueError as e:
            errors.append(f"{raw.get('name', '?')}: {e}")
            continue
        key = P.normalise_name(r["name"])
        seen.add(key)
        cur = have.get(key)
        if cur is None:
            new.append(r)
            continue
        diffs = []
        for f in COMPARE:
            a, b = cur.get(f), r.get(f)
            if (a or None) != (b or None) and str(a or "") != str(b or ""):
                diffs.append({"field": LABELS[f], "old": a or "–", "new": b or "–"})
        if diffs:
            changed.append({"id": cur["id"], "row": r, "diffs": diffs})
        else:
            same += 1
    missing = [f for k, f in have.items() if k not in seen]
    return {"new": new, "changed": changed, "same": same, "missing": missing, "errors": errors}


def today_iso() -> str:
    return (date.today() + timedelta(0)).isoformat()
