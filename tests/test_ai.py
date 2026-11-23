"""AI audit with Gemini: key from the platform owner's AI settings; marks stay formula-based; review is shown.
Google is not called in tests: the HTTP call is replaced by a fake answer."""
import io
import json
import re

import app.ai as ai
from app.db import get_db
from app.demo import seed
from tests.test_demo import login

SECTIONS = ["5.1", "5.2", "5.3", "5.4", "5.5"]
calls = []


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(req, timeout=0):
    calls.append(req.full_url)
    prompt = json.loads(req.data)["contents"][0]["parts"][0]["text"]
    if '"ok": true' in prompt:
        answer = {"ok": True}
    else:
        assert "marks by NBA formula" in prompt and "Table No. 5.1.2" in prompt
        answer = {"overall_score": 78, "summary": "Well supported by data. Retention measures need detail.",
                  "sections": [{"code": c, "score": 8, "verdict": "Clear and backed by the tables.",
                                "strengths": ["Numbers match Table"], "gaps": ["Add plan"],
                                "data_checks": [], "questions": [f"Show the records behind {c}."]} for c in SECTIONS],
                  "top_actions": ["Add retention measures"]}
    body = {"candidates": [{"content": {"parts": [{"text": json.dumps(answer)}]}}]}
    return FakeResp(json.dumps(body).encode())


def tok(c, url):
    return re.search(r'name="_csrf" value="([^"]+)"', c.get(url).get_data(as_text=True)).group(1)


def test_ai_audit(app, monkeypatch):
    monkeypatch.setattr(ai.urllib.request, "urlopen", fake_urlopen)
    seed(app)
    hod = login(app, "hod@cmrit.local")
    assert "Run AI audit" not in hod.get("/programs/1").get_data(as_text=True)      # no key yet
    owner = login(app, "owner@cmrsarathi.local", platform=True)
    r = owner.post("/platform/ai", data={"_csrf": tok(owner, "/platform/ai"), "key": "AIzaTESTKEY123", "model": "",
                                          "action": "save"}, follow_redirects=True)
    assert "The key works" in r.get_data(as_text=True)
    with app.app_context():
        assert get_db().execute("SELECT value FROM app_settings WHERE key = 'gemini_api_key'").fetchone()[0] == "AIzaTESTKEY123"
    page = hod.get("/programs/1").get_data(as_text=True)
    assert "Run AI audit" in page
    r = hod.post("/programs/1/audit/run", data={"_csrf": tok(hod, "/programs/1"), "kind": "ai"}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "AI audit complete" in html and "AI peer-team review" in html and "78" in html
    assert "Show the records behind 5.3." in html             # AI questions join the peer-team questions
    assert "Criterion 5: grade" in html                       # formula marks still there
    # the principal can run the AI audit too; same SAR text -> answer from the cache, no new call
    n = len(calls)
    principal = login(app, "principal@cmrit.local")
    r = principal.post("/programs/1/audit/run", data={"_csrf": tok(principal, "/programs/1"), "kind": "ai"},
                       follow_redirects=True)
    assert "AI peer-team review" in r.get_data(as_text=True) and len(calls) == n
    # the member cannot run audits
    member = login(app, "member@cmrit.local")
    assert member.post("/programs/1/audit/run", data={"_csrf": tok(member, "/programs/1"), "kind": "ai"}).status_code == 403
    # Word report carries the AI review
    with app.app_context():
        aid = get_db().execute("SELECT MAX(id) FROM audits").fetchone()[0]
    import docx
    d = docx.Document(io.BytesIO(hod.get(f"/programs/1/audit/{aid}/report.docx").data))
    assert any("AI peer-team review" in p.text for p in d.paragraphs)


def test_ai_error_keeps_the_audit(app, monkeypatch):
    def refused(req, timeout=0):
        raise ai.urllib.error.HTTPError(req.full_url, 400, "bad", {}, io.BytesIO(b"API key not valid"))
    monkeypatch.setattr(ai.urllib.request, "urlopen", refused)
    app.config["GEMINI_API_KEY"] = "bad"
    seed(app)
    hod = login(app, "hod@cmrit.local")
    r = hod.post("/programs/1/audit/run", data={"_csrf": tok(hod, "/programs/1"), "kind": "ai"}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "AI review did not run" in html and "Criterion 5: grade" in html
