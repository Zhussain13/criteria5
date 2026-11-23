# Changelog

## 23 Nov 2026 — formal NBA SAR, ERP class-wise data, AI audit with Gemini

- **Formal SAR in the NBA format (April 2026):** Table No. 5A (faculty details), NBA instruction lines, Table No.
  5.1.2 with every UG/PG program and class (B, C, D / A, B), DS, AS, S, DF, AF, F, PoP, FF, TF and SFR per year,
  Table No. 5.2.1, 5.3.1 (two-row header, average numbers), 5.4.1 year by year with total hours, 5.5.1, and the
  assessment of every item. Rebuilt from the data on every view, so any data change shows at once.
- Drafted write-ups that nobody has edited follow the data too; edited ones are never touched.
- **ERP demo data per year:** each academic year has its own staff list and class-wise student strength of
  B.E. CSE, B.E. CSE (AI & ML), B.E. CSE (Data Science) and M.Tech CSE; imported into `student_strength`.
- **AI audit (Gemini, free key):** platform owner › AI settings (or GEMINI_API_KEY). "Run AI audit" for principal,
  HoD, criteria head. Marks stay formula-based; Gemini gives a write-up score (0-100), section scores (0-10),
  strengths, gaps, text-vs-numbers checks, peer-team questions and top actions. One cached request per audit.
- 32 tests (AI tested with a fake Google answer).

## 23 Nov 2026 — SAR written by the website; live document; new rights

- **SAR document (live, Google Docs style):** the website drafts the text of 5.1-5.5 from the data and builds the
  NBA tables from the data every time. People type straight on the page; it saves itself; others see the change
  within seconds. A section edited by a person is kept unless the head or HoD presses "Re-draft".
- **One click "Prepare everything":** ERP import for every assessment year, proof re-check, SAR draft, audit.
- **Rights:** criteria members see and type only the sections assigned to them and cannot delete anything; the
  criteria head edits, adds, deletes and re-drafts the whole SAR of their criteria and assigns sections; the HoD can
  do every operation; the principal views and runs the audit only (and creates user accounts).
- Section assignment in Program › Team. The Word SAR follows the live document (a member's copy has only their sections).
- 29 tests.

## 23 Nov 2026 — ERP import by academic year

- The ERP import is now year-wise, as the ERP stores its data: pick the ERP academic year (e.g. 2023-24) and the
  SAR year (e.g. CAYm2). That year's staff list (with that year's designation, degree, load) and student strength
  go into that SAR year. The matching SAR year is suggested; a mismatch is flagged.
- "Import all assessment years" imports CAY to CAYm3, each from its own ERP year.
- Preview shows the SAR numbers now and after the import (S, DS, F, Ph.D., M.Tech, cadre, FF), new and changed faculty.
- New tables `faculty_year` (year-wise staff list) and `erp_imports`; counting uses the ERP list for imported years.
- Numbers page and faculty list show "ERP 2023-24" on years that came from the ERP.

## 23 Nov 2026 — ERP link and shared updates

- **Import from ERP** button on the faculty list and the Numbers page: reads the faculty records and student
  strength from the ERP and fills the Criterion 5 SAR tables. A preview shows new faculty, changed details
  (e.g. promotion, leaving date) and student numbers before anything is saved. Demo connection with sample data
  for now (`ERP_MODE=demo`); `database` mode waits for read-only access from CMRIT IT.
- **Recent updates**: every change (ERP import, faculty added or edited, proof uploaded, numbers, entries, audit)
  is logged with who and when, and shown to the criteria head, HoD (dashboard) and principal (home page).
- Faculty records show their source (ERP / file / typed) and who changed them last; proofs show who uploaded them.
- Fix: a "0" in an imported sheet (e.g. part-time) was read as empty.
- 26 tests.

## 23 Nov 2026 — first version (Criterion 5 pilot)

The committee asked for one criterion working end to end before the full project. Criterion 5 was picked because
it needs no AI.

- New repo built from CMRSARathi: same design, institution login and roles (platform owner, principal, HoD,
  criteria head, criteria member). The IQAC-admin and Criterion 9 roles were dropped; the principal manages users.
- Rules cut to Criterion 5 only (100 marks), from the NBA Tier I SAR format of April 2026.
- Faculty list = NBA Table B2.A, with ERP/Excel import (column names matched automatically).
- Every Criterion 5 number is counted from the faculty list per year (F, PoP, FF, Ph.D., M.Tech, cadre, retention,
  HoD and Professor conditions); only student strength and three pre-qualifier facts are typed.
- Proof check of appointment letters and degree certificates with free OCR: name, institute, joining date,
  designation, degree, university, copied files.
- Audit: marks as claimed and marks if only checked proofs count, faculty issues, pre-qualifiers, faculty conditions
  for 3/6-year accreditation, fix list, peer-team questions; Word report and Criterion 5 SAR draft with Table B2.A.
- AI removed (no tokens used).
- Demo: CMRIT, B.E. CSE, 130 made-up faculty named by position and staff code only, sample proofs with planted errors.
- 24 tests (NBA worked examples, counting rules, proof checks, roles, demo).
