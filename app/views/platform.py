"""Platform owner: add institutions (subscribing colleges), their plans, and their first admin."""
import re
import secrets

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
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


@bp.route("/ai", methods=("GET", "POST"))
@roles_required("superadmin")
def ai_settings():
    """The Gemini API key for the AI audit (free key from Google AI Studio)."""
    from .. import ai

    db = get_db()
    if request.method == "POST":
        if request.form.get("action") == "remove":
            db.execute("DELETE FROM app_settings WHERE key = 'gemini_api_key'")
            db.commit()
            flash("API key removed. The AI audit is off.", "ok")
            return redirect(url_for("platform.ai_settings"))
        key = request.form.get("key", "").strip()
        model = request.form.get("model", "").strip()
        if key:
            db.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('gemini_api_key', ?)", (key,))
        db.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('ai_model', ?)", (model,))
        db.commit()
        if request.form.get("action") == "test" or key:
            try:
                used = ai.test_key()
                flash(f"The key works. Model answering: {used}.", "ok")
            except ai.AIError as e:
                flash(str(e), "error")
        else:
            flash("Saved.", "ok")
        return redirect(url_for("platform.ai_settings"))
    k = ai.api_key()
    src = "environment (GEMINI_API_KEY)" if current_app.config.get("GEMINI_API_KEY") else ("saved here" if k else "")
    model = db.execute("SELECT value FROM app_settings WHERE key = 'ai_model'").fetchone()
    return render_template("platform/ai.html", masked=(k[:4] + "…" + k[-4:]) if k else "", source=src,
                           model=model["value"] if model else "", models=ai.DEFAULT_MODELS)
