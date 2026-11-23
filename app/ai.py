"""AI audit with Google's free Gemini API key (AI Studio): an NBA peer-team style review of the written SAR.

What the AI does, and what it does not:
- The MARKS of 5.1-5.5 always come from the NBA formulas in the code (exact). The AI never changes them.
- The AI reads the SAR as a peer team would: the website's SAR part plus the department's write-up of every
  section, and gives each section a review score (0-10), remarks, what is missing, any place where the text
  does not agree with the numbers in the tables, and the questions the peer team is likely to ask.
- ONE request reviews the whole criterion (saves the free quota). Answers are cached: the same SAR text is never
  sent twice. If the free quota is used up or the key is wrong, the audit still runs without the AI part.

The key: set GEMINI_API_KEY (environment, e.g. on Render), or the platform owner pastes it once under
Platform › AI settings. Free keys come from https://aistudio.google.com/apikey. On the free tier Google may use
the content to improve its products: use dummy data, or a paid key / a campus model for real SAR data.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.request

from flask import current_app, g

from .db import get_db

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# tried in this order; a model the key cannot use (404) is skipped
DEFAULT_MODELS = ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash", "gemma-3-27b-it"]
TIMEOUT, RETRIES, BACKOFF = 90, 2, 5


def _setting(key: str) -> str:
    try:
        row = get_db().execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else ""
    except Exception:
        return ""


def api_key() -> str:
    return current_app.config.get("GEMINI_API_KEY") or _setting("gemini_api_key")


def models() -> list[str]:
    chosen = current_app.config.get("AI_MODEL") or _setting("ai_model")
    return ([chosen] if chosen else []) + [m for m in DEFAULT_MODELS if m != chosen]


def enabled() -> bool:
    return bool(api_key()) and not g.get("ai_quota_exhausted")


def describe() -> str:
    return g.get("ai_model_used") or models()[0]


class AIError(Exception):
    pass


def _parse_json(text: str):
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def call(prompt: str) -> dict:
    """Send one prompt; returns the JSON answer. Raises AIError with a plain reason."""
    key = api_key()
    if not key:
        raise AIError("No Gemini API key set.")
    ck = hashlib.sha256(("c5:" + prompt).encode()).hexdigest()
    db = get_db()
    row = db.execute("SELECT response FROM ai_cache WHERE key = ?", (ck,)).fetchone()
    if row:
        return json.loads(row["response"])
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                       "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}).encode()
    last = ""
    for model in models():
        req = urllib.request.Request(ENDPOINT.format(model=model), data=body,
                                     headers={"Content-Type": "application/json", "x-goog-api-key": key})
        for attempt in range(RETRIES + 1):
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                    data = json.loads(resp.read())
                parts = data["candidates"][0]["content"]["parts"]
                result = _parse_json("".join(p.get("text", "") for p in parts if not p.get("thought")))
                db.execute("INSERT OR REPLACE INTO ai_cache (key, response) VALUES (?, ?)", (ck, json.dumps(result)))
                db.commit()
                g.ai_model_used = model
                return result
            except urllib.error.HTTPError as e:
                msg = ""
                try:
                    msg = e.read().decode("utf-8", "ignore")
                except Exception:
                    pass
                if e.code in (400, 401, 403) and ("API key" in msg or "API_KEY" in msg or e.code in (401, 403)):
                    raise AIError("The Gemini API key was refused. Check the key in AI settings.")
                if e.code == 404:
                    last = f"model {model} not available for this key"
                    break  # try the next model
                if e.code == 429 and ("PerDay" in msg or "per day" in msg.lower()):
                    g.ai_quota_exhausted = True
                    raise AIError("The free Gemini quota for today is used up. Try again tomorrow.")
                if e.code in (429, 500, 502, 503, 504) and attempt < RETRIES:
                    time.sleep(BACKOFF * (attempt + 1))
                    continue
                last = f"Gemini error {e.code}"
                break
            except (TimeoutError, OSError, KeyError, ValueError) as e:
                if attempt < RETRIES:
                    time.sleep(BACKOFF * (attempt + 1))
                    continue
                last = f"no answer from Gemini ({type(e).__name__})"
                break
    raise AIError("The AI review could not run: " + (last or "unknown error") + ".")


PROMPT = """You are the program evaluator of an NBA (National Board of Accreditation, India) peer team, auditing
Criterion 5 "Faculty Information" (100 marks) of a UG Engineering Tier I SAR (SAR format of April 2026).

