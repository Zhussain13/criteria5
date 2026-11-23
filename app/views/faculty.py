"""Faculty list (NBA Table B2.A): add, edit, import from Excel/CSV or an ERP export, and the proofs."""
from __future__ import annotations

import csv
import io
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import (Blueprint, Response, abort, current_app, flash, g, redirect, render_template, request,
                   send_from_directory, url_for)

from .. import proofs as P
from ..db import get_db
from ..faculty_proofs import KINDS, check_proof, institute_aliases, proof_status, recheck_faculty
from ..roster import YEARS, Roster
from ..years import AssessmentYears
from .. import activity
from ..erp import NotConnected, compare, connector
from .auth import login_required, program_access

bp = Blueprint("faculty", __name__, url_prefix="/programs/<int:program_id>/faculty")

FIELDS = ["name", "designation", "designation_at_joining", "designated_on", "joining_date", "leaving_date",
          "qualification", "phd_date", "university", "specialization", "department", "allied", "nature",
          "full_time", "first_year", "is_hod", "apaar_id", "experience_years"]
DATE_FIELDS = {"designated_on", "joining_date", "leaving_date", "phd_date"}
BOOL_FIELDS = {"allied", "full_time", "first_year", "is_hod"}
NATURES = ["Regular", "Contract", "Ad hoc", "Professor of Practice"]
DESIGNATIONS = ["Professor", "Associate Professor", "Assistant Professor", "Professor of Practice"]
DEGREES = ["Ph.D.", "M.Tech", "M.E.", "M.S.", "M.Sc.", "B.E.", "B.Tech", "Other"]

# Column names as they come from an ERP export (e.g. Juno Campus) or the NBA Table B2.A headings.
ALIASES = {
    "name": ["name", "faculty name", "name of the faculty", "employee name", "staff name"],
    "designation": ["designation", "present designation", "current designation", "designation on paper", "post"],
    "designation_at_joining": ["designation at joining", "designation at time of joining in this institution",
                               "designation at the time of joining", "joining designation"],
    "designated_on": ["designated on", "date designated", "date of designation", "promotion date",
                      "the date on which designated as professor associate professor if any"],
    "joining_date": ["joining date", "date of joining", "date of joining in this institution", "doj"],
    "leaving_date": ["leaving date", "date of leaving", "date of leaving if any", "dol", "relieving date"],
    "qualification": ["qualification", "highest degree", "highest qualification", "degree"],
    "phd_date": ["phd date", "ph d date", "phd awarded on", "date of phd", "phd awarded date"],
    "university": ["university", "university of highest degree"],
    "specialization": ["specialization", "area of specialization", "specialisation"],
    "department": ["department", "dept", "department name"],
    "allied": ["allied", "allied department", "is allied"],
    "nature": ["nature", "nature of association", "appointment status", "appointment type", "employment type"],
    "full_time": ["full time", "full time part time", "if contractual mention full time or part time"],
    "first_year": ["first year", "100 first year load", "first year load", "100 first year teaching load"],
    "is_hod": ["hod", "is hod", "head of department"],
    "apaar_id": ["apaar id", "apaar faculty id", "apaar"],
    "experience_years": ["experience", "experience years", "total experience"],
}


def _key(h: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(h or "").lower()).split())


HEADER_MAP = {_key(a): f for f, names in ALIASES.items() for a in names}


def parse_date(s) -> str | None:
    if s is None:
        return None
    if hasattr(s, "strftime"):
        return s.strftime("%Y-%m-%d")
    s = str(s).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%d-%b-%Y", "%d %b %Y",
                "%d %B %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"'{s}' is not a date (use DD-MM-YYYY)")


def parse_bool(v, default=0) -> int:
    t = ("" if v is None else str(v)).strip().lower()
    if not t:
        return default
    return 1 if t in ("1", "yes", "y", "true", "full time", "full-time", "ft", "allied") else 0


