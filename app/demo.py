"""Demo data so anyone can try the Criterion 5 app in a minute.

    flask --app run seed-demo

Creates CMRIT with one program (B.E. Computer Science and Engineering), a made-up faculty list of about
130 people (Department + two allied departments) and their appointment letters and degree certificates
as PDF files. NO real names: people are named by position and staff code only ("CSE Faculty 001",
"HoD CSE", "Principal"). A few proofs are wrong on purpose (wrong joining date, wrong file, missing
certificate, a copy of someone else's letter) so the audit has something real to find.

Logins (password demo-password; on the login page the user name alone also works):
- Platform owner   owner@cmrsarathi.local
- Principal        principal@cmrit.local
- HoD              hod@cmrit.local
- Criteria head    head@cmrit.local      (Criterion 5)
- Criteria member  member@cmrit.local    (Criterion 5)
"""
from __future__ import annotations

import io
import json
import random
from datetime import date, timedelta

import click
from werkzeug.security import generate_password_hash

DEMO_PASSWORD = "demo-password"
CURRENT_CAY = 2025   # upcoming NBA visit: CAY 2025-26, CAYm1 2024-25, CAYm2 2023-24, CAYm3 2022-23
INSTITUTE = "CMR Institute of Technology"
LOGINS = [
    ("principal", "Principal", "principal"),
    ("hod", "HoD - CSE", "hod"),
    ("head", "Criteria head - Criterion 5", "crit_head"),
    ("member", "Criteria member - Criterion 5", "member"),
]
MEMBER_SECTIONS = "5.1,5.2,5.4"   # the demo criteria member types these SAR sections
DEPTS = [("CSE", "CSE", 0, 64), ("AIML", "CSE (AI & ML)", 1, 34), ("DS", "CSE (Data Science)", 1, 30)]
STUDENTS = {("S", "CAY"): 2160, ("S", "CAYm1"): 2040, ("S", "CAYm2"): 1920,
            ("DS", "CAY"): 1200, ("DS", "CAYm1"): 1140, ("DS", "CAYm2"): 1080,
            ("OTHER_UG", "CAY"): 2, ("APPROVAL_5Y", "CAY"): 1, ("BATCHES_PASSED", "CAY"): 20}
VISITING = [  # SAR Table 5.4.1: Year, name, designation & organisation, course, hours
    ["CAYm1", "Visiting Faculty 01", "Senior Architect, software company", "Cloud Computing", "30"],
    ["CAYm1", "Visiting Faculty 02", "Data Scientist, analytics firm", "Machine Learning Lab", "28"],
    ["CAYm2", "Visiting Faculty 03", "Security Lead, IT services company", "Cyber Security", "40"],
    ["CAYm3", "Visiting Faculty 04", "Engineering Manager, product company", "Software Engineering", "36"],
    ["CAYm3", "Visiting Faculty 05", "Principal Engineer, semiconductor firm", "Computer Architecture", "26"],
]


