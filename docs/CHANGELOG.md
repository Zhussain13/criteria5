# Changelog

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
