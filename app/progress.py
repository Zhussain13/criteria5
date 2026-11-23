"""How far Criterion 5 is prepared, and what to do next.

Criterion 5 is ready when (1) the faculty list is in, (2) the few numbers that are not counted from it
(student strength, pre-qualifier facts) are typed in, and (3) every counted faculty member has a checked
appointment letter and, for Ph.D. / M.Tech holders, a checked degree certificate.
"""
from __future__ import annotations

from flask import url_for

from . import rules
from .faculty_proofs import proof_status
from .roster import YEARS, Roster, degree_of
from .years import AssessmentYears


def program_progress(db, p) -> dict:
    rs = rules.load(p["sar_version"])
    pid = p["id"]
    claims = db.execute(
        "SELECT c.id, c.item_code, c.criterion, c.tables_json, "
        "(SELECT COUNT(*) FROM proofs WHERE claim_id = c.id) AS n_proofs FROM claims c WHERE program_id = ?",
        (pid,)).fetchall()
    metrics = {(r["key"], r["year"]) for r in db.execute("SELECT key, year FROM metrics WHERE program_id = ?", (pid,))}
    faculty = db.execute("SELECT * FROM faculty WHERE program_id = ?", (pid,)).fetchall()
    last = db.execute("SELECT id, awarded, maximum, run_at FROM audits WHERE program_id = ? ORDER BY id DESC LIMIT 1",
                      (pid,)).fetchone()
    has_54 = any(c["item_code"] == "5.4" and c["tables_json"] not in ("", "{}") for c in claims)

    typed = [f for f in rs.get("data_fields", []) if not f.get("optional") and f.get("auto") != "roster"
             and not (f.get("auto") == "visiting" and has_54)]
    cells = [(f["key"], y) for f in typed for y in f["years"]]
    filled = sum(1 for c in cells if c in metrics)

    years = AssessmentYears(p["cay_start_year"], p["ay_start_month"])
    ros = Roster.from_db(db, p, faculty=faculty)
    counted = [f for f in faculty if any(ros.present(f, y) for y in YEARS)]
    st = proof_status(db, pid)
    need = sum(1 + (degree_of(f["qualification"]) in ("phd", "mtech")) for f in counted)
    ok = sum((st.get(f["id"], {}).get("appointment") == "verified") +
             (degree_of(f["qualification"]) in ("phd", "mtech") and st.get(f["id"], {}).get("degree") == "verified")
             for f in counted)
    missing_proofs = need - ok

    parts = [1.0 if faculty else 0.0, filled / len(cells) if cells else 1.0, ok / need if need else 0.0]
    pct = round(100 * (0.25 * parts[0] + 0.25 * parts[1] + 0.5 * parts[2]))
    crit = rs["criteria"][5]
    crits = {5: {"name": crit["name"], "max": crit["max"], "items": len(rs["items"]), "pct": pct,
                 "entries": len(claims), "no_proof": sum(1 for c in claims if not c["n_proofs"]),
                 "empty_items": [], "data_cells": len(cells), "data_filled": filled, "tables_missing": [],
                 "proofs_needed": need, "proofs_ok": ok}}
    written = db.execute("SELECT COUNT(*) FROM sar_sections WHERE program_id = ? AND html != ''", (pid,)).fetchone()[0]
    return {"criteria": crits, "overall": pct, "sar_written": written, "sar_total": len(rs["items"]), "faculty": len(faculty), "counted": len(counted), "last": last,
            "entries": len(claims), "no_proof": crits[5]["no_proof"], "proofs_needed": need, "proofs_ok": ok,
            "missing_proofs": missing_proofs, "data_cells": len(cells), "data_filled": filled}


def next_steps(p, prog: dict, allowed: set[int]) -> list[dict]:
    """A short to-do list in the order a department would work."""
    pid = p["id"]
    fac = url_for("faculty.list_faculty", program_id=pid)
    steps = [
        {"done": prog["faculty"] > 0, "title": "Bring in the faculty list (Table B2.A)",
         "detail": f"{prog['faculty']} people, {prog['counted']} counted in the assessment years" if prog["faculty"] else
         "Import the ERP export (Excel/CSV) in one go, or add people one by one.", "url": fac, "cta": "Open faculty list"},
        {"done": prog["data_filled"] >= prog["data_cells"], "title": "Type the student numbers and pre-qualifier facts",
         "detail": f"{prog['data_filled']} of {prog['data_cells']} entered. Everything else is counted from the faculty list.",
         "url": url_for("claims.data_sheet", program_id=pid, criterion=5), "cta": "Open numbers"},
        {"done": prog["proofs_needed"] > 0 and prog["missing_proofs"] == 0,
         "title": "Upload and check the faculty proofs",
         "detail": f"{prog['proofs_ok']} of {prog['proofs_needed']} appointment letters / degree certificates checked."
         if prog["proofs_needed"] else "Appointment letters and degree certificates, read with free OCR.",
         "url": url_for("faculty.list_faculty", program_id=pid, show="issues"), "cta": "Proofs to fix"},
        {"done": prog["sar_written"] >= prog["sar_total"], "title": "Draft the SAR (one click)",
         "detail": f"{prog['sar_written']} of {prog['sar_total']} sections written. The website drafts the text and fills "
                   "the NBA tables from the data; the team polishes it.",
         "url": url_for("document.view", program_id=pid), "cta": "Open SAR document"},
        {"done": prog["last"] is not None, "title": "Run the Criterion 5 audit",
         "detail": "Re-run it after every change to see the new marks." if prog["last"] else
         "Marks for 5.1-5.5, the reason for every lost mark, pre-qualifiers and the fix list.",
         "url": None, "cta": None, "audit": True},
    ]
    return steps