def clean_row(raw: dict, program) -> dict:
    """One faculty record from a form or an imported row. Raises ValueError with a plain message."""
    r = {f: raw.get(f, "") for f in FIELDS}
    r = {k: (v.strip() if isinstance(v, str) else v) for k, v in r.items()}
    if not r["name"]:
        raise ValueError("Name is required")
    for f in DATE_FIELDS:
        r[f] = parse_date(r[f])
    dept = (r["department"] or "").strip()
    own = (program["department"] or "").strip().lower()
    allied_default = 1 if dept and own and dept.lower() != own else 0
    r["allied"] = parse_bool(r["allied"], allied_default)
    r["full_time"] = parse_bool(r["full_time"], 1) if str(r["full_time"]).lower() not in ("part time", "part-time", "pt") else 0
    r["first_year"] = parse_bool(r["first_year"])
    r["is_hod"] = parse_bool(r["is_hod"])
    nat = (r["nature"] or "Regular").strip()
    r["nature"] = next((n for n in NATURES if nat.lower().startswith(n.lower()[:5])), nat)
    if "practice" in (r["designation"] or "").lower():
        r["nature"] = "Professor of Practice"
    if r["joining_date"] and r["leaving_date"] and r["leaving_date"] < r["joining_date"]:
        raise ValueError("Leaving date is before the joining date")
    if not r["designation_at_joining"]:
        r["designation_at_joining"] = r["designation"]
    r["experience_years"] = str(r["experience_years"] or "")
    return r


