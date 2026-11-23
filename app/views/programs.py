"""Programs: list, create, setup (assessment years) and the team."""
import json
from datetime import date

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .. import rules
from ..db import get_db
from ..years import AssessmentYears
from ..permissions import ROLE_LABELS, TEAM_ROLES, access, audit_option, visible_programs
from .auth import login_required, program_access, roles_required

bp = Blueprint("programs", __name__, url_prefix="/programs")


@bp.app_context_processor
def program_nav():
    """Side menu for pages inside a program: criteria with progress, data, faculty, report."""
    from ..progress import program_progress

    va = request.view_args or {}
    if not g.get("user") or "program_id" not in va:
        return {}
    db = get_db()
    p = db.execute("SELECT * FROM programs WHERE id = ?", (va["program_id"],)).fetchone()
    if p is None:
        return {}
    acc = access(db, g.user, p)
    if acc is None:
        return {}
    try:
        prog = program_progress(db, p)
    except Exception:  # never break a page because of the menu
        return {}
    return {"nav_program": p, "nav_progress": prog, "nav_criterion": va.get("criterion"),
            "nav_endpoint": request.endpoint, "nav_view": acc["view"], "nav_role": acc["role"]}


@login_required
def index():
    if g.user["role"] == "superadmin":
        return redirect(url_for("platform.institutions"))
    db = get_db()
    progs = visible_programs(db, g.user)
    from ..progress import program_progress

    stats = {}
    for p in progs:
        acc = access(db, g.user, p)
        pr = program_progress(db, p)
        last = pr["last"]
        audit_row = db.execute("SELECT report_json FROM audits WHERE id = ?", (last["id"],)).fetchone() if last else None
        outcome = json.loads(audit_row[0]).get("award", {}).get("status") if audit_row else None
        stats[p["id"]] = {"last": last, "progress": pr["overall"], "outcome": outcome, "faculty": pr["faculty"],
                          "counted": pr["counted"], "proofs_ok": pr["proofs_ok"], "proofs_needed": pr["proofs_needed"],
                          "pct": round(100 * last["awarded"] / last["maximum"]) if last and last["maximum"] else 0}
    overview = g.user["role"] == "principal"
    return render_template("programs/index.html", programs=progs, stats=stats, overview=overview)


def _read_program_form():
    f = request.form
    errors = []
    name = f.get("name", "").strip()
    dept = f.get("department", "").strip()
    inst = f.get("institute_name", "").strip()
    try:
        cay = int(f.get("cay_start_year", ""))
        if not 2000 <= cay <= 2100:
            raise ValueError
    except ValueError:
        errors.append("Enter the CAY start year, e.g. 2026 for 2026-27.")
        cay = None
    try:
        month = int(f.get("ay_start_month", "7"))
        if not 1 <= month <= 12:
            raise ValueError
    except ValueError:
        errors.append("Academic year start month must be 1-12.")
        month = 7
    version = f.get("sar_version", "")
    if version not in rules.available_versions():
        errors.append("Choose a SAR format.")
    if not name or not dept or not inst:
        errors.append("Program name, department and institute name are required.")
    return errors, dict(name=name, department=dept, institute_name=inst, tier=f.get("tier", "Tier I"),
                        sar_version=version, cay_start_year=cay, ay_start_month=month)


@bp.route("/new", methods=("GET", "POST"))
@roles_required("principal", "hod")
def create():
    if request.method == "POST":
        errors, data = _read_program_form()
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            db = get_db()
            data["institution_id"] = g.user["institution_id"]
            cur = db.execute(
                "INSERT INTO programs (name, department, institute_name, tier, sar_version, cay_start_year, "
                "ay_start_month, institution_id) VALUES (:name, :department, :institute_name, :tier, :sar_version, "
                ":cay_start_year, :ay_start_month, :institution_id)", data)
            if g.user["role"] != "principal":
                db.execute("INSERT INTO program_members (program_id, user_id) VALUES (?, ?)",
                           (cur.lastrowid, g.user["id"]))
            db.commit()
            flash("Program created.", "ok")
            return redirect(url_for("programs.detail", program_id=cur.lastrowid))
    return render_template("programs/form.html", program=None, versions=rules.available_versions(),
                           this_year=current_app.config.get("DEFAULT_CAY", date.today().year))


