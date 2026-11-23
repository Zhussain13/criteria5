"""Faculty list (NBA Table B2.A) -> the year-wise numbers of Criterion 5.

The department keeps ONE faculty list (typed in, or imported from the ERP). From it the app counts,
for each academic year, every number Criterion 5 needs: F, PoP, FF, Ph.D. / M.Tech counts, the cadre
(AF1, AF2, AF3), the retention bands (A-E), the HoD and Professor conditions of the pre-qualifiers.
So nobody counts by hand, and the numbers always agree with the list.

Counting rules (written down so the IQAC can check them; change the constants if NBA says otherwise):
- A person counts in an academic year if they were on the rolls for at least MIN_DAYS_IN_YEAR days
  of that year (between joining and leaving). VERIFY with IQAC.
- Designation and highest degree are taken as on the MID-YEAR date (1 January of the academic year):
  a faculty member promoted, or awarded a Ph.D., after that date counts with the old one that year.
- Years of service for retention are counted up to the last day of the academic year.
- F = full-time faculty (regular or contractual) in the department + allied departments, Professors of
  Practice excluded. PoP = full-time Professors of Practice (limited to 20% of required faculty S/20,
  as in NBA Annexure-I). FF = those with 100% first-year teaching load.
- Retention counts department faculty only (RF = DS/20), full-time, PoP excluded.

In "verified only" mode (what a strict peer team would accept) a person counts only when their
appointment letter has been checked, and a Ph.D. / M.Tech counts only when the degree certificate
has been checked; otherwise the Ph.D. holder is counted with no higher degree.
"""
from __future__ import annotations

from datetime import date, timedelta

from .years import AssessmentYears

MIN_DAYS_IN_YEAR = 183
YEARS = ["CAY", "CAYm1", "CAYm2"]

PROF, ASSOC, ASST, POP = "Professor", "Associate Professor", "Assistant Professor", "Professor of Practice"


def _d(s) -> date | None:
    if not s:
        return None
    if isinstance(s, date):
        return s
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _get(f, key, default=None):
    """Read a field from a dict or a sqlite3.Row."""
    try:
        v = f[key]
    except (KeyError, IndexError):
        return default
    return default if v is None else v


def cadre_of(designation: str) -> str:
    """Normalise a designation to Professor / Associate / Assistant / Professor of Practice / ''."""
    d = (designation or "").lower().replace(".", " ")
    if "practice" in d:
        return POP
    if "assoc" in d:
        return ASSOC
    if "asst" in d or "assistant" in d:
        return ASST
    if "prof" in d or "hod" in d or "head" in d or "principal" in d or "dean" in d:
        return PROF
    return ""


def degree_of(qualification: str) -> str:
    """'phd', 'mtech' (M.Tech / M.E. / M.S. Engg) or 'other'."""
    q = (qualification or "").lower().replace(".", "").replace(" ", "")
    if "phd" in q or "doctor" in q:
        return "phd"
    if q.startswith(("mtech", "me", "ms", "mengg")) or "mtech" in q or "masterofeng" in q or "masteroftech" in q:
        return "mtech"
    return "other"


def is_pop(f) -> bool:
    return (f["nature"] or "").lower().startswith("professor of practice") or cadre_of(f["designation"]) == POP


def is_full_time(f) -> bool:
    return bool(f["full_time"]) if f["full_time"] is not None else True


def is_regular(f) -> bool:
    return (f["nature"] or "Regular").lower().startswith("regular")


def in_dept(f) -> bool:
    return not f["allied"]


