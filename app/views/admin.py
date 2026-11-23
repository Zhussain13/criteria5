"""User management (IQAC / admin only)."""
import secrets

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from ..db import get_db
from .auth import ROLE_LABELS, roles_required

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/users", methods=("GET", "POST"))
@roles_required("principal", "hod")
def users():
    db = get_db()
    new_password = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        role = request.form.get("role", "")
        if not name or "@" not in email or role not in ROLE_LABELS:
            flash("Name, a valid email and a role are required.", "error")
        elif db.execute("SELECT 1 FROM users WHERE lower(email) = ?", (email,)).fetchone():
            flash("A user with this email already exists.", "error")
        else:
            new_password = secrets.token_urlsafe(9)
            db.execute("INSERT INTO users (name, email, password_hash, role, institution_id) VALUES (?, ?, ?, ?, ?)",
                       (name, email, generate_password_hash(new_password), role, g.user["institution_id"]))
            db.commit()
            flash(f"User created. Temporary password for {email}: {new_password} (share it privately).", "ok")
            return redirect(url_for("admin.users"))
    rows = db.execute("""
        SELECT u.id, u.name, u.email, u.role, u.created_at,
               (SELECT GROUP_CONCAT(p.name || CASE WHEN m.criteria != '' THEN ' (C' || m.criteria || ')' ELSE '' END, '; ')
                FROM program_members m JOIN programs p ON p.id = m.program_id WHERE m.user_id = u.id) AS programs
        FROM users u WHERE u.institution_id = ? ORDER BY u.role, u.name""", (g.user["institution_id"],)).fetchall()
    return render_template("admin/users.html", users=rows, roles=ROLE_LABELS)


@bp.route("/users/<int:uid>/reset", methods=("POST",))
@roles_required("principal", "hod")
def reset(uid):
    db = get_db()
    pw = secrets.token_urlsafe(9)
    db.execute("UPDATE users SET password_hash = ? WHERE id = ? AND institution_id = ?",
               (generate_password_hash(pw), uid, g.user["institution_id"]))
    db.commit()
    flash(f"New temporary password: {pw} (share it privately).", "ok")
    return redirect(url_for("admin.users"))


ROLE_ALIASES = {"principal": "principal", "hod": "hod", "head of department": "hod", "criteria head": "crit_head",
                "crit_head": "crit_head", "criterion head": "crit_head", "member": "member", "faculty": "member",
                "criteria member": "member"}


def _read_sheet(upload) -> list[dict]:
    import csv
    import io
    name = upload.filename.lower()
    if name.endswith(".csv"):
        return list(csv.DictReader(io.StringIO(upload.read().decode("utf-8-sig"))))
    if name.endswith(".xlsx"):
        import openpyxl
        ws = openpyxl.load_workbook(upload, read_only=True, data_only=True).active
        rows = ws.iter_rows(values_only=True)
        head = [str(h or "").strip().lower() for h in next(rows)]
        return [{head[i]: ("" if v is None else str(v).strip()) for i, v in enumerate(r) if i < len(head)} for r in rows]
    raise ValueError("Upload a .csv or .xlsx file.")


@bp.route("/users/import", methods=("POST",))
@roles_required("principal", "hod")
def import_users():
    """Create many accounts at once from Excel/CSV: name, email, role, program, criteria.
    New people get a temporary password (shown once); faculty with a program are added to its team."""
    db = get_db()
    up = request.files.get("sheet")
    if not up or not up.filename:
        flash("Choose the Excel or CSV file.", "error")
        return redirect(url_for("admin.users"))
    try:
        rows = _read_sheet(up)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("admin.users"))
    iid = g.user["institution_id"]
    programs = {p["name"].lower(): p["id"] for p in db.execute("SELECT id, name FROM programs WHERE institution_id = ?", (iid,))}
    programs.update({p["department"].lower(): p["id"] for p in db.execute(
        "SELECT id, department FROM programs WHERE institution_id = ?", (iid,))})
    created, problems = [], []
    for i, r in enumerate(rows, start=2):
        r = {str(k).strip().lower(): (v or "").strip() for k, v in r.items()}
        name, email = r.get("name", ""), r.get("email", "").lower()
        role = ROLE_ALIASES.get(r.get("role", "").lower())
        if not name and not email:
            continue
        if not name or "@" not in email or not role:
            problems.append(f"Row {i}: needs name, email and a role (principal, hod, criteria head, member, institute, admin).")
            continue
        existing = db.execute("SELECT id, institution_id FROM users WHERE lower(email) = ?", (email,)).fetchone()
        if existing and existing["institution_id"] != iid:
            problems.append(f"Row {i}: {email} belongs to another institution.")
            continue
        if existing:
            uid = existing["id"]
        else:
            pw = secrets.token_urlsafe(9)
            uid = db.execute("INSERT INTO users (name, email, password_hash, role, institution_id) VALUES (?, ?, ?, ?, ?)",
                             (name, email, generate_password_hash(pw), role, iid)).lastrowid
            created.append({"name": name, "email": email, "role": ROLE_LABELS.get(role, role), "password": pw})
        prog = r.get("program", "").lower()
        if prog and role in ("hod", "crit_head", "member"):
            pid = programs.get(prog)
            if pid is None:
                problems.append(f"Row {i}: program '{r.get('program')}' not found (use its name or department, e.g. CSE).")
            else:
                crit = ",".join(c.strip() for c in r.get("criteria", "").replace(";", ",").split(",") if c.strip().isdigit())
                db.execute("INSERT OR REPLACE INTO program_members (program_id, user_id, criteria) VALUES (?, ?, ?)",
                           (pid, uid, crit))
    db.commit()
    for pmsg in problems[:15]:
        flash(pmsg, "error")
    flash(f"Imported: {len(created)} new account(s). Share each temporary password privately; people change it after logging in.", "ok")
    rows = db.execute("SELECT id, name, email, role, created_at, '' AS programs FROM users WHERE institution_id = ? "
                      "ORDER BY role, name", (iid,)).fetchall()
    return render_template("admin/users.html", users=rows, roles=ROLE_LABELS, created=created)