def save_row(db, program_id: int, r: dict, fid: int | None = None, source: str = "manual") -> int:
    cols = FIELDS + ["source", "updated_by", "updated_at"]
    uid = g.user["id"] if g.get("user") else None
    vals = [r[c] for c in FIELDS] + [source, uid, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")]
    if fid:
        db.execute("UPDATE faculty SET " + ", ".join(f"{c} = ?" for c in cols) + " WHERE id = ? AND program_id = ?",
                   (*vals, fid, program_id))
        return fid
    return db.execute("INSERT INTO faculty (program_id, " + ", ".join(cols) + ") VALUES (?, " +
                      ", ".join("?" * len(cols)) + ")", (program_id, *vals)).lastrowid


def read_sheet(upload) -> list[dict]:
    name = upload.filename.lower()
    if name.endswith(".csv"):
        rows = list(csv.DictReader(io.StringIO(upload.read().decode("utf-8-sig"))))
    elif name.endswith(".xlsx"):
        import openpyxl

        ws = openpyxl.load_workbook(upload, read_only=True, data_only=True).active
        it = ws.iter_rows(values_only=True)
        head = [str(h or "") for h in next(it)]
        rows = [{head[i]: v for i, v in enumerate(r) if i < len(head)} for r in it]
    else:
        raise ValueError("Upload a .csv or .xlsx file.")
    out = []
    for row in rows:
        mapped = {}
        for h, v in row.items():
            f = HEADER_MAP.get(_key(h))
            if f and f not in mapped:
                mapped[f] = "" if v is None else v
        if any(str(v).strip() for v in mapped.values()):
            out.append(mapped)
    return out


def _can_edit(acc) -> bool:
    return bool(acc["manage_faculty"])


@bp.route("/")
@login_required
def list_faculty(program_id):
    acc = program_access(program_id)
    p = acc["program"]
    db = get_db()
    faculty = db.execute("SELECT * FROM faculty WHERE program_id = ? ORDER BY allied, "
                         "CASE WHEN is_hod THEN 0 ELSE 1 END, name", (program_id,)).fetchall()
    years = AssessmentYears(p["cay_start_year"], p["ay_start_month"])
    pres = {r["id"]: r for r in Roster(faculty, years).presence_table()}
    st = proof_status(db, program_id)
    counts = {y: sum(1 for r in pres.values() if r["years"][y]) for y in YEARS}
    show = request.args.get("show", "")
    if show == "issues":
        faculty = [f for f in faculty if st.get(f["id"], {}).get("appointment") != "verified"
                   or (f["qualification"] in ("Ph.D.",) and st.get(f["id"], {}).get("degree") != "verified")]
    con = connector()
    last_erp = db.execute("SELECT a.created_at, a.detail, u.name FROM activity a LEFT JOIN users u ON u.id = a.user_id "
                          "WHERE a.program_id = ? AND a.action = 'Imported from ERP' ORDER BY a.id DESC LIMIT 1",
                          (program_id,)).fetchone()
    return render_template("faculty/list.html", p=p, acc=acc, faculty=faculty, pres=pres, st=st, counts=counts,
                           years=[{"name": y, "label": years.label(y)} for y in YEARS], can_edit=_can_edit(acc),
                           show=show, erp=con.status() if con else None, last_erp=last_erp)


@bp.route("/new", methods=("GET", "POST"))
@bp.route("/<int:fid>/edit", methods=("GET", "POST"))
@login_required
def edit(program_id, fid=None):
    acc = program_access(program_id)
    if not _can_edit(acc):
        abort(403)
    p = acc["program"]
    db = get_db()
    f = None
    if fid:
        f = db.execute("SELECT * FROM faculty WHERE id = ? AND program_id = ?", (fid, program_id)).fetchone()
        if f is None:
            abort(404)
    if request.method == "POST":
        form = dict(request.form)
        for b in BOOL_FIELDS:
            form[b] = "1" if request.form.get(b) else "0"
        try:
            r = clean_row(form, p)
        except ValueError as e:
            flash(str(e), "error")
            return render_template("faculty/form.html", p=p, f=form, fid=fid, natures=NATURES,
                                   designations=DESIGNATIONS, degrees=DEGREES)
        new_id = save_row(db, program_id, r, fid)
        recheck_faculty(db, p, new_id)
        activity.log(db, program_id, "Edited faculty" if fid else "Added faculty", r["name"])
        db.commit()
        flash("Saved. The Criterion 5 numbers are counted again from the list.", "ok")
        return redirect(url_for("faculty.detail", program_id=program_id, fid=new_id))
    return render_template("faculty/form.html", p=p, f=dict(f) if f else {"nature": "Regular", "full_time": 1,
                           "department": p["department"]}, fid=fid, natures=NATURES, designations=DESIGNATIONS,
                           degrees=DEGREES)


@bp.route("/<int:fid>")
@login_required
def detail(program_id, fid):
    acc = program_access(program_id)
    p = acc["program"]
    db = get_db()
    f = db.execute("SELECT * FROM faculty WHERE id = ? AND program_id = ?", (fid, program_id)).fetchone()
    if f is None:
        abort(404)
    years = AssessmentYears(p["cay_start_year"], p["ay_start_month"])
    pres = Roster([f], years).presence_table()[0]
    files = db.execute("SELECT * FROM faculty_proofs WHERE faculty_id = ? ORDER BY kind, id", (fid,)).fetchall()
    files = [{**dict(x), "checks": json.loads(x["checks_json"] or "[]"),
              "by": (db.execute("SELECT name FROM users WHERE id = ?", (x["uploaded_by"],)).fetchone() or {"name": ""})["name"]
              if x["uploaded_by"] else ""} for x in files]
    updated_by = db.execute("SELECT name FROM users WHERE id = ?", (f["updated_by"],)).fetchone() if f["updated_by"] else None
    st = proof_status(db, program_id).get(fid, {})
    return render_template("faculty/detail.html", p=p, acc=acc, f=f, pres=pres, files=files, st=st, kinds=KINDS,
                           updated_by=updated_by["name"] if updated_by else "",
                           years=[{"name": y, "label": years.label(y)} for y in YEARS], can_edit=_can_edit(acc))


@bp.route("/<int:fid>/delete", methods=("POST",))
@login_required
def delete(program_id, fid):
    acc = program_access(program_id)
    if not _can_edit(acc):
        abort(403)
    db = get_db()
    row = db.execute("SELECT name FROM faculty WHERE id = ? AND program_id = ?", (fid, program_id)).fetchone()
    if row:
        activity.log(db, program_id, "Removed faculty", row["name"])
    db.execute("DELETE FROM faculty WHERE id = ? AND program_id = ?", (fid, program_id))
    db.commit()
    flash("Removed from the faculty list.", "ok")
    return redirect(url_for("faculty.list_faculty", program_id=program_id))


def _upload_dir(program_id: int) -> Path:
    d = Path(current_app.config["UPLOAD_FOLDER"]) / "faculty" / str(program_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def store_proof(db, program, f, kind: str, filename: str, data: bytes, user_id=None) -> dict:
    """Save one proof file, read it (free OCR) and check it against the faculty record."""
    ext = Path(filename).suffix.lower()
    stored = f"{uuid.uuid4().hex}{ext}"
    path = _upload_dir(program["id"]) / stored
    path.write_bytes(data)
    text = P.extract_text(path)
    res = check_proof(dict(f), kind, text, institute_aliases(program))
    db.execute("INSERT INTO faculty_proofs (faculty_id, kind, original_name, stored_name, sha256, ocr_text, status, "
               "checks_json, uploaded_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (f["id"], kind, filename, stored, P.sha256_of(path), text, res["status"], json.dumps(res["checks"]), user_id))
    return res


@bp.route("/<int:fid>/proofs", methods=("POST",))
@login_required
def upload(program_id, fid):
    acc = program_access(program_id)
    if not _can_edit(acc):
        abort(403)
    p = acc["program"]
    db = get_db()
    f = db.execute("SELECT * FROM faculty WHERE id = ? AND program_id = ?", (fid, program_id)).fetchone()
    kind = request.form.get("kind", "")
    if f is None or kind not in KINDS:
        abort(400)
    n, bad = 0, 0
    for up in request.files.getlist("files"):
        if not up or not up.filename:
            continue
        ext = up.filename.rsplit(".", 1)[-1].lower()
        if ext not in current_app.config["ALLOWED_EXTENSIONS"]:
            flash(f"{up.filename}: only PDF, PNG or JPG files.", "error")
            continue
        res = store_proof(db, p, f, kind, up.filename, up.read(), g.user["id"])
        n += 1
        bad += res["status"] != "verified"
        activity.log(db, program_id, "Uploaded proof",
                     f"{KINDS[kind]} of {f['name']}: " + ("checked" if res["status"] == "verified" else "does not match"))
    db.commit()
    if n:
        flash(f"{n} file(s) read and checked." + (f" {bad} need attention: see the reasons below." if bad else " All good."),
              "ok" if not bad else "error")
    return redirect(url_for("faculty.detail", program_id=program_id, fid=fid))


@bp.route("/<int:fid>/proofs/<int:proof_id>")
@login_required
def view_proof(program_id, fid, proof_id):
    program_access(program_id)
    row = get_db().execute("SELECT fp.* FROM faculty_proofs fp JOIN faculty f ON f.id = fp.faculty_id "
                           "WHERE fp.id = ? AND fp.faculty_id = ? AND f.program_id = ?",
                           (proof_id, fid, program_id)).fetchone()
    if row is None:
        abort(404)
    return send_from_directory(_upload_dir(program_id), row["stored_name"], download_name=row["original_name"])


@bp.route("/<int:fid>/proofs/<int:proof_id>/delete", methods=("POST",))
@login_required
def delete_proof(program_id, fid, proof_id):
    acc = program_access(program_id)
    if not _can_edit(acc):
        abort(403)
    db = get_db()
    activity.log(db, program_id, "Removed proof", f"from faculty #{fid}")
    db.execute("DELETE FROM faculty_proofs WHERE id = ? AND faculty_id IN (SELECT id FROM faculty WHERE id = ? AND "
               "program_id = ?)", (proof_id, fid, program_id))
    db.commit()
    flash("File removed.", "ok")
    return redirect(url_for("faculty.detail", program_id=program_id, fid=fid))


@bp.route("/import", methods=("POST",))
@login_required
def import_list(program_id):
    acc = program_access(program_id)
    if not _can_edit(acc):
        abort(403)
    p = acc["program"]
    db = get_db()
    up = request.files.get("sheet")
    if not up or not up.filename:
        flash("Choose the Excel or CSV file.", "error")
        return redirect(url_for("faculty.list_faculty", program_id=program_id))
    try:
        rows = read_sheet(up)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("faculty.list_faculty", program_id=program_id))
    replace = request.form.get("mode") == "replace"
    existing = {P.normalise_name(f["name"]): f["id"] for f in
                db.execute("SELECT id, name FROM faculty WHERE program_id = ?", (program_id,))}
    added = updated = 0
    errors = []
    for i, raw in enumerate(rows, start=2):
        try:
            r = clean_row(raw, p)
        except ValueError as e:
            errors.append(f"Row {i}: {e}")
            continue
        fid = existing.get(P.normalise_name(r["name"]))
        if fid and replace:
            save_row(db, program_id, r, fid, source="file")
            recheck_faculty(db, p, fid)
            updated += 1
        elif fid:
            continue
        else:
            existing[P.normalise_name(r["name"])] = save_row(db, program_id, r, source="file")
            added += 1
    activity.log(db, program_id, "Imported faculty file", f"{up.filename}: {added} added, {updated} updated")
    db.commit()
    for e in errors[:10]:
        flash(e, "error")
    flash(f"Imported: {added} added, {updated} updated" + (f", {len(errors)} rows skipped" if errors else "") + ".", "ok")
    return redirect(url_for("faculty.list_faculty", program_id=program_id))


@bp.route("/template.csv")
@login_required
def template(program_id):
    program_access(program_id)
    head = ["Name", "Designation", "Designation at joining", "Date designated", "Date of joining", "Date of leaving",
            "Highest degree", "PhD date", "University", "Area of specialization", "Department", "Allied",
            "Nature of association", "Full time", "First year", "HoD", "APAAR ID", "Experience"]
    sample = ["Associate Professor 1", "Associate Professor", "Assistant Professor", "01-07-2023", "15-07-2018", "",
              "Ph.D.", "20-03-2022", "VTU", "Machine Learning", "CSE", "No", "Regular", "Yes", "No", "No", "", "12"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(head)
    w.writerow(sample)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=faculty_list_template.csv"})


@bp.route("/recheck", methods=("POST",))
@login_required
def recheck_all(program_id):
    acc = program_access(program_id)
    if not _can_edit(acc):
        abort(403)
    db = get_db()
    for f in db.execute("SELECT id FROM faculty WHERE program_id = ?", (program_id,)).fetchall():
        recheck_faculty(db, acc["program"], f["id"])
    db.commit()
    flash("All proofs checked again.", "ok")
    return redirect(url_for("faculty.list_faculty", program_id=program_id))


# ------------------------------------------------------------------ ERP link

@bp.route("/erp")
@login_required
def erp_preview(program_id):
    """Read the ERP and show what would change, before anything is saved."""
    acc = program_access(program_id)
    if not _can_edit(acc):
        abort(403)
    p = acc["program"]
    con = connector()
    if con is None:
        abort(404)
    status = con.status()
    diff, students, error = None, {}, ""
    try:
        diff = compare(get_db(), p, con.faculty(p))
        students = con.students(p)
    except NotConnected as e:
        error = str(e)
    db = get_db()
    typed = {(r["key"], r["year"]): r["value"] for r in db.execute(
        "SELECT key, year, value FROM metrics WHERE program_id = ? AND key IN ('S', 'DS')", (program_id,))}
    years = AssessmentYears(p["cay_start_year"], p["ay_start_month"])
    stu_rows = [{"key": k, "year": y, "label": years.label(y), "erp": v, "now": typed.get((k, y))}
                for (k, y), v in sorted(students.items())]
    return render_template("faculty/erp.html", p=p, acc=acc, status=status, diff=diff, students=stu_rows, error=error)


@bp.route("/erp", methods=("POST",))
@login_required
def erp_import(program_id):
    """Copy the ERP data into the SAR tables: new faculty, changed details, student strength."""
    acc = program_access(program_id)
    if not _can_edit(acc):
        abort(403)
    p = acc["program"]
    con = connector()
    if con is None:
        abort(404)
    db = get_db()
    try:
        diff = compare(db, p, con.faculty(p))
        students = con.students(p)
    except NotConnected as e:
        flash(str(e), "error")
        return redirect(url_for("faculty.erp_preview", program_id=program_id))
    for r in diff["new"]:
        save_row(db, program_id, r, source="erp")
    for ch in diff["changed"]:
        save_row(db, program_id, ch["row"], ch["id"], source="erp")
        recheck_faculty(db, p, ch["id"])
    n_stu = 0
    if request.form.get("students"):
        for (k, y), v in students.items():
            db.execute("INSERT INTO metrics (program_id, key, year, value, updated_by) VALUES (?, ?, ?, ?, ?) "
                       "ON CONFLICT(program_id, key, year) DO UPDATE SET value = excluded.value, "
                       "updated_by = excluded.updated_by, updated_at = datetime('now')",
                       (program_id, k, y, v, g.user["id"]))
            n_stu += 1
    msg = (f"{len(diff['new'])} new faculty, {len(diff['changed'])} updated"
           + (f", {n_stu} student numbers" if n_stu else ""))
    activity.log(db, program_id, "Imported from ERP", msg)
    db.commit()
    flash(f"Imported from the ERP: {msg}. The Criterion 5 numbers and SAR tables are updated.", "ok")
    return redirect(url_for("faculty.list_faculty", program_id=program_id))
