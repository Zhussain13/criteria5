"""The app end to end: roles, faculty import, proof upload and check, numbers, audit, Word files."""
import io
import re

from reportlab.pdfgen import canvas

from app.db import get_db
from tests.conftest import add_user, csrf, login_as


def make_pdf(lines):
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 780
    for line in lines:
        c.drawString(60, y, line)
        y -= 20
    c.save()
    buf.seek(0)
    return buf


def create_program(client):
    tok = csrf(client, "/programs/new")
    r = client.post("/programs/new", data={
        "_csrf": tok, "name": "B.E. Computer Science and Engineering", "department": "CSE",
        "institute_name": "CMR Institute of Technology, CMRIT", "tier": "Tier I",
        "sar_version": "criterion5_tier1_2026", "cay_start_year": "2025", "ay_start_month": "7"})
    assert r.status_code == 302
    return int(re.search(r"/programs/(\d+)", r.headers["Location"]).group(1))


ROSTER_CSV = (
    "Name,Designation,Date of joining,Date of leaving,Highest degree,PhD date,Department,Nature of association,HoD\n"
    "HoD CSE,Professor,16-07-2012,,Ph.D.,18-03-2011,CSE,Regular,Yes\n"
    "CSE Faculty 001,Associate Professor,01-07-2016,,Ph.D.,10-01-2019,CSE,Regular,No\n"
    "CSE Faculty 002,Assistant Professor,01-07-2020,,M.Tech,,CSE,Regular,No\n"
    "AIML Faculty 001,Assistant Professor,01-07-2021,,M.E.,,CSE (AI & ML),Contract,No\n"
)


def test_login_required_and_csrf(client):
    assert client.get("/").status_code == 302
    assert client.post("/auth/login", data={"email": "admin@test.in", "password": "x"}).status_code == 400


