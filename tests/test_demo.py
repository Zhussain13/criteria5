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


def _tok(html):
    return re.search(r'name="_csrf" value="([^"]+)"', html).group(1)


def test_erp_year_wise_import_seen_by_head_hod_principal(app):
    seed(app)
    member = login(app, "member@cmrit.local")
    page = member.get("/programs/1/faculty/").get_data(as_text=True)
    assert "Import from ERP" in page and "Demo ERP connection" in page
    # ERP 2023-24 into CAYm2: the years match
    pv = member.get("/programs/1/faculty/erp?ay=2023&slot=CAYm2").get_data(as_text=True)
    assert "ERP 2023-24 → CAYm2 (2023-24): the years match" in pv
    # a wrong pairing is flagged
    bad = member.get("/programs/1/faculty/erp?ay=2023&slot=CAYm1").get_data(as_text=True)
    assert "which is <b>2024-25</b> in this SAR" in bad
    r = member.post("/programs/1/faculty/erp", data={"_csrf": _tok(pv), "ay": "2023", "slot": "CAYm2", "students": "1"},
                    follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "ERP 2023-24 into CAYm2" in html and "ERP 2023-24" in html      # shown on the Numbers page header
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT value FROM metrics WHERE key = 'S' AND year = 'CAYm2'").fetchone()[0] == 1920
        labels = {r[0] for r in db.execute("SELECT DISTINCT ay_label FROM faculty_year WHERE year = 'CAYm2'")}
        assert labels == {"2023-24"}
        n_snap = db.execute("SELECT COUNT(*) FROM faculty_year WHERE year = 'CAYm2'").fetchone()[0]
    # the CAYm2 count now comes from the ERP's 2023-24 staff list
    from app.roster import merged_metrics
    with app.app_context():
        db = get_db()
        prog = db.execute("SELECT * FROM programs WHERE id = 1").fetchone()
        m, auto = merged_metrics(db, prog)
        assert ("F", "CAYm2") in auto and 0 < m[("F", "CAYm2")] <= n_snap
    # import every assessment year at once: the two new joinees of 2025-26 come in with CAY
    r = member.post("/programs/1/faculty/erp", data={"_csrf": _tok(pv), "all": "1"}, follow_redirects=True)
    with app.app_context():
        db = get_db()
        assert {r[0]: r[1] for r in db.execute("SELECT year, ay_label FROM erp_imports")} == {
            "CAY": "2025-26", "CAYm1": "2024-25", "CAYm2": "2023-24", "CAYm3": "2022-23"}
        assert db.execute("SELECT COUNT(*) FROM faculty WHERE name IN ('CSE Faculty 065', 'DS Faculty 031')").fetchone()[0] == 2
        f = db.execute("SELECT source, leaving_date FROM faculty WHERE name = 'AIML Faculty 005'").fetchone()
        assert f["source"] == "erp" and f["leaving_date"]
    # the member's work is visible to the criteria head, the HoD and the principal
    for email in ("head@cmrit.local", "hod@cmrit.local", "principal@cmrit.local"):
        c = login(app, email)
        dash = c.get("/programs/1").get_data(as_text=True)
        assert "Imported from ERP" in dash and "Criteria member - Criterion 5" in dash, email
        assert "CSE Faculty 065" in c.get("/programs/1/faculty/").get_data(as_text=True), email
        assert "ERP 2023-24" in c.get("/programs/1/criteria/5/data").get_data(as_text=True), email
    assert "Recent updates" in login(app, "principal@cmrit.local").get("/").get_data(as_text=True)


def test_erp_off(app):
    app.config["ERP_MODE"] = "off"
    seed(app)
    c = login(app, "hod@cmrit.local")
    assert "Import from ERP" not in c.get("/programs/1/faculty/").get_data(as_text=True)
    assert c.get("/programs/1/faculty/erp").status_code == 404
