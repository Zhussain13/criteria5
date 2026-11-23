"""Run the NBA-style audit and show / download the report."""
import io
import json
import re

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file, url_for

from ..permissions import ROLE_LABELS, audit_option

from ..db import get_db
from .auth import login_required, program_access

bp = Blueprint("audit", __name__, url_prefix="/programs/<int:program_id>/audit")


def _audit_and_save(program_id: int, user_id: int, role: str = "", kind: str = "rules", scope=()) -> int:
    """Run the Criterion 5 audit (formulas + proof checks, no AI) and store it. Returns the audit id."""
    from ..c5audit import build_report

    db = get_db()
    p = db.execute("SELECT * FROM programs WHERE id = ?", (program_id,)).fetchone()
    report = build_report(db, p)
    report["scope"] = []
    if kind == "ai":
        report["ai"] = ai_review(db, p, report)
        report["kind"] = "ai"
        report["kind_label"] = "NBA formulas + proof checks + AI peer-team review (" + \
            (report["ai"].get("model") or "Gemini") + ")" if not report["ai"].get("error") else report["kind_label"]
        for sct in report["ai"].get("sections", []):
            report["peer_team_questions"].extend(sct.get("questions", []))
        report["peer_team_questions"] = list(dict.fromkeys(report["peer_team_questions"]))[:50]
    cur = db.execute("INSERT INTO audits (program_id, run_by, awarded, maximum, report_json, kind, run_role, scope) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, '')",
                     (program_id, user_id, report["awarded"], report["audited_max"], json.dumps(report),
                      report["kind"], role))
    from .. import activity
    ai_txt = ""
    if report.get("ai") and not report["ai"].get("error"):
        ai_txt = f"; AI review {report['ai']['overall_score']}/100"
    activity.log(db, program_id, "Ran the AI audit" if kind == "ai" else "Ran the audit",
                 f"{report['awarded']} / 100 (only checked proofs: {report['verified_only']['awarded']}){ai_txt}", user_id)
    db.commit()
    return cur.lastrowid


def ai_review(db, p, report) -> dict:
    """Gemini reviews the SAR of every section like a peer team. Marks stay as computed by the formulas."""
    from .. import ai
    from ..sar_draft import Facts, auto_html, section_codes
    from .document import refresh_drafts

    refresh_drafts(db, p)
    f = Facts(db, p)
    texts = {r["code"]: r["html"] for r in db.execute("SELECT code, html FROM sar_sections WHERE program_id = ?",
                                                     (p["id"],))}
    marks = {it["code"]: it for it in report["criteria"][0]["items"]}
    secs = [{"code": c, "title": marks[c]["title"], "max": marks[c]["max"], "marks": marks[c]["awarded"],
             "auto_html": auto_html(c, f), "writeup_html": texts.get(c, "")} for c in section_codes(p)]
    years = ", ".join(f"{y} = {f.lbl[y]}" for y in ("CAY", "CAYm1", "CAYm2", "CAYm3"))
    try:
        return ai.review(p, secs, years)
    except ai.AIError as e:
        return {"error": str(e)}


def running_job(program_id: int):
    """Criterion 5 audits finish in a second, so nothing ever runs in the background."""
    return None


def start_audit(acc: dict, kind: str = "rules") -> tuple[str, int | None]:
    from .. import ai

    db = get_db()
    if audit_option(db, acc) is None:
        abort(403)
    if kind == "ai" and not ai.enabled():
        return "The AI audit needs a Gemini API key (Platform owner › AI settings).", None
    audit_id = _audit_and_save(acc["program"]["id"], g.user["id"], acc["role"], kind)
    if kind == "ai":
        rep = json.loads(get_db().execute("SELECT report_json FROM audits WHERE id = ?", (audit_id,)).fetchone()[0])
        if rep.get("ai", {}).get("error"):
            return "Audit complete, but the AI review did not run: " + rep["ai"]["error"], audit_id
        return "AI audit complete.", audit_id
    return "Audit complete.", audit_id


@bp.route("/run", methods=("POST",))
@login_required
def run(program_id):
    acc = program_access(program_id)
    msg, audit_id = start_audit(acc, "ai" if request.form.get("kind") == "ai" else "rules")
    flash(msg, "error" if ("did not run" in msg or "needs" in msg) else "ok")
    if audit_id:
        return redirect(url_for("audit.report", program_id=program_id, audit_id=audit_id))
    back = request.form.get("back", "")
    return redirect(back if back.startswith("/") and not back.startswith("//") else url_for("programs.detail", program_id=program_id))


def _load(program_id, audit_id):
    row = get_db().execute("SELECT * FROM audits WHERE id = ? AND program_id = ?", (audit_id, program_id)).fetchone()
    if row is None:
        abort(404)
    return row, json.loads(row["report_json"])


