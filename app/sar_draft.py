"""The website writes the Criterion 5 SAR itself, from the data (no AI).

Each section (5.1 to 5.5) has two parts:
- the TEXT: drafted here from fixed NBA-style sentences filled with the department's own numbers. The team can
  edit it like a Google Doc; once a person changes it, a later "draft again" leaves it alone unless the criteria
  head or HoD chooses to re-draft that section.
- the TABLES: the NBA tables with the numbers, built fresh every time from the faculty list, the ERP data and
  the data sheet. Nobody types them and they can never be out of date.
"""
from __future__ import annotations

import html as H
import json
from datetime import datetime, timezone

from .roster import merged_metrics
from .rules import load as load_rules
from .years import AssessmentYears

YEARS3 = ["CAY", "CAYm1", "CAYm2"]
SFR_BANDS = [(15, 30), (17, 27), (19, 24), (21, 21), (23, 18), (25, 15)]


def _n(v, nd=2) -> str:
    if v is None:
        return "–"
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:.{nd}f}"


def _table(title: str, head: list[str], rows: list[list], foot: list | None = None) -> str:
    th = "".join(f"<th>{H.escape(h)}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{H.escape(str(c))}</td>" for c in r) + "</tr>" for r in rows)
    if foot:
        body += "<tr>" + "".join(f"<td><strong>{H.escape(str(c))}</strong></td>" for c in foot) + "</tr>"
    return (f'<p class="tcap"><strong>{H.escape(title)}</strong></p>'
            f'<table border="1" style="border-collapse:collapse;width:100%"><tr>{th}</tr>{body}</table>')


class Facts:
    """Every number the Criterion 5 sections talk about, worked out once."""

    def __init__(self, db, program):
        self.p = program
        self.years = AssessmentYears(program["cay_start_year"], program["ay_start_month"])
        self.m, self.auto = merged_metrics(db, program)
        self.lbl = {y: self.years.label(y) for y in ["CAY", "CAYm1", "CAYm2", "CAYm3"]}
        allied = {r[0] for r in db.execute("SELECT DISTINCT department FROM faculty WHERE program_id = ? AND allied = 1 "
                                           "AND department != ''", (program["id"],))}
        self.allied = sorted(allied)
        self.claims_54 = [dict(c) for c in db.execute(
            "SELECT * FROM claims WHERE program_id = ? AND item_code = '5.4' ORDER BY id", (program["id"],))]
        self.erp = {r["year"]: r["ay_label"] for r in db.execute(
            "SELECT year, ay_label FROM erp_imports WHERE program_id = ? ORDER BY id", (program["id"],))}

    def g(self, k, y, d=0.0):
        return self.m.get((k, y), d) or 0.0

    def sfr(self, y):
        tf = self.g("F", y) + self.g("POP", y) - self.g("FF", y)
        return self.g("S", y) / tf if tf > 0 else None

    def fqi(self, y):
        rf = self.g("S", y) / 20
        return 2.5 * (10 * self.g("PHD", y) + 4 * self.g("MTECH", y)) / rf if rf else None

    def fr(self, y):
        rf = self.g("DS", y) / 20
        b, c, d, e = (self.g(k, y) for k in ("RET_B", "RET_C", "RET_D", "RET_E"))
        return min(10, (b + 2 * c + 3 * d + 4 * e) / rf * 2.5) if rf else None

    def avg(self, fn):
        vals = [fn(y) for y in YEARS3]
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None

    def depts(self) -> str:
        own = self.p["department"]
        return own + (" and the allied departments " + ", ".join(self.allied) if self.allied else "")

    def source(self) -> str:
        from_erp = [f"{y} ({a})" for y, a in self.erp.items()]
        return ("The faculty and student numbers were read from the institute ERP for " + ", ".join(from_erp) +
                " and counted from the faculty list (Table B2.A)." if from_erp else
                "The faculty numbers are counted from the faculty list (Table B2.A).")


# ------------------------------------------------------------------ tables (always from the data)

