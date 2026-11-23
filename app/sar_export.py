"""Module 1 export: a draft SAR in Word, in the NBA order of the program's SAR format.

Built from what the department has entered: data sheets become the SAR tables (with the NBA
calculations worked out underneath), claims become lists with their proof files, written sections
come from the SAR text entered for each sub-criterion, and an evidence index numbers every proof
with the NBA List of Documents code (P.x / I.x) so the files can be put in folders for the visit.

Pure function: dicts in, .docx bytes out. No Flask, no database.
"""
from __future__ import annotations

import io
import re
from collections import defaultdict

from docx import Document
from docx.shared import Cm, Pt

from .richtext import add_html

from .engine import run_audit
from .rules import load as load_rules
from .years import YEAR_NAMES, AssessmentYears, display_name

TODO = "[To be written: no SAR text entered for this sub-criterion yet.]"


def _code_key(code: str):
    return [int(x) if x.isdigit() else x for x in re.split(r"[.]", code)]


def _evidence_key(code: str):
    m = re.match(r"([A-Z]+)\.(\d+)", code or "")
    return (m.group(1), int(m.group(2))) if m else ("Z", 0)


def _num(v) -> str:
    if v is None:
        return ""
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def _table(doc, header: list[str], rows: list[list[str]]):
    t = doc.add_table(rows=1, cols=len(header))
    t.style = "Light Grid Accent 1"
    for i, h in enumerate(header):
        t.rows[0].cells[i].text = h
    for r in rows:
        cells = t.add_row().cells
        for i, x in enumerate(r):
            cells[i].text = x
    return t


def _dates(c: dict) -> str:
    s, e = c.get("start_date") or "", c.get("end_date") or ""
    return f"{s} to {e}" if s and e and s != e else (s or e)


def _write_section(doc, c: dict):
    """One team member's text for a section, with the formatting they gave it in the editor."""
    if (c.get("sar_html") or "").strip():
        add_html(doc, c["sar_html"])
    else:
        for para in (c.get("sar_text") or "").strip().split("\n\n"):
            doc.add_paragraph(para.strip())


def _sar_tables(doc, spec: dict, filled: list[dict]):
    """One NBA SAR table (Table No. x.y), once per entry that filled it, or empty to be filled."""
    for t in filled or [None]:
        doc.add_paragraph().add_run(f"Table No. {spec['id']}: {spec['title']}").bold = True
        for name in spec["fields"]:
            doc.add_paragraph(f"{name}: {(t or {}).get('fields', {}).get(name, '')}")
        if spec["columns"]:
            n = len(spec["columns"])
            rows = [(r + [""] * n)[:n] for r in (t or {}).get("rows", [])] if t else \
                [[lab] + [""] * (n - 1) for lab in spec["rows"]] or [[""] * n]
            _table(doc, [c["name"] for c in spec["columns"]], rows)
        if not t:
            doc.add_paragraph("[To be filled: this table is required by the SAR format.]")
        if spec.get("note"):
            doc.add_paragraph(f"Note: {spec['note']}")


def evidence_numbers(rules: dict, claims: list[dict]) -> dict:
    """Number every proof file by its NBA document code: {proof key: 'P.12/3'}, in SAR order."""
    counter = defaultdict(int)
    out = {}
    for code in sorted(rules["items"], key=_code_key):
        ev = rules["items"][code].get("evidence_file") or "Other"
        for c in claims:
            if c["item_code"] != code:
                continue
            for i, p in enumerate(c.get("proofs", [])):
                counter[ev] += 1
                out[(c.get("id"), i)] = f"{ev}/{counter[ev]}"
    return out


DEFAULT_SETTINGS = {"font": "Times New Roman", "size": 12, "margin_top": 2.54, "margin_bottom": 2.54,
                    "margin_left": 2.54, "margin_right": 2.54}


