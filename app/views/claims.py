"""SAR claims (criterion-wise data entry) and proof upload."""
import json
import re
import uuid
from datetime import datetime, timezone

from markupsafe import escape
from pathlib import Path

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request,
                   send_from_directory, url_for)
from werkzeug.utils import secure_filename

from .. import ai, proofs, rules
from ..richtext import sanitize, to_text
from ..db import get_db
from .audit import running_job
from .auth import login_required, program_access

bp = Blueprint("claims", __name__, url_prefix="/programs/<int:program_id>/criteria")

PERSON_EVENT = ["person_name", "title", "organizer", "start_date"]
FIELDS_BY_TYPE = {
    "points_by_duration": ["person_name", "title", "organizer", "start_date", "end_date"],
    "resource_persons": PERSON_EVENT,
    "faculty_share": PERSON_EVENT,
    "count_events": ["title", "organizer", "start_date", "end_date"],
    "counts": ["kind", "person_name", "title", "start_date"],
    "papers": ["kind", "person_name", "title", "start_date", "indexing", "doi", "affiliation"],
    "amount_bands": ["person_name", "title", "organizer", "start_date", "amount"],
    "seed_money": ["kind", "person_name", "title", "start_date", "amount"],
    "rubric": ["title", "sar_text"],
}
DOCUMENT_FIELDS = ["title", "sar_text"]  # supporting documents for data-sheet items
FIELD_LABELS = {
    "kind": "Type",
    "person_name": "Faculty / student name",
    "title": "Title / description",
    "organizer": "Organised by / agency / organisation",
    "start_date": "Date (start, publication, grant or sanction date)",
    "end_date": "End date",
    "indexing": "Indexing (e.g. Scopus, WoS)",
    "doi": "DOI",
    "affiliation": "Affiliation shown on the publication",
    "amount": "Amount (in lakh)",
    "sar_text": "Text of this SAR section / notes",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def parse_tables(form, specs: list[dict]) -> dict:
    """Read the SAR tables from the form: inputs t{table}_f{field} and t{table}_r{row}_c{col}."""
    out = {}
    for ti, spec in enumerate(specs):
        fields = {name: form.get(f"t{ti}_f{fi}", "").strip() for fi, name in enumerate(spec["fields"])}
        cells = {}
        for key in form.keys():
            m = re.fullmatch(rf"t{ti}_r(\d+)_c(\d+)", key)
            if m and int(m.group(2)) < len(spec["columns"]):
                cells.setdefault(int(m.group(1)), {})[int(m.group(2))] = form.get(key, "").strip()
        rows = [[cells[r].get(c, "") for c in range(len(spec["columns"]))] for r in sorted(cells)]
        rows = [r for r in rows if any(r)]
        labels = set(spec["rows"])
        has_data = any(fields.values()) or any(
            any(v for i, v in enumerate(r) if not (i == 0 and v in labels)) for r in rows)
        if has_data:
            out[spec["id"]] = {"fields": {k: v for k, v in fields.items() if v}, "rows": rows}
    return out


def table_rows_for_form(spec: dict, saved: dict | None) -> list[list[str]]:
    """Rows to show in the form: what was saved, else the format's row labels, else 3 empty rows."""
    n = len(spec["columns"])
    if saved and saved.get("rows"):
        return [(r + [""] * n)[:n] for r in saved["rows"]]
    if spec["rows"]:
        return [[lab] + [""] * (n - 1) for lab in spec["rows"]]
    return [[""] * n for _ in range(3)]


def fields_for(item: dict) -> list[str]:
    if item["type"] in rules.DATA_TYPES:
        return DOCUMENT_FIELDS
    return FIELDS_BY_TYPE[item["type"]]


def _crit_option(db, acc, criterion):
    """Audit button on a criterion page: criteria heads re-check their criteria, the institute team audits its own."""
    from ..permissions import audit_option
    opt = audit_option(db, acc, ai.enabled())
    if opt and acc["audit"] in ("update", "institute") and criterion in opt["scope"]:
        return opt
    return None


def _check_criterion(acc, criterion):
    """403 if the user may not even see this criterion; returns whether they may edit it."""
    if criterion not in range(1, 11):
        abort(404)
    if criterion not in acc["view"]:
        abort(403)
    return criterion in acc["edit"]


def _rich(data: dict):
    """The SAR text comes from the Word-style editor as HTML: clean it and keep a plain-text copy for the audit."""
    if "sar_text" in data:
        html = sanitize(data["sar_text"] or "")
        data["sar_html"] = html
        data["sar_text"] = to_text(html)


@bp.route("/<int:criterion>")
@login_required
def list_claims(program_id, criterion):
    acc = program_access(program_id)
    can_edit = _check_criterion(acc, criterion)
    p = acc["program"]
    rs = rules.load(p["sar_version"])
    items = rules.items_for(p["sar_version"], criterion)
    db = get_db()
    rows = db.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM proofs WHERE claim_id = c.id) AS n_proofs FROM claims c "
        "WHERE program_id = ? AND criterion = ? ORDER BY item_code, start_date", (program_id, criterion)).fetchall()
    team = db.execute("""
        SELECT u.id, u.name, u.role, m.criteria,
               (SELECT COUNT(*) FROM claims c WHERE c.program_id = :p AND c.criterion = :c AND c.created_by = u.id) AS entries,
               (SELECT COUNT(*) FROM claims c WHERE c.program_id = :p AND c.criterion = :c AND c.created_by = u.id
                   AND (c.sar_text != '' OR c.sar_html != '')) AS written,
               (SELECT COUNT(*) FROM proofs pr JOIN claims c ON c.id = pr.claim_id
                   WHERE c.program_id = :p AND c.criterion = :c AND c.created_by = u.id) AS proofs,
               (SELECT MAX(COALESCE(c.updated_at, c.created_at)) FROM claims c
                   WHERE c.program_id = :p AND c.criterion = :c AND (c.created_by = u.id OR c.updated_by = u.id)) AS last
        FROM program_members m JOIN users u ON u.id = m.user_id
        WHERE m.program_id = :p AND u.role IN ('crit_head', 'member')""", {"p": program_id, "c": criterion}).fetchall()
    team = [t for t in team if str(criterion) in (t["criteria"] or "").split(",")]
    return render_template("claims/list.html", p=p, criterion=criterion, crit=rs["criteria"][criterion],
                           items=items, claims=rows, can_edit=can_edit, data_types=rules.DATA_TYPES, team=team,
                           acc=acc, option=_crit_option(db, acc, criterion), job=running_job(program_id),
                           has_data=bool(rules.data_fields_for(p["sar_version"], criterion)))


