"""SQLite helpers."""
import sqlite3
from pathlib import Path

import click
from flask import current_app, g


_upgraded: set[str] = set()


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"], timeout=30)  # background audits write too
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        if current_app.config["DATABASE"] not in _upgraded:  # bring older databases up to date, once
            _upgraded.add(current_app.config["DATABASE"])
            migrate(g.db)
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _cols(db, table):
    return {r[1] for r in db.execute(f"PRAGMA table_info({table})")}


def migrate(db):
    """Create missing tables and add columns that newer versions need. Safe to run any number of times."""
    schema = (Path(__file__).parent / "schema.sql").read_text()
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'users'").fetchone():
        db.executescript(schema)
        return
    add = {
        "claims": {"kind": "TEXT NOT NULL DEFAULT ''", "tables_json": "TEXT NOT NULL DEFAULT '{}'",
                   "sar_html": "TEXT NOT NULL DEFAULT ''", "updated_by": "INTEGER", "updated_at": "TEXT"},
        "programs": {"institution_id": "INTEGER", "doc_settings": "TEXT NOT NULL DEFAULT '{}'"},
        "faculty": {"qualification": "TEXT NOT NULL DEFAULT ''", "experience_years": "TEXT NOT NULL DEFAULT ''",
                    "teaching_areas": "TEXT NOT NULL DEFAULT ''", "research_areas": "TEXT NOT NULL DEFAULT ''",
                    "publications": "INTEGER", "profile_url": "TEXT NOT NULL DEFAULT ''",
                    "designation_at_joining": "TEXT NOT NULL DEFAULT ''", "designated_on": "TEXT", "phd_date": "TEXT",
                    "university": "TEXT NOT NULL DEFAULT ''", "specialization": "TEXT NOT NULL DEFAULT ''",
                    "department": "TEXT NOT NULL DEFAULT ''", "allied": "INTEGER NOT NULL DEFAULT 0",
                    "nature": "TEXT NOT NULL DEFAULT 'Regular'", "full_time": "INTEGER NOT NULL DEFAULT 1",
                    "first_year": "INTEGER NOT NULL DEFAULT 0", "is_hod": "INTEGER NOT NULL DEFAULT 0",
                    "apaar_id": "TEXT NOT NULL DEFAULT ''", "source": "TEXT NOT NULL DEFAULT 'manual'",
                    "updated_by": "INTEGER", "updated_at": "TEXT"},
        "audits": {"kind": "TEXT NOT NULL DEFAULT 'rules'", "run_role": "TEXT NOT NULL DEFAULT ''",
                   "scope": "TEXT NOT NULL DEFAULT ''", "counted": "INTEGER NOT NULL DEFAULT 1"},
    }
    db.executescript(schema)  # new tables (CREATE ... IF NOT EXISTS); existing ones are left alone
    for table, cols in add.items():
        have = _cols(db, table)
        for col, decl in cols.items():
            if col not in have:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    if "sections" not in _cols(db, "program_members"):
        db.execute("ALTER TABLE program_members ADD COLUMN sections TEXT NOT NULL DEFAULT ''")
    have = _cols(db, "audit_jobs")
    for col, decl in {"kind": "TEXT NOT NULL DEFAULT 'full_ai'", "scope": "TEXT NOT NULL DEFAULT ''"}.items():
        if col not in have:
            db.execute(f"ALTER TABLE audit_jobs ADD COLUMN {col} {decl}")
    if "institution_id" not in _cols(db, "users"):
        # version 1-2 users table had a fixed list of roles: rebuild it without that limit
        db.executescript("""
            PRAGMA foreign_keys = OFF;
            CREATE TABLE users_new (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, role TEXT NOT NULL,
                institution_id INTEGER REFERENCES institutions(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL DEFAULT (datetime('now')));
            INSERT INTO users_new (id, name, email, password_hash, role, created_at)
                SELECT id, name, email, password_hash,
                       CASE role WHEN 'coordinator' THEN 'hod' WHEN 'incharge' THEN 'member' ELSE role END,
                       created_at FROM users;
            DROP TABLE users;
            ALTER TABLE users_new RENAME TO users;
            PRAGMA foreign_keys = ON;
        """)
    # data from before institutions existed belongs to one default institution
    if db.execute("SELECT 1 FROM programs WHERE institution_id IS NULL UNION ALL "
                  "SELECT 1 FROM users WHERE institution_id IS NULL AND role != 'superadmin'").fetchone():
        row = db.execute("SELECT id FROM institutions ORDER BY id LIMIT 1").fetchone()
        iid = row[0] if row else db.execute(
            "INSERT INTO institutions (name, short_name, code) VALUES "
            "('CMR Institute of Technology', 'CMRIT', 'cmrit')").lastrowid
        db.execute("UPDATE programs SET institution_id = ? WHERE institution_id IS NULL", (iid,))
        db.execute("UPDATE users SET institution_id = ? WHERE institution_id IS NULL AND role != 'superadmin'", (iid,))
    db.commit()


def init_db():
    migrate(get_db())


@click.command("init-db")
@click.option("--admin-email", default="admin@cmrit.local", show_default=True)
@click.option("--admin-password", prompt=True, hide_input=True, confirmation_prompt=True)
def init_db_command(admin_email, admin_password):
    """Create tables and the platform owner account (who then adds institutions and their admins)."""
    from werkzeug.security import generate_password_hash

    init_db()
    db = get_db()
    exists = db.execute("SELECT 1 FROM users WHERE email = ?", (admin_email,)).fetchone()
    if not exists:
        db.execute(
            "INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, 'superadmin')",
            ("Platform owner", admin_email, generate_password_hash(admin_password)),
        )
        db.commit()
        click.echo(f"Created platform owner {admin_email}. Log in via 'Platform owner' on the login page.")
    else:
        click.echo("Admin user already exists")
    click.echo("Database ready.")


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    from .demo import seed_demo_command
    app.cli.add_command(seed_demo_command)
