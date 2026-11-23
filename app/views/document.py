"""The Criterion 5 SAR as one live document (like Google Docs).

- Everyone works on the same document; what one person types is saved within seconds and appears for the others
  (the page asks for changes every few seconds).
- Criteria members see and type only the sections assigned to them; the criteria head and HoD see and edit the
  whole document; the principal reads it and runs the audit.
- The text is drafted by the website (app/sar_draft.py); the NBA tables are always built from the data.
"""
from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from .. import activity
from ..db import get_db
from ..permissions import audit_option
from ..richtext import sanitize
from ..rules import load as load_rules
from ..sar_draft import Facts, auto_html, draft_all, section_codes, table_5a
from .auth import login_required, program_access

bp = Blueprint("document", __name__, url_prefix="/programs/<int:program_id>/document")


def refresh_drafts(db, p):
    """Text still as the website drafted it follows the data at once; text a person edited is never touched."""
    if draft_all(db, p, None, only_drafted=True):
        db.commit()


def _sections(db, p, acc, codes=None) -> list[dict]:
    refresh_drafts(db, p)
    rules = load_rules(p["sar_version"])
    rows = {r["code"]: r for r in db.execute(
        "SELECT s.*, u.name AS by_name FROM sar_sections s LEFT JOIN users u ON u.id = s.updated_by "
        "WHERE s.program_id = ?", (p["id"],))}
    f = Facts(db, p)
    out = []
    for code in codes or section_codes(p):
        if code not in acc["sections_view"]:
            continue
        it, r = rules["items"][code], rows.get(code)
        out.append({"code": code, "title": it["title"], "max": it["max"], "html": r["html"] if r else "",
                    "version": r["version"] if r else 0, "drafted": bool(r and r["drafted"]),
                    "by": (r["by_name"] if r else "") or "", "at": (r["updated_at"] if r else "") or "",
                    "tables": auto_html(code, f), "edit": code in acc["sections_edit"]})
    return out


def _table_5a(db, p, acc) -> str:
    return table_5a(Facts(db, p)) if acc["sections_view"] else ""


@bp.route("/")
@login_required
def view(program_id):
    acc = program_access(program_id)
    p = acc["program"]
    db = get_db()
    secs = _sections(db, p, acc)
    team = db.execute("SELECT u.name, u.role, m.sections FROM program_members m JOIN users u ON u.id = m.user_id "
                      "WHERE m.program_id = ? AND u.role = 'member'", (program_id,)).fetchall()
    owners = {}
    for t in team:
        for c in (t["sections"] or "").split(","):
            if c.strip():
                owners.setdefault(c.strip(), []).append(t["name"])
    return render_template("document/view.html", p=p, acc=acc, sections=secs, owners=owners, t5a=_table_5a(db, p, acc),
                           option=audit_option(db, acc), crit=load_rules(p["sar_version"])["criteria"][5])


@bp.route("/state")
@login_required
def state(program_id):
    """For the live view: every visible section's version, text and tables."""
    acc = program_access(program_id)
    secs = _sections(get_db(), acc["program"], acc)
    return jsonify({s["code"]: {"version": s["version"], "html": s["html"], "tables": s["tables"], "by": s["by"],
                                "at": s["at"]} for s in secs})


@bp.route("/<code>", methods=("POST",))
@login_required
def save(program_id, code):
    """Autosave of one section (JSON: html, base). Refuses a save based on an older version."""
    acc = program_access(program_id)
    if code not in acc["sections_edit"]:
        abort(403)
    db = get_db()
    data = request.get_json(silent=True) or request.form
    html = sanitize(data.get("html", ""))
    base = int(data.get("base", -1))
    row = db.execute("SELECT s.*, u.name AS by_name FROM sar_sections s LEFT JOIN users u ON u.id = s.updated_by "
                     "WHERE program_id = ? AND code = ?", (program_id, code)).fetchone()
    cur = row["version"] if row else 0
    if base >= 0 and base != cur:
        return jsonify({"conflict": True, "version": cur, "html": row["html"], "by": row["by_name"] or ""}), 409
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("INSERT INTO sar_sections (program_id, code, html, drafted, version, updated_by, updated_at) "
               "VALUES (?, ?, ?, 0, 1, ?, ?) ON CONFLICT(program_id, code) DO UPDATE SET html = excluded.html, "
               "drafted = 0, version = sar_sections.version + 1, updated_by = excluded.updated_by, "
               "updated_at = excluded.updated_at", (program_id, code, html, g.user["id"], now))
    recent = db.execute("SELECT 1 FROM activity WHERE program_id = ? AND user_id = ? AND action = ? "
                        "AND created_at > datetime('now', '-15 minutes')",
                        (program_id, g.user["id"], f"Wrote SAR {code}")).fetchone()
    if not recent:
        activity.log(db, program_id, f"Wrote SAR {code}", load_rules(acc["program"]["sar_version"])["items"][code]["title"])
    db.commit()
    return jsonify({"version": cur + 1, "by": g.user["name"], "at": now})