def tables_html(code: str, f: Facts) -> str:
    L = f.lbl
    if code == "5.1":
        rows = [[f"{y} ({L[y]})", _n(f.g("S", y)), _n(f.g("F", y)), _n(f.g("POP", y)), _n(f.g("FF", y)), _n(f.sfr(y))]
                for y in YEARS3]
        return _table("Table 5.1: Student-faculty ratio (Department + allied departments)",
                      ["Year", "S (students)", "F (full-time faculty)", "PoP", "FF (100% first year)", "SFR = S/(F+PoP-FF)"],
                      rows, ["Average SFR", "", "", "", "", _n(f.avg(f.sfr))])
    if code == "5.2":
        rows = [[f"{y} ({L[y]})", _n(f.g("PHD", y)), _n(f.g("MTECH", y)), _n(f.g("S", y) / 20, 1), _n(f.fqi(y))]
                for y in YEARS3]
        return _table("Table 5.2: Faculty qualification", ["Year", "X (Ph.D.)", "Y (M.Tech/M.E.)", "RF = S/20",
                                                           "FQI = 2.5 x (10X + 4Y)/RF"],
                      rows, ["Average FQI", "", "", "", _n(f.avg(f.fqi))])
    if code == "5.3":
        rows = []
        for y in YEARS3:
            rf = f.g("S", y) / 20
            rows.append([f"{y} ({L[y]})", _n(rf / 9, 1), _n(f.g("AF1", y)), _n(2 * rf / 9, 1), _n(f.g("AF2", y)),
                         _n(6 * rf / 9, 1), _n(f.g("AF3", y))])
        return _table("Table 5.3: Faculty cadre proportion (required RF1:RF2:RF3 = 1:2:6)",
                      ["Year", "Required Professors (RF1)", "Available (AF1)", "Required Assoc. Prof. (RF2)",
                       "Available (AF2)", "Required Asst. Prof. (RF3)", "Available (AF3)"], rows)
    if code == "5.4":
        rows = []
        for c in f.claims_54:
            t = (json.loads(c["tables_json"] or "{}") or {}).get("5.4.1") or {}
            rows += [[r[0] + (f" ({L.get(r[0], '')})" if r[0] in L else "")] + list(r[1:5]) for r in t.get("rows", []) if r and r[0]]
        out = _table("Table 5.4.1: Visiting / adjunct faculty / professors of practice and their teaching loads",
                     ["Year", "Name of the person", "Designation & organisation", "Course", "Hours"],
                     rows or [["–", "No entries yet", "", "", ""]])
        hrs = [[f"{y} ({L[y]})", _n(f.g("ADJ_HOURS", y)), "Yes" if f.g("ADJ_HOURS", y) >= 50 else "No"]
               for y in ("CAYm1", "CAYm2", "CAYm3")]
        return out + _table("Table 5.4.2: Hours per year", ["Year", "Total hours", "50 hours or more"], hrs)
    if code == "5.5":
        rows = [[f"{y} ({L[y]})"] + [_n(f.g(k, y)) for k in ("RET_A", "RET_B", "RET_C", "RET_D", "RET_E")] +
                [_n(f.g("DS", y) / 20, 1), _n(f.fr(y))] for y in YEARS3]
        return _table("Table 5.5: Faculty retention (Department)",
                      ["Year", "A (<1 yr)", "B (1-2)", "C (2-3)", "D (3-4)", "E (>4)", "RF = DS/20",
                       "FR = ((B+2C+3D+4E)/RF) x 2.5"], rows, ["Average", "", "", "", "", "", "", _n(f.avg(f.fr))])
    return ""


# ------------------------------------------------------------------ text (drafted, then edited by people)

