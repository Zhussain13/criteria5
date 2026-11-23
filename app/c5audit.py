"""The Criterion 5 audit, the way an NBA peer team would do it.

1. Counts every year-wise number from the faculty list (app/roster.py) plus the student numbers.
2. Scores 5.1-5.5 with the NBA formulas (app/engine.py, the same code as CMRSARathi).
3. Scores again counting ONLY faculty whose proofs were checked ("if the peer team accepts only
   verified proofs"), so the department sees how many marks are at risk.
4. Lists every faculty member whose appointment letter or degree certificate is missing or does
   not match, with the fix and the question the peer team would ask.
5. Checks the faculty conditions of the NBA pre-qualifiers and of the 3-year / 6-year award.
No AI is used anywhere.
"""
from __future__ import annotations

import json

from .engine import r2, run_audit
from .faculty_proofs import faculty_issues, proof_status
from .roster import YEARS, merged_metrics
from .years import AssessmentYears

# Faculty conditions for the award (NBA Tier I Manual 2026); Criterion 5 alone cannot give the award,
# but these conditions come straight from the faculty data.
AWARD_FACULTY = {
    "six_years": {"label": "6-year accreditation", "max_sfr": 20, "phd_pct": 30},
    "three_years": {"label": "3-year accreditation", "max_sfr": 25, "phd_pct": 20},
}


def _claims(db, pid: int) -> list[dict]:
    out = []
    for c in db.execute("SELECT * FROM claims WHERE program_id = ? ORDER BY id", (pid,)).fetchall():
        d = dict(c)
        d["tables"] = json.loads(d.get("tables_json") or "{}")
        d["proofs"] = [{"sha256": p["sha256"], "name": p["original_name"], "ocr_text": p["ocr_text"],
                        "extracted": json.loads(p["extracted_json"] or "{}")}
                       for p in db.execute("SELECT * FROM proofs WHERE claim_id = ? ORDER BY id", (c["id"],))]
        out.append(d)
    return out


def _phd_pct(m: dict) -> float | None:
    phd = [m.get(("PHD", y), 0) for y in ("CAY", "CAYm1")]
    rf = [m.get(("S", y), 0) / 20 for y in ("CAY", "CAYm1")]
    if not all(rf):
        return None
    return 100 * (sum(phd) / 2) / (sum(rf) / 2)


def build_report(db, program) -> dict:
    p = dict(program)
    pid = p["id"]
    years = AssessmentYears(p["cay_start_year"], p["ay_start_month"])
    claims = _claims(db, pid)
    faculty = [dict(f) for f in db.execute("SELECT * FROM faculty WHERE program_id = ?", (pid,))]

    metrics, auto = merged_metrics(db, p)
    report = run_audit(p, claims, faculty, metrics)
    strict_metrics, _ = merged_metrics(db, p, verified_only=True)
    strict = run_audit(p, claims, faculty, strict_metrics)

    crit = report["criteria"][0]
    strict_items = {it["code"]: it["awarded"] for it in strict["criteria"][0]["items"]}
    for it in crit["items"]:
        it["awarded_verified"] = strict_items.get(it["code"], 0)
    report["verified_only"] = {"awarded": strict["awarded"], "lost": r2(report["awarded"] - strict["awarded"]),
                               "pre_qualifiers": strict["pre_qualifiers"]}

    # numbers used, and which of them the app counted itself
    # for 5.2, 5.3 and 5.5 the evidence is the faculty proofs (P.21), not a separate document entry
    st_all = proof_status(db, pid)
    n_ok = sum(1 for v in st_all.values() if v.get("appointment") == "verified")
    for it in crit["items"]:
        if it["code"] in ("5.2", "5.3", "5.5") and faculty:
            it["notes"] = [n for n in it["notes"] if not n.startswith("No supporting documents attached")]
            it["notes"].append(f"Evidence: appointment letters and degree certificates on the faculty list "
                               f"({n_ok} appointment letters checked). Counted from the faculty list (Table B2.A).")
    report["numbers"] = [{"key": k, "year": y, "value": v, "auto": (k, y) in auto,
                          "verified": strict_metrics.get((k, y))}
                         for (k, y), v in sorted(metrics.items())]

    issues = faculty_issues(db, p, years)
    report["faculty_issues"] = issues
    st = proof_status(db, pid)
    from .roster import Roster

    ros = Roster.from_db(db, p, faculty=faculty)
    counted = [f for f in faculty if any(ros.present(f, y) for y in YEARS)]
    report["proof_summary"] = {
        "faculty_counted": len(counted),
        "appointment_verified": sum(1 for f in counted if st.get(f["id"], {}).get("appointment") == "verified"),
        "degree_needed": sum(1 for f in counted if _needs_degree(f)),
        "degree_verified": sum(1 for f in counted if _needs_degree(f) and st.get(f["id"], {}).get("degree") == "verified"),
        "with_issues": len(issues),
    }
    for i in issues:
        for pr in i["problems"]:
            report["peer_team_questions"].append(pr["question"])
    report["peer_team_questions"] = list(dict.fromkeys(report["peer_team_questions"]))[:40]
    if report["verified_only"]["lost"] > 0.05:
        report["fix_list"].insert(0, {"code": "P.21", "title": f"Upload or correct the proofs of {len(issues)} faculty "
                                      f"(marks at risk if the peer team accepts only verified proofs)",
                                      "marks_recoverable": report["verified_only"]["lost"], "criterion": 5})

    # award conditions that depend on faculty data
    sfr = report["award"].get("sfr_avg")
    phd = _phd_pct(metrics)
    conds = []
    for key, c in AWARD_FACULTY.items():
        unmet = []
        if sfr is None or sfr > c["max_sfr"]:
            unmet.append(f"Average SFR <= {c['max_sfr']} (now {'unknown' if sfr is None else sfr})")
        if phd is None or phd < c["phd_pct"]:
            unmet.append(f"Ph.D. faculty >= {c['phd_pct']}% of required faculty (now "
                         f"{'unknown' if phd is None else f'{phd:.1f}%'})")
        conds.append({"label": c["label"], "met": not unmet, "unmet": unmet})
    g = crit["grade"]
    meaning = {"Y": "meets the NBA level (75% or more)", "C": "close to the level (60-75%)",
               "W": "weak (40-60%): blocks a 6-year award", "D": "deficient (below 40%): blocks any award"}[g]
    report["award"] = {"status": f"Criterion 5: grade {g}, {meaning}", "grade": g, "sfr_avg": sfr,
                       "phd_pct": None if phd is None else round(phd, 1), "faculty_conditions": conds}
    report["kind"] = "rules"
    report["kind_label"] = "NBA formulas + proof checks (no AI)"
    report["ai_used"] = False
    return report


def _needs_degree(f) -> bool:
    from .roster import degree_of

    return degree_of(f["qualification"]) in ("phd", "mtech")