def _pdf(lines: list[str]) -> bytes:
    """A one-page text PDF (pdfplumber reads it exactly; no OCR needed)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for i, line in enumerate(lines):
        c.setFont("Helvetica-Bold" if i == 0 else "Helvetica", 14 if i == 0 else 11)
        c.drawString(60, y, line)
        y -= 26 if i == 0 else 18
    c.showPage()
    c.save()
    return buf.getvalue()


def appointment_letter(name: str, designation: str, joining: date, dept: str) -> bytes:
    return _pdf([INSTITUTE, "Bengaluru, Karnataka", "", "APPOINTMENT ORDER", "",
                 f"Date: {joining - timedelta(days=10):%d-%m-%Y}", "",
                 f"To: {name}", "",
                 f"You are appointed as {designation} in the Department of {dept}.",
                 f"You joined duty on {joining:%d-%m-%Y}. The appointment is on a regular, full-time basis.",
                 "", "Principal", INSTITUTE])


def degree_certificate(name: str, degree: str, university: str, awarded: date) -> bytes:
    full = {"Ph.D.": "Doctor of Philosophy (Ph.D.)", "M.Tech": "Master of Technology (M.Tech)",
            "M.E.": "Master of Engineering (M.E.)"}[degree]
    return _pdf([university, "", "DEGREE CERTIFICATE", "", "This is to certify that", name,
                 f"has been awarded the degree of {full}", f"on {awarded:%d %B %Y}.", "", "Registrar", university])


def _faculty_rows(rng: random.Random) -> list[dict]:
    """A made-up Table B2.A: about 130 people across the department and two allied departments."""
    rows = []
    cay_start = date(CURRENT_CAY, 7, 1)
    unis = ["Visvesvaraya Technological University", "Bangalore University", "Anna University",
            "Indian Institute of Science", "Mysore University"]
    for code, dept, allied, n in DEPTS:
        for i in range(1, n + 1):
            r = rng.random()
            designation = "Professor" if r < 0.10 else "Associate Professor" if r < 0.30 else "Assistant Professor"
            years_ago = rng.choice([0.2, 0.5, 1.3, 2.4, 3.2, 4.5, 6, 8, 10, 12]) if designation == "Assistant Professor" \
                else rng.choice([3.5, 5, 7, 9, 12, 15])
            joining = cay_start + timedelta(days=int(rng.randint(10, 300) - years_ago * 365))
            phd = designation != "Assistant Professor" and rng.random() < 0.85 or \
                designation == "Assistant Professor" and rng.random() < 0.18
            row = {"name": f"{code} Faculty {i:03d}", "designation": designation, "designation_at_joining": designation,
                   "designated_on": None, "joining_date": joining.isoformat(), "leaving_date": None,
                   "qualification": "Ph.D." if phd else rng.choice(["M.Tech", "M.Tech", "M.E."]),
                   "phd_date": None, "university": rng.choice(unis), "specialization": rng.choice(
                       ["Machine Learning", "Networks", "Data Science", "Cyber Security", "Cloud Computing",
                        "Image Processing", "Algorithms", "IoT"]), "department": dept, "allied": allied,
                   "nature": "Regular" if rng.random() < 0.9 else "Contract", "full_time": 1, "first_year": 0,
                   "is_hod": 0, "apaar_id": "", "experience_years": str(int(years_ago) + rng.randint(0, 6))}
            if phd:
                row["phd_date"] = (joining + timedelta(days=rng.randint(-2000, 2400))).isoformat()
                if date.fromisoformat(row["phd_date"]) > date(CURRENT_CAY, 10, 1):
                    row["phd_date"] = date(CURRENT_CAY - 1, 9, 15).isoformat()
            if designation != "Assistant Professor" and rng.random() < 0.4 and years_ago >= 5:
                row["designation_at_joining"] = "Assistant Professor" if designation == "Associate Professor" else "Associate Professor"
                row["designated_on"] = date(CURRENT_CAY - rng.randint(0, 3), 8, 1).isoformat()
            if rng.random() < 0.08 and years_ago > 1.5:  # some left during the assessment years
                row["leaving_date"] = date(CURRENT_CAY - rng.randint(1, 2), rng.choice([1, 6, 11]), 30 if rng.random() < .5 else 15).isoformat()
            rows.append(row)
    # the HoD of the department: a regular Professor with a Ph.D.
    hod = rows[0]
    hod.update(name="HoD CSE", designation="Professor", designation_at_joining="Associate Professor",
               designated_on=date(2019, 8, 1).isoformat(), joining_date=date(2012, 7, 16).isoformat(), leaving_date=None,
               qualification="Ph.D.", phd_date=date(2011, 3, 18).isoformat(), nature="Regular", is_hod=1)
    for r in rows[5:9]:
        r["first_year"] = 1  # 100% first-year load: counted in FF
    rows.append({**rows[-1], "name": "Professor of Practice 01", "designation": "Professor of Practice",
                 "designation_at_joining": "Professor of Practice", "nature": "Professor of Practice",
                 "qualification": "M.Tech", "phd_date": None, "department": "CSE", "allied": 0,
                 "joining_date": date(CURRENT_CAY - 2, 8, 1).isoformat(), "leaving_date": None, "designated_on": None})
    rows.append({**rows[-1], "name": "Adjunct Faculty 01", "nature": "Contract", "full_time": 0,
                 "designation": "Assistant Professor", "designation_at_joining": "Assistant Professor"})
    return rows


def seed(app) -> dict:
    from .db import get_db, init_db
    from .faculty_proofs import proof_status
    from .views.faculty import save_row, store_proof

    with app.app_context():
        init_db()
        db = get_db()
        if db.execute("SELECT 1 FROM users WHERE email = 'principal@cmrit.local'").fetchone():
            return {"skipped": True}
        pw = generate_password_hash(DEMO_PASSWORD)
        row = db.execute("SELECT id FROM institutions WHERE code = 'cmrit'").fetchone()
        iid = row[0] if row else db.execute(
            "INSERT INTO institutions (name, short_name, code) VALUES (?, 'CMRIT', 'cmrit')", (INSTITUTE,)).lastrowid
        if not db.execute("SELECT 1 FROM users WHERE role = 'superadmin'").fetchone():
            db.execute("INSERT INTO users (name, email, password_hash, role) VALUES "
                       "('Platform owner', 'owner@cmrsarathi.local', ?, 'superadmin')", (pw,))
        pid = db.execute(
            "INSERT INTO programs (name, department, institute_name, tier, sar_version, cay_start_year, ay_start_month, "
            "institution_id) VALUES ('B.E. Computer Science and Engineering', 'CSE', ?, 'Tier I', "
            "'criterion5_tier1_2026', ?, 7, ?)", (f"{INSTITUTE}, CMRIT", CURRENT_CAY, iid)).lastrowid
        users = {}
        for key, name, role in LOGINS:
            users[key] = db.execute("INSERT INTO users (name, email, password_hash, role, institution_id) VALUES (?, ?, ?, ?, ?)",
                                    (name, f"{key}@cmrit.local", pw, role, iid)).lastrowid
            if role != "principal":
                db.execute("INSERT INTO program_members (program_id, user_id, criteria, sections) VALUES (?, ?, ?, ?)",
                           (pid, users[key], "" if role == "hod" else "5", MEMBER_SECTIONS if role == "member" else ""))
        for (k, y), v in STUDENTS.items():
            db.execute("INSERT INTO metrics (program_id, key, year, value) VALUES (?, ?, ?, ?)", (pid, k, y, v))

        rng = random.Random(5)
        rows = _faculty_rows(rng)
        prog = db.execute("SELECT * FROM programs WHERE id = ?", (pid,)).fetchone()
        ids = [save_row(db, pid, r, source="erp") for r in rows]   # as if read from the ERP last month
        faculty = {f["id"]: f for f in db.execute("SELECT * FROM faculty WHERE program_id = ?", (pid,))}

        # proofs: most correct, a few wrong on purpose (what a peer team would catch)
        no_letter = set(rng.sample(ids[10:], 5))
        wrong_date = set(rng.sample([i for i in ids[10:] if i not in no_letter], 3))
        wrong_file = set(rng.sample([i for i in ids[10:] if i not in no_letter | wrong_date], 2))
        phd_ids = [i for i in ids if faculty[i]["qualification"] == "Ph.D." and faculty[i]["name"] != "HoD CSE"]
        no_cert = set(rng.sample(phd_ids, 4))
        mtech_cert = set(rng.sample([i for i in phd_ids if i not in no_cert], 2))
        first_letter = None
        for fid in ids:
            f = faculty[fid]
            if fid not in no_letter:
                j = date.fromisoformat(f["joining_date"])
                name = f["name"]
                if fid in wrong_date:
                    j = j + timedelta(days=rng.choice([31, 92, 365]))
                if fid in wrong_file:
                    name = faculty[ids[ids.index(fid) - 1]]["name"]  # the neighbour's letter uploaded by mistake
                data = appointment_letter(name, f["designation_at_joining"] or f["designation"], j, f["department"])
                if fid == ids[20]:
                    first_letter = (fid, data)
                store_proof(db, prog, f, "appointment", f"appointment_{f['name'].replace(' ', '_')}.pdf", data)
            if f["qualification"] in ("Ph.D.", "M.Tech", "M.E.") and fid not in no_cert:
                deg = "M.Tech" if fid in mtech_cert else f["qualification"]
                awarded = date.fromisoformat(f["phd_date"]) if f["phd_date"] and deg == "Ph.D." else date(2008, 9, 1)
                store_proof(db, prog, f, "degree", f"degree_{f['name'].replace(' ', '_')}.pdf",
                            degree_certificate(f["name"], deg, f["university"], awarded))
        # one copied letter: the same file uploaded for a second person
        dup_target = faculty[ids[30]]
        db.execute("DELETE FROM faculty_proofs WHERE faculty_id = ? AND kind = 'appointment'", (dup_target["id"],))
        store_proof(db, prog, dup_target, "appointment", "appointment_copy.pdf", first_letter[1])

        # 5.4 visiting faculty table + student-strength document for 5.1
        cid = db.execute("INSERT INTO claims (program_id, criterion, item_code, title, sar_text, tables_json, created_by) "
                         "VALUES (?, 5, '5.4', 'Visiting / adjunct faculty (Table 5.4.1)', ?, ?, ?)",
                         (pid, "Industry experts handled courses and labs every year.",
                          json.dumps({"5.4.1": {"fields": {}, "rows": VISITING}}), users["head"])).lastrowid
        _attach(app, db, cid, "visiting_faculty_hours.pdf",
                _pdf([INSTITUTE, "Visiting faculty: hours handled", ""] + [" | ".join(v) for v in VISITING]))
        cid = db.execute("INSERT INTO claims (program_id, criterion, item_code, title, sar_text, created_by) VALUES "
                         "(?, 5, '5.1', 'Student strength, Department + allied (CAY, CAYm1, CAYm2)', ?, ?)",
                         (pid, "Sanctioned and admitted students as per the ERP admissions data.", users["member"])).lastrowid
        _attach(app, db, cid, "student_strength.pdf",
                _pdf([INSTITUTE, "Student strength (UG + PG), Department of CSE and allied departments", "",
                      "2025-26: 2160 (Department alone 1200)", "2024-25: 2040 (Department alone 1140)",
                      "2023-24: 1920 (Department alone 1080)"]))
        # the SAR text drafted by the website; the member then adds a line to 5.4
        from .sar_draft import draft_all

        draft_all(db, prog, users["head"])
        db.execute("UPDATE sar_sections SET html = html || ?, drafted = 0, version = version + 1, updated_by = ?, "
                   "updated_at = datetime('now', '-2 days') WHERE program_id = ? AND code = '5.4'",
                   ("<p>Courses handled: Cloud Computing, Machine Learning Lab, Cyber Security, Software Engineering and "
                    "Computer Architecture. Students rated these sessions highly in the course-end feedback.</p>",
                    users["member"], pid))
        # what the team did so far (shown to the head, HoD and principal under "Recent updates")
        for when, who, action, detail in [
            ("-20 days", "member", "Imported from ERP", f"{len(ids)} new faculty, 0 updated, 6 student numbers"),
            ("-18 days", "member", "Uploaded proof", "appointment letters and degree certificates of the department"),
            ("-16 days", "head", "Uploaded proof", "appointment letters and degree certificates of the allied departments"),
            ("-12 days", "head", "Added to 5.4", "Visiting / adjunct faculty (Table 5.4.1)"),
            ("-11 days", "member", "Added to 5.1", "Student strength, Department + allied"),
            ("-10 days", "hod", "Updated numbers", "pre-qualifier facts on the Criterion 5 data sheet"),
            ("-9 days", "hod", "Assigned SAR sections", f"Criteria member - Criterion 5: {MEMBER_SECTIONS.replace(',', ', ')}"),
            ("-3 days", "head", "Drafted the SAR", "5 section(s) drafted"),
            ("-2 days", "member", "Wrote SAR 5.4", "Visiting/adjunct faculty/professor of practice"),
        ]:
            db.execute("INSERT INTO activity (program_id, user_id, action, detail, created_at) VALUES "
                       "(?, ?, ?, ?, datetime('now', ?))", (pid, users[who], action, detail, when))
        db.commit()
        from .views.audit import _audit_and_save

        _audit_and_save(pid, users["hod"], "hod")
        st = proof_status(db, pid)
        return {"skipped": False, "program": pid, "faculty": len(ids),
                "letters_ok": sum(1 for v in st.values() if v.get("appointment") == "verified")}


def _attach(app, db, claim_id: int, filename: str, data: bytes):
    import hashlib
    import uuid
    from pathlib import Path

    from . import proofs as P

    stored = f"{uuid.uuid4().hex}.pdf"
    path = Path(app.config["UPLOAD_FOLDER"]) / stored
    path.write_bytes(data)
    text = P.extract_text(path)
    db.execute("INSERT INTO proofs (claim_id, original_name, stored_name, sha256, ocr_text, extracted_json) "
               "VALUES (?, ?, ?, ?, ?, ?)", (claim_id, filename, stored, hashlib.sha256(data).hexdigest(), text,
                                             json.dumps(P.extract_fields(text))))


@click.command("seed-demo")
def seed_demo_command():
    """Create the demo: CMRIT, B.E. CSE, a made-up faculty list with proofs, and the role logins."""
    from flask import current_app

    out = seed(current_app._get_current_object())
    if out.get("skipped"):
        click.echo("Demo data already there.")
        return
    click.echo(f"Demo ready: {out['faculty']} faculty, {out['letters_ok']} appointment letters checked.")
    click.echo("Logins (password demo-password): owner@cmrsarathi.local (platform owner), principal@cmrit.local, "
               "hod@cmrit.local, head@cmrit.local, member@cmrit.local")