@bp.route("/draft", methods=("POST",))
@login_required
def draft(program_id):
    """Draft the text of every untouched section (or re-draft one section) from the data."""
    acc = program_access(program_id)
    if not acc["can_draft"]:
        abort(403)
    db = get_db()
    code = request.form.get("code")
    if code and code not in acc["sections_edit"]:
        abort(403)
    n = draft_all(db, acc["program"], g.user["id"], [code] if code else None, overwrite=bool(code))
    activity.log(db, program_id, "Drafted the SAR", f"section {code} re-drafted" if code else f"{n} section(s) drafted")
    db.commit()
    flash(f"Section {code} drafted again from the data." if code else
          (f"{n} section(s) drafted from the data. Sections a person has written were left as they are."
           if n else "Everything is already drafted; the tables always show the latest data."), "ok")
    return redirect(url_for("document.view", program_id=program_id) + (f"#s{code}" if code else ""))


@bp.route("/<code>/clear", methods=("POST",))
@login_required
def clear(program_id, code):
    acc = program_access(program_id)
    if not acc["can_delete"] or code not in acc["sections_edit"]:
        abort(403)
    db = get_db()
    db.execute("UPDATE sar_sections SET html = '', drafted = 0, version = version + 1, updated_by = ?, "
               "updated_at = datetime('now') WHERE program_id = ? AND code = ?", (g.user["id"], program_id, code))
    activity.log(db, program_id, f"Cleared SAR {code}", "")
    db.commit()
    flash(f"Section {code} cleared.", "ok")
    return redirect(url_for("document.view", program_id=program_id) + f"#s{code}")


@bp.route("/prepare", methods=("POST",))
@login_required
def prepare(program_id):
    """One click: import every assessment year from the ERP, check all proofs again, draft the SAR, run the audit."""
    from ..erp import NotConnected, connector, import_year
    from ..faculty_proofs import recheck_faculty
    from ..years import AssessmentYears
    from .audit import _audit_and_save

    acc = program_access(program_id)
    if not acc["can_draft"]:
        abort(403)
    p = acc["program"]
    db = get_db()
    done = []
    con = connector()
    if con is not None:
        try:
            years = AssessmentYears(p["cay_start_year"], p["ay_start_month"])
            avail = set(con.years(p))
            for y in ("CAY", "CAYm1", "CAYm2", "CAYm3"):
                if years.start_of(y) in avail:
                    activity.log(db, program_id, "Imported from ERP",
                                 import_year(db, p, con, years.start_of(y), y, g.user["id"]))
            done.append("ERP data imported for every assessment year")
        except NotConnected as e:
            done.append(f"ERP skipped ({e})")
    for f in db.execute("SELECT id FROM faculty WHERE program_id = ?", (program_id,)).fetchall():
        recheck_faculty(db, p, f["id"])
    done.append("all proofs checked again")
    n = draft_all(db, p, g.user["id"])
    done.append(f"{n} SAR section(s) drafted")
    activity.log(db, program_id, "Prepared Criterion 5 in one click", "; ".join(done))
    db.commit()
    audit_id = _audit_and_save(program_id, g.user["id"], acc["role"])
    flash("Done in one click: " + "; ".join(done) + "; audit run.", "ok")
    return redirect(url_for("audit.report", program_id=program_id, audit_id=audit_id))
