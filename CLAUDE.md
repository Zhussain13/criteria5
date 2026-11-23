# CLAUDE.md — CMRSARathi · Criterion 5

One-criterion pilot of CMRSARathi (https://github.com/Zhussain13/CMRSARathi) for the CMRIT Mega Project committee:
NBA Criterion 5 (Faculty Information, 100 marks), end to end, with no AI.

- **Owner:** Prof. Zaheed Hussain (Zhussain13), Dept. of CSE, CMRIT. Co-PI: Prof. Thushara Bharathi.
- He reads on his phone: plain, short English; lead with the result.
- **No real names** in demo data, code or docs: position names and staff codes only ("CSE Faculty 001", "HoD CSE").
- **Roles stay exactly:** platform owner (superadmin), principal, hod, crit_head, member. No admin / institute role.
  Rights (owner, 23 Nov): member = typing/data entry only in assigned SAR sections, no delete; crit_head = whole SAR
  of their criteria (add/edit/delete); hod = every operation; principal = view + run audit only.
- **Least human work:** the website drafts the SAR (app/sar_draft.py); tables always come from the data; one-click buttons.
- **Marks never come from AI.** Every mark comes from the NBA formulas in `app/engine.py`; proofs are read with
  pdfplumber / Tesseract. Optional AI audit (owner, 23 Nov): Gemini (free AI Studio key, GEMINI_API_KEY or
  Platform › AI settings) reviews the written SAR like a peer team (score per section, gaps, text-vs-numbers checks,
  questions) in ONE cached request (`app/ai.py`). Never put the key in git.
- **Formal SAR:** the document follows the NBA SAR format, April 2026 (Table No. 5A, 5.1.2, 5.2.1, 5.3.1, 5.4.1,
  5.5.1); the SAR part is rebuilt from the data on every view; untouched drafted write-ups follow the data too.
- Commits: author `Shaik Mohammed Zaheed Hussain <zhussain9753@gmail.com>`, no co-author lines.
- Every change: update README.md and docs/CHANGELOG.md in the same commit; keep `python -m pytest -q` passing.

## Layout

```
app/rules/criterion5_tier1_2026.yaml  the only rules file (items 5.1-5.5, data fields, Table 5.4.1, award)
app/roster.py          counts per year from the faculty list; MIN_DAYS_IN_YEAR and mid-year rules (VERIFY with IQAC)
app/faculty_proofs.py  proof checks and status; faculty_issues() for the report
app/c5audit.py         build_report(): engine run, strict (verified-only) run, issues, pre-qualifiers, award conditions
app/views/faculty.py   faculty list, edit, import (ALIASES = ERP column names), proof upload
app/erp.py             ERP link, year-wise: years(), year_data(); DemoERP (sample) / DatabaseERP (to fill); compare(),
                       year_preview(), import_year() -> faculty_year + erp_imports + S/DS of that SAR year
app/sar_draft.py       formal SAR part per section (auto_html, live) + department write-up draft (draft_html); Table 5A
app/ai.py              Gemini AI peer-team review (one request, cached, model fallback, quota handling)
app/views/document.py  live SAR document (sections, autosave, /state polling, draft, clear, prepare)
app/activity.py        activity log shown as "Recent updates" to head, HoD, principal
app/demo.py            seed-demo
```

## Next steps

1. Real ERP link: fill DatabaseERP in app/erp.py once CMRIT IT gives a read-only account and the table structure.
2. Confirm the counting rules with the IQAC (6 months in a year; designation/degree on 1 January).
3. Fold the faculty-list engine back into CMRSARathi when the committee approves all nine criteria.
