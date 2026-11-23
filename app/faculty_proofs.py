"""Proofs behind the faculty list, checked the way a peer team checks them (free OCR, no AI).

Each faculty member needs (NBA List of Documents, file P.21):
- appointment letter      -> name, this institute, date of joining, designation
- highest degree certificate -> name, the degree (Ph.D. / M.Tech / M.E.), university
- promotion letter (optional) -> name, the new designation, date of designation

The text is read with pdfplumber (text PDFs) or Tesseract OCR (scans/photos), both free and local.
Each check gives a plain reason, so the department knows exactly what to fix.
"""
from __future__ import annotations

import json
import re
from datetime import date

from . import proofs as P
from .roster import ASSOC, ASST, POP, PROF, _d, cadre_of, degree_of

KINDS = {
    "appointment": "Appointment letter",
    "degree": "Highest degree certificate",
    "promotion": "Promotion / designation letter",
}

DEGREE_WORDS = {
    "phd": [r"doctor\s+of\s+philosophy", r"ph\.?\s*d\b", r"doctorate"],
    "mtech": [r"master\s+of\s+technology", r"master\s+of\s+engineering", r"\bm\.?\s*tech\b", r"\bm\.?\s*e\.?\b",
              r"master\s+of\s+science\s*\(?\s*engg"],
}
CADRE_WORDS = {
    PROF: [r"(?<!associate )(?<!assistant )\bprofessor\b"],
    ASSOC: [r"associate\s+professor"],
    ASST: [r"assistant\s+professor", r"asst\.?\s+prof"],
    POP: [r"professor\s+of\s+practice"],
}


def _has(patterns: list[str], text: str) -> bool:
    t = " ".join(text.lower().split())
    return any(re.search(p, t) for p in patterns)


def _check(ok: bool | None, text: str) -> dict:
    return {"ok": ok, "text": text}


def check_proof(f: dict, kind: str, text: str, institute_aliases: list[str]) -> dict:
    """Check one proof file against the faculty record. Returns {status, checks: [...]}."""
    checks = []
    if len(text.strip()) < 15:
        return {"status": "problem", "checks": [_check(False, "No readable text. Upload a clearer scan or the PDF.")]}
    name_ok = P.name_in_text(f["name"], text)
    checks.append(_check(name_ok, "Name on the document matches the faculty list" if name_ok else
                         f"Name '{f['name']}' not found on the document"))
    dates = P.find_dates(text)
    if kind == "appointment":
        inst_ok = any(a and a in " ".join(text.lower().split()) for a in institute_aliases)
        checks.append(_check(inst_ok, "Issued by this institute" if inst_ok else "Institute name not found on the letter"))
        j = _d(f.get("joining_date"))
        if j is None:
            checks.append(_check(None, "No joining date in the faculty list to compare with"))
        elif j in dates:
            checks.append(_check(True, f"Joining date {j:%d-%m-%Y} found on the letter"))
        else:
            seen = ", ".join(d.strftime("%d-%m-%Y") for d in dates[:3]) or "no date"
            checks.append(_check(False, f"Joining date {j:%d-%m-%Y} not on the letter (letter shows {seen})"))
        want = cadre_of(f.get("designation_at_joining") or f.get("designation"))
        if want:
            ok = _has(CADRE_WORDS[want], text)
            checks.append(_check(ok, f"Designation '{want}' found" if ok else f"Designation '{want}' not found on the letter"))
    elif kind == "degree":
        deg = degree_of(f.get("qualification"))
        if deg in DEGREE_WORDS:
            ok = _has(DEGREE_WORDS[deg], text)
            label = "Ph.D." if deg == "phd" else "M.Tech / M.E."
            checks.append(_check(ok, f"{label} degree found on the certificate" if ok else
                                 f"The certificate does not show a {label} degree"))
            if deg == "phd" and _d(f.get("phd_date")):
                pd = _d(f["phd_date"])
                ok = pd in dates or any(d.year == pd.year for d in dates)
                checks.append(_check(ok, f"Ph.D. award date ({pd:%d-%m-%Y}) matches" if ok else
                                     f"Ph.D. award date {pd:%d-%m-%Y} not on the certificate"))
        else:
            checks.append(_check(None, "Highest degree is not Ph.D./M.Tech: this does not change the marks"))
        uni = (f.get("university") or "").strip()
        if uni:
            words = [w for w in re.findall(r"[a-z]{4,}", uni.lower()) if w not in {"university", "institute", "technology"}]
            ok = all(w in text.lower() for w in words) if words else None
            checks.append(_check(ok, f"University '{uni}' found" if ok else f"University '{uni}' not found"))
    elif kind == "promotion":
        want = cadre_of(f.get("designation"))
        if want:
            ok = _has(CADRE_WORDS[want], text)
            checks.append(_check(ok, f"New designation '{want}' found" if ok else f"Designation '{want}' not found"))
        on = _d(f.get("designated_on"))
        if on:
            ok = on in dates
            checks.append(_check(ok, f"Date of designation {on:%d-%m-%Y} found" if ok else
                                 f"Date of designation {on:%d-%m-%Y} not on the letter"))
    bad = [c for c in checks if c["ok"] is False]
    return {"status": "verified" if not bad else "problem", "checks": checks}


