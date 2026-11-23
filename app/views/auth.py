"""Login, logout and access control."""
import functools

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from datetime import date

from ..db import get_db
from ..permissions import ALL_ROLES, ROLE_LABELS, access  # noqa: F401  (ROLE_LABELS used by admin views)

bp = Blueprint("auth", __name__, url_prefix="/auth")


def subscription_problem(inst) -> str | None:
    """Why an institution cannot log in right now (inactive or subscription ended), else None."""
    if inst is None:
        return "Institution not found."
    if not inst["active"]:
        return f"{inst['name']}: access is paused. Please contact the CMRSARathi team."
    if inst["valid_until"] and inst["valid_until"] < date.today().isoformat():
        return f"{inst['name']}: the subscription ended on {inst['valid_until']}. Please renew to continue."
    return None


@bp.before_app_request
def load_user():
    uid = session.get("user_id")
    g.user = None
    g.institution = None
    if uid is not None:
        db = get_db()
        g.user = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if g.user is not None and g.user["institution_id"]:
            g.institution = db.execute("SELECT * FROM institutions WHERE id = ?",
                                       (g.user["institution_id"],)).fetchone()
            problem = subscription_problem(g.institution)
            if problem:
                session.clear()
                g.user = g.institution = None
                flash(problem, "error")


def login_required(view):
    @functools.wraps(view)
    def wrapped(**kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.path))
        return view(**kwargs)
    return wrapped


def roles_required(*roles):
    def deco(view):
        @functools.wraps(view)
        def wrapped(**kwargs):
            if g.user is None:
                return redirect(url_for("auth.login", next=request.path))
            if g.user["role"] not in roles:
                abort(403)
            return view(**kwargs)
        return wrapped
    return deco


def program_access(program_id: int) -> dict:
    """Return what the current user may do in this program (see permissions.access), else 404/403."""
    db = get_db()
    prog = db.execute("SELECT * FROM programs WHERE id = ?", (program_id,)).fetchone()
    if prog is None:
        abort(404)
    acc = access(db, g.user, prog)
    if acc is None:
        abort(403)
    return acc


@bp.route("/login", methods=("GET", "POST"))
def login():
    """Step 1: choose the institution (like a single-sign-on list). Step 2: email and password."""
    db = get_db()
    code = request.values.get("inst", "")
    if not code:
        insts = db.execute("SELECT * FROM institutions WHERE active = 1 ORDER BY name").fetchall()
        return render_template("auth/choose.html", institutions=insts)
    platform = code == "platform"
    inst = None if platform else db.execute("SELECT * FROM institutions WHERE code = ?", (code,)).fetchone()
    if not platform:
        problem = subscription_problem(inst)
        if problem:
            flash(problem, "error")
            return redirect(url_for("auth.login"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if email and "@" not in email and inst is not None:
            email = f"{email}@{inst['code']}.local"   # short usernames, e.g. "hod" -> hod@cmrit.local
        user = db.execute("SELECT * FROM users WHERE lower(email) = ?", (email,)).fetchone()
        ok = user is not None and check_password_hash(user["password_hash"], password)
        if ok and platform:
            ok = user["role"] == "superadmin"
        elif ok:
            ok = user["role"] != "superadmin" and user["institution_id"] == inst["id"]
        if not ok:
            flash("Incorrect email or password for " + ("the platform owner." if platform else inst["name"] + "."), "error")
        else:
            csrf = session.get("_csrf")
            session.clear()
            session["user_id"] = user["id"]
            if csrf:
                session["_csrf"] = csrf
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("index"))
    demo_users = []
    if current_app.config.get("DEMO") and inst is not None:
        # one button per leadership role; criteria heads/members only for the made-up demo accounts
        demo_users = db.execute(
            "SELECT name, email, role, (SELECT GROUP_CONCAT(p.department, ', ') FROM program_members m JOIN programs p "
            "ON p.id = m.program_id WHERE m.user_id = users.id AND users.role = 'hod') AS depts "
            "FROM users WHERE institution_id = ? AND email IN (?, ?, ?, ?) "
            "ORDER BY CASE role WHEN 'principal' THEN 0 WHEN 'hod' THEN 1 WHEN 'crit_head' THEN 2 ELSE 3 END, id",
            (inst["id"], *[f"{k}@{inst['code']}.local" for k in ("principal", "hod", "head", "member")])).fetchall()
    return render_template("auth/login.html", inst=inst, platform=platform, code=code, demo_users=demo_users,
                           roles=ALL_ROLES)


@bp.route("/password", methods=("GET", "POST"))
@login_required
def password():
    if request.method == "POST":
        from werkzeug.security import generate_password_hash
        current = request.form.get("current", "")
        new = request.form.get("new", "")
        if not check_password_hash(g.user["password_hash"], current):
            flash("Current password is wrong.", "error")
        elif len(new) < 10:
            flash("New password must be at least 10 characters.", "error")
        elif new != request.form.get("confirm", ""):
            flash("New passwords do not match.", "error")
        else:
            db = get_db()
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new), g.user["id"]))
            db.commit()
            flash("Password changed.", "ok")
            return redirect(url_for("index"))
    return render_template("auth/password.html")


@bp.route("/logout", methods=("POST",))
def logout():
    inst = g.institution["code"] if g.get("institution") else ("platform" if g.user and g.user["role"] == "superadmin" else "")
    session.clear()
    return redirect(url_for("auth.login", inst=inst) if inst else url_for("auth.login"))
