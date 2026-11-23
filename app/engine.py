"""The audit engine: checks every SAR claim and data table against proofs and NBA rules,
like a peer team, and computes marks.

All marks are computed here by plain code from the formulas in the NBA Evaluation
Guidelines / SAR format. AI (optional) only reads messy proofs and judges written sections.

Pure functions: no Flask, no database. Feed it dicts, get a report dict back.
"""
from __future__ import annotations

import re

from collections import defaultdict
from datetime import date
from typing import Callable

from . import proofs as P
from .consistency import consistency_checks
from .rules import load as load_rules, tables_text
from .years import AssessmentYears, display_name

ACCEPTED, PARTIAL, REJECTED = "accepted", "partly accepted", "rejected"


def _d(s) -> date | None:
    if not s:
        return None
    if isinstance(s, date):
        return s
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def institute_aliases(program: dict) -> list[str]:
    return [a.strip().lower() for a in program["institute_name"].split(",") if a.strip()]


def mentions_institute(text: str, aliases: list[str]) -> bool:
    t = " ".join(text.lower().split())
    return any(a in t for a in aliases)


def find_faculty(name: str, faculty: list[dict]) -> dict | None:
    target = P.normalise_name(name)
    for f in faculty:
        if P.normalise_name(f["name"]) == target:
            return f
    for f in faculty:
        if f["name"] and P.name_in_text(f["name"], name):
            return f
    return None


def r2(x: float) -> float:
    return round(x + 1e-9, 2)


class Finding:
    """Result for one claim or one check."""

    def __init__(self, claim: dict):
        self.claim = claim
        self.status = ACCEPTED
        self.points = 0.0
        self.reasons: list[str] = []
        self.fixes: list[str] = []
        self.questions: list[str] = []
        self.year: str | None = None
        self.used_date: date | None = None

    def reject(self, reason: str, fix: str = "", question: str = ""):
        self.status = REJECTED
        self.points = 0
        self.reasons.append(reason)
        if fix:
            self.fixes.append(fix)
        if question:
            self.questions.append(question)

    def partial(self, reason: str, fix: str = ""):
        if self.status == ACCEPTED:
            self.status = PARTIAL
        self.reasons.append(reason)
        if fix:
            self.fixes.append(fix)

    def as_dict(self) -> dict:
        c = self.claim
        return {
            "claim_id": c.get("id"),
            "person": c.get("person_name", "") or "",
            "title": c.get("title", "") or "",
            "kind": c.get("kind", "") or "",
            "status": self.status,
            "points": r2(self.points),
            "year": self.year,
            "date": self.used_date.isoformat() if self.used_date else None,
            "reasons": self.reasons,
            "fixes": self.fixes,
            "questions": self.questions,
        }


# ================================================================ common claim checks

def _proof_text(claim: dict) -> str:
    return "\n".join(p.get("ocr_text", "") for p in claim.get("proofs", []))


def _proof_fields(claim: dict) -> dict:
    merged: dict = {}
    for p in claim.get("proofs", []):
        for k, v in (p.get("extracted") or {}).items():
            merged.setdefault(k, v)
    return merged


def common_checks(f: Finding, rule: dict, ctx: dict) -> bool:
    """Checks every claim shares. Returns False if the claim is rejected."""
    c = f.claim
    years: AssessmentYears = ctx["years"]
    proofs = c.get("proofs", [])
    text = _proof_text(c)
    fields = _proof_fields(c)

    if rule.get("requires_proof", True) and not proofs:
        f.reject(
            "No proof attached for this claim.",
            "Upload the certificate, letter or report that supports this entry.",
            f"Can you show the original document for '{c.get('title') or c.get('person_name')}'?",
        )
        return False

    for p in proofs:
        owners = ctx["sha_owner"].get(p["sha256"], [])
        if len(owners) > 1 and owners[0] != c.get("id"):
            f.reject(
                "The same proof file is already used for another claim.",
                "Attach the correct, separate proof for this entry.",
                "Why is the same certificate shown for two different entries?",
            )
            return False

    if proofs and not text.strip():
        f.partial(
            "Proof could not be read (scan too unclear or empty). Checked using the SAR entry only.",
            "Upload a clearer scan (at least 200 dpi) or a text PDF.",
        )

    person = c.get("person_name", "") or ""
    if person and text.strip() and not P.name_in_text(person, text):
        f.reject(
            f"The name '{person}' does not appear on the proof.",
            "Attach the document issued in this person's name.",
            f"Whose document is this? The SAR lists it under {person}.",
        )
        return False

    claim_start, claim_end = _d(c.get("start_date")), _d(c.get("end_date"))
    proof_start, proof_end = _d(fields.get("start_date")), _d(fields.get("end_date"))
    if proof_start and claim_start and proof_start != claim_start:
        f.partial(
            f"SAR says {claim_start:%d %b %Y}; the proof shows {proof_start:%d %b %Y}. The proof date is used.",
            "Correct the date in the SAR to match the proof.",
        )
    used_start = proof_start or claim_start
    used_end = proof_end or claim_end or used_start
    f.used_date = used_start

    window = rule.get("window")
    if window:
        if not used_start:
            f.reject(
                "No date found on the proof or in the SAR entry, so the assessment year cannot be confirmed.",
                "Add the date to the SAR entry and attach a dated proof.",
            )
            return False
        first, last = years.window_bounds(window)
        if not (first <= used_start <= last):
            f.reject(
                f"Dated {used_start:%d %b %Y}, outside the assessment period for this item "
                f"({years.window_text(window)}). Not counted.",
                f"Replace it with one dated between {first:%d %b %Y} and {last:%d %b %Y}, if available.",
            )
            return False
        f.year = years.name_of(used_start)

    if rule.get("requires_on_roster") and ctx["faculty"] and person:
        fac = find_faculty(person, ctx["faculty"])
        if not fac:
            f.reject(
                f"'{person}' is not on the department's faculty roster.",
                "Add the faculty member to the roster with joining date, or correct the name.",
            )
            return False
        joined, left = _d(fac.get("joining_date")), _d(fac.get("leaving_date"))
        if used_start and joined and used_start < joined:
            f.reject(
                f"Dated {used_start:%d %b %Y}, before {person} joined on {joined:%d %b %Y}; "
                "it belongs to the previous institution.",
                "Remove it, or replace it with an activity done after joining.",
                f"Was {person} working here when this activity took place?",
            )
            return False
        if used_start and left and used_start > left:
            f.reject(f"Dated {used_start:%d %b %Y}, after {person} left on {left:%d %b %Y}.", "Remove this entry.")
            return False

    ctx["_dates"] = (used_start, used_end)
    return True


