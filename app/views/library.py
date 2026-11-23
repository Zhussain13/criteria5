"""The program's SAR document (all sections merged in NBA order) and its proofs library.

Everyone sees only the criteria they are allowed to see: a criteria member their criteria, the HoD
the whole program, the principal every program.
"""
import json
from collections import defaultdict

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from markupsafe import Markup, escape

from .. import rules
from ..db import get_db
from ..sar_export import DEFAULT_SETTINGS
from .auth import login_required, program_access

bp = Blueprint("library", __name__, url_prefix="/programs/<int:program_id>")


def _names(db, ids):
    ids = [i for i in ids if i]
    if not ids:
        return {}
    rows = db.execute(f"SELECT id, name FROM users WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    return {r["id"]: r["name"] for r in rows}


@bp.route("/sar")
@login_required
def sar_document(program_id):
    """The SAR now lives on the live document page."""
    program_access(program_id)
    return redirect(url_for("document.view", program_id=program_id))


def _old_sar_document(program_id):
    acc = program_access(program_id)
    p = acc["program"]
    db = get_db()
    rs = rules.load(p["sar_version"])
    wanted = request.args.get("c", type=int)
    show = [n for n in rs["criteria"] if n in acc["view"] and (wanted is None or n == wanted)]
    claims = db.execute("SELECT * FROM claims WHERE program_id = ? ORDER BY item_code, id", (program_id,)).fetchall()
    names = _names(db, {c["created_by"] for c in claims} | {c["updated_by"] for c in claims})
    by_item = defaultdict(list)
    for c in claims:
        by_item[c["item_code"]].append(c)

    def section_html(c):
        if (c["sar_html"] or "").strip():
            return Markup(c["sar_html"])  # cleaned when saved (richtext.sanitize)
        return Markup("<p>" + str(escape(c["sar_text"] or "")).replace("\n", "<br>") + "</p>") if c["sar_text"] else None

    criteria = []
    for num in show:
        items = []
        done = total = 0
        for code, it in sorted(((c, i) for c, i in rs["items"].items() if i["criterion"] == num),
                               key=lambda kv: [int(x) if x.isdigit() else x for x in kv[0].split(".")]):
            entries = by_item.get(code, [])
            texts = [(c, section_html(c)) for c in entries if (c["sar_html"] or c["sar_text"] or "").strip()]
            tables = []
            for spec in rs.get("tables", {}).get(code, []):
                filled = [json.loads(c["tables_json"] or "{}").get(spec["id"]) for c in entries]
                tables.append({"spec": spec, "filled": [t for t in filled if t]})
            written = bool(texts) or any(t["filled"] for t in tables)
            if it["type"] == "rubric":
                total += 1
                done += 1 if written else 0
            items.append({"code": code, "item": it, "texts": texts, "tables": tables, "written": written,
                          "entries": entries, "is_written_section": it["type"] == "rubric"})
        criteria.append({"number": num, "name": rs["criteria"][num]["name"], "max": rs["criteria"][num]["max"],
                         "items": items, "done": done, "total": total,
                         "pct": round(100 * done / total) if total else 100,
                         "can_edit": num in acc["edit"]})
    settings = {**DEFAULT_SETTINGS, **json.loads(p["doc_settings"] or "{}")}
    return render_template("library/sar.html", p=p, acc=acc, criteria=criteria, names=names, wanted=wanted,
                           all_criteria=[n for n in rs["criteria"] if n in acc["view"]], settings=settings)


@bp.route("/sar/settings", methods=("POST",))
@login_required
def sar_settings(program_id):
    acc = program_access(program_id)
    if not acc["manage_program"]:
        abort(403)
    f = request.form
    try:
        s = {"font": f.get("font", "Times New Roman")[:40], "size": float(f.get("size", 12))}
        for k in ("margin_top", "margin_bottom", "margin_left", "margin_right"):
            s[k] = min(6.0, max(0.5, float(f.get(k, 2.54))))
    except ValueError:
        flash("Margins and size must be numbers.", "error")
        return redirect(url_for("library.sar_document", program_id=program_id))
    db = get_db()
    db.execute("UPDATE programs SET doc_settings = ? WHERE id = ?", (json.dumps(s), program_id))
    db.commit()
    flash("Document settings saved: they apply to the editor and the Word download.", "ok")
    return redirect(url_for("library.sar_document", program_id=program_id))


@bp.route("/proofs")
@login_required
def proofs(program_id):
    acc = program_access(program_id)
    p = acc["program"]
    db = get_db()
    rs = rules.load(p["sar_version"])
    wanted = request.args.get("c", type=int)
    rows = db.execute("""
        SELECT c.id AS claim_id, c.criterion, c.item_code, c.title, c.person_name, c.start_date, c.created_by,
               pr.id AS proof_id, pr.original_name, pr.created_at, pr.extracted_json
        FROM claims c LEFT JOIN proofs pr ON pr.claim_id = c.id
        WHERE c.program_id = ? ORDER BY c.criterion, c.item_code, c.id, pr.id""", (program_id,)).fetchall()
    rows = [r for r in rows if r["criterion"] in acc["view"] and (wanted is None or r["criterion"] == wanted)]
    names = _names(db, {r["created_by"] for r in rows})
    groups = defaultdict(lambda: defaultdict(list))
    for r in rows:
        groups[r["criterion"]][r["item_code"]].append(r)
    n_files = sum(1 for r in rows if r["proof_id"])
    missing = sorted({(r["criterion"], r["claim_id"], r["item_code"], r["title"]) for r in rows if not r["proof_id"]})
    return render_template("library/proofs.html", p=p, acc=acc, groups=groups, items=rs["items"],
                           crit_names={n: c["name"] for n, c in rs["criteria"].items()}, names=names,
                           n_files=n_files, missing=missing, wanted=wanted,
                           all_criteria=[n for n in rs["criteria"] if n in acc["view"]])