@bp.route("/<int:criterion>/new", methods=("GET", "POST"))
@login_required
def new_claim(program_id, criterion):
    acc = program_access(program_id)
    if not _check_criterion(acc, criterion):
        abort(403)
    p = acc["program"]
    items = rules.items_for(p["sar_version"], criterion)
    if not items:
        flash("Audit rules for this criterion are not added yet.", "error")
        return redirect(url_for("claims.list_claims", program_id=program_id, criterion=criterion))
    code = request.values.get("item_code") or next(iter(items))
    if code not in items:
        abort(400)
    item = items[code]
    fields = fields_for(item)
    kinds = rules.kinds_for(item)
    specs = rules.tables_for(p["sar_version"], code)
    if request.method == "POST":
        data = {f: request.form.get(f, "").strip() or None for f in fields}
        data["tables_json"] = json.dumps(parse_tables(request.form, specs))
        _rich(data)
        if "kind" in data and kinds and data["kind"] not in {k for k, _ in kinds}:
            flash("Choose the type of entry.", "error")
            return _form(p, criterion, items, code, item, fields, request.form, kinds, specs,
                         parse_tables(request.form, specs))
        if "amount" in data and data["amount"]:
            try:
                data["amount"] = float(data["amount"])
            except ValueError:
                flash("Amount must be a number (in lakh).", "error")
                return _form(p, criterion, items, code, item, fields, request.form, kinds, specs,
                             parse_tables(request.form, specs))
        db = get_db()
        cols = ["program_id", "criterion", "item_code", "created_by", "updated_by", "updated_at"] + list(data)
        vals = [program_id, criterion, code, g.user["id"], g.user["id"], _now()] + [
            ("" if v is None and k not in ("start_date", "end_date", "amount") else v) for k, v in data.items()]
        cur = db.execute(f"INSERT INTO claims ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
        db.commit()
        files = [f for f in request.files.getlist("proof") if f and f.filename]
        n = save_proofs(program_id, cur.lastrowid, files) if files else 0
        flash("Entry saved" + (f" with {n} proof file(s)." if n else ". Now attach the proof."), "ok")
        if request.form.get("next") == "another":
            return redirect(url_for("claims.new_claim", program_id=program_id, criterion=criterion, item_code=code))
        return redirect(url_for("claims.detail", program_id=program_id, criterion=criterion, claim_id=cur.lastrowid))
    return _form(p, criterion, items, code, item, fields, {}, kinds, specs, {})


def _form(p, criterion, items, code, item, fields, form, kinds, specs, saved, claim=None):
    tables = [{**spec, "index": ti, "form_rows": table_rows_for_form(spec, saved.get(spec["id"])),
               "field_values": (saved.get(spec["id"]) or {}).get("fields", {})} for ti, spec in enumerate(specs)]
    return render_template("claims/form.html", p=p, criterion=criterion, items=items, code=code, item=item,
                           fields=fields, labels=FIELD_LABELS, form=form, kinds=kinds, tables=tables, claim=claim)


@bp.route("/<int:criterion>/<int:claim_id>/edit", methods=("GET", "POST"))
@login_required
def edit_claim(program_id, criterion, claim_id):
    acc = program_access(program_id)
    if not _check_criterion(acc, criterion):
        abort(403)
    p = acc["program"]
    c = _get_claim(program_id, criterion, claim_id)
    items = rules.items_for(p["sar_version"], criterion)
    item = items.get(c["item_code"])
    if item is None:
        abort(404)
    fields = fields_for(item)
    kinds = rules.kinds_for(item)
    specs = rules.tables_for(p["sar_version"], c["item_code"])
    if request.method == "POST":
        data = {f: request.form.get(f, "").strip() or None for f in fields}
        if "amount" in data and data["amount"]:
            try:
                data["amount"] = float(data["amount"])
            except ValueError:
                flash("Amount must be a number (in lakh).", "error")
                return _form(p, criterion, items, c["item_code"], item, fields, request.form, kinds, specs,
                             parse_tables(request.form, specs), claim=c)
        data = {k: ("" if v is None and k not in ("start_date", "end_date", "amount") else v) for k, v in data.items()}
        data["tables_json"] = json.dumps(parse_tables(request.form, specs))
        _rich(data)
        data["updated_by"], data["updated_at"] = g.user["id"], _now()
        db = get_db()
        db.execute(f"UPDATE claims SET {', '.join(k + ' = ?' for k in data)} WHERE id = ?", [*data.values(), claim_id])
        db.commit()
        flash("Entry updated.", "ok")
        return redirect(url_for("claims.detail", program_id=program_id, criterion=criterion, claim_id=claim_id))
    form = dict(c)
    form["sar_text"] = c["sar_html"] or escape(c["sar_text"] or "").replace("\n", "<br>")
    return _form(p, criterion, items, c["item_code"], item, fields, form, kinds, specs,
                 json.loads(c["tables_json"] or "{}"), claim=c)


def _get_claim(program_id, criterion, claim_id):
    c = get_db().execute("SELECT * FROM claims WHERE id = ? AND program_id = ? AND criterion = ?",
                         (claim_id, program_id, criterion)).fetchone()
    if c is None:
        abort(404)
    return c


@bp.route("/<int:criterion>/<int:claim_id>")
@login_required
def detail(program_id, criterion, claim_id):
    acc = program_access(program_id)
    can_edit = _check_criterion(acc, criterion)
    c = _get_claim(program_id, criterion, claim_id)
    item = rules.load(acc["program"]["sar_version"])["items"].get(c["item_code"], {})
    pr = get_db().execute("SELECT * FROM proofs WHERE claim_id = ? ORDER BY id", (claim_id,)).fetchall()
    pr = [{**dict(x), "extracted": json.loads(x["extracted_json"] or "{}")} for x in pr]
    saved = json.loads(c["tables_json"] or "{}")
    tables = [{**spec, "saved": saved.get(spec["id"])} for spec in rules.tables_for(acc["program"]["sar_version"], c["item_code"])]
    return render_template("claims/detail.html", p=acc["program"], criterion=criterion, c=c, item=item,
                           proofs=pr, can_edit=can_edit, labels=FIELD_LABELS, tables=tables,
                           fields=fields_for(item) if item else [], kinds=dict(rules.kinds_for(item)) if item else {})


def save_proofs(program_id: int, claim_id: int, files) -> int:
    """Store, read (text/OCR) and check uploaded proof files for one entry. Returns how many were saved."""
    folder = Path(current_app.config["UPLOAD_FOLDER"]) / str(program_id)
    folder.mkdir(parents=True, exist_ok=True)
    db = get_db()
    saved = 0
    for f in files:
        original = secure_filename(f.filename) or "proof"
        ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
        if ext not in current_app.config["ALLOWED_EXTENSIONS"]:
            flash(f"{original}: only PDF, PNG or JPG files are allowed.", "error")
            continue
        stored = f"{uuid.uuid4().hex}.{ext}"
        path = folder / stored
        f.save(path)
        digest = proofs.sha256_of(path)
        text = proofs.extract_text(path)
        fields = proofs.extract_fields(text)
        if not fields.get("start_date") and ai.enabled():  # only pay for AI when the free reader failed
            ai_fields = ai.extract_fields(text)
            for k, v in ai_fields.items():
                fields.setdefault(k, v)
            fields["read_by"] = "ai"
        saved += 1
        db.execute("INSERT INTO proofs (claim_id, original_name, stored_name, sha256, ocr_text, extracted_json) "
                   "VALUES (?, ?, ?, ?, ?, ?)", (claim_id, original, stored, digest, text, json.dumps(fields)))
        dup = db.execute("SELECT COUNT(*) FROM proofs p JOIN claims c ON c.id = p.claim_id "
                         "WHERE p.sha256 = ? AND c.program_id = ? AND p.claim_id != ?",
                         (digest, program_id, claim_id)).fetchone()[0]
        if dup:
            flash(f"{original}: this exact file is already attached to another entry.", "error")
        elif not text:
            flash(f"{original}: uploaded, but no text could be read. A clearer scan will audit better.", "error")
        else:
            flash(f"{original}: uploaded and read.", "ok")
    db.commit()
    return saved


@bp.route("/<int:criterion>/<int:claim_id>/proofs", methods=("POST",))
@login_required
def upload_proof(program_id, criterion, claim_id):
    acc = program_access(program_id)
    if not _check_criterion(acc, criterion):
        abort(403)
    _get_claim(program_id, criterion, claim_id)
    files = [f for f in request.files.getlist("proof") if f and f.filename]
    if not files:
        flash("Choose a file to upload.", "error")
        return redirect(url_for("claims.detail", program_id=program_id, criterion=criterion, claim_id=claim_id))
    save_proofs(program_id, claim_id, files)
    return redirect(url_for("claims.detail", program_id=program_id, criterion=criterion, claim_id=claim_id))


@bp.route("/<int:criterion>/<int:claim_id>/proofs/<int:proof_id>")
@login_required
def view_proof(program_id, criterion, claim_id, proof_id):
    _check_criterion(program_access(program_id), criterion)
    _get_claim(program_id, criterion, claim_id)
    pr = get_db().execute("SELECT * FROM proofs WHERE id = ? AND claim_id = ?", (proof_id, claim_id)).fetchone()
    if pr is None:
        abort(404)
    folder = Path(current_app.config["UPLOAD_FOLDER"]) / str(program_id)
    return send_from_directory(folder, pr["stored_name"], download_name=pr["original_name"])


@bp.route("/<int:criterion>/<int:claim_id>/proofs/<int:proof_id>/delete", methods=("POST",))
@login_required
def delete_proof(program_id, criterion, claim_id, proof_id):
    acc = program_access(program_id)
    if not _check_criterion(acc, criterion):
        abort(403)
    db = get_db()
    pr = db.execute("SELECT * FROM proofs WHERE id = ? AND claim_id = ?", (proof_id, claim_id)).fetchone()
    if pr:
        (Path(current_app.config["UPLOAD_FOLDER"]) / str(program_id) / pr["stored_name"]).unlink(missing_ok=True)
        db.execute("DELETE FROM proofs WHERE id = ?", (proof_id,))
        db.commit()
    return redirect(url_for("claims.detail", program_id=program_id, criterion=criterion, claim_id=claim_id))


@bp.route("/<int:criterion>/<int:claim_id>/delete", methods=("POST",))
@login_required
def delete_claim(program_id, criterion, claim_id):
    acc = program_access(program_id)
    if not _check_criterion(acc, criterion):
        abort(403)
    db = get_db()
    for pr in db.execute("SELECT stored_name FROM proofs WHERE claim_id = ?", (claim_id,)).fetchall():
        (Path(current_app.config["UPLOAD_FOLDER"]) / str(program_id) / pr["stored_name"]).unlink(missing_ok=True)
    db.execute("DELETE FROM claims WHERE id = ? AND program_id = ?", (claim_id, program_id))
    db.commit()
    flash("Entry deleted.", "ok")
    return redirect(url_for("claims.list_claims", program_id=program_id, criterion=criterion))


@bp.route("/<int:criterion>/data", methods=("GET", "POST"))
@login_required
def data_sheet(program_id, criterion):
    """Year-wise numbers for the SAR tables of this criterion (e.g. Table 4A, 5.1.2, 9.1.1)."""
    from ..years import AssessmentYears, display_name

    acc = program_access(program_id)
    can_edit = _check_criterion(acc, criterion)
    p = acc["program"]
    fields = rules.data_fields_for(p["sar_version"], criterion)
    if not fields:
        abort(404)
    db = get_db()
    if request.method == "POST":
        if not can_edit:
            abort(403)
        errors, saved = [], 0
        for f in fields:
            if f.get("auto") == "roster":
                continue  # counted from the faculty list, never typed
            for y in f["years"]:
                raw = request.form.get(f"{f['key']}__{y}", "").strip()
                if raw == "":
                    db.execute("DELETE FROM metrics WHERE program_id = ? AND key = ? AND year = ?",
                               (program_id, f["key"], y))
                    continue
                try:
                    val = float(raw)
                    if val < 0:
                        raise ValueError
                except ValueError:
                    errors.append(f"{f['key']} ({display_name(y)}): '{raw}' is not a valid number.")
                    continue
                db.execute("INSERT INTO metrics (program_id, key, year, value, updated_by) VALUES (?, ?, ?, ?, ?) "
                           "ON CONFLICT(program_id, key, year) DO UPDATE SET value = excluded.value, "
                           "updated_by = excluded.updated_by, updated_at = datetime('now')",
                           (program_id, f["key"], y, val, g.user["id"]))
                saved += 1
        db.commit()
        for e in errors:
            flash(e, "error")
        flash(f"Saved {saved} values.", "ok")
        return redirect(url_for("claims.data_sheet", program_id=program_id, criterion=criterion))

    from ..roster import merged_metrics

    values, auto = merged_metrics(db, p)
    years_obj = AssessmentYears(p["cay_start_year"], p["ay_start_month"])
    all_years = []
    for f in fields:
        for y in f["years"]:
            if y not in all_years:
                all_years.append(y)
    order = ["CAY", "CAYm1", "CAYm2", "CAYm3", "CAYm4", "CAYm5", "CAYm6"]
    all_years.sort(key=order.index)
    headers = [{"name": y, "display": display_name(y), "label": years_obj.label(y)} for y in all_years]
    crit = rules.load(p["sar_version"])["criteria"][criterion]
    return render_template("claims/data.html", p=p, criterion=criterion, crit=crit, fields=fields,
                           headers=headers, values=values, can_edit=can_edit,
                           auto=set(auto))
