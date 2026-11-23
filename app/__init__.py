"""CMRSARathi Criterion 5: NBA Faculty Information (Criterion 5) preparation and audit, no AI."""
import os
import secrets
from pathlib import Path

from flask import Flask, abort, request, session


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    app.config.from_mapping(
        SECRET_KEY=os.environ.get("CMRSARATHI_SECRET_KEY", "dev-only-change-me"),
        DATABASE=os.path.join(app.instance_path, "criteria5.sqlite"),
        UPLOAD_FOLDER=os.environ.get(
            "CMRSARATHI_UPLOADS", os.path.join(app.instance_path, "uploads")
        ),
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,  # 20 MB per upload
        ALLOWED_EXTENSIONS={"pdf", "png", "jpg", "jpeg"},
        # Current academic year of the upcoming NBA visit (2025 = CAY 2025-26): default for new programs.
        DEFAULT_CAY=int(os.environ.get("DEFAULT_CAY", "2025")),
        DEMO=os.environ.get("CMRSARATHI_DEMO", "") == "1",
        # AI audit (Gemini, free key from AI Studio). Empty = the platform owner can paste it under Platform > AI settings.
        GEMINI_API_KEY=os.environ.get("GEMINI_API_KEY", ""),
        AI_MODEL=os.environ.get("AI_MODEL", ""),
        # ERP link: "demo" (sample data), "database" (read-only ERP_DB_URL from CMRIT IT) or "off"
        ERP_MODE=os.environ.get("ERP_MODE", "demo"),
        ERP_DB_URL=os.environ.get("ERP_DB_URL", ""),  # show the demo login on the login page
    )
    if test_config:
        app.config.update(test_config)

    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    if app.config["SECRET_KEY"] == "dev-only-change-me" and not app.config.get("TESTING"):
        app.logger.warning("Using the default SECRET_KEY. Set CMRSARATHI_SECRET_KEY in production.")

    from . import db
    db.init_app(app)

    # --- simple CSRF protection for all POST forms ---
    @app.before_request
    def csrf_protect():
        if request.method == "POST" and not app.config.get("WTF_CSRF_DISABLED"):
            token = session.get("_csrf")
            sent = request.form.get("_csrf") or request.headers.get("X-CSRF")
            if not token or token != sent:
                abort(400, "Form expired or invalid. Please go back, reload and try again.")

    @app.context_processor
    def inject_csrf():
        if "_csrf" not in session:
            session["_csrf"] = secrets.token_hex(16)
        return {"csrf_token": session["_csrf"]}

    @app.context_processor
    def inject_ai():
        from . import ai as _ai
        return {"ai_on": _ai.enabled}

    import json as _json
    app.jinja_env.filters["fromjson"] = lambda v: _json.loads(v) if v else {}

    from .views import auth, programs, claims, audit, admin, platform, library, faculty, document
    app.register_blueprint(auth.bp)
    app.register_blueprint(programs.bp)
    app.register_blueprint(claims.bp)
    app.register_blueprint(audit.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(platform.bp)
    app.register_blueprint(library.bp)
    app.register_blueprint(faculty.bp)
    app.register_blueprint(document.bp)
    app.add_url_rule("/", endpoint="index", view_func=programs.index)

    return app