class Roster:
    """One program's faculty list, read year by year."""

    def __init__(self, faculty: list, years: AssessmentYears, proof_status: dict | None = None,
                 verified_only: bool = False):
        self.faculty = [dict(f) for f in faculty]
        self.years = years
        self.status = proof_status or {}
        self.verified_only = verified_only

    # --- per person, per year ---------------------------------------------------------------
    def bounds(self, y: str) -> tuple[date, date]:
        start = date(self.years.start_of(y), self.years.start_month, 1)
        nxt = date(start.year + 1, start.month, 1)
        return start, nxt - timedelta(days=1)

    def mid(self, y: str) -> date:
        a, _ = self.bounds(y)
        return date(a.year + 1, 1, 1) if a.month > 1 else date(a.year, 7, 1)

    def days_in(self, f, y: str) -> int:
        a, b = self.bounds(y)
        j, lv = _d(_get(f, "joining_date")), _d(_get(f, "leaving_date"))
        s = max(a, j) if j else a
        e = min(b, lv) if lv else b
        return max(0, (e - s).days + 1)

    def present(self, f, y: str) -> bool:
        if self.days_in(f, y) < MIN_DAYS_IN_YEAR:
            return False
        if self.verified_only and self.status.get(f["id"], {}).get("appointment") != "verified":
            return False
        return True

    def designation_in(self, f, y: str) -> str:
        """Cadre in that year: the joining designation until the date of designation (promotion)."""
        cur = cadre_of(_get(f, "designation"))
        on = _d(_get(f, "designated_on"))
        if on and on > self.mid(y) and _get(f, "designation_at_joining"):
            return cadre_of(f["designation_at_joining"])
        return cur

    def degree_in(self, f, y: str) -> str:
        deg = degree_of(_get(f, "qualification"))
        if deg == "phd":
            got = _d(_get(f, "phd_date"))
            if got and got > self.mid(y):
                deg = "mtech"  # the Ph.D. came later that year or after: counted with the master's degree
        if self.verified_only and deg != "other" and self.status.get(f["id"], {}).get("degree") != "verified":
            return "other"
        return deg

    def service_years(self, f, y: str) -> float:
        j = _d(_get(f, "joining_date"))
        if not j:
            return 0
        _, b = self.bounds(y)
        lv = _d(_get(f, "leaving_date"))
        end = min(b, lv) if lv else b
        return max(0, (end - j).days + 1) / 365.25

    # --- counts ------------------------------------------------------------------------------
    def teaching(self, y: str) -> list:
        """Full-time faculty in Department + allied, PoP excluded, present that year."""
        return [f for f in self.faculty if self.present(f, y) and is_full_time(f) and not is_pop(f)]

    def metrics(self, students: dict | None = None) -> dict:
        """{(key, year): value} for every `auto: roster` field. `students` holds S for the PoP limit."""
        students = students or {}
        out = {}
        for y in YEARS:
            ft = self.teaching(y)
            pops = [f for f in self.faculty if self.present(f, y) and is_full_time(f) and is_pop(f)]
            n_pop = len(pops)
            s = students.get(("S", y))
            if s:
                n_pop = min(n_pop, int(0.2 * s / 20))  # NBA: PoP counted up to 20% of required faculty
            cad = [self.designation_in(f, y) for f in ft]
            deg = [self.degree_in(f, y) for f in ft]
            dept = [f for f in ft if in_dept(f)]
            out.update({
                ("F", y): len(ft), ("POP", y): n_pop, ("FF", y): sum(1 for f in ft if _get(f, "first_year")),
                ("PHD", y): deg.count("phd"), ("MTECH", y): deg.count("mtech"),
                ("AF1", y): cad.count(PROF), ("AF2", y): cad.count(ASSOC), ("AF3", y): cad.count(ASST),
                ("AF", y): len(dept),
            })
            bands = {"RET_A": 0, "RET_B": 0, "RET_C": 0, "RET_D": 0, "RET_E": 0}
            for f in dept:
                sy = self.service_years(f, y)
                key = "RET_A" if sy < 1 else "RET_B" if sy < 2 else "RET_C" if sy < 3 else "RET_D" if sy < 4 else "RET_E"
                bands[key] += 1
            out.update({(k, y): v for k, v in bands.items()})
        for y in ("CAY", "CAYm1"):
            reg_phd = [f for f in self.teaching(y) if is_regular(f) and self.degree_in(f, y) == "phd"]
            out[("PROF_PHD", y)] = sum(1 for f in reg_phd if self.designation_in(f, y) == PROF)
            out[("ASSOC_PHD", y)] = sum(1 for f in reg_phd if self.designation_in(f, y) == ASSOC)
        hods = [f for f in self.faculty if _get(f, "is_hod") and in_dept(f) and self.present(f, "CAY")]
        out[("HOD_PHD", "CAY")] = 1 if any(is_regular(f) and self.degree_in(f, "CAY") == "phd" for f in hods) else 0
        return out

    def presence_table(self) -> list[dict]:
        """For the faculty page: in which counted years each person counts, with cadre and degree."""
        rows = []
        for f in self.faculty:
            rows.append({"id": f["id"], "years": {y: self.present(f, y) for y in YEARS},
                         "cadre": {y: self.designation_in(f, y) for y in YEARS},
                         "degree": {y: self.degree_in(f, y) for y in YEARS}})
        return rows


def adjunct_hours(claims: list) -> dict:
    """Total hours per year from SAR Table 5.4.1 rows (Year, name, designation, course, hours)."""
    import json

    out: dict = {}
    for c in claims:
        if c["item_code"] != "5.4":
            continue
        t = (json.loads(c["tables_json"] or "{}") or {}).get("5.4.1") or {}
        for row in t.get("rows", []):
            if len(row) < 5 or not row[0]:
                continue
            try:
                h = float(str(row[4]).strip() or 0)
            except ValueError:
                continue
            out[("ADJ_HOURS", row[0])] = out.get(("ADJ_HOURS", row[0]), 0) + h
    return out


def merged_metrics(db, program, verified_only: bool = False) -> tuple[dict, dict]:
    """Typed-in numbers + numbers counted from the faculty list and Table 5.4.1.
    Returns (metrics, auto) where auto is the part that was counted by the app."""
    from .faculty_proofs import proof_status

    pid = program["id"]
    typed = {(r["key"], r["year"]): r["value"] for r in db.execute(
        "SELECT key, year, value FROM metrics WHERE program_id = ?", (pid,))}
    faculty = db.execute("SELECT * FROM faculty WHERE program_id = ?", (pid,)).fetchall()
    years = AssessmentYears(program["cay_start_year"], program["ay_start_month"])
    auto: dict = {}
    if faculty:
        auto.update(Roster(faculty, years, proof_status(db, pid), verified_only).metrics(typed))
    claims = db.execute("SELECT item_code, tables_json FROM claims WHERE program_id = ? AND item_code = '5.4'",
                        (pid,)).fetchall()
    auto.update(adjunct_hours(claims))
    return {**typed, **auto}, auto