def build_sar_docx(program: dict, claims: list[dict], faculty: list[dict], metrics: dict,
                   criteria: set | None = None, settings: dict | None = None, sections: dict | None = None) -> bytes:
    """sections: {code: (text html, tables html)} from the live SAR document; codes missing are left out."""
    """criteria: only these criteria (None = all). settings: font, size (pt) and margins (cm)."""
    rules = load_rules(program["sar_version"])
    cfg = {**DEFAULT_SETTINGS, **(settings or {})}
    years = AssessmentYears(program["cay_start_year"], program.get("ay_start_month", 7))
    report = run_audit(program, claims, faculty, metrics)       # for the worked calculations
    notes = {it["code"]: it["notes"] for c in report["criteria"] for it in c["items"]}
    ev_no = evidence_numbers(rules, claims)
    by_item = defaultdict(list)
    for c in claims:
        by_item[c["item_code"]].append(c)

    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = cfg["font"]
    st.font.size = Pt(float(cfg["size"]))
    for sec in doc.sections:
        sec.top_margin, sec.bottom_margin = Cm(float(cfg["margin_top"])), Cm(float(cfg["margin_bottom"]))
        sec.left_margin, sec.right_margin = Cm(float(cfg["margin_left"])), Cm(float(cfg["margin_right"]))

    doc.add_heading("Self Assessment Report (SAR) - draft", 0)
    doc.add_paragraph(f"{program.get('name', '')} ({program.get('department', '')}), {program.get('institute_name', '')}")
    doc.add_paragraph(f"SAR format: {rules['name']}. Current academic year (CAY): {years.label('CAY')}.")
    doc.add_paragraph("Draft generated by CMRSARathi from the data sheets, entries and proofs in the app. "
                      "Check every table and fill the sections marked 'To be written' before submission.")
    doc.add_heading("Assessment years", 1)
    _table(doc, ["Name", "Academic year", "From", "To"],
           [[display_name(r["name"]), r["label"], r["from"].isoformat(), r["to"].isoformat()] for r in years.table()])

    for num, crit in rules["criteria"].items():
        if criteria is not None and num not in criteria:
            continue
        doc.add_page_break()
        doc.add_heading(f"Criterion {num}: {crit['name']} ({crit['max']} marks)", 1)

        fields = [f for f in rules.get("data_fields", []) if f["criterion"] == num]
        if fields:
            ys = sorted({y for f in fields for y in f["years"]}, key=YEAR_NAMES.index)
            doc.add_heading(f"Data tables for Criterion {num}", 2)
            rows = [[f["label"]] + [_num(metrics.get((f["key"], y))) if y in f["years"] else "-" for y in ys]
                    for f in fields]
            _table(doc, ["Item"] + [f"{display_name(y)} {years.label(y)}" for y in ys], rows)
            missing = sum(1 for f in fields for y in f["years"] if metrics.get((f["key"], y)) is None)
            if missing:
                doc.add_paragraph(f"{missing} value(s) not entered yet (blank cells).")

        if num == 5 and faculty:
            doc.add_heading("Table B2.A: Faculty details (Department + allied departments)", 2)
            rows = []
            for i, f in enumerate(faculty, 1):
                rows.append([str(i), f.get("name") or "", f.get("qualification") or "", f.get("university") or "",
                             f.get("specialization") or "", f.get("joining_date") or "",
                             f.get("designation_at_joining") or f.get("designation") or "", f.get("designation") or "",
                             f.get("designated_on") or "", f.get("nature") or "",
                             "No" if f.get("leaving_date") else "Yes", f.get("leaving_date") or "",
                             (f.get("department") or "") + (" (allied)" if f.get("allied") else "")])
            _table(doc, ["S.No.", "Name", "Highest degree", "University", "Specialization", "Date of joining",
                         "Designation at joining", "Present designation", "Date designated", "Nature of association",
                         "Currently associated", "Date of leaving", "Department"], rows)
            proofs = [[f.get("name") or "", x["kind"], x["name"], "checked" if x["status"] == "verified" else "does not match"]
                      for f in faculty for x in f.get("proofs", [])]
            if proofs:
                doc.add_heading("Faculty proofs (NBA file P.21)", 3)
                _table(doc, ["Faculty", "Document", "File name", "Check"], proofs)

        for code in sorted((c for c, it in rules["items"].items() if it["criterion"] == num), key=_code_key):
            item = rules["items"][code]
            doc.add_heading(f"{code} {item['title']} ({item['max']} marks)", 2)
            if item.get("evidence_file"):
                doc.add_paragraph(f"Evidence: NBA List of Documents, file {item['evidence_file']}.")
            entries = by_item.get(code, [])

            if sections is not None:
                if code not in sections:
                    doc.add_paragraph("(Section not included.)")
                    continue
                text, tables = sections[code]
                if text.strip():
                    add_html(doc, text)
                else:
                    doc.add_paragraph(TODO)
                add_html(doc, tables)
                continue

            if item["type"] == "rubric":
                written = [c for c in entries if (c.get("sar_html") or c.get("sar_text") or "").strip()]
                for c in written:
                    _write_section(doc, c)
                if not written:
                    doc.add_paragraph(TODO)
            else:
                calc = [n for n in notes.get(code, []) if not n.startswith("Missing data")
                        and not n.startswith("No supporting documents")]
                if calc and item["type"] not in ("points_by_duration", "resource_persons", "faculty_share",
                                                 "count_events", "counts", "papers", "amount_bands", "seed_money"):
                    doc.add_paragraph("Calculation (NBA formula, from the data tables):")
                    for n in calc:
                        doc.add_paragraph(n, style="List Bullet")
                for c in entries:
                    if (c.get("sar_html") or c.get("sar_text") or "").strip():
                        _write_section(doc, c)

            for spec in rules.get("tables", {}).get(code, []):
                filled = [c["tables"][spec["id"]] for c in entries if (c.get("tables") or {}).get(spec["id"])]
                _sar_tables(doc, spec, filled)

            listed = [c for c in entries if c.get("person_name") or c.get("start_date") or c.get("amount")
                      or c.get("proofs") or item["type"] != "rubric"]
            if listed:
                rows = []
                for i, c in enumerate(listed, 1):
                    files = "; ".join(f"{ev_no.get((c.get('id'), k), '')} {p.get('name', '')}".strip()
                                      for k, p in enumerate(c.get("proofs", []))) or "NO PROOF ATTACHED"
                    rows.append([str(i), c.get("person_name") or "", c.get("title") or "",
                                 " ".join(x for x in [c.get("kind") or "", c.get("organizer") or ""] if x),
                                 _dates(c), _num(c.get("amount")), files])
                _table(doc, ["#", "Name", "Title", "Type / organiser", "Dates", "Amount (lakh)", "Proof (evidence no.)"], rows)

    doc.add_page_break()
    doc.add_heading("Evidence index", 1)
    doc.add_paragraph("Keep one folder per NBA document code (for example P.12) and name each file with its "
                      "evidence number, so the peer team can find any proof quickly.")
    groups = defaultdict(list)
    for code, item in rules["items"].items():
        ev = item.get("evidence_file") or "Other"
        for c in by_item.get(code, []):
            for k, p in enumerate(c.get("proofs", [])):
                groups[ev].append([ev_no[(c.get("id"), k)], code, c.get("person_name") or "",
                                   c.get("title") or "", p.get("name", "")])
    if not groups:
        doc.add_paragraph("No proof documents uploaded yet.")
    for ev in sorted(groups, key=_evidence_key):
        doc.add_heading(f"File {ev}", 2)
        rows = sorted(groups[ev], key=lambda r: int(r[0].rsplit("/", 1)[1]))
        _table(doc, ["Evidence no.", "Sub-criterion", "Name", "Title", "File name"], rows)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
