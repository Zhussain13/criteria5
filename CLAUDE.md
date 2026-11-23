# CLAUDE.md — CMRSARathi · Criterion 5

One-criterion pilot of CMRSARathi (https://github.com/Zhussain13/CMRSARathi) for the CMRIT Mega Project committee:
NBA Criterion 5 (Faculty Information, 100 marks), end to end, with no AI.

- **Owner:** Prof. Zaheed Hussain (Zhussain13), Dept. of CSE, CMRIT. Co-PI: Prof. Thushara Bharathi.
- He reads on his phone: plain, short English; lead with the result.
- **No real names** in demo data, code or docs: position names and staff codes only ("CSE Faculty 001", "HoD CSE").
- **Roles stay exactly:** platform owner (superadmin), principal, hod, crit_head, member. No admin / institute role.
- **No AI.** Every mark comes from the NBA formulas in `app/engine.py`; proofs are read with pdfplumber / Tesseract.
- Commits: author `Shaik Mohammed Zaheed Hussain <zhussain9753@gmail.com>`, no co-author lines.
- Every change: update README.md and docs/CHANGELOG.md in the same commit; keep `python -m pytest -q` passing.

## Layout

```
app/rules/criterion5_tier1_2026.yaml  the only rules file (items 5.1-5.5, data fields, Table 5.4.1, award)
app/roster.py          counts per year from the faculty list; MIN_DAYS_IN_YEAR and mid-year rules (VERIFY with IQAC)
app/faculty_proofs.py  proof checks and status; faculty_issues() for the report
app/c5audit.py         build_report(): engine run, strict (verified-only) run, issues, pre-qualifiers, award conditions
app/views/faculty.py   faculty list, edit, import (ALIASES = ERP column names), proof upload
app/erp.py             ERP link: DemoERP (sample data) / DatabaseERP (to fill once CMRIT IT gives access); compare()
app/activity.py        activity log shown as "Recent updates" to head, HoD, principal
app/demo.py            seed-demo
```

## Next steps

1. Real ERP link: fill DatabaseERP in app/erp.py once CMRIT IT gives a read-only account and the table structure.
2. Confirm the counting rules with the IQAC (6 months in a year; designation/degree on 1 January).
3. Fold the faculty-list engine back into CMRSARathi when the committee approves all nine criteria.
