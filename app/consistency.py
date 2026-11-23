"""Cross-criteria consistency checks: numbers in one part of the SAR that do not agree with
numbers elsewhere, or that cannot be true. A peer team spots these quickly and asks about them,
so they are listed on the report even though they do not change the marks.

Found in the first real-data trial (CSE SAR, April 2022): placement 4.5 vs 7.3 mismatch,
SFR arithmetic error, admissions above intake.

Pure function: takes the data-sheet values {(key, year): value}, returns a list of dicts.
"""
from __future__ import annotations

from .years import YEAR_NAMES, display_name

ERROR, CHECK = "error", "check"   # error: cannot be true; check: probably wrong, needs an explanation


def _shift(year: str, by: int) -> str | None:
    i = YEAR_NAMES.index(year) + by
    return YEAR_NAMES[i] if 0 <= i < len(YEAR_NAMES) else None


def consistency_checks(metrics: dict, program_years: int = 4) -> list[dict]:
    m = metrics or {}
    out: list[dict] = []

    def v(key, year):
        x = m.get((key, year))
        return None if x is None else float(x)

    def years_with(*keys):
        ys = {y for (k, y) in m if k == keys[0]}
        return [y for y in YEAR_NAMES if y in ys and all(v(k, y) is not None for k in keys)]

    def add(severity, criteria, title, detail, question, fix):
        out.append({"severity": severity, "criteria": criteria, "title": title, "detail": detail,
                    "question": question, "fix": fix})

    # ---- admissions above intake (Criterion 4)
    for y in years_with("N", "N1"):
        n, n1 = v("N", y), v("N1", y)
        if n1 > n:
            add(CHECK, "4", f"Admissions above sanctioned intake in {display_name(y)}",
                f"N1 = {n1:g} admitted against an intake of N = {n:g}.",
                f"{n1 - n:g} more students were admitted than the sanctioned intake in {display_name(y)}. "
                "Show the approval for the extra seats.",
                "Attach the AICTE/university approval for the extra seats, or correct N or N1.")

    # ---- graduated vs admitted (Criterion 4)
    for y in years_with("GRAD"):
        grad = v("GRAD", y)
        if v("ADM", y) is not None:
            adm, label = v("ADM", y), "admitted (incl. lateral entry)"
        elif v("N1", y) is not None:
            adm = sum(v(k, y) or 0 for k in ("N1", "N2", "N3", "N5"))
            label = "admitted (N1 + N2 + N3 + N5)"
        else:
            continue
        if grad > adm:
            add(ERROR, "4", f"More graduates than students admitted, batch {display_name(y)}",
                f"Graduated {grad:g} but {label} {adm:g}.",
                f"How did {grad:g} students graduate from a batch of {adm:g}?",
                "Recheck the batch table (Table 4C): graduates cannot exceed students admitted.")
    for y in years_with("GRAD_NB", "GRAD"):
        if v("GRAD_NB", y) > v("GRAD", y):
            add(ERROR, "4", f"Graduates without backlogs exceed all graduates, batch {display_name(y)}",
                f"Without backlogs {v('GRAD_NB', y):g} > graduated in stipulated period {v('GRAD', y):g}.",
                "Graduates without backlogs are more than all graduates. Which number is right?",
                "Recheck both rows of the success-index table.")

    # ---- placement table (Criterion 4)
    for y in years_with("FS"):
        fs = v("FS", y)
        got = sum(v(k, y) or 0 for k in ("PLACED", "HIGHER", "ENTRE"))
        if got > fs:
            add(CHECK, "4", f"Placed + higher studies + entrepreneurs exceed final-year students, batch {display_name(y)}",
                f"{got:g} counted against {fs:g} final-year students (a student counted twice?).",
                "Some students seem to be counted twice in the placement table. Show the student-wise list.",
                "Count each student once (placement, higher studies or entrepreneurship).")
        adm = v("ADM", y)
        if adm is None and v("N1", y) is not None:
            adm = sum(v(k, y) or 0 for k in ("N1", "N2", "N3", "N5"))
        if adm is not None and fs > adm:
            add(CHECK, "4", f"Final-year students exceed students admitted, batch {display_name(y)}",
                f"FS = {fs:g} but {adm:g} admitted to this batch.",
                f"Where did the extra {fs - adm:g} final-year students come from?",
                "Recheck FS against the batch table.")

    # ---- placement in Criterion 4 vs Criterion 7 (older format, Table B.7.3)
    pairs = [("PLACED", "P7_PLACED", "placed"), ("HIGHER", "P7_HIGHER", "higher studies"),
             ("ENTRE", "P7_ENTRE", "entrepreneurs")]
    for grad_year in ("CAYm1", "CAYm2", "CAYm3"):
        batch = _shift(grad_year, program_years - 1)   # entered CAYm4 -> graduated CAYm1
        diffs = []
        for k4, k7, label in pairs:
            a, b = v(k4, batch), v(k7, grad_year)
            if a is not None and b is not None and a != b:
                diffs.append(f"{label}: {a:g} in Criterion 4 vs {b:g} in Criterion 7")
        if diffs:
            add(CHECK, "4, 7", f"Placement figures differ between Criterion 4 and Criterion 7 ({grad_year} graduates)",
                f"Batch {display_name(batch)}, graduated in {grad_year}: " + "; ".join(diffs) + ".",
                "Placement numbers in Criterion 4 and Criterion 7 do not match for the same batch. Which is correct?",
                "Use one student-wise placement list for both tables.")

    # ---- academic performance index inputs
    for p in ("API1", "API2", "API3"):
        for y in years_with(f"{p}_Y", f"{p}_Z"):
            if v(f"{p}_Y", y) > v(f"{p}_Z", y):
                add(ERROR, "4", f"{p[-1]}{'st' if p == 'API1' else 'nd' if p == 'API2' else 'rd'} year, {y}: "
                    "more successful students than appeared",
                    f"Y = {v(f'{p}_Y', y):g}, Z = {v(f'{p}_Z', y):g}.",
                    "More students passed than appeared for the exam. Please explain.",
                    "Recheck Y (successful) and Z (appeared).")
        for y in years_with(f"{p}_X"):
            if v(f"{p}_X", y) > 10:
                add(ERROR, "4", f"Mean GPA above 10 ({p}, {y})", f"X = {v(f'{p}_X', y):g}.",
                    "The mean GPA is above 10. Was a percentage entered instead of a 10-point value?",
                    "Enter the mean on a 10-point scale (percentage / 10).")

    # ---- SFR as written in the SAR vs recomputed (Criterion 5)
    for y in years_with("SFR_SAR", "S", "F"):
        tf = v("F", y) + (v("POP", y) or 0) - (v("FF", y) or 0)
        if tf <= 0:
            continue
        calc = v("S", y) / tf
        if abs(calc - v("SFR_SAR", y)) >= 0.05:
            add(ERROR, "5", f"SFR arithmetic does not match, {y}",
                f"The SAR writes {v('SFR_SAR', y):g}; the data give {v('S', y):g} / {tf:g} = {calc:.2f}.",
                f"Your SFR for {y} does not follow from your own student and faculty numbers. Please explain.",
                f"Correct the SFR in the SAR to {calc:.2f}, or correct S / F.")

    # ---- faculty numbers (Criterion 5)
    for y in years_with("PHD", "F"):
        phd, f = v("PHD", y), v("F", y)
        if phd > f:
            add(ERROR, "5", f"More Ph.D. faculty than faculty, {y}", f"Ph.D. {phd:g} > F {f:g}.",
                "The number of Ph.D. faculty is more than the total faculty. Please explain.",
                "Recheck the faculty table; use the same scope (Department + allied) for both.")
        elif v("MTECH", y) is not None and phd + v("MTECH", y) > f:
            add(CHECK, "5", f"Ph.D. + M.Tech faculty exceed total faculty, {y}",
                f"Ph.D. {phd:g} + M.Tech {v('MTECH', y):g} > F {f:g} (Ph.D. holders counted again under M.Tech?).",
                "Are Ph.D. holders also counted under M.Tech/M.E.? NBA counts each faculty once (Y excludes X).",
                "Count M.Tech/M.E. faculty without a Ph.D. only.")
    for y in years_with("AF1", "AF2", "AF3", "F"):
        cadre = v("AF1", y) + v("AF2", y) + v("AF3", y)
        if cadre != v("F", y):
            add(CHECK, "5", f"Cadre total differs from faculty count, {y}",
                f"Professors + Associate + Assistant = {cadre:g}, but F = {v('F', y):g}.",
                "The cadre table and the faculty count do not agree. Which faculty are left out or added?",
                "Make the cadre table and F cover the same faculty.")
    for y in years_with("DS", "S"):
        if v("DS", y) > v("S", y):
            add(ERROR, "5", f"Department students exceed Department + allied students, {y}",
                f"DS = {v('DS', y):g} > S = {v('S', y):g}.",
                "Students of the Department alone are more than Department + allied. Please explain.",
                "Recheck S and DS.")
    for y in years_with("RET_A", "AF"):
        total = sum(v(k, y) or 0 for k in ("RET_A", "RET_B", "RET_C", "RET_D", "RET_E"))
        if total != v("AF", y):
            add(CHECK, "5", f"Retention table total differs from Department faculty, {y}",
                f"A + B + C + D + E = {total:g}, but faculty in the Department AF = {v('AF', y):g}.",
                "The retention table does not cover the same faculty as the faculty list. Please explain.",
                "Every Department faculty member should appear once in the retention table.")
    for key, cadre, label in (("PROF_PHD", "AF1", "Professors"), ("ASSOC_PHD", "AF2", "Associate Professors")):
        for y in years_with(key, cadre):
            if v(key, y) > v(cadre, y):
                add(ERROR, "5", f"{label} with Ph.D. exceed {label.lower()} available, {y}",
                    f"{key} = {v(key, y):g} > {cadre} = {v(cadre, y):g}.",
                    f"More {label.lower()} with Ph.D. than {label.lower()} in the cadre table. Please explain.",
                    "Recheck the pre-qualifier rows against the cadre table.")
    if v("PROF_OK", "CAY") == 1 and v("PROF_PHD", "CAY") is not None:
        p, a = v("PROF_PHD", "CAY"), v("ASSOC_PHD", "CAY") or 0
        if not (p >= 1 and p + a >= 2):
            add(ERROR, "5", "Professor condition marked as met, but the counts say no",
                f"Marked yes, but CAY has {p:g} Professor(s) and {a:g} Associate Professor(s) with Ph.D.",
                "Which Professors / Associate Professors with Ph.D. meet the condition?",
                "Correct the yes/no row or the counts.")
    if v("HOD_PHD", "CAY") == 1 and v("PHD", "CAY") == 0:
        add(ERROR, "5", "HoD marked as Ph.D. holder, but no Ph.D. faculty in CAY",
            "HOD_PHD = 1 while Ph.D. faculty (CAY) = 0.",
            "The HoD has a Ph.D. but the faculty table shows none. Please explain.",
            "Correct the Ph.D. count for CAY.")

    out.sort(key=lambda x: (x["severity"] != ERROR, x["criteria"]))
    return out
