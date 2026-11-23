"""Platform owner: add institutions (subscribing colleges), their plans, and their first admin."""
import re
import secrets

from flask import Blueprint, flash, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from ..db import get_db
from .auth import roles_required

bp = Blueprint("platform", __name__, url_prefix="/platform")

PLANS = ["College Plan", "Program Plan", "Trial"]


@bp.route("/", methods=("GET", "POST"))
@roles_required("superadmin")
def institutions():
    db = get_db()
    if request.method == "POST":
        f = request.form
        name, short = f.get("name", "").strip(), f.get("short_name", "").strip()
        code = re.sub(r"[^a-z0-9-]", "", (f.get("code") or short or name).lower().replace(" ", "-"))[:30]
        email = f.get("admin_email", "").strip().lower()
        if not name or not code or "@" not in email:
            flash("Institution name, a short code and the principal's email are required.", "error")
        elif db.execute("SELECT 1 FROM institutions WHERE code = ?", (code,)).fetchone():
            flash(f"The code '{code}' is already used.", "error")
        elif db.execute("SELECT 1 FROM users WHERE lower(email) = ?", (email,)).fetchone():
            flash("A user with that principal email already exists.", "error")
        else:
            iid = db.execute("INSERT INTO institutions (name, short_name, code, plan, valid_until) VALUES (?, ?, ?, ?, ?)",
                             (name, short, code, f.get("plan") if f.get("plan") in PLANS else PLANS[0],
                              f.get("valid_until") or None)).lastrowid
            pw = secrets.token_urlsafe(9)
            db.execute("INSERT INTO users (name, email, password_hash, role, institution_id) VALUES (?, ?, ?, 'principal', ?)",
                       (f.get("admin_name", "").strip() or "Principal", email, generate_password_hash(pw), iid))
            db.commit()
            flash(f"{name} added. Principal login: {email} / temporary password {pw} (share it privately).", "ok")
            return redirect(url_for("platform.institutions"))
    rows = db.execute("""
        SELECT i.*, (SELECT COUNT(*) FROM programs p WHERE p.institution_id = i.id) AS n_programs,
               (SELECT COUNT(*) FROM users u WHERE u.institution_id = i.id) AS n_users
        FROM institutions i ORDER BY i.name""").fetchall()
    return render_template("platform/institutions.html", institutions=rows, plans=PLANS)


@bp.route("/<int:iid>", methods=("POST",))
@roles_required("superadmin")
def update(iid):
    db = get_db()
    f = request.form
    db.execute("UPDATE institutions SET plan = ?, valid_until = ?, active = ? WHERE id = ?",
               (f.get("plan") if f.get("plan") in PLANS else PLANS[0], f.get("valid_until") or None,
                1 if f.get("active") else 0, iid))
    db.commit()
    flash("Institution updated.", "ok")
    return redirect(url_for("platform.institutions"))