def test_full_flow(app, logged_in):
    add_user(app, "hod@test.in", "hod")
    c = login_as(app, "hod@test.in")      # the HoD creates the program and does every operation
    pid = create_program(c)
    # import the faculty list (ERP / Table B2.A style column names)
    tok = csrf(c, f"/programs/{pid}/faculty/")
    r = c.post(f"/programs/{pid}/faculty/import", data={"_csrf": tok, "sheet": (io.BytesIO(ROSTER_CSV.encode()), "erp.csv")},
               content_type="multipart/form-data", follow_redirects=True)
    assert "4 added" in r.get_data(as_text=True)
    with app.app_context():
        db = get_db()
        rows = {f["name"]: f for f in db.execute("SELECT * FROM faculty")}
        assert rows["AIML Faculty 001"]["allied"] == 1 and rows["HoD CSE"]["is_hod"] == 1
        fid = rows["CSE Faculty 001"]["id"]
    # upload a matching appointment letter and a wrong one
    tok = csrf(c, f"/programs/{pid}/faculty/{fid}")
    good = make_pdf(["CMR Institute of Technology", "To: CSE Faculty 001",
                     "You are appointed as Associate Professor.", "Date of joining: 01-07-2016"])
    r = c.post(f"/programs/{pid}/faculty/{fid}/proofs", data={"_csrf": tok, "kind": "appointment", "files": (good, "a.pdf")},
               content_type="multipart/form-data", follow_redirects=True)
    assert "All good" in r.get_data(as_text=True)
    bad = make_pdf(["CMR Institute of Technology", "To: CSE Faculty 001", "Master of Technology"])
    r = c.post(f"/programs/{pid}/faculty/{fid}/proofs", data={"_csrf": tok, "kind": "degree", "files": (bad, "d.pdf")},
               content_type="multipart/form-data", follow_redirects=True)
    assert "does not show a Ph.D." in r.get_data(as_text=True)
    # numbers: typed student strength; counted rows cannot be typed over
    tok = csrf(c, f"/programs/{pid}/criteria/5/data")
    c.post(f"/programs/{pid}/criteria/5/data", data={"_csrf": tok, "S__CAY": "80", "S__CAYm1": "80", "S__CAYm2": "80",
                                                   "DS__CAY": "60", "F__CAY": "999"})
    with app.app_context():
        keys = {(m["key"], m["year"]) for m in get_db().execute("SELECT key, year FROM metrics")}
        assert ("S", "CAY") in keys and ("F", "CAY") not in keys
    page = c.get(f"/programs/{pid}/criteria/5/data").get_data(as_text=True)
    assert "from faculty list" in page
    # audit
    tok = csrf(c, f"/programs/{pid}")
    r = c.post(f"/programs/{pid}/audit/run", data={"_csrf": tok}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "Criterion 5: grade" in html and "Only checked proofs" in html and "HoD CSE" in html
    audit_id = int(re.search(rf"/programs/{pid}/audit/(\d+)/report.docx", html).group(1))
    for url in (f"/programs/{pid}/audit/{audit_id}/report.docx", f"/programs/{pid}/audit/sar.docx"):
        r = c.get(url)
        assert r.status_code == 200 and r.data[:2] == b"PK", url
    for url in ("/", f"/programs/{pid}", f"/programs/{pid}/faculty/", f"/programs/{pid}/faculty/?show=issues",
                f"/programs/{pid}/faculty/{fid}", f"/programs/{pid}/faculty/{fid}/edit", f"/programs/{pid}/faculty/new",
                f"/programs/{pid}/criteria/5", f"/programs/{pid}/document/", f"/programs/{pid}/proofs",
                "/admin/users", "/programs/help", f"/programs/{pid}/faculty/template.csv"):
        assert c.get(url).status_code == 200, url


def test_roles(app, logged_in):
    add_user(app, "hod@test.in", "hod")
    pid = create_program(login_as(app, "hod@test.in"))
    add_user(app, "head@test.in", "crit_head", pid, "5")
    add_user(app, "member@test.in", "member", pid, "5")
    add_user(app, "other@test.in", "member")
    hod, head, member, other = (login_as(app, e) for e in ("hod@test.in", "head@test.in", "member@test.in", "other@test.in"))
    for cl in (hod, head, member):
        assert cl.get(f"/programs/{pid}").status_code == 200
        assert cl.get(f"/programs/{pid}/faculty/new").status_code == 200   # all three keep the faculty list
    assert head.get("/admin/users").status_code == 403 and member.get("/admin/users").status_code == 403
    assert hod.get("/admin/users").status_code == 200
    # the principal only views and runs the audit
    principal = logged_in
    assert principal.get(f"/programs/{pid}").status_code == 200
    assert principal.get(f"/programs/{pid}/faculty/new").status_code == 403
    assert "Run Criterion 5 audit" in principal.get(f"/programs/{pid}").get_data(as_text=True)
    assert principal.get("/programs/new").status_code == 403
    assert other.get(f"/programs/{pid}").status_code == 403
    assert "Run Criterion 5 audit" in head.get(f"/programs/{pid}").get_data(as_text=True)
    assert "Run Criterion 5 audit" not in member.get(f"/programs/{pid}").get_data(as_text=True)
    tok = csrf(member, f"/programs/{pid}")
    assert member.post(f"/programs/{pid}/audit/run", data={"_csrf": tok}).status_code == 403


def test_platform_owner_adds_institution_with_principal(app, client):
    from werkzeug.security import generate_password_hash

    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO users (name, email, password_hash, role) VALUES ('Owner', 'owner@x.in', ?, 'superadmin')",
                   (generate_password_hash("ownerpass123"),))
        db.commit()
    tok = csrf(client, "/auth/login?inst=platform")
    r = client.post("/auth/login?inst=platform", data={"_csrf": tok, "email": "owner@x.in", "password": "ownerpass123"})
    assert r.status_code == 302
    tok = csrf(client, "/platform/")
    r = client.post("/platform/", data={"_csrf": tok, "name": "Demo College", "short_name": "DC", "code": "dc",
                                        "admin_email": "principal@dc.in", "admin_name": "Principal"},
                    follow_redirects=True)
    assert "Principal login" in r.get_data(as_text=True)
    with app.app_context():
        assert get_db().execute("SELECT role FROM users WHERE email = 'principal@dc.in'").fetchone()[0] == "principal"
