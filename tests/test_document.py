"""The live SAR document: drafted by the website, section rights per role, live saves, one-click prepare."""
import re

from app.db import get_db
from app.demo import seed
from tests.test_demo import login


def tok(c, url="/programs/1/document/"):
    return re.search(r'name="_csrf" value="([^"]+)"', c.get(url).get_data(as_text=True)).group(1)


def test_document_rights_and_live_saves(app):
    seed(app)
    head, member, hod, principal = (login(app, f"{r}@cmrit.local") for r in ("head", "member", "hod", "principal"))
    # the website drafted every section, with the department's own numbers and the NBA tables
    full = hod.get("/programs/1/document/").get_data(as_text=True)
    for code in ("5.1", "5.2", "5.3", "5.4", "5.5"):
        assert f'id="s{code}"' in full
    for t in ("Table No. 5A: Faculty details", "Table No. 5.1.2: Student-faculty ratio", "Table No. 5.2.1",
              "Table No. 5.3.1", "Table No. 5.4.1", "Table No. 5.5.1", "Average SFR for 3 years", "Assessment:"):
        assert t in full, t
    assert "Written by the website" in full and "Department&#39;s write-up" in full or "Department's write-up" in full
    # the member sees only the sections assigned to them
    mine = member.get("/programs/1/document/").get_data(as_text=True)
    assert 'id="s5.1"' in mine and 'id="s5.4"' in mine and 'id="s5.3"' not in mine and 'id="s5.5"' not in mine
    assert "Prepare everything" not in mine and "Clear" not in mine
    t = tok(member)
    st = member.get("/programs/1/document/state").get_json()
    assert set(st) == {"5.1", "5.2", "5.4"}
    # member types in an assigned section: saved and seen by head, HoD and principal at once
    r = member.post("/programs/1/document/5.1", json={"html": "<p>Member note on SFR.</p><script>x()</script>",
                                                      "base": st["5.1"]["version"]}, headers={"X-CSRF": t})
    assert r.status_code == 200
    for c in (head, hod, principal):
        s = c.get("/programs/1/document/state").get_json()
        assert "Member note on SFR." in s["5.1"]["html"] and "<script" not in s["5.1"]["html"]
        assert s["5.1"]["by"] == "Criteria member - Criterion 5"
    # an old version cannot overwrite a newer one
    r = member.post("/programs/1/document/5.1", json={"html": "<p>old</p>", "base": st["5.1"]["version"]},
                    headers={"X-CSRF": t})
    assert r.status_code == 409
    # not assigned: refused; principal: read only
    assert member.post("/programs/1/document/5.3", json={"html": "<p>x</p>"}, headers={"X-CSRF": t}).status_code == 403
    pt = tok(principal)
    assert principal.post("/programs/1/document/5.1", json={"html": "<p>x</p>"}, headers={"X-CSRF": pt}).status_code == 403
    assert principal.post("/programs/1/document/draft", data={"_csrf": pt}).status_code == 403
    assert "Run audit" in principal.get("/programs/1/document/").get_data(as_text=True)
    # members cannot delete; the criteria head can re-draft and clear any section of the criterion
    assert member.post("/programs/1/document/5.1/clear", data={"_csrf": t}).status_code == 403
    ht = tok(head)
    assert head.post("/programs/1/document/5.5/clear", data={"_csrf": ht}).status_code == 302
    assert head.get("/programs/1/document/state").get_json()["5.5"]["html"] == ""
    head.post("/programs/1/document/draft", data={"_csrf": ht})   # fills the cleared one, keeps the member's text
    s = head.get("/programs/1/document/state").get_json()
    assert "served the institute" in s["5.5"]["html"] and "Member note on SFR." in s["5.1"]["html"]
    # HoD assigns sections to the member
    with app.app_context():
        uid = get_db().execute("SELECT id FROM users WHERE email = 'member@cmrit.local'").fetchone()[0]
    hod.post(f"/programs/1/members/{uid}/sections", data={"_csrf": tok(hod, "/programs/1"), "sections": ["5.3"]})
    assert set(member.get("/programs/1/document/state").get_json()) == {"5.3"}


def test_member_cannot_delete_faculty_or_entries(app):
    seed(app)
    member = login(app, "member@cmrit.local")
    t = tok(member, "/programs/1/faculty/")
    assert member.post("/programs/1/faculty/2/delete", data={"_csrf": t}).status_code == 403
    assert "Remove" not in member.get("/programs/1/faculty/2").get_data(as_text=True)


def test_one_click_prepare_and_word(app):
    seed(app)
    hod = login(app, "hod@cmrit.local")
    r = hod.post("/programs/1/document/prepare", data={"_csrf": tok(hod)}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "Done in one click" in html and "Criterion 5: grade" in html
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM erp_imports").fetchone()[0] == 4
    w = hod.get("/programs/1/audit/sar.docx")
    import io

    import docx
    d = docx.Document(io.BytesIO(w.data))
    text = "\n".join(p.text for p in d.paragraphs)
    assert "The SFR was" in text and "Assessment:" in text and len(d.tables) >= 7
    cells = " ".join(c.text for t in d.tables for r in t.rows for c in r.cells)
    assert "UG1. B" in cells and "PG1. A" in cells and "TF = Total faculty" in cells   # class-wise SFR from the ERP
    member = login(app, "member@cmrit.local")
    mt = "\n".join(p.text for p in docx.Document(io.BytesIO(member.get("/programs/1/audit/sar.docx").data)).paragraphs)
    assert "Section not included" in mt     # only the member's own sections are in their copy


def test_drafted_text_follows_the_data(app):
    seed(app)
    hod = login(app, "hod@cmrit.local")
    before = hod.get("/programs/1/document/state").get_json()["5.2"]["html"]
    with app.app_context():
        db = get_db()
        db.execute("UPDATE faculty SET qualification = 'Ph.D.', phd_date = '2010-01-01' WHERE name LIKE 'CSE Faculty 00%'")
        db.commit()
    after = hod.get("/programs/1/document/state").get_json()["5.2"]["html"]
    assert before != after                       # untouched draft rewritten with the new Ph.D. count
    member_text = hod.get("/programs/1/document/state").get_json()["5.4"]["html"]
    assert "Courses handled" in member_text      # the member's edited section was not touched
