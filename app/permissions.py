"""Who can see and do what. One place for every access rule, so the pages stay consistent.

Roles (per institution). This app covers Criterion 5 only and uses no AI.
- principal   sees every program and the full SAR; runs the audit. Edits nothing (creates user accounts only).
- hod         program head: every operation on every criterion of their program(s): add, edit, delete,
              draft, import, team and section assignment, audit.
- crit_head   criteria head: the full SAR of their criteria: add, edit, delete, draft, import; runs the audit.
- member      criteria member: data entry and typing only, in the SAR sections assigned to them (5.1, 5.2 ...);
              sees only those sections; deletes nothing; runs no audits.
- superadmin  platform owner: manages institutions and their subscriptions; no SAR data access.
"""
from __future__ import annotations

from .rules import load as load_rules

ROLE_LABELS = {
    "principal": "Principal",
    "hod": "HoD (program head)",
    "crit_head": "Criteria head",
    "member": "Criteria member",
}
ALL_ROLES = {**ROLE_LABELS, "superadmin": "Platform owner"}
TEAM_ROLES = ("hod", "crit_head", "member")          # added to a program's team
INSTITUTION_WIDE = ("principal",)  # see all programs of the institution


def _criteria_list(text: str) -> set[int]:
    return {int(x) for x in (text or "").split(",") if x.strip().isdigit()}


def access(db, user, prog) -> dict | None:
    """What `user` may do in program `prog`; None if they may not see it at all."""
    if user is None or prog is None or user["role"] == "superadmin":
        return None
    if user["institution_id"] != prog["institution_id"]:
        return None
    rules = load_rules(prog["sar_version"])
    all_c = set(rules["criteria"])
    inst_c = set(rules.get("institute_criteria", [])) & all_c
    role = user["role"]
    m = db.execute("SELECT criteria, sections FROM program_members WHERE program_id = ? AND user_id = ?",
                   (prog["id"], user["id"])).fetchone()
    mine = _criteria_list(m["criteria"]) & all_c if m else set()
    all_sections = set(rules["items"])
    out = {"program": prog, "role": role, "view": set(), "edit": set(), "audit": None, "audit_scope": set(),
           "limit": None, "manage_team": False, "manage_program": False, "manage_faculty": False,
           "institute_criteria": inst_c, "program_criteria": all_c - inst_c,
           "sections_view": set(), "sections_edit": set(), "can_delete": False, "can_draft": False}
    if role == "principal":
        out.update(view=all_c, audit="full", sections_view=all_sections)
    elif m is None:
        return None
    elif role == "hod":
        out.update(view=all_c, edit=all_c, audit="full", manage_team=True, manage_program=True, manage_faculty=True,
                   sections_view=all_sections, sections_edit=all_sections, can_delete=True, can_draft=True)
    elif role == "crit_head":
        secs = {c for c in all_sections if rules["items"][c]["criterion"] in mine}
        out.update(view=mine, edit=mine, audit="full" if mine else None, manage_faculty=bool(mine),
                   sections_view=secs, sections_edit=secs, can_delete=bool(mine), can_draft=bool(mine))
    elif role == "member":
        assigned = {c.strip() for c in (m["sections"] or "").split(",") if c.strip()} & all_sections
        assigned = {c for c in assigned if rules["items"][c]["criterion"] in mine}
        out.update(view=mine, edit=mine, manage_faculty=bool(mine), sections_view=assigned, sections_edit=assigned)
    else:
        return None
    out["criteria"] = out["edit"]  # older code reads "criteria" as "may edit"
    return out


def visible_programs(db, user) -> list:
    """Programs the user can open (their institution's, filtered by team membership where needed)."""
    if user is None or user["role"] == "superadmin":
        return []
    if user["role"] in INSTITUTION_WIDE:
        return db.execute("SELECT * FROM programs WHERE institution_id = ? ORDER BY name",
                          (user["institution_id"],)).fetchall()
    return db.execute(
        "SELECT p.* FROM programs p JOIN program_members m ON m.program_id = p.id "
        "WHERE m.user_id = ? AND p.institution_id = ? ORDER BY p.name",
        (user["id"], user["institution_id"])).fetchall()


def full_ai_used(db, program_id: int, role: str) -> int:
    return db.execute("SELECT COUNT(*) FROM audits WHERE program_id = ? AND kind = 'full_ai' AND run_role = ? "
                      "AND counted = 1", (program_id, role)).fetchone()[0]


def audit_option(db, acc: dict, ai_on: bool = False) -> dict | None:
    """The audit button this user gets: Criterion 5 is checked with NBA formulas and free OCR only."""
    if not acc["audit"]:
        return None
    return {"kind": "rules", "label": "Run Criterion 5 audit", "scope": set(), "left": None,
            "note": "NBA formulas and proof checks; no AI is used"}
