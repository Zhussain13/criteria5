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


def test_erp_import_by_member_is_seen_by_head_hod_principal(app):
    seed(app)
    member = login(app, "member@cmrit.local")
    page = member.get("/programs/1/faculty/").get_data(as_text=True)
    assert "Import from ERP" in page and "Demo ERP connection" in page
    preview = member.get("/programs/1/faculty/erp").get_data(as_text=True)
    assert "CSE Faculty 065" in preview and "DS Faculty 031" in preview      # new joinees in the ERP
    assert "AIML Faculty 005" in preview                                     # left: leaving date changed
    tok = re.search(r'name="_csrf" value="([^"]+)"', preview).group(1)
    r = member.post("/programs/1/faculty/erp", data={"_csrf": tok, "students": "1"}, follow_redirects=True)
    assert "Imported from the ERP: 2 new faculty, 2 updated" in r.get_data(as_text=True)
    with app.app_context():
        db = get_db()
        f = db.execute("SELECT source, leaving_date FROM faculty WHERE name = 'AIML Faculty 005'").fetchone()
        assert f["source"] == "erp" and f["leaving_date"]
        assert db.execute("SELECT COUNT(*) FROM faculty WHERE name IN ('CSE Faculty 065', 'DS Faculty 031')").fetchone()[0] == 2
    # a second look finds nothing new
    again = member.get("/programs/1/faculty/erp").get_data(as_text=True)
    assert "Import 0 faculty changes" in again
    # the member's work is visible to the criteria head, the HoD and the principal
    for email in ("head@cmrit.local", "hod@cmrit.local", "principal@cmrit.local"):
        c = login(app, email)
        dash = c.get("/programs/1").get_data(as_text=True)
        assert "Imported from ERP" in dash and "Criteria member - Criterion 5" in dash, email
        assert "CSE Faculty 065" in c.get("/programs/1/faculty/").get_data(as_text=True), email
    assert "Recent updates" in login(app, "principal@cmrit.local").get("/").get_data(as_text=True)


def test_erp_off(app):
    app.config["ERP_MODE"] = "off"
    seed(app)
    c = login(app, "hod@cmrit.local")
    assert "Import from ERP" not in c.get("/programs/1/faculty/").get_data(as_text=True)
    assert c.get("/programs/1/faculty/erp").status_code == 404
