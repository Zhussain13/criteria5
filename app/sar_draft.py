"""The website writes the Criterion 5 SAR itself, in the NBA SAR format (Tier I, April 2026), from the data. No AI.

Each section (5.1 to 5.5) has two parts:
- the SAR PART (live): NBA's instruction line, the department's statement with its own numbers, the NBA tables
  (Table No. 5A, 5.1.2, 5.2.1, 5.3.1, 5.4.1, 5.5.1) and the assessment. It is rebuilt every time from the faculty
  list, the ERP data and the data sheet, so the moment any data changes, the SAR changes with it.
- the DEPARTMENT'S WRITE-UP (typed by people): what only the department can say (policies, measures, courses).
  The website writes a first draft; members polish it like a Google Doc.
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
        return f"{int(round(v)):,}"
    return f"{v:,.{nd}f}"


def _e(s) -> str:
    return H.escape(str(s))


def _table(title: str, head: list, rows: list[list], foot: list | None = None, bold_rows: set | None = None) -> str:
    """head: list of str, or list of header rows of (text, colspan, rowspan)."""
    if head and isinstance(head[0], list):
        th = "".join("<tr>" + "".join(f'<th colspan="{c}" rowspan="{r}">{_e(t)}</th>' for t, c, r in hr) + "</tr>"
                     for hr in head)
    else:
        th = "<tr>" + "".join(f"<th>{_e(h)}</th>" for h in head) + "</tr>"
    body = ""
    for i, r in enumerate(rows):
        b = bold_rows and i in bold_rows
        body += "<tr>" + "".join(f"<td>{'<strong>' if b else ''}{_e(c)}{'</strong>' if b else ''}</td>" for c in r) + "</tr>"
    if foot:
        body += "<tr>" + "".join(f"<td><strong>{_e(c)}</strong></td>" for c in foot) + "</tr>"
    return (f'<p class="tcap"><strong>{_e(title)}</strong></p>'
            f'<table border="1" style="border-collapse:collapse;width:100%">{th}{body}</table>')


def _note(text: str) -> str:
    return f'<p class="nba-note"><em>{text}</em></p>'


def _assess(text: str) -> str:
    return f'<p class="assess"><strong>Assessment:</strong> {text}</p>'


class Facts:
    """Every number the Criterion 5 sections talk about, worked out once."""

    def __init__(self, db, program):
        self.p = program
        self.years = AssessmentYears(program["cay_start_year"], program["ay_start_month"])
        self.m, self.auto = merged_metrics(db, program)
        self.lbl = {y: self.years.label(y) for y in ["CAY", "CAYm1", "CAYm2", "CAYm3"]}
        pid = program["id"]
        self.allied = sorted({r[0] for r in db.execute(
            "SELECT DISTINCT department FROM faculty WHERE program_id = ? AND allied = 1 AND department != ''", (pid,))})
        self.claims_54 = [dict(c) for c in db.execute(
            "SELECT * FROM claims WHERE program_id = ? AND item_code = '5.4' ORDER BY id", (pid,))]
        self.erp = {r["year"]: r["ay_label"] for r in db.execute(
            "SELECT year, ay_label FROM erp_imports WHERE program_id = ? ORDER BY id", (pid,))}
        self.classes = {}
        for r in db.execute("SELECT * FROM student_strength WHERE program_id = ? ORDER BY seq", (pid,)):
            self.classes.setdefault(r["year"], []).append(dict(r))
        self.faculty = [dict(f) for f in db.execute(
            "SELECT * FROM faculty WHERE program_id = ? ORDER BY allied, is_hod DESC, name", (pid,))]

    def g(self, k, y, d=0.0):
        return self.m.get((k, y), d) or 0.0

    def tf(self, y):
        return self.g("F", y) + self.g("POP", y) - self.g("FF", y)

    def sfr(self, y):
        return self.g("S", y) / self.tf(y) if self.tf(y) > 0 else None

    def fqi(self, y):
        rf = self.g("S", y) / 20
        return 2.5 * (10 * self.g("PHD", y) + 4 * self.g("MTECH", y)) / rf if rf else None

    def fr(self, y):
        rf = self.g("DS", y) / 20
        b, c, d, e = (self.g(k, y) for k in ("RET_B", "RET_C", "RET_D", "RET_E"))
        return min(10, (b + 2 * c + 3 * d + 4 * e) / rf * 2.5) if rf else None

    def avg(self, fn):
        vals = [v for v in (fn(y) for y in YEARS3) if v is not None]
        return sum(vals) / len(vals) if vals else None

    def avg_key(self, k):
        return sum(self.g(k, y) for y in YEARS3) / 3

    def sfr_marks(self):
        a = self.avg(self.sfr)
        if a is None:
            return 0
        return next((m for b, m in SFR_BANDS if a < b), 15 if a <= 25 else 0)

    def cadre_marks(self):
        rf = sum(self.g("S", y) / 20 for y in YEARS3) / 3
        a1, a2, a3 = self.avg_key("AF1"), self.avg_key("AF2"), self.avg_key("AF3")
        if not rf or (a1 == 0 and a2 == 0):
            return 0.0, rf
        return min(25, (a1 / (rf / 9) + 0.6 * a2 / (2 * rf / 9) + 0.4 * a3 / (6 * rf / 9)) * 12.5), rf

    def adj_marks(self):
        yrs = sum(3 for y in ("CAYm1", "CAYm2", "CAYm3") if self.g("ADJ_HOURS", y) >= 50)
        prov = 1 if any(self.g("ADJ_HOURS", y) > 0 for y in ("CAYm1", "CAYm2", "CAYm3")) or self.claims_54 else 0
        return min(10, prov + yrs)

    def dept(self) -> str:
        return self.p["department"]

    def cluster(self) -> str:
        return self.dept() + (" and the allied departments " + ", ".join(self.allied) if self.allied else "")

    def source_line(self) -> str:
        if self.erp:
            order = ["CAY", "CAYm1", "CAYm2", "CAYm3"]
            return ("Source: institute ERP (" + ", ".join(f"{y} from {a}" for y, a in sorted(
                self.erp.items(), key=lambda x: order.index(x[0]))) + ") and the faculty list, Table No. 5A.")
        return "Source: the faculty list (Table No. 5A) and the department's data sheet."


# ------------------------------------------------------------------ the SAR part (live, never typed)

def table_5a(f: Facts) -> str:
    rows = []
    for i, x in enumerate(f.faculty, 1):
        rows.append([i, x["name"], x["apaar_id"] or "–", x["qualification"], x["university"] or "–",
                     x["specialization"] or "–", x["joining_date"] or "", x["designation_at_joining"] or x["designation"],
                     x["designation"], x["designated_on"] or "–", x["nature"] + ("" if x["full_time"] else " (part time)"),
                     "N" if x["leaving_date"] else "Y", x["leaving_date"] or "–",
                     (x["department"] or f.dept()) + (" (allied)" if x["allied"] else "")])
    return _table("Table No. 5A: Faculty details (Department and allied departments)",
                  ["S.N.", "Name of the faculty", "APAAR faculty ID", "Highest degree", "University",
                   "Area of specialization", "Date of joining in this institution", "Designation at time of joining",
                   "Present designation", "Date designated as Professor / Associate Professor", "Nature of association",
                   "Currently associated (Y/N)", "Date of leaving", "Department"], rows)


def _sfr_table(f: Facts) -> str:
    L = f.lbl
    cls = {y: f.classes.get(y, []) for y in YEARS3}
    ref = next((cls[y] for y in YEARS3 if cls[y]), [])
    ug = [c for c in ref if c["level"] == "UG"]
    pg = [c for c in ref if c["level"] == "PG"]
    out = ""
    if ref:
        out += ("<p>No. of UG (Engineering) programs in the Department including allied departments/clusters (UGn): "
                f"<strong>{len(ug)}</strong> – " + "; ".join(f"UG{i} = {_e(c['name'])}" for i, c in enumerate(ug, 1))
                + f".<br>No. of PG (Engineering) programs (PGm): <strong>{len(pg)}</strong>"
                + (" – " + "; ".join(f"PG{i} = {_e(c['name'])}" for i, c in enumerate(pg, 1)) if pg else "") + ".</p>")

    def by(y, seq):
        return next((c for c in cls[y] if c["seq"] == seq), None)

    rows, bold = [], set()
    for i, c in enumerate(ug, 1):
        for lab, key in (("B", "c1"), ("C", "c2"), ("D", "c3")):
            rows.append([f"UG{i}. {lab}"] + [_n(by(y, c["seq"])[key]) if by(y, c["seq"]) else "–" for y in YEARS3])
        bold.add(len(rows))
        rows.append([f"UG{i} ({c['name']})"] + [_n(sum(by(y, c["seq"])[k] for k in ("c1", "c2", "c3")))
                                                if by(y, c["seq"]) else "–" for y in YEARS3])
    for i, c in enumerate(pg, 1):
        for lab, key in (("A", "c1"), ("B", "c2")):
            rows.append([f"PG{i}. {lab}"] + [_n(by(y, c["seq"])[key]) if by(y, c["seq"]) else "–" for y in YEARS3])
        bold.add(len(rows))
        rows.append([f"PG{i} ({c['name']})"] + [_n(by(y, c["seq"])["c1"] + by(y, c["seq"])["c2"])
                                                if by(y, c["seq"]) else "–" for y in YEARS3])
    base = len(rows)
    rows += [
        ["DS = Total no. of students in the Department"] + [_n(f.g("DS", y)) for y in YEARS3],
        ["AS = Total no. of students in allied departments"] + [_n(f.g("S", y) - f.g("DS", y)) for y in YEARS3],
        ["S = Total no. of students in the Department (DS) and allied departments (AS)"] +
        [f"S{i} = {_n(f.g('S', y))}" for i, y in enumerate(YEARS3, 1)],
        ["DF = No. of faculty members in the Department"] + [_n(f.g("AF", y)) for y in YEARS3],
        ["AF = No. of faculty members in the allied departments"] + [_n(f.g("F", y) - f.g("AF", y)) for y in YEARS3],
        ["F = Total no. of faculty members in the Department (DF) and allied departments (AF)"] +
        [f"F{i} = {_n(f.g('F', y))}" for i, y in enumerate(YEARS3, 1)],
        ["PoP = Total no. of PoP in the Department and allied departments"] + [_n(f.g("POP", y)) for y in YEARS3],
        ["FF = Faculty in F with a 100% teaching load in first-year courses"] + [_n(f.g("FF", y)) for y in YEARS3],
        ["TF = Total faculty = F + PoP − FF"] + [_n(f.tf(y)) for y in YEARS3],
        ["Student Faculty Ratio (SFR) = S/TF"] + [f"SFR{i} = {_n(f.g('S', y))}/{_n(f.tf(y))} = {_n(f.sfr(y))}"
                                                  for i, y in enumerate(YEARS3, 1)],
    ]
    bold |= {base + 2, base + 5, base + 8, base + 9}
    avg = f.avg(f.sfr)
    out += _table("Table No. 5.1.2: Student-faculty ratio", ["Year"] + [f"{y} ({L[y]})" for y in YEARS3], rows,
                  ["Average SFR for 3 years", f"Average SFR = ({' + '.join(_n(f.sfr(y)) for y in YEARS3)})/3 = {_n(avg)}",
                   "", ""], bold_rows=bold)
    if not ref:
        out += _note("Class-wise strength (UG B, C, D; PG A, B) is filled in when a year is imported from the ERP.")
    return out


def auto_html(code: str, f: Facts) -> str:
    L = f.lbl
    ycols = [f"{y} ({L[y]})" for y in YEARS3]
    if code == "5.1":
        avg = f.avg(f.sfr)
        return (_note("(SFR to be calculated at Department level considering all UG and PG engineering programs in the "
                      "Department; include allied department programs/clusters as well.)")
                + _sfr_table(f)
                + _assess(f"Average SFR = <strong>{_n(avg)}</strong> → <strong>{f.sfr_marks()} / 30</strong> "
                          "(SFR &lt; 15: 30, &lt; 17: 27, &lt; 19: 24, &lt; 21: 21, &lt; 23: 18, &lt; 25: 15, &gt; 25: 0).")
                + f'<p class="src">{_e(f.source_line())}</p>')
    if code == "5.2":
        rows = [[f"{y} ({L[y]})", _n(f.g("PHD", y)), _n(f.g("MTECH", y)), _n(f.g("S", y) / 20, 1), _n(f.fqi(y))]
                for y in YEARS3]
        avg = f.avg(f.fqi)
        return (_note("FQI = 2.5 × [(10X + 4Y)/RF]; X = faculty with Ph.D. or equivalent, Y = faculty with M.Tech/M.E. or "
                      "equivalent, RF = required faculty for a 20:1 SFR, RF = S/20 (Department including allied departments).")
                + _table("Table No. 5.2.1: Faculty qualification", ["Year", "X", "Y", "RF", "FQI = 2.5 * [(10X + 4Y)/RF]"],
                         rows, ["Average assessment", "", "", "", _n(avg)])
                + _assess(f"Average FQI = <strong>{_n(avg)}</strong> → <strong>{_n(min(25, avg or 0))} / 25</strong>."))
    if code == "5.3":
        rows = []
        for y in YEARS3:
            rf = f.g("S", y) / 20
            rows.append([f"{y} ({L[y]})", _n(rf / 9), _n(f.g("AF1", y)), _n(2 * rf / 9), _n(f.g("AF2", y)),
                         _n(6 * rf / 9), _n(f.g("AF3", y))])
        marks, rf = f.cadre_marks()
        foot = ["Average numbers", f"RF1 = {_n(rf / 9)}", f"AF1 = {_n(f.avg_key('AF1'))}", f"RF2 = {_n(2 * rf / 9)}",
                f"AF2 = {_n(f.avg_key('AF2'))}", f"RF3 = {_n(6 * rf / 9)}", f"AF3 = {_n(f.avg_key('AF3'))}"]
        head = [[("Year", 1, 2), ("Professors", 2, 1), ("Associate Professors", 2, 1), ("Assistant Professors", 2, 1)],
                [("Required faculty (RF1)", 1, 1), ("Available faculty (AF1)", 1, 1), ("Required faculty (RF2)", 1, 1),
                 ("Available faculty (AF2)", 1, 1), ("Required faculty (RF3)", 1, 1), ("Available faculty (AF3)", 1, 1)]]
        return (_note("Faculty cadre proportion is 1 (RF1) : 2 (RF2) : 6 (RF3) of the faculty required for a 20:1 "
                      "student-faculty ratio (S of section 5.1). Cadre, qualification and experience as per AICTE/UGC norms.")
                + _table("Table No. 5.3.1: Faculty cadre proportion details", head, rows, foot)
                + _assess("Faculty cadre proportion marks = [(AF1/RF1) + (AF2/RF2) × 0.6 + (AF3/RF3) × 0.4] × 12.5 = "
                          f"<strong>{_n(marks)} / 25</strong> (limited to 25; zero if AF1 = AF2 = 0)."))
    if code == "5.4":
        by_year: dict = {}
        for c in f.claims_54:
            t = (json.loads(c["tables_json"] or "{}") or {}).get("5.4.1") or {}
            for r in t.get("rows", []):
                if r and r[0]:
                    by_year.setdefault(r[0], []).append(r)
        rows, bold = [], set()
        for y in ("CAYm1", "CAYm2", "CAYm3"):
            bold.add(len(rows))
            rows.append([f"{y} ({L[y]})", "", "", "", ""])
            for i, r in enumerate(by_year.get(y, []), 1):
                rows.append([i] + list(r[1:5]))
            if not by_year.get(y):
                rows.append(["–", "No entries", "", "", ""])
            bold.add(len(rows))
            rows.append(["", "Total no. of hours:", "", "", _n(f.g("ADJ_HOURS", y))])
        return (_note("(Participation and contributions in teaching, learning or practical work by visiting, adjunct and "
                      "emeritus faculty, Professors of Practice engaged on an hourly or part-time basis, from industry, "
                      "research organisations and reputed institutions, during the assessment period.)")
                + _table("Table No. 5.4.1: List of visiting/adjunct faculty/professor of practice and their teaching and "
                         "practical loads", ["S.N.", "Name of the person", "Designation & organization", "Name of the course",
                                             "No. of hours handled"], rows, bold_rows=bold)
                + _assess("Provision of visiting/adjunct faculty: 1 mark; 3 marks for every year with 50 or more hours ("
                          + "; ".join(f"{L[y]}: {_n(f.g('ADJ_HOURS', y))} h" for y in ("CAYm1", "CAYm2", "CAYm3"))
                          + f") → <strong>{f.adj_marks()} / 10</strong>."))
    if code == "5.5":
        rows = [
            ["RF = No. of required faculty in the Department (RF = DS/20)"] +
            [f"{_n(f.g('DS', y))}/20 = {_n(f.g('DS', y) / 20)}" for y in YEARS3],
            ["AF = No. of available faculty members in the Department"] + [_n(f.g("AF", y)) for y in YEARS3],
            ["A = faculty at the current institute with less than 1 year"] + [_n(f.g("RET_A", y)) for y in YEARS3],
            ["B = more than 1 year and less than 2 years"] + [_n(f.g("RET_B", y)) for y in YEARS3],
            ["C = more than 2 years and less than 3 years"] + [_n(f.g("RET_C", y)) for y in YEARS3],
            ["D = more than 3 years and less than 4 years"] + [_n(f.g("RET_D", y)) for y in YEARS3],
            ["E = more than 4 years"] + [_n(f.g("RET_E", y)) for y in YEARS3],
            ["FR = (((A*0)+(B*1)+(C*2)+(D*3)+(E*4))/RF) * 2.50 (points limited to 10)"] +
            [f"FR_{i} = {_n(f.fr(y))}" for i, y in enumerate(YEARS3, 1)],
        ]
        avg = f.avg(f.fr)
        return (_table("Table No. 5.5.1: Faculty retention ratio", ["Item"] + ycols, rows,
                       ["Average FR = ((FR_1 + FR_2 + FR_3)/3) (marks limited to 10)", _n(avg), "", ""], bold_rows={7})
                + _assess(f"Average FR = <strong>{_n(avg)} / 10</strong>."))
    return ""


def tables_html(code: str, f: Facts) -> str:   # older name, still used by the Word export
    return auto_html(code, f)


# ------------------------------------------------------------------ the department's write-up (drafted, then edited)

def draft_html(code: str, f: Facts) -> str:
    L = f.lbl

    def P(s):
        return f"<p>{s}</p>"
    if code == "5.1":
        return (P(f"The student-faculty ratio is calculated for the Department of {_e(f.cluster())}, considering all UG and "
                  "PG engineering programs. Students admitted under supernumerary quotas are not counted, lateral entry is "
                  "limited to 10% of the sanctioned intake, and faculty with a 100% first-year teaching load are excluded, "
                  "as the NBA SAR format requires.")
                + P(f"The SFR was {_n(f.sfr('CAY'))} in {L['CAY']}, {_n(f.sfr('CAYm1'))} in {L['CAYm1']} and "
                    f"{_n(f.sfr('CAYm2'))} in {L['CAYm2']}, an average of {_n(f.avg(f.sfr))}. "
                    "<em>Add the department's plan to keep the SFR within 20:1 (recruitment approved for the next year).</em>"))
    if code == "5.2":
        return (P(f"The Department and its allied departments had {_n(f.g('PHD', 'CAY'))} faculty with a Ph.D. and "
                  f"{_n(f.g('MTECH', 'CAY'))} with an M.Tech/M.E. in {L['CAY']}. Every degree is supported by the degree "
                  "certificate in the faculty file (NBA List of Documents, P.21).")
                + P("<em>Add how the institute supports faculty to complete a Ph.D. (study leave, research centre, "
                    "fee support) and the number of faculty pursuing a Ph.D.</em>"))
    if code == "5.3":
        return (P(f"In {L['CAY']} the Department and its allied departments had {_n(f.g('AF1', 'CAY'))} Professors, "
                  f"{_n(f.g('AF2', 'CAY'))} Associate Professors and {_n(f.g('AF3', 'CAY'))} Assistant Professors, appointed "
                  "and promoted as per AICTE/UGC norms through duly constituted selection committees.")
                + P("<em>Add the career-advancement and recruitment steps planned to meet the 1:2:6 cadre ratio.</em>"))
    if code == "5.4":
        return (P("The department invites experts from industry, research organisations and reputed institutions as "
                  "visiting/adjunct faculty and professors of practice to handle courses and laboratories, so that students "
                  "learn current industry practice.")
                + P("<em>Add the courses handled and the outcomes of these sessions (projects, internships, feedback).</em>"))
    if code == "5.5":
        return (P(f"{_n(f.g('RET_E', 'CAY'))} of the {_n(f.g('AF', 'CAY'))} faculty of the department in {L['CAY']} have "
                  "served the institute for more than four years.")
                + P("<em>Add the measures that help retain faculty: career growth, research and seed-money support, "
                    "FDP sponsorship, welfare and recognition.</em>"))
    return ""


def section_codes(program) -> list[str]:
    rules = load_rules(program["sar_version"])
    return sorted(rules["items"], key=lambda c: [int(x) for x in c.split(".")])


def draft_all(db, program, user_id, codes=None, overwrite: bool = False, only_drafted: bool = False) -> int:
    """Write the drafted write-up of every section that is empty or still untouched (or all, with overwrite).
    only_drafted: refresh only sections that already hold the website's draft (used to follow data changes)."""
    f = Facts(db, program)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    n = 0
    for code in codes or section_codes(program):
        row = db.execute("SELECT * FROM sar_sections WHERE program_id = ? AND code = ?", (program["id"], code)).fetchone()
        if row and not overwrite and row["html"].strip() and not row["drafted"]:
            continue  # a person has written this section: leave it
        if only_drafted and not (row and row["drafted"]):
            continue
        html = draft_html(code, f)
        if row and row["html"] == html:
            continue
        db.execute("INSERT INTO sar_sections (program_id, code, html, drafted, version, updated_by, updated_at) "
                   "VALUES (?, ?, ?, 1, 1, ?, ?) ON CONFLICT(program_id, code) DO UPDATE SET html = excluded.html, "
                   "drafted = 1, version = sar_sections.version + 1, updated_by = excluded.updated_by, "
                   "updated_at = excluded.updated_at", (program["id"], code, html, user_id, now))
        n += 1
    return n