def checked(item: dict, claims: list[dict], ctx: dict):
    """Run common checks on every claim; yield (finding, passed)."""
    findings = []
    for c in claims:
        f = Finding(c)
        findings.append(f)
        yield f, common_checks(f, item, ctx)
    ctx["_findings"] = findings


# ================================================================ data helpers

class Data:
    def __init__(self, metrics: dict, years: AssessmentYears):
        self.m = metrics
        self.years = years
        self.missing: list[str] = []

    def get(self, key: str, year: str, default: float | None = None) -> float | None:
        v = self.m.get((key, year))
        if v is None:
            self.missing.append(f"{key} ({display_name(year)})")
            return default
        return float(v)

    def has(self, key: str, year: str) -> bool:
        return self.m.get((key, year)) is not None


def _avg(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _evidence_check(code: str, claims: list[dict], ctx: dict, notes: list[str], findings: list[Finding]):
    """Data items: evaluators verify the data for each assessment year from documents."""
    docs = [c for c in claims if c.get("proofs")]
    for c in claims:
        f = Finding(c)
        if not c.get("proofs"):
            f.reject("Supporting document entry has no file attached.", "Attach the document.")
        findings.append(f)
    if not docs:
        notes.append("No supporting documents attached. Evaluators verify this data for each assessment year; "
                      "marks are at risk until evidence is attached.")
        return False
    return True


def _data_note(data: Data, notes: list[str]):
    if data.missing:
        uniq = list(dict.fromkeys(data.missing))
        notes.append("Missing data (counted as 0, as an evaluator would): " + ", ".join(uniq[:12])
                     + (" ..." if len(uniq) > 12 else ""))
    data.missing = []


# ================================================================ scorers: Criterion 4

def score_enrolment_ratio(item, claims, ctx):
    data, notes, findings = ctx["data"], [], []
    ers = []
    for y in item["years"]:
        n = data.get("N", y)
        n1 = data.get("N1", y, 0)
        n4 = data.get("N4", y, 0)
        er = (n1 + n4) / n if n else 0
        ers.append(er)
        notes.append(f"{display_name(y)}: ER = ({n1:g} + {n4:g}) / {n or 0:g} = {er:.2f}")
    _data_note(data, notes)
    pct = _avg(ers) * 100
    marks = next((b["marks"] for b in item["bands"] if pct >= b["min"]), 0)
    notes.append(f"Average ER = {pct:.1f}% -> {marks} marks")
    _evidence_check(item["code"], claims, ctx, notes, findings)
    return marks, findings, notes


def _floor_A(n: float, n2: float) -> float:
    return n + min(n2, 0.1 * n)


def score_success_rate(item, claims, ctx):
    data, notes, findings = ctx["data"], [], []
    srs = []
    for b in item["batches"]:
        n, n1, n2 = data.get("N", b, 0), data.get("N1", b, 0), data.get("N2", b, 0)
        n3, n5, n6 = data.get("N3", b, 0), data.get("N5", b, 0), data.get("N6", b, 0)
        grad = data.get("GRAD", b, 0)
        a = n1 + n2 + n3 + n5 - n6
        floor = _floor_A(n, n2)
        note_a = ""
        if a < floor:
            note_a = f" (A raised to N + N2 limited to 10% of N = {floor:g})"
            a = floor
        sr = 100 * grad / a if a else 0
        srs.append(sr)
        notes.append(f"{display_name(b)}: SR = {grad:g} / {a:g} = {sr:.1f}%{note_a}")
    _data_note(data, notes)
    avg = _avg(srs)
    marks = min(item["max"], 1.5 * avg / 10)
    notes.append(f"Average SR = {avg:.1f}% -> 1.5 x {avg:.1f}/10 = {marks:.2f}")
    _evidence_check(item["code"], claims, ctx, notes, findings)
    return marks, findings, notes


def api_values(data: Data, prefix: str, years: list[str]) -> dict:
    out = {}
    for y in years:
        x, yy, z = data.get(f"{prefix}_X", y, 0), data.get(f"{prefix}_Y", y, 0), data.get(f"{prefix}_Z", y, 0)
        out[y] = x * (yy / z) if z else 0
    return out


def score_api(item, claims, ctx):
    data, notes, findings = ctx["data"], [], []
    vals = api_values(data, item["prefix"], item["years"])
    for y, v in vals.items():
        notes.append(f"{display_name(y)}: API = {v:.2f}")
    _data_note(data, notes)
    avg = _avg(list(vals.values()))
    mult = item.get("multiplier", 1)
    marks = min(item["max"], mult * avg)
    notes.append(f"Average API = {avg:.2f} -> " + (f"{mult:g} x {avg:.2f} = " if mult != 1 else "") + f"{marks:.2f} marks")
    _evidence_check(item["code"], claims, ctx, notes, findings)
    return marks, findings, notes


def score_placement(item, claims, ctx):
    data, notes, findings = ctx["data"], [], []
    ps = []
    for b in item["batches"]:
        fs = data.get("FS", b, 0)
        n, n2 = data.get("N", b, 0), data.get("N2", b, 0)
        x, y, z = data.get("PLACED", b, 0), data.get("HIGHER", b, 0), data.get("ENTRE", b, 0)
        floor = _floor_A(n, n2) if item.get("floor", True) else 0
        note_fs = ""
        if fs < floor:
            note_fs = f" (FS raised to N + N2 limited to 10% of N = {floor:g})"
            fs = floor
        p = min(100.0, 100 * (x + y + z) / fs) if fs else 0
        ps.append(p)
        notes.append(f"{display_name(b)}: P = ({x:g} + {y:g} + {z:g}) / {fs:g} = {p:.1f}%{note_fs}")
    _data_note(data, notes)
    avg = _avg(ps)
    mult = item.get("multiplier", 0.3)
    marks = min(item["max"], mult * avg)
    notes.append(f"Average placement index = {avg:.1f} -> {mult:g} x {avg:.1f} = {marks:.2f}")
    _evidence_check(item["code"], claims, ctx, notes, findings)
    return marks, findings, notes


# ================================================================ scorers: Criterion 5

def sfr_values(data: Data, years: list[str]) -> dict:
    out = {}
    for y in years:
        s = data.get("S", y, 0)
        tf = data.get("F", y, 0) + data.get("POP", y, 0) - data.get("FF", y, 0)
        out[y] = s / tf if tf > 0 else None
    return out


def score_sfr(item, claims, ctx):
    data, notes, findings = ctx["data"], [], []
    vals = sfr_values(data, item["years"])
    for y, v in vals.items():
        notes.append(f"{display_name(y)}: SFR = S / (F + PoP - FF) = " + (f"{v:.2f}" if v else "not computable"))
    _data_note(data, notes)
    usable = [v for v in vals.values() if v]
    if len(usable) < len(vals):
        notes.append("SFR could not be computed for every year; enter S, F, PoP and FF for all three years.")
        _evidence_check(item["code"], claims, ctx, notes, findings)
        return 0, findings, notes
    avg = _avg(usable)
    ctx["sfr_avg"] = avg
    if "upto" in item["bands"][0]:
        marks = next((b["marks"] for b in item["bands"] if avg <= b["upto"]), 0)
    else:
        marks = next((b["marks"] for b in item["bands"] if avg < b["below"]), 15 if avg <= 25 else 0)
    notes.append(f"Average SFR = {avg:.2f} -> {marks} marks")
    _evidence_check(item["code"], claims, ctx, notes, findings)
    return marks, findings, notes


def score_fqi(item, claims, ctx):
    data, notes, findings = ctx["data"], [], []
    vals = []
    for y in item["years"]:
        s = data.get("S", y, 0)
        rf = s / 20
        x, yy = data.get("PHD", y, 0), data.get("MTECH", y, 0)
        fqi = 2.5 * (10 * x + 4 * yy) / rf if rf else 0
        vals.append(fqi)
        notes.append(f"{display_name(y)}: FQI = 2.5 x (10 x {x:g} + 4 x {yy:g}) / {rf:.1f} = {fqi:.2f}")
    _data_note(data, notes)
    avg = _avg(vals)
    marks = min(item["max"], avg)
    notes.append(f"Average FQI = {avg:.2f} -> {marks:.2f} marks (limited to {item['max']})")
    _evidence_check(item["code"], claims, ctx, notes, findings)
    return marks, findings, notes


def score_cadre(item, claims, ctx):
    data, notes, findings = ctx["data"], [], []
    rf = _avg([data.get("S", y, 0) / 20 for y in item["years"]])
    af1 = _avg([data.get("AF1", y, 0) for y in item["years"]])
    af2 = _avg([data.get("AF2", y, 0) for y in item["years"]])
    af3 = _avg([data.get("AF3", y, 0) for y in item["years"]])
    _data_note(data, notes)
    rf1, rf2, rf3 = rf / 9, 2 * rf / 9, 6 * rf / 9
    if not rf or (af1 == 0 and af2 == 0):
        marks = 0
        notes.append("AF1 = AF2 = 0 (or no student data): zero marks.")
    else:
        marks = min(item["max"], (af1 / rf1 + af2 / rf2 * 0.6 + af3 / rf3 * 0.4) * 12.5)
        notes.append(f"Averages: RF1 {rf1:.1f}, AF1 {af1:.1f}; RF2 {rf2:.1f}, AF2 {af2:.1f}; RF3 {rf3:.1f}, AF3 {af3:.1f}")
        notes.append(f"Marks = (AF1/RF1 + AF2/RF2 x 0.6 + AF3/RF3 x 0.4) x 12.5 = {marks:.2f} (limited to 25)")
    _evidence_check(item["code"], claims, ctx, notes, findings)
    return marks, findings, notes


def score_adjunct(item, claims, ctx):
    data, notes, findings = ctx["data"], [], []
    marks, any_hours = 0, False
    for y in item["years"]:
        h = data.get("ADJ_HOURS", y, 0)
        any_hours = any_hours or h > 0
        got = item["marks_per_year"] if h >= item["hours_needed"] else 0
        marks += got
        notes.append(f"{display_name(y)}: {h:g} hours -> {got} marks (needs {item['hours_needed']}+)")
    _data_note(data, notes)
    has_docs = _evidence_check(item["code"], claims, ctx, notes, findings)
    if any_hours or has_docs:
        marks += item["provision_marks"]
        notes.append(f"Provision of visiting/adjunct faculty: +{item['provision_marks']}")
    return min(item["max"], marks), findings, notes


def score_retention(item, claims, ctx):
    data, notes, findings = ctx["data"], [], []
    vals = []
    for y in item["years"]:
        rf = data.get("DS", y, 0) / 20
        b, c, d, e = (data.get(k, y, 0) for k in ("RET_B", "RET_C", "RET_D", "RET_E"))
        data.get("RET_A", y, 0)
        fr = min(10, (b + 2 * c + 3 * d + 4 * e) / rf * 2.5) if rf else 0
        vals.append(fr)
        notes.append(f"{display_name(y)}: FR = ((B + 2C + 3D + 4E) / RF) x 2.5 = {fr:.2f}  (RF = DS/20 = {rf:.1f})")
    _data_note(data, notes)
    avg = min(item["max"], _avg(vals))
    notes.append(f"Average FR = {avg:.2f}")
    _evidence_check(item["code"], claims, ctx, notes, findings)
    return avg, findings, notes


# ================================================================ scorers: claim-based (mostly Criterion 6)

def score_points_by_duration(item, claims, ctx):
    notes = []
    per_person_year = defaultdict(float)
    year_points = defaultdict(float)
    for f, ok in checked(item, claims, ctx):
        if not ok:
            continue
        c = f.claim
        fields = _proof_fields(c)
        start, end = ctx["_dates"]
        if item.get("exclude_parent_institute"):
            organizer = (c.get("organizer") or "") + " " + (fields.get("organizer") or "")
            if organizer.strip() and mentions_institute(organizer, ctx["aliases"]):
                f.reject("Held at the parent institute; only participation in external programmes is counted.",
                         "Count only FDPs/STTPs attended at other institutions.")
                continue
        days = fields.get("duration_days") or fields.get("span_days")
        if not days and start and end:
            days = (end - start).days + 1
        if not days:
            f.reject("Duration of the programme could not be found on the proof.",
                     "Attach a certificate that states the dates or number of days.")
            continue
        pts = next((b["points"] for b in item["duration_bands"] if b["min_days"] <= days <= b["max_days"]), 0)
        if pts == 0:
            f.reject(f"Programme lasted {days} day(s); at least 2 days are needed to earn points.",
                     "Only programmes of 2 days or more are counted.")
            continue
        if c.get("start_date") and c.get("end_date"):
            claimed = (_d(c["end_date"]) - _d(c["start_date"])).days + 1
            if claimed != days:
                f.partial(f"SAR states {claimed} days; the proof shows {days} days. Points based on the proof.",
                          "Correct the duration in the SAR.")
        key = (P.normalise_name(c.get("person_name", "")), f.year)
        cap = item.get("max_points_per_person_per_year", 999)
        allowed = max(0, min(pts, cap - per_person_year[key]))
        if allowed < pts:
            f.partial(f"A faculty member scores at most {cap} points a year; {allowed} of {pts} counted.")
        per_person_year[key] += allowed
        f.points = allowed
        year_points[f.year] += allowed

    data: Data = ctx["data"]
    aps = []
    for y in item["window"]:
        rf = data.get("DS", y, 0) / 20
        mult = item.get("multiplier", 1)
        ap = min(item["year_cap"], mult * year_points[y] / (0.5 * rf)) if rf else 0
        aps.append(ap)
        notes.append(f"{display_name(y)}: AP = " + (f"{mult:g} x " if mult != 1 else "")
                     + f"{year_points[y]:g} / (0.5 x {rf:.1f}) = {ap:.2f}  (RF = DS/20, limited to {item['year_cap']})")
    _data_note(data, notes)
    marks = min(item["max"], _avg(aps))
    notes.append(f"Average over 3 years = {marks:.2f}")
    return marks, ctx.pop("_findings", []), notes


def score_resource_persons(item, claims, ctx):
    notes = []
    persons = defaultdict(set)
    for f, ok in checked(item, claims, ctx):
        if ok:
            persons[f.year].add(P.normalise_name(f.claim.get("person_name", "")))
            f.points = 1
    counts = [len(persons[y]) for y in item["window"]]
    for y, n in zip(item["window"], counts):
        notes.append(f"{display_name(y)}: {n} faculty as resource persons")
    avg = _avg(counts)
    marks = 5 if avg > 3 else (2 if avg > 2 else 0)
    notes.append(f"Average {avg:.2f} faculty per year -> {marks} marks (more than 3 = 5; more than 2 = 2)")
    return marks, ctx.pop("_findings", []), notes


def _range_marks(pct: float, item: dict) -> float:
    if "bands" in item:
        return next((b["marks"] for b in item["bands"] if pct >= b["min"]), 0)
    ranges = sorted(item["ranges"], key=lambda r: -r["above"])
    for i, r in enumerate(ranges):
        if pct > r["above"] or (r["above"] == 0 and pct >= 0):
            upper = ranges[i - 1]["above"] if i > 0 else r["above"] + 20
            frac = min(1.0, max(0.0, (pct - r["above"]) / (upper - r["above"])))
            return round(r["low"] + frac * (r["high"] - r["low"]), 1)
    return 0


def score_faculty_share(item, claims, ctx):
    notes = []
    persons = defaultdict(set)
    for f, ok in checked(item, claims, ctx):
        if ok:
            persons[f.year].add(P.normalise_name(f.claim.get("person_name", "")))
            f.points = 1
    data: Data = ctx["data"]
    pcts = []
    for y in item["window"]:
        af = data.get("AF", y, 0)
        pct = 100 * len(persons[y]) / af if af else 0
        pcts.append(pct)
        notes.append(f"{display_name(y)}: {len(persons[y])} of {af:g} faculty = {pct:.1f}%")
    _data_note(data, notes)
    avg = _avg(pcts)
    marks = min(item["max"], _range_marks(avg, item))
    notes.append(f"Average {avg:.1f}% of available faculty -> {marks} marks"
                 + (" (estimate: NBA gives a range; the exact mark within it is the evaluator's call)" if "ranges" in item
                    else " (estimate: no fixed NBA formula)"))
    return marks, ctx.pop("_findings", []), notes


def score_count_events(item, claims, ctx):
    notes = []
    per_year = defaultdict(float)
    for f, ok in checked(item, claims, ctx):
        if not ok:
            continue
        fields = _proof_fields(f.claim)
        start, end = ctx["_dates"]
        days = fields.get("duration_days") or fields.get("span_days") or (
            (end - start).days + 1 if start and end else None)
        if not days or days < item["min_days"]:
            f.reject(f"Duration {'unknown' if not days else f'{days} day(s)'} is below the minimum "
                     f"{item['min_days']} days for an FDP/STTP.", f"Only programmes of at least {item['min_days']} days count.")
            continue
        f.points = item["points_per_event"]
        per_year[f.year] += f.points
    total = 0.0
    for y in item["window"]:
        s = min(item["year_cap"], per_year[y])
        notes.append(f"{display_name(y)}: {per_year[y]:g} points -> {s:g} (max {item['year_cap']} a year)")
        total += s
    return min(item["total_cap"], item["max"], total), ctx.pop("_findings", []), notes


def score_counts(item, claims, ctx):
    notes = []
    count = defaultdict(int)
    for f, ok in checked(item, claims, ctx):
        if ok:
            k = f.claim.get("kind") or ""
            if k not in {c["kind"] for c in item["components"]}:
                f.partial("Type of entry not chosen, so it could not be counted under a component.",
                          "Edit the entry and choose its type.")
                continue
            count[k] += 1
            f.points = 1
    marks = 0.0
    for comp in item["components"]:
        n = count[comp["kind"]]
        m = comp["marks"] * min(1.0, n / comp["target"]) if comp["target"] else 0
        marks += m
        notes.append(f"{comp['label']}: {n} accepted -> {m:.1f} of {comp['marks']} (target {comp['target']})")
    notes.append("Estimate: NBA asks for 'number of' without a formula; targets are set in the rules file and "
                 "can be changed by the department.")
    return min(item["max"], marks), ctx.pop("_findings", []), notes


def score_papers(item, claims, ctx):
    notes = []
    allowed = [a.lower() for a in item["quality_indexing"]]
    n_ok = n_quality = 0
    per_year_kind = defaultdict(int)
    for f, ok in checked(item, claims, ctx):
        if not ok:
            continue
        c = f.claim
        text = _proof_text(c)
        if item.get("requires_affiliation"):
            if not mentions_institute((c.get("affiliation") or "") + " " + text, ctx["aliases"]):
                f.reject("The publication does not show this institute as the author's affiliation.",
                         "Only publications with the current institute's affiliation are counted.",
                         "Which institution is named as the affiliation on this paper?")
                continue
        idx = (c.get("indexing") or "").lower()
        if any(a in idx for a in allowed):
            n_quality += 1
        elif c.get("kind") in ("journal", "conference"):
            f.partial(f"Indexing '{c.get('indexing') or 'not stated'}' does not count towards quality "
                      "(Scopus/WoS-type indexing does).", "Give indexing proof if the paper is indexed.")
        if c.get("kind") in ("journal", "conference") and not (c.get("doi") or _proof_fields(c).get("doi")):
            f.partial("No DOI given or found.", "Add the DOI to the entry.")
        n_ok += 1
        f.points = 1
        per_year_kind[(f.year, c.get("kind"))] += 1
    ctx["pub_counts"] = per_year_kind
    count_m = item["count_marks"] * min(1.0, n_ok / item["count_target"])
    quality_m = item["quality_marks"] * (n_quality / n_ok) if n_ok else 0
    notes.append(f"A. Number: {n_ok} accepted publications -> {count_m:.1f} of {item['count_marks']} "
                 f"(target {item['count_target']} over 3 years, estimate)")
    notes.append(f"B. Quality: {n_quality} of {n_ok} in Scopus/WoS-type indexing -> {quality_m:.1f} of {item['quality_marks']} (estimate)")
    return min(item["max"], count_m + quality_m), ctx.pop("_findings", []), notes


def _band_above(amount: float, bands: list[dict]) -> float:
    return next((b["marks"] for b in sorted(bands, key=lambda b: -b["above"]) if amount > b["above"]), 0)


def score_amount_bands(item, claims, ctx):
    notes = []
    amount = 0.0
    for f, ok in checked(item, claims, ctx):
        if not ok:
            continue
        if not f.claim.get("amount"):
            f.reject("Amount (in lakh) not given.", "Enter the sanctioned amount and attach the sanction letter.")
            continue
        amount += float(f.claim["amount"])
        f.points = float(f.claim["amount"])
    marks = _band_above(amount, item["bands"])
    notes.append(f"Accepted cumulative amount (CAYm1 to CAYm3): Rs {amount:g} lakh -> {marks} marks")
    return min(item["max"], marks), ctx.pop("_findings", []), notes


def score_seed_money(item, claims, ctx):
    notes = []
    received = utilized = 0.0
    for f, ok in checked(item, claims, ctx):
        if not ok:
            continue
        amt = float(f.claim.get("amount") or 0)
        if not amt:
            f.reject("Amount (in lakh) not given.", "Enter the amount and attach the sanction/utilisation letter.")
            continue
        if f.claim.get("kind") == "utilized":
            utilized += amt
        else:
            received += amt
        f.points = amt
    a = _band_above(received, item["bands"])
    b = item["utilization_marks"] * min(1.0, utilized / received) if received else 0
    notes.append(f"A. Received Rs {received:g} lakh -> {a} marks")
    notes.append(f"B. Utilised Rs {utilized:g} lakh of {received:g} -> {b:.1f} of {item['utilization_marks']}")
    return min(item["max"], a + b), ctx.pop("_findings", []), notes


# ================================================================ scorers: Criterion 8 and 9.1

def _trend(base: float, latest: float) -> float:
    if not base and not latest:
        return 0.0  # no data for either year: nothing to show
    if latest > base:
        return 1.0
    if latest == base:
        return 0.5
    return 0.0


def score_improvement_faculty(item, claims, ctx):
    data, notes, findings = ctx["data"], [], []
    yrs = ["CAYm1", "CAYm2", "CAYm3"]
    phd = _avg([data.get("PHD", y, 0) for y in yrs])
    rf = _avg([data.get("S", y, 0) / 20 for y in ["CAY", "CAYm1", "CAYm2"]])
    pct = 100 * phd / rf if rf else 0
    a = _band_above(pct, item["phd_bands"])
    notes.append(f"A. Average Ph.D. faculty {phd:.1f} = {pct:.1f}% of required faculty -> {a} of {item['phd_marks']} "
                 "(VERIFY: compared with RF = S/20)")
    pc = ctx.get("pub_counts", {})
    j3, j1 = pc.get(("CAYm3", "journal"), 0), pc.get(("CAYm1", "journal"), 0)
    c3, c1 = pc.get(("CAYm3", "conference"), 0), pc.get(("CAYm1", "conference"), 0)
    b = item["journal_marks"] * _trend(j3, j1)
    c = item["conference_marks"] * _trend(c3, c1)
    notes.append(f"B. Journal papers: CAYm3 {j3} -> CAYm1 {j1}: {b:.1f} of {item['journal_marks']} (estimate: improvement over base year)")
    notes.append(f"C. Conference papers: CAYm3 {c3} -> CAYm1 {c1}: {c:.1f} of {item['conference_marks']} (estimate)")
    _data_note(data, notes)
    return min(item["max"], a + b + c), findings, notes


def score_improvement_api(item, claims, ctx):
    data, notes, findings = ctx["data"], [], []
    marks = 0.0
    for lvl in item["levels"]:
        v = api_values(data, lvl["prefix"], ["CAYm1", "CAYm3"])
        m = lvl["marks"] * _trend(v["CAYm3"], v["CAYm1"])
        marks += m
        notes.append(f"{lvl['prefix']}: API CAYm3 {v['CAYm3']:.2f} -> CAYm1 {v['CAYm1']:.2f}: {m:.1f} of {lvl['marks']} (estimate)")
    _data_note(data, notes)
    _evidence_check(item["code"], claims, ctx, notes, findings)
    return min(item["max"], marks), findings, notes


def score_fysfr(item, claims, ctx):
    data, notes, findings = ctx["data"], [], []
    pcts = []
    for y in item["years"]:
        rf4 = data.get("S4", y, 0) / 20
        ns1, ns2 = data.get("NS1", y, 0), data.get("NS2", y, 0)
        pct = 100 * (ns1 * 0.8 + ns2 * 0.2) / rf4 if rf4 else 0
        pcts.append(pct)
        notes.append(f"{display_name(y)}: (NS1 x 0.8 + NS2 x 0.2) / RF4 = ({ns1:g} x 0.8 + {ns2:g} x 0.2) / {rf4:.1f} = {pct:.1f}%")
    _data_note(data, notes)
    avg = _avg(pcts)
    marks = _band_above(avg, item["bands"])
    notes.append(f"Average {avg:.1f}% -> {marks} marks")
    _evidence_check(item["code"], claims, ctx, notes, findings)
    return marks, findings, notes


# ================================================================ scorers: older Tier II format (until 2025)

def score_success_index(item, claims, ctx):
    """Old Tier II 4.2.1 / 4.2.2: marks = weight x average SI; SI = graduates / admitted (1st yr + lateral)."""
    data, notes, findings = ctx["data"], [], []
    sis = []
    for b in item["batches"]:
        adm, grad = data.get("ADM", b, 0), data.get(item["numerator"], b, 0)
        si = grad / adm if adm else 0
        sis.append(si)
        notes.append(f"{display_name(b)}: SI = {grad:g} / {adm:g} = {si:.2f}")
    _data_note(data, notes)
    avg = _avg(sis)
    marks = min(item["max"], item["weight"] * avg)
    notes.append(f"Average SI = {avg:.2f} -> {item['weight']} x {avg:.2f} = {marks:.2f}")
    _evidence_check(item["code"], claims, ctx, notes, findings)
    return marks, findings, notes


def score_retention_pct(item, claims, ctx):
    """Old Tier II 5.4: % of required faculty retained (base year CAYm2), banded."""
    data, notes, findings = ctx["data"], [], []
    pcts = []
    for y in item["years"]:
        rf = data.get("S", y, 0) / 20
        kept = data.get("RETAINED", y, 0)
        pct = min(100.0, 100 * kept / rf) if rf else 0
        pcts.append(pct)
        notes.append(f"{display_name(y)}: retained {kept:g} of required {rf:.1f} (S/20) = {pct:.1f}%")
    _data_note(data, notes)
    avg = _avg(pcts)
    marks = next((b["marks"] for b in item["bands"] if avg >= b["min"]), 0)
    notes.append(f"Average {avg:.1f}% -> {marks} marks")
    _evidence_check(item["code"], claims, ctx, notes, findings)
    return marks, findings, notes


def score_fysfr_ratio(item, claims, ctx):
    """Old Tier II 8.1: per year min(5, 5 x 20 / FYSFR); zero if FYSFR > 25; averaged."""
    data, notes, findings = ctx["data"], [], []
    vals = []
    for y in item["years"]:
        s, f = data.get("FY_S", y, 0), data.get("FY_F", y, 0)
        r = s / f if f else None
        v = 0 if not r or r > 25 else min(item["max"], item["max"] * 20 / r)
        vals.append(v)
        notes.append(f"{display_name(y)}: FYSFR = {s:g} / {f:g} = " + (f"{r:.2f} -> {v:.2f}" if r else "not computable"))
    _data_note(data, notes)
    avg = _avg(vals)
    notes.append(f"Average = {avg:.2f}")
    _evidence_check(item["code"], claims, ctx, notes, findings)
    return avg, findings, notes


def score_fy_qualification(item, claims, ctx):
    """Old Tier II 8.2: (5x + 3y) / RF, RF = first-year students / 20; averaged, limited to max."""
    data, notes, findings = ctx["data"], [], []
    vals = []
    for y in item["years"]:
        rf = data.get("FY_S", y, 0) / 20
        x, yy = data.get("FY_PHD", y, 0), data.get("FY_PG", y, 0)
        v = min(item["max"], (5 * x + 3 * yy) / rf) if rf else 0
        vals.append(v)
        notes.append(f"{display_name(y)}: (5 x {x:g} + 3 x {yy:g}) / {rf:.1f} = {v:.2f}")
    _data_note(data, notes)
    avg = _avg(vals)
    notes.append(f"Average = {avg:.2f}")
    _evidence_check(item["code"], claims, ctx, notes, findings)
    return avg, findings, notes


def score_improvement_data(item, claims, ctx):
    """Improvement over the base year (CAYm3) for each listed number (estimate: NBA gives no formula)."""
    data, notes, findings = ctx["data"], [], []
    marks = 0.0
    for comp in item["components"]:
        base, latest = data.get(comp["key"], "CAYm3", 0), data.get(comp["key"], "CAYm1", 0)
        m = comp["marks"] * _trend(base, latest)
        marks += m
        notes.append(f"{comp['label']}: CAYm3 {base:g} -> CAYm1 {latest:g}: {m:.1f} of {comp['marks']} (estimate)")
    _data_note(data, notes)
    _evidence_check(item["code"], claims, ctx, notes, findings)
    return min(item["max"], marks), findings, notes


# ================================================================ scorer: written sections

def score_rubric(item, claims, ctx):
    findings, notes = [], []
    comps = item["components"]
    if not claims:
        notes.append("No SAR text or evidence entered for this item.")
        return 0.0, findings, notes
    sar_text = "\n".join((c.get("sar_text", "") or "") + "\n" + tables_text(c.get("tables")) for c in claims)
    evidence = "\n".join(_proof_text(c) for c in claims)
    labels = [c["label"] for c in comps]

    met, reasons, suggestions, method = None, None, [], ""
    reviewer: Callable | None = ctx.get("ai_review")
    scope = ctx.get("ai_scope")
    if scope is not None and item.get("criterion") not in scope:
        reviewer = ctx.get("ai_cached")  # outside this audit's scope: only re-use earlier AI results
    if reviewer:
        result = reviewer(item["title"], labels, sar_text, evidence)
        if result:
            met = [bool(x) for x in result.get("met", [])][: len(comps)]
            reasons = result.get("reasons", [])
            suggestions = result.get("suggestions", [])
            method = "AI review against the evaluation guidelines"
    if met is None:
        t = " ".join((sar_text + "\n" + evidence).lower().split())
        met = [any(re.search(r"(?<![a-z0-9])" + re.escape(str(k).lower()) + r"(?![a-z0-9])", t)
                   for k in comp.get("keywords", [])) for comp in comps]
        reasons = ["Evidence words found." if m else "No evidence found for this point." for m in met]
        method = "keyword check (AI off): a rough estimate only"
    has_proof = any(c.get("proofs") for c in claims)

    f = Finding({"id": claims[0].get("id"), "person_name": "", "title": item["title"]})
    marks = 0.0
    for i, comp in enumerate(comps):
        ok = met[i] if i < len(met) else False
        why = reasons[i] if reasons and i < len(reasons) else ""
        if ok:
            marks += comp["marks"]
        else:
            f.partial(f"Not shown ({comp['marks']} marks): {comp['label']}. {why}".strip(),
                      f"Add evidence for: {comp['label']}.")
            f.questions.append(f"Show us the evidence for: {comp['label'][0].lower() + comp['label'][1:]}.")
    if not has_proof:
        marks *= 0.5
        f.partial("No documents attached: evaluators need documentary evidence, so only half is estimated.",
                  f"Attach the documents from file {item.get('evidence_file', '')} of the NBA list of documents.")
    filled = {tid for c in claims for tid in (c.get("tables") or {})}
    for spec in ctx.get("tables", {}).get(item.get("code"), []):
        if spec["id"] not in filled:
            f.partial(f"Table No. {spec['id']} ({spec['title']}) asked for in the SAR format is not filled.",
                      f"Fill Table No. {spec['id']} in this entry (Edit entry).")
    f.fixes.extend(suggestions)
    f.points = round(marks, 1)
    if marks == 0:
        f.status = REJECTED
    findings.append(f)
    notes.append(f"{sum(1 for m in met if m)} of {len(comps)} components evidenced ({method}). Estimated marks.")
    return f.points, findings, notes


SCORERS = {
    "success_index": score_success_index,
    "retention_pct": score_retention_pct,
    "fysfr_ratio": score_fysfr_ratio,
    "fy_qualification": score_fy_qualification,
    "improvement_data": score_improvement_data,
    "rubric": score_rubric,
    "enrolment_ratio": score_enrolment_ratio,
    "success_rate": score_success_rate,
    "api": score_api,
    "placement": score_placement,
    "sfr": score_sfr,
    "fqi": score_fqi,
    "cadre": score_cadre,
    "adjunct": score_adjunct,
    "retention": score_retention,
    "points_by_duration": score_points_by_duration,
    "resource_persons": score_resource_persons,
    "faculty_share": score_faculty_share,
    "count_events": score_count_events,
    "counts": score_counts,
    "papers": score_papers,
    "amount_bands": score_amount_bands,
    "seed_money": score_seed_money,
    "improvement_faculty": score_improvement_faculty,
    "improvement_api": score_improvement_api,
    "fysfr": score_fysfr,
}
# Order matters: 6.2.1 must run before 8.3 (8.3 uses its publication counts).


def grade(percent: float, bands: dict) -> str:
    if percent >= bands["Y"]:
        return "Y"
    if percent >= bands["C"]:
        return "C"
    if percent >= bands["W"]:
        return "W"
    return "D"


def prof_condition(data: Data, year: str, extra: int = 0) -> bool | None:
    """2 Professors, or 1 Professor + 1 Associate Professor, regular with Ph.D.; plus N more
    (Professors or Associate Professors) for the other UG programs (NBA Annexure-I, case 2).
    Returns None when the counts are not entered."""
    if (("PROF_PHD", year) not in data.m) and (("ASSOC_PHD", year) not in data.m):
        return None
    p, a = data.get("PROF_PHD", year, 0), data.get("ASSOC_PHD", year, 0)
    return p >= 1 and p + a >= 2 + extra


def pre_qualifiers(data: Data, sfr_avg: float | None) -> list[dict]:
    """NBA Pro-forma for Pre-Qualifiers, Tier I UG Engineering (Annexure-I, 28-04-2026)."""
    out = []

    def add(no, text, current, ok):
        status = "Not entered" if ok is None else ("Complied" if ok else "Not complied")
        out.append({"no": no, "text": text, "current": current, "status": status})

    v = data.m
    appr = v.get(("APPROVAL_5Y", "CAY"))
    add(1, "Approval of the competent authority (AICTE etc.) for the previous five years",
        "" if appr is None else ("Yes" if appr else "No"), None if appr is None else appr >= 1)
    add(2, "Average SFR (CAY, CAYm1, CAYm2) in Department + allied departments <= 25:1",
        "" if sfr_avg is None else f"{sfr_avg:.2f}", None if sfr_avg is None else sfr_avg <= 25)
    n = int(v.get(("OTHER_UG", "CAY"), 0))
    years_ok = [prof_condition(data, y, n) for y in ("CAY", "CAYm1")]
    cur = ", ".join(f"{y}: {data.get('PROF_PHD', y, 0):g} Prof + {data.get('ASSOC_PHD', y, 0):g} Assoc"
                    for y in ("CAY", "CAYm1"))
    need = "2 Professors or 1 Professor + 1 Associate Professor, regular with Ph.D., in CAY and CAYm1"
    if n:
        need += f", plus {n} more Professor(s)/Associate Professor(s) with Ph.D. for the other UG programs"
    add(3, need, "" if None in years_ok else cur, None if None in years_ok else all(years_ok))
    phd = [v.get(("PHD", y)) for y in ("CAY", "CAYm1")]
    rf = [data.get("S", y, 0) / 20 for y in ("CAY", "CAYm1")]
    if None in phd or not all(rf):
        add(4, "Ph.D. faculty >= 20% of required faculty (S/20), average of CAY and CAYm1", "", None)
    else:
        pct = 100 * _avg(phd) / _avg(rf)
        add(4, "Ph.D. faculty >= 20% of required faculty (S/20), average of CAY and CAYm1", f"{pct:.1f}%", pct >= 20)
    b = v.get(("BATCHES_PASSED", "CAY"))
    add(5, "At least two batches have passed out in the program", "" if b is None else f"{b:g}",
        None if b is None else b >= 2)
    h = v.get(("HOD_PHD", "CAY"))
    add(6, "HoD appointed on regular basis with Ph.D. in CAY", "" if h is None else ("Yes" if h else "No"),
        None if h is None else h >= 1)
    return out


def award_decision_marks(rules: dict, criteria: list[dict], data: Data, sfr_avg: float | None) -> dict:
    """Old Tier II (Manual 2019, 4.2): total marks plus minimums and faculty conditions."""
    total = sum(c["awarded"] for c in criteria)
    by_num = {c["number"]: c for c in criteria}
    phd = _avg([data.get("PHD", y, 0) for y in ("CAY", "CAYm1")])
    rf = _avg([data.get("S", y, 0) / 20 for y in ("CAY", "CAYm1")])
    phd_pct = 100 * phd / rf if rf else 0
    adm = []
    for y in ("CAYm1", "CAYm2", "CAYm3"):
        n = data.get("N", y, 0)
        if n:
            adm.append(100 * data.get("N1", y, 0) / n)
    adm_pct = _avg(adm) if adm else 0
    hod = data.get("HOD_PHD", "CAY", 0)
    data.missing = []

    def check(rule):
        unmet = []
        if total < rule["min_total"]:
            unmet.append(f"Total at least {rule['min_total']} of 1000 (now {total:.0f})")
        for num in rule.get("min_pct_criteria", []):
            c = by_num.get(num)
            if c and c["percent"] < rule["min_pct"]:
                unmet.append(f"Criterion {num} at least {rule['min_pct']}% (now {c['percent']:.0f}%)")
        if sfr_avg is None or sfr_avg > rule["max_sfr"]:
            unmet.append(f"Average SFR <= {rule['max_sfr']}:1 (now {'unknown' if sfr_avg is None else f'{sfr_avg:.2f}'})")
        if phd_pct < rule["phd_pct_of_RF"]:
            unmet.append(f"Ph.D. faculty >= {rule['phd_pct_of_RF']}% of required faculty, avg of CAY and CAYm1 (now {phd_pct:.1f}%)")
        if adm_pct < rule["min_admission_pct"]:
            unmet.append(f"Admissions >= {rule['min_admission_pct']}% of intake, avg CAYm1-CAYm3 (now {adm_pct:.0f}%)")
        if rule.get("needs_hod_phd") and hod < 1:
            unmet.append("HoD with Ph.D. in CAY")
        if data.get(rule.get("prof_key", "PROF_OK"), "CAY", 0) < 1:
            unmet.append(rule["prof_text"])
        return unmet

    six = check(rules["award"]["six_years"])
    three = check(rules["award"]["three_years"])
    status = "Accreditation for 6 years" if not six else ("Accreditation for 3 years" if not three else "Not accredited")
    grades = [c["grade"] for c in criteria]
    return {"status": status, "unmet_six": six, "unmet_three": three,
            "grades": {g: grades.count(g) for g in "YCWD"}, "total": round(total, 1),
            "phd_pct": round(phd_pct, 1), "sfr_avg": None if sfr_avg is None else round(sfr_avg, 2)}


def award_decision(rules: dict, criteria: list[dict], data: Data, sfr_avg: float | None) -> dict:
    if rules["award"].get("style") == "marks":
        return award_decision_marks(rules, criteria, data, sfr_avg)
    grades = [c["grade"] for c in criteria if c["grade"]]
    nY, nW, nD = grades.count("Y"), grades.count("W"), grades.count("D")
    phd = _avg([data.get("PHD", y, 0) for y in ("CAY", "CAYm1")])
    rf = _avg([data.get("S", y, 0) / 20 for y in ("CAY", "CAYm1")])
    phd_pct = 100 * phd / rf if rf else 0
    hod = data.get("HOD_PHD", "CAY", 0)
    counted = [prof_condition(data, y) for y in ("CAY", "CAYm1")]
    prof_ok = all(counted) if None not in counted else data.get("PROF_OK", "CAY", 0) >= 1
    data.missing = []

    def check(rule):
        unmet = []
        if nY < rule["min_Y"]:
            unmet.append(f"At least {rule['min_Y']} criteria with grade Y (now {nY})")
        if "allow_W" in rule and nW > rule["allow_W"]:
            unmet.append(f"No criterion with grade W (now {nW})")
        if nD > rule["allow_D"]:
            unmet.append(f"No criterion with grade D (now {nD})")
        if phd_pct < rule["phd_pct_of_RF"]:
            unmet.append(f"Ph.D. faculty >= {rule['phd_pct_of_RF']}% of required faculty, avg of CAY and CAYm1 (now {phd_pct:.1f}%)")
        if sfr_avg is None or sfr_avg > rule["max_sfr"]:
            unmet.append(f"Average SFR <= {rule['max_sfr']}:1 (now {'unknown' if sfr_avg is None else f'{sfr_avg:.2f}'})")
        if hod < 1:
            unmet.append("HoD appointed on regular basis with Ph.D. in CAY")
        if not prof_ok:
            unmet.append("2 Professors, or 1 Professor + 1 Associate Professor, regular with Ph.D. in CAY and CAYm1")
        return unmet

    six = check(rules["award"]["six_years"])
    three = check(rules["award"]["three_years"])
    status = "Accreditation for 6 years" if not six else ("Accreditation for 3 years" if not three else "Not accredited")
    return {"status": status, "unmet_six": six, "unmet_three": three,
            "grades": {"Y": nY, "C": grades.count("C"), "W": nW, "D": nD},
            "phd_pct": round(phd_pct, 1), "sfr_avg": None if sfr_avg is None else round(sfr_avg, 2)}


def run_audit(program: dict, claims: list[dict], faculty: list[dict], metrics: dict | None = None,
              ai_review: Callable | None = None, ai_scope: set | None = None,
              ai_cached: Callable | None = None) -> dict:
    """ai_scope: criteria the AI reviews now (None = all); other criteria use ai_cached (earlier results)."""
    rules = load_rules(program["sar_version"])
    years = AssessmentYears(program["cay_start_year"], program.get("ay_start_month", 7))
    data = Data(metrics or {}, years)

    sha_owner = defaultdict(list)
    for c in sorted(claims, key=lambda c: c.get("id") or 0):
        for p in c.get("proofs", []):
            if c.get("id") not in sha_owner[p["sha256"]]:
                sha_owner[p["sha256"]].append(c.get("id"))

    ctx = {"years": years, "faculty": faculty, "aliases": institute_aliases(program), "sha_owner": sha_owner,
           "ai_review": ai_review, "data": data, "sfr_avg": None, "tables": rules.get("tables", {}),
           "ai_scope": ai_scope, "ai_cached": ai_cached}

    by_item = defaultdict(list)
    for c in claims:
        by_item[c["item_code"]].append(c)

    # 6.2.1 before 8.3
    order = sorted(rules["items"].items(), key=lambda kv: (kv[1]["type"].startswith("improvement"), 0))
    results = {}
    for code, item in order:
        item = {**item, "code": code}
        awarded, findings, notes = SCORERS[item["type"]](item, by_item.get(code, []), ctx)
        results[code] = (item, awarded, findings, notes)

    report_criteria = []
    total = total_max = 0.0
    for num, crit in rules["criteria"].items():
        items_out, awarded_c, max_c = [], 0.0, 0.0
        for code, item in rules["items"].items():
            if item["criterion"] != num:
                continue
            it, awarded, findings, notes = results[code]
            items_out.append({
                "code": code, "title": item["title"], "max": item["max"], "awarded": r2(awarded),
                "type": item["type"], "notes": notes, "evidence_file": item.get("evidence_file", ""),
                "window": years.window_text(item["window"]) if item.get("window") else "",
                "findings": [f.as_dict() for f in findings],
            })
            awarded_c += awarded
            max_c += item["max"]
        pct = 100 * awarded_c / crit["max"] if crit["max"] else 0
        report_criteria.append({
            "number": num, "name": crit["name"], "max": crit["max"], "audited_max": max_c,
            "awarded": r2(awarded_c), "percent": round(pct, 1),
            "grade": grade(pct, rules["grade_bands"]),
            "coverage": "full" if max_c >= crit["max"] else ("partial" if max_c else "none"),
            "items": items_out,
        })
        total += awarded_c
        total_max += max_c

    fixes = []
    for cr in report_criteria:
        for it in cr["items"]:
            lost = it["max"] - it["awarded"]
            if lost > 0.05:
                fixes.append({"code": it["code"], "title": it["title"], "marks_recoverable": r2(lost),
                              "criterion": cr["number"]})
    fixes.sort(key=lambda x: -x["marks_recoverable"])

    consistency = consistency_checks(data.m)
    questions = [c["question"] for c in consistency]
    for cr in report_criteria:
        for it in cr["items"]:
            for fd in it["findings"]:
                questions.extend(fd["questions"])

    return {
        "program": {k: program.get(k) for k in ("name", "department", "institute_name", "tier", "sar_version")},
        "rules_name": rules["name"],
        "years": [{**r, "from": r["from"].isoformat(), "to": r["to"].isoformat()} for r in years.table()],
        "criteria": report_criteria,
        "awarded": r2(total),
        "audited_max": total_max,
        "pre_qualifiers": pre_qualifiers(data, ctx["sfr_avg"]) if rules.get("pre_qualifiers") else [],
        "award": award_decision(rules, report_criteria, data, ctx["sfr_avg"]),
        "fix_list": fixes,
        "consistency": consistency,
        "peer_team_questions": list(dict.fromkeys(questions))[:40],
    }
