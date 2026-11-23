"""Link to the college ERP (Juno Campus at CMRIT): read the faculty list and student strength straight into
the Criterion 5 SAR tables, so the criteria member does not type them.

Modes (environment variable ERP_MODE):
- "demo"      (default) a DEMO connection with sample data shaped like a Juno Campus export. It shows exactly
              how the real link will behave; the screen says clearly that it is sample data.
- "database"  read-only connection to the ERP database (ERP_DB_URL). Needs the table structure and a read-only
              account from CMRIT IT; until then it reports "not connected".
- "off"       no ERP button.

The ERP keeps its data academic year by academic year. The import works the same way: pick the ERP academic year
(e.g. 2023-24) and the SAR year it belongs to (e.g. CAYm2); that year's staff list (who was on the rolls, with the
designation, degree and load of that year) and that year's student strength go into that SAR year.

Every connector returns records in the same shape as the faculty list (app/views/faculty.FIELDS) plus a
"this_year" part, so the import, the comparison and the proof re-checks are the same whatever the source.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

from flask import current_app

from .years import AssessmentYears, ay_label


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

    # ---- year-wise data, as the ERP stores it -------------------------------------------------
    STUDENTS_BY_YEAR = {0: (2160, 1200), 1: (2040, 1140), 2: (1920, 1080), 3: (1800, 1020), 4: (1680, 960)}

    def years(self, program) -> list[int]:
        """Academic years (start year) the ERP has data for, newest first."""
        from .demo import CURRENT_CAY

        return [CURRENT_CAY - i for i in range(5)]

    def year_data(self, program, start: int) -> dict:
        """The ERP's records for one academic year: staff on the rolls that year (with that year's designation,
        degree and load) and the student strength of that year."""
        from .demo import CURRENT_CAY
        from .roster import Roster, cadre_of

        years = AssessmentYears(start)
        master = self.faculty(program)
        ros = Roster([{**r, "id": i} for i, r in enumerate(master)], years)
        staff = []
        for i, r in enumerate(master):
            f = {**r, "id": i}
            if not ros.present(f, "CAY"):
                continue
            deg = {"phd": "Ph.D."}.get(ros.degree_in(f, "CAY"), r["qualification"] if r["qualification"] != "Ph.D." else "M.Tech")
            this = {"designation": ros.designation_in(f, "CAY") or cadre_of(r["designation"]), "qualification": deg,
                    "nature": r["nature"], "full_time": r["full_time"], "first_year": r["first_year"], "allied": r["allied"]}
            staff.append({**r, "this_year": this})
        s, ds = self.STUDENTS_BY_YEAR.get(CURRENT_CAY - start, (0, 0))
        return {"faculty": staff, "students": {"S": s, "DS": ds}}


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

    def years(self, program) -> list[int]:
        raise NotConnected(self.status().detail)

    def year_data(self, program, start: int) -> dict:
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


def year_preview(db, program, con, start: int, slot: str) -> dict:
    """What importing ERP academic year `start` into SAR year `slot` would do."""
    from .roster import Roster, merged_metrics

    data = con.year_data(program, start)
    diff = compare(db, program, [{k: v for k, v in r.items() if k != "this_year"} for r in data["faculty"]])
    # the counts that year's ERP list gives, next to what the SAR shows now
    recs = [{**r, "id": i} for i, r in enumerate(data["faculty"])]
    years = AssessmentYears(program["cay_start_year"], program["ay_start_month"])
    tmp = Roster(recs, years, snapshots={slot: {i: r["this_year"] for i, r in enumerate(data["faculty"])}})
    stu = {("S", slot): data["students"]["S"], ("DS", slot): data["students"]["DS"]}
    after = tmp.metrics(stu) if slot in ("CAY", "CAYm1", "CAYm2") else {}
    now, _ = merged_metrics(db, program)
    keys = [("S", "Students, Department + allied (S)"), ("DS", "Students, Department alone (DS)"),
            ("F", "Full-time faculty (F)"), ("PHD", "Faculty with Ph.D."), ("MTECH", "Faculty with M.Tech/M.E."),
            ("AF1", "Professors"), ("AF2", "Associate Professors"), ("AF3", "Assistant Professors"),
            ("FF", "100% first-year load (FF)")]
    rows = []
    for k, label in keys:
        new = stu.get((k, slot)) if k in ("S", "DS") else after.get((k, slot))
        if new is None:
            continue
        rows.append({"key": k, "label": label, "now": now.get((k, slot)), "erp": new})
    return {"ay": start, "ay_label": ay_label(start), "slot": slot, "slot_label": years.label(slot),
            "match": ay_label(start) == years.label(slot), "staff": len(data["faculty"]), "diff": diff,
            "rows": rows, "data": data}


def import_year(db, program, con, start: int, slot: str, user_id, with_students: bool = True) -> str:
    """Put ERP academic year `start` into SAR year `slot`: staff list (people + that year's details) and
    student strength. Returns a one-line summary."""
    from . import proofs as P
    from .faculty_proofs import recheck_faculty
    from .views.faculty import save_row

    pv = year_preview(db, program, con, start, slot)
    for r in pv["diff"]["new"]:
        save_row(db, program["id"], r, source="erp")
    for ch in pv["diff"]["changed"]:
        save_row(db, program["id"], ch["row"], ch["id"], source="erp")
        recheck_faculty(db, program, ch["id"])
    ids = {P.normalise_name(f["name"]): f["id"] for f in
           db.execute("SELECT id, name FROM faculty WHERE program_id = ?", (program["id"],))}
    db.execute("DELETE FROM faculty_year WHERE year = ? AND faculty_id IN (SELECT id FROM faculty WHERE program_id = ?)",
               (slot, program["id"]))
    for r in pv["data"]["faculty"]:
        t = r["this_year"]
        db.execute("INSERT OR REPLACE INTO faculty_year (faculty_id, year, ay_label, designation, qualification, nature, "
                   "full_time, first_year, allied) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (ids[P.normalise_name(r["name"])], slot, pv["ay_label"], t["designation"], t["qualification"],
                    t["nature"], t["full_time"], t["first_year"], t["allied"]))
    if with_students:
        for k in ("S", "DS"):
            db.execute("INSERT INTO metrics (program_id, key, year, value, updated_by) VALUES (?, ?, ?, ?, ?) "
                       "ON CONFLICT(program_id, key, year) DO UPDATE SET value = excluded.value, "
                       "updated_by = excluded.updated_by, updated_at = datetime('now')",
                       (program["id"], k, slot, pv["data"]["students"][k], user_id))
    summary = (f"ERP {pv['ay_label']} into {slot}: {pv['staff']} staff that year, {len(pv['diff']['new'])} new, "
               f"{len(pv['diff']['changed'])} updated" + (", student strength" if with_students else ""))
    db.execute("INSERT INTO erp_imports (program_id, year, ay_label, user_id, summary) VALUES (?, ?, ?, ?, ?)",
               (program["id"], slot, pv["ay_label"], user_id, summary))
    return summary


def last_imports(db, program_id: int) -> dict:
    """{SAR year: latest import row} for showing "from ERP 2023-24" next to each year."""
    out = {}
    for r in db.execute("SELECT e.*, u.name AS user_name FROM erp_imports e LEFT JOIN users u ON u.id = e.user_id "
                        "WHERE e.program_id = ? ORDER BY e.id", (program_id,)):
        out[r["year"]] = dict(r)
    return out