@bp.route("/<int:program_id>")
@login_required
def detail(program_id):
    acc = program_access(program_id)
    p = acc["program"]
    db = get_db()
    years = AssessmentYears(p["cay_start_year"], p["ay_start_month"])
    rs = rules.load(p["sar_version"])
    members = db.execute(
        "SELECT u.id, u.name, u.email, u.role, m.criteria FROM program_members m JOIN users u ON u.id = m.user_id "
        "WHERE m.program_id = ? ORDER BY u.name", (program_id,)).fetchall()
    users = db.execute("SELECT id, name, email, role FROM users WHERE institution_id = ? AND role IN (%s) ORDER BY name"
                       % ",".join("?" * len(TEAM_ROLES)), (p["institution_id"], *TEAM_ROLES)).fetchall()
    last = db.execute("SELECT * FROM audits WHERE program_id = ? ORDER BY id DESC LIMIT 1", (program_id,)).fetchone()
    from ..progress import next_steps, program_progress

    prog = program_progress(db, p)
    steps = next_steps(p, prog, acc["edit"])
    option = audit_option(db, acc)
    report = json.loads(last["report_json"]) if last else None
    return render_template("programs/detail.html", p=p, years=years.table(), criteria=rs["criteria"], items=rs["items"],
                           prog=prog, steps=steps, report=report, members=members, users=users, acc=acc, last=last,
                           rules_name=rs["name"], option=option, role_labels=ROLE_LABELS)


@bp.route("/<int:program_id>/edit", methods=("GET", "POST"))
@login_required
def edit(program_id):
    acc = program_access(program_id)
    if not acc["manage_program"]:
        abort(403)
    p = acc["program"]
    if request.method == "POST":
        errors, data = _read_program_form()
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            data["id"] = program_id
            db = get_db()
            db.execute("UPDATE programs SET name=:name, department=:department, institute_name=:institute_name, "
                       "tier=:tier, sar_version=:sar_version, cay_start_year=:cay_start_year, "
                       "ay_start_month=:ay_start_month WHERE id=:id", data)
            db.commit()
            flash("Program updated.", "ok")
            return redirect(url_for("programs.detail", program_id=program_id))
    return render_template("programs/form.html", program=p, versions=rules.available_versions(),
                           this_year=current_app.config.get("DEFAULT_CAY", date.today().year))


@bp.route("/<int:program_id>/members", methods=("POST",))
@login_required
def add_member(program_id):
    acc = program_access(program_id)
    if not acc["manage_team"]:
        abort(403)
    db = get_db()
    try:
        uid = int(request.form.get("user_id", ""))
    except ValueError:
        flash("Choose a user.", "error")
        return redirect(url_for("programs.detail", program_id=program_id) + "#team")
    u = db.execute("SELECT * FROM users WHERE id = ? AND institution_id = ?", (uid, acc["program"]["institution_id"])).fetchone()
    if u is None or u["role"] not in TEAM_ROLES:
        flash("Only HoDs, criteria heads and criteria members are added to a program team.", "error")
        return redirect(url_for("programs.detail", program_id=program_id) + "#team")
    crit = "5" if u["role"] in ("crit_head", "member") else ""  # this app has one criterion
    db.execute("INSERT OR REPLACE INTO program_members (program_id, user_id, criteria) VALUES (?, ?, ?)",
               (program_id, uid, crit))
    db.commit()
    flash("Team updated.", "ok")
    return redirect(url_for("programs.detail", program_id=program_id) + "#team")


@bp.route("/<int:program_id>/members/<int:uid>/remove", methods=("POST",))
@login_required
def remove_member(program_id, uid):
    if not program_access(program_id)["manage_team"]:
        abort(403)
    db = get_db()
    db.execute("DELETE FROM program_members WHERE program_id = ? AND user_id = ?", (program_id, uid))
    db.commit()
    flash("Removed from the team.", "ok")
    return redirect(url_for("programs.detail", program_id=program_id) + "#team")


@bp.route("/help")
@login_required
def help_page():
    """Plain-language guide: what each role does, step by step."""
    return render_template("help.html")