The marks of every item were already computed with the NBA formulas and are FINAL: do not change or recompute
them. Your job is what a peer team does on top of the numbers: judge how well each section is presented and
justified, find gaps, find any sentence in the department's write-up that does not agree with the numbers in the
tables, and list the questions you would ask during the visit. Be specific and short. Use only what is given.

Program: {program}
Assessment years: {years}

{sections}

Answer in JSON only, exactly in this shape:
{{"overall_score": <0-100, quality of the whole Criterion 5 write-up>,
  "summary": "<3 sentences: overall impression as an evaluator>",
  "sections": [{{"code": "5.1", "score": <0-10>, "verdict": "<one line>",
                 "strengths": ["..."], "gaps": ["..."], "data_checks": ["<text vs table mismatch, if any>"],
                 "questions": ["<peer-team question>"]}}, ... one per section ...],
  "top_actions": ["<most important fix before the visit>", "...", "..."]}}
"""


def _plain(html: str) -> str:
    t = re.sub(r"<(br|/p|/tr|/h\d|/li)[^>]*>", "\n", html or "")
    t = re.sub(r"</t[dh]>", " | ", t)
    t = re.sub(r"<[^>]+>", "", t)
    import html as H

    return re.sub(r"\n\s*\n+", "\n", H.unescape(t)).strip()


def review(program, sections: list[dict], years: str) -> dict:
    """sections: [{code, title, max, marks, auto_html, writeup_html}] -> AI review (dict)."""
    blocks = []
    for s in sections:
        blocks.append(f"=== {s['code']} {s['title']} — marks by NBA formula: {s['marks']} / {s['max']} ===\n"
                      f"[SAR part, written from the data]\n{_plain(s['auto_html'])[:6000]}\n"
                      f"[Department's write-up]\n{_plain(s['writeup_html'])[:4000] or '(empty)'}\n")
    prompt = PROMPT.format(program=f"{program['name']} ({program['department']}), {program['institute_name']}",
                           years=years, sections="\n".join(blocks))
    out = call(prompt)
    secs = {str(x.get("code")): x for x in out.get("sections", []) if isinstance(x, dict)}
    clean = []
    for s in sections:
        x = secs.get(s["code"], {})
        try:
            score = max(0.0, min(10.0, float(x.get("score", 0))))
        except (TypeError, ValueError):
            score = 0.0
        clean.append({"code": s["code"], "title": s["title"], "score": round(score, 1),
                      "verdict": str(x.get("verdict", ""))[:300],
                      **{k: [str(v)[:400] for v in (x.get(k) or [])][:6] for k in ("strengths", "gaps", "data_checks", "questions")}})
    try:
        overall = max(0, min(100, round(float(out.get("overall_score", 0)))))
    except (TypeError, ValueError):
        overall = round(10 * sum(c["score"] for c in clean) / max(1, len(clean)))
    return {"model": describe(), "overall_score": overall, "summary": str(out.get("summary", ""))[:1200],
            "sections": clean, "top_actions": [str(a)[:400] for a in (out.get("top_actions") or [])][:6]}


def test_key() -> str:
    """Small call to check the key works. Returns the model that answered."""
    call('Reply with JSON only: {"ok": true}. Check id: ' + str(time.time()))
    return describe()


def extract_fields(text: str) -> dict:   # proofs are read with free OCR only
    return {}


def review_written(*args, **kwargs):     # not used in Criterion 5 (no written-only items)
    return None
