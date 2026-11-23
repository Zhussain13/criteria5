"""The demo: no real names, every login works, every page opens, the audit finds the planted problems."""
import re

from app.db import get_db
from app.demo import DEMO_PASSWORD, seed


def login(app, email, platform=False):
    c = app.test_client()
    path = "/auth/login?inst=platform" if platform else "/auth/login?inst=cmrit"
    tok = re.search(r'name="_csrf" value="([^"]+)"', c.get(path).get_data(as_text=True)).group(1)
    r = c.post(path, data={"email": email, "password": DEMO_PASSWORD, "_csrf": tok})
    assert r.status_code == 302, email
    return c


def test_demo(app):
    out = seed(app)
    assert out["faculty"] > 100
    with app.app_context():
        db = get_db()
        names = [r[0] for r in db.execute("SELECT name FROM faculty")] + [r[0] for r in db.execute("SELECT name FROM users")]
        # position names and staff codes only
        assert all(re.fullmatch(r"(CSE|AIML|DS) Faculty \d{3}|HoD CSE|Professor of Practice 01|Adjunct Faculty 01|"
                                r"Principal|HoD - CSE|Criteria (head|member) - Criterion 5|Platform owner|Principal", n)
                   for n in names), names
        pid = db.execute("SELECT id FROM programs WHERE department = 'CSE'").fetchone()[0]
        import json
        rep = json.loads(db.execute("SELECT report_json FROM audits WHERE program_id = ?", (pid,)).fetchone()[0])
    assert 0 < rep["verified_only"]["awarded"] < rep["awarded"] <= 100
    reasons = " ".join(p["reason"] for i in rep["faculty_issues"] for p in i["problems"])
    for planted in ("No appointment letter", "Joining date", "not found on the document", "same file",
                    "does not show a Ph.D."):
        assert planted in reasons, planted
    for email in ("principal@cmrit.local", "hod@cmrit.local", "head@cmrit.local", "member@cmrit.local"):
        c = login(app, email)
        for url in ("/", f"/programs/{pid}", f"/programs/{pid}/faculty/", f"/programs/{pid}/criteria/5/data",
                    f"/programs/{pid}/criteria/5", f"/programs/{pid}/audit/1"):
            assert c.get(url).status_code == 200, (email, url)
    c = login(app, "principal", platform=False)  # bare user name works on the demo
    assert c.get("/").status_code == 200
    owner = login(app, "owner@cmrsarathi.local", platform=True)
    assert owner.get("/platform/").status_code == 200
