import re

import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from app.db import get_db, init_db


@pytest.fixture
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "DATABASE": str(tmp_path / "test.sqlite"),
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "SECRET_KEY": "test",
    })
    with app.app_context():
        init_db()
        db = get_db()
        iid = db.execute("INSERT INTO institutions (name, short_name, code) VALUES "
                         "('Test Institute of Technology', 'TIT', 'test')").lastrowid
        db.execute("INSERT INTO users (name, email, password_hash, role, institution_id) VALUES (?, ?, ?, 'principal', ?)",
                   ("Principal", "admin@test.in", generate_password_hash("adminpass123"), iid))
        db.commit()
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


def csrf(client, path="/auth/login?inst=test"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf" value="([^"]+)"', html).group(1)


@pytest.fixture
def logged_in(client):
    tok = csrf(client)
    r = client.post("/auth/login?inst=test", data={"email": "admin@test.in", "password": "adminpass123", "_csrf": tok})
    assert r.status_code == 302
    return client


def add_user(app, email, role, program_id=None, criteria=""):
    """Create a user in the test institution (password 'pass1234567') and optionally add them to a program team."""
    with app.app_context():
        db = get_db()
        uid = db.execute("INSERT INTO users (name, email, password_hash, role, institution_id) VALUES (?, ?, ?, ?, 1)",
                         (email.split("@")[0], email, generate_password_hash("pass1234567"), role)).lastrowid
        if program_id:
            db.execute("INSERT INTO program_members (program_id, user_id, criteria) VALUES (?, ?, ?)",
                       (program_id, uid, criteria))
        db.commit()
    return uid


def login_as(app, email):
    c = app.test_client()
    tok = csrf(c)
    r = c.post("/auth/login?inst=test", data={"email": email, "password": "pass1234567", "_csrf": tok})
    assert r.status_code == 302, email
    return c