def draft_html(code: str, f: Facts) -> str:
    L, P = f.lbl, lambda s: f"<p>{s}</p>"
    yrs = ", ".join(f"{L[y]}" for y in YEARS3)
    if code == "5.1":
        vals = "; ".join(f"{_n(f.sfr(y))} in {L[y]}" for y in YEARS3)
        avg = f.avg(f.sfr)
        marks = next((m for b, m in SFR_BANDS if avg is not None and avg < b), 15 if avg and avg <= 25 else 0)
        return (P(f"The student-faculty ratio (SFR) is worked out for the Department of {H.escape(f.depts())}, as the NBA "
                  "SAR format asks: SFR = S / (F + PoP − FF). S is the number of students in all UG and PG engineering "
                  "programs, F the full-time regular and contractual faculty, PoP the full-time Professors of Practice "
                  "(counted up to 20% of the required faculty) and FF the faculty with a 100% first-year teaching load.")
                + P(f"The SFR is {vals}. The average SFR for {yrs} is <strong>{_n(avg)}</strong>, which falls in the "
                    f"band that gives {marks} of 30 marks (Table 5.1).") + P(H.escape(f.source())))
    if code == "5.2":
        avg = f.avg(f.fqi)
        return (P("The faculty qualification index is FQI = 2.5 × (10X + 4Y) / RF, where X is the number of faculty with a "
                  "Ph.D., Y the number with an M.Tech / M.E. and RF the required faculty (S/20), for the Department and "
                  "its allied departments.")
                + P("The department had " + "; ".join(f"{_n(f.g('PHD', y))} Ph.D. and {_n(f.g('MTECH', y))} M.Tech/M.E. "
                                                      f"holders in {L[y]}" for y in YEARS3)
                    + f". The average FQI is <strong>{_n(avg)}</strong> (maximum 25; Table 5.2). Every degree is backed by "
                    "the degree certificate on the faculty list (NBA file P.21)."))
    if code == "5.3":
        return (P("The faculty cadre is compared with the NBA requirement of Professors, Associate Professors and Assistant "
                  "Professors in the ratio 1:2:6 of the required faculty (S/20), for the Department and allied departments.")
                + P("Available cadre: " + "; ".join(f"{L[y]}: {_n(f.g('AF1', y))} Professors, {_n(f.g('AF2', y))} "
                                                    f"Associate Professors, {_n(f.g('AF3', y))} Assistant Professors"
                                                    for y in YEARS3) + ". The comparison with the required numbers is in Table 5.3."))
    if code == "5.4":
        hrs = "; ".join(f"{_n(f.g('ADJ_HOURS', y))} hours in {L[y]}" for y in ("CAYm1", "CAYm2", "CAYm3"))
        return (P("The department invites visiting and adjunct faculty and professors of practice from industry to teach "
                  "courses and laboratories, so that students learn current practice.")
                + P(f"Their interaction with students was {hrs} (Table 5.4.1 and 5.4.2). NBA gives marks for every year "
                    "with at least 50 hours.") + P("<em>Add the names of the courses and the outcomes of these sessions.</em>"))
    if code == "5.5":
        avg = f.avg(f.fr)
        return (P("Faculty retention is measured on the department's faculty by years of service at this institute: A "
                  "less than one year, B one to two years, C two to three, D three to four and E more than four years. "
                  "FR = ((B + 2C + 3D + 4E) / RF) × 2.5, with RF = DS/20.")
                + P("In " + ", ".join(f"{L[y]}, {_n(f.g('RET_E', y))} faculty had served more than four years" for y in YEARS3)
                    + f". The average retention score is <strong>{_n(avg)}</strong> of 10 (Table 5.5).")
                + P("<em>Add the measures the institute takes to retain faculty (career growth, research support, "
                    "welfare).</em>"))
    return ""


def section_codes(program) -> list[str]:
    rules = load_rules(program["sar_version"])
    return sorted(rules["items"], key=lambda c: [int(x) for x in c.split(".")])


def draft_all(db, program, user_id, codes=None, overwrite: bool = False) -> int:
    """Write the drafted text of every section that is empty or still untouched (or all, with overwrite)."""
    f = Facts(db, program)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    n = 0
    for code in codes or section_codes(program):
        row = db.execute("SELECT * FROM sar_sections WHERE program_id = ? AND code = ?", (program["id"], code)).fetchone()
        if row and not overwrite and row["html"].strip() and not row["drafted"]:
            continue  # a person has written this section: leave it
        html = draft_html(code, f)
        if row and row["html"] == html:
            continue
        db.execute("INSERT INTO sar_sections (program_id, code, html, drafted, version, updated_by, updated_at) "
                   "VALUES (?, ?, ?, 1, 1, ?, ?) ON CONFLICT(program_id, code) DO UPDATE SET html = excluded.html, "
                   "drafted = 1, version = sar_sections.version + 1, updated_by = excluded.updated_by, "
                   "updated_at = excluded.updated_at", (program["id"], code, html, user_id, now))
        n += 1
    return n