@bp.route("/<int:audit_id>")
@login_required
def report(program_id, audit_id):
    p = program_access(program_id)["program"]
    row, rep = _load(program_id, audit_id)
    history = get_db().execute("SELECT id, run_at, awarded, maximum FROM audits WHERE program_id = ? "
                               "ORDER BY id DESC LIMIT 10", (program_id,)).fetchall()
    return render_template("audit/report.html", p=p, row=row, r=rep, history=history)


@bp.route("/sar.docx")
@login_required
def sar_draft(program_id):
    """Module 1: draft SAR in NBA order, built from the data sheets, entries and proofs."""
    acc = program_access(program_id)
    from ..sar_export import build_sar_docx
    from ..c5audit import _claims
    from ..roster import merged_metrics

    db = get_db()
    claims = _claims(db, program_id)
    faculty = [dict(f) for f in db.execute("SELECT * FROM faculty WHERE program_id = ? ORDER BY allied, "
                                           "is_hod DESC, name", (program_id,))]
    for f in faculty:
        f["proofs"] = [{"kind": x["kind"], "name": x["original_name"], "status": x["status"]} for x in db.execute(
            "SELECT kind, original_name, status FROM faculty_proofs WHERE faculty_id = ? ORDER BY kind", (f["id"],))]
    metrics, _ = merged_metrics(db, acc["program"])
    wanted = {int(c) for c in request.args.getlist("c") if c.isdigit()} or set(acc["view"])
    crit = wanted & set(acc["view"])
    claims = [c for c in claims if c["criterion"] in crit]
    from ..sar_draft import Facts, tables_html
    from .document import refresh_drafts

    refresh_drafts(db, acc["program"])
    facts = Facts(db, acc["program"])
    texts = {r["code"]: r["html"] for r in db.execute("SELECT code, html FROM sar_sections WHERE program_id = ?",
                                                     (program_id,))}
    sections = {c: (texts.get(c, ""), tables_html(c, facts)) for c in acc["sections_view"]}
    if acc["role"] == "member":  # a member downloads only the sections assigned to them
        faculty = []
    data = build_sar_docx(dict(acc["program"]), claims, faculty, metrics, criteria=crit,
                          settings=json.loads(acc["program"]["doc_settings"] or "{}"), sections=sections)
    name = re.sub(r"[^A-Za-z0-9]+", "_", acc["program"]["name"]).strip("_") or "program"
    if crit != set(acc["view"]) or len(crit) == 1:
        name += "_Criterion_" + "_".join(str(c) for c in sorted(crit))
    return send_file(io.BytesIO(data), as_attachment=True, download_name=f"SAR_{name}.docx",
                     mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@bp.route("/<int:audit_id>/report.docx")
@login_required
def download(program_id, audit_id):
    program_access(program_id)
    row, rep = _load(program_id, audit_id)
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = "Calibri"
    st.font.size = Pt(10.5)
    doc.add_heading("CMRSARathi - Criterion 5 Audit Report", 0)
    pr = rep["program"]
    doc.add_paragraph(f"{pr['name']} ({pr['department']}), {pr['institute_name']}")
    doc.add_paragraph(f"SAR format: {rep['rules_name']}. Audit run: {row['run_at']} UTC.")
    doc.add_paragraph(
        f"Criterion 5 (Faculty Information): {rep['awarded']} of {rep['audited_max']} marks. "
        f"If the peer team accepts only the proofs that were checked: {rep['verified_only']['awarded']}. "
        "Every mark follows the NBA formulas; proofs are read with free OCR, no AI. "
        "The NBA peer team takes the final decision.")

    doc.add_heading("Assessment years", 1)
    t = doc.add_table(rows=1, cols=4)
    t.style = "Light Grid Accent 1"
    for i, h in enumerate(["Name", "Academic year", "From", "To"]):
        t.rows[0].cells[i].text = h
    for y in rep["years"]:
        cells = t.add_row().cells
        cells[0].text, cells[1].text, cells[2].text, cells[3].text = y["name"], y["label"], y["from"], y["to"]

    doc.add_heading("Summary by criterion", 1)
    t = doc.add_table(rows=1, cols=5)
    t.style = "Light Grid Accent 1"
    for i, h in enumerate(["Criterion", "Max", "Percent", "Awarded", "Grade"]):
        t.rows[0].cells[i].text = h
    for c in rep["criteria"]:
        cells = t.add_row().cells
        cells[0].text = f"{c['number']}. {c['name']}"
        cells[1].text = str(c["max"])
        cells[2].text = f"{c['percent']}%"
        cells[3].text = str(c["awarded"])
        cells[4].text = c["grade"] or "-"

    pq = rep.get("pre_qualifiers") or []
    if pq:
        doc.add_heading("Pre-visit qualifiers (NBA Annexure-I, April 2026)", 1)
        t = doc.add_table(rows=1, cols=4)
        t.style = "Light Grid Accent 1"
        for i, h in enumerate(["#", "Qualifier", "Current status", "Compliance"]):
            t.rows[0].cells[i].text = h
        for q in pq:
            cells = t.add_row().cells
            cells[0].text, cells[1].text = str(q["no"]), q["text"]
            cells[2].text, cells[3].text = q["current"] or "-", q["status"]

    aw = rep.get("award")
    if aw:
        doc.add_heading(aw["status"], 1)
        doc.add_paragraph(f"Average SFR: {aw['sfr_avg'] if aw['sfr_avg'] is not None else 'unknown'}. "
                          f"Ph.D. faculty: {aw['phd_pct'] if aw['phd_pct'] is not None else 'unknown'}% of required faculty.")
        for c in aw.get("faculty_conditions", []):
            doc.add_paragraph(f"{c['label']}: " + ("faculty conditions met" if c["met"] else "not met - " + "; ".join(c["unmet"])),
                              style="List Bullet")

    aiv = rep.get("ai") or {}
    if aiv and not aiv.get("error"):
        doc.add_heading(f"AI peer-team review: {aiv['overall_score']} / 100 ({aiv.get('model', '')})", 1)
        doc.add_paragraph(aiv.get("summary", ""))
        doc.add_paragraph("The marks stay as computed by the NBA formulas; this review rates the written SAR.")
        for a_ in aiv.get("top_actions", []):
            doc.add_paragraph(a_, style="List Number")
        for s_ in aiv.get("sections", []):
            doc.add_heading(f"{s_['code']} {s_['title']}: {s_['score']} / 10", 2)
            if s_.get("verdict"):
                doc.add_paragraph(s_["verdict"])
            for k, label in (("strengths", "Strength"), ("gaps", "Gap"), ("data_checks", "Text vs numbers")):
                for x in s_.get(k, []):
                    doc.add_paragraph(f"{label}: {x}", style="List Bullet")

    issues = rep.get("faculty_issues") or []
    ps = rep.get("proof_summary") or {}
    doc.add_heading("Faculty proofs (NBA file P.21)", 1)
    if ps:
        doc.add_paragraph(f"Faculty counted: {ps['faculty_counted']}. Appointment letters checked: "
                          f"{ps['appointment_verified']}. Degree certificates checked: {ps['degree_verified']} of "
                          f"{ps['degree_needed']}. Faculty with a problem: {ps['with_issues']}.")
    if issues:
        t = doc.add_table(rows=1, cols=3)
        t.style = "Light Grid Accent 1"
        for i, h in enumerate(["Faculty", "Problem", "What to do"]):
            t.rows[0].cells[i].text = h
        for i in issues:
            for pr in i["problems"]:
                cells = t.add_row().cells
                cells[0].text = f"{i['name']} ({i['designation']})"
                cells[1].text, cells[2].text = pr["reason"], pr["fix"]

    doc.add_heading("Fix list (most marks first)", 1)
    for fx in rep["fix_list"]:
        doc.add_paragraph(f"{fx['code']} {fx['title']}: up to {fx['marks_recoverable']} marks recoverable",
                          style="List Bullet")

    cons = rep.get("consistency") or []
    if cons:
        doc.add_heading("Numbers that do not agree", 1)
        doc.add_paragraph("These do not change the marks, but a peer team will notice them and ask.")
        for x in cons:
            para = doc.add_paragraph(style="List Bullet")
            para.add_run(("Cannot be true: " if x["severity"] == "error" else "Check: ") + x["title"]).bold = True
            para.add_run(f" (Criterion {x['criteria']}). {x['detail']} What to do: {x['fix']}")

    for c in rep["criteria"]:
        if not c["items"]:
            continue
        doc.add_heading(f"Criterion {c['number']}: {c['name']}", 1)
        for it in c["items"]:
            doc.add_heading(f"{it['code']} {it['title']} - {it['awarded']} / {it['max']}", 2)
            if it["window"]:
                doc.add_paragraph(f"Assessment years counted: {it['window']}")
            for n in it["notes"]:
                doc.add_paragraph(n, style="List Bullet")
            for f in it["findings"]:
                para = doc.add_paragraph()
                para.add_run(f"{f['status'].upper()}: ").bold = True
                para.add_run(" - ".join(x for x in [f["person"], f["title"]] if x) + f" ({f['points']} pts)")
                for r_ in f["reasons"]:
                    doc.add_paragraph(f"Reason: {r_}", style="List Bullet")
                for fx in f["fixes"]:
                    doc.add_paragraph(f"What to do: {fx}", style="List Bullet")

    if rep["peer_team_questions"]:
        doc.add_heading("Questions the peer team may ask", 1)
        for q in rep["peer_team_questions"]:
            doc.add_paragraph(q, style="List Number")

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"Criterion5_audit_{audit_id}.docx",
                     mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