def institute_aliases(program) -> list[str]:
    return [a.strip().lower() for a in (program["institute_name"] or "").split(",") if a.strip()]


def recheck_faculty(db, program, faculty_id: int):
    """Re-run the checks of every proof of one person (after their details are edited)."""
    f = db.execute("SELECT * FROM faculty WHERE id = ?", (faculty_id,)).fetchone()
    if f is None:
        return
    for pr in db.execute("SELECT * FROM faculty_proofs WHERE faculty_id = ?", (faculty_id,)).fetchall():
        res = check_proof(dict(f), pr["kind"], pr["ocr_text"], institute_aliases(program))
        db.execute("UPDATE faculty_proofs SET status = ?, checks_json = ? WHERE id = ?",
                   (res["status"], json.dumps(res["checks"]), pr["id"]))


def proof_status(db, program_id: int) -> dict:
    """{faculty_id: {'appointment': 'verified'|'problem'|'missing', 'degree': ..., 'duplicate': bool}}."""
    rows = db.execute(
        "SELECT fp.*, f.qualification FROM faculty_proofs fp JOIN faculty f ON f.id = fp.faculty_id "
        "WHERE f.program_id = ?", (program_id,)).fetchall()
    owners: dict = {}
    for r in rows:
        owners.setdefault(r["sha256"], set()).add(r["faculty_id"])
    out: dict = {}
    for r in rows:
        st = out.setdefault(r["faculty_id"], {})
        dup = len(owners[r["sha256"]]) > 1
        status = "problem" if dup else r["status"]
        if st.get(r["kind"]) != "verified":  # one good file is enough
            st[r["kind"]] = status
        st["duplicate"] = st.get("duplicate", False) or dup
    return out


def faculty_issues(db, program, years_obj) -> list[dict]:
    """Every faculty member whose proofs would not satisfy a peer team, with the fix."""
    from .roster import Roster, YEARS

    pid = program["id"]
    faculty = db.execute("SELECT * FROM faculty WHERE program_id = ? ORDER BY name", (pid,)).fetchall()
    st = proof_status(db, pid)
    ros = Roster(faculty, years_obj)
    detail = {}
    for r in db.execute("SELECT fp.faculty_id, fp.kind, fp.checks_json FROM faculty_proofs fp JOIN faculty f "
                        "ON f.id = fp.faculty_id WHERE f.program_id = ?", (pid,)):
        bad = [c["text"] for c in json.loads(r["checks_json"] or "[]") if c.get("ok") is False]
        if bad:
            detail.setdefault((r["faculty_id"], r["kind"]), []).extend(bad)
    out = []
    for f in faculty:
        counted = [y for y in YEARS if ros.present(f, y)]
        if not counted:
            continue
        s = st.get(f["id"], {})
        problems = []
        a = s.get("appointment", "missing")
        if a == "missing":
            problems.append(("appointment", "No appointment letter uploaded",
                             "Upload the appointment letter (P.21)", "Show the appointment letter of " + f["name"] + "."))
        elif a == "problem":
            why = "; ".join(detail.get((f["id"], "appointment"), [])) or "same file used for another person"
            problems.append(("appointment", "Appointment letter does not match: " + why,
                             "Correct the faculty details or upload the right letter",
                             f"The appointment letter of {f['name']} does not match the faculty list. Please explain."))
        deg = degree_of(f["qualification"])
        if deg in ("phd", "mtech"):
            d = s.get("degree", "missing")
            label = "Ph.D." if deg == "phd" else "M.Tech/M.E."
            if d == "missing":
                problems.append(("degree", f"No {label} certificate uploaded",
                                 f"Upload the {label} degree certificate",
                                 f"Show the {label} certificate of {f['name']}."))
            elif d == "problem":
                why = "; ".join(detail.get((f["id"], "degree"), [])) or "same file used for another person"
                problems.append(("degree", f"{label} certificate does not match: " + why,
                                 "Check the qualification in the faculty list or upload the right certificate",
                                 f"The {label} certificate of {f['name']} does not match. Please explain."))
        if problems:
            out.append({"id": f["id"], "name": f["name"], "designation": f["designation"],
                        "qualification": f["qualification"], "years": counted,
                        "problems": [{"kind": k, "reason": r, "fix": fx, "question": q} for k, r, fx, q in problems]})
    return out


def today() -> date:
    return date.today()
