# CASE-STUDY-DRAFT.md — Document Intake QC Agent

_Running draft — update at every phase close (PROJECT-STANDARD.md exit ritual). Final pass uses the case-study skill → portfolio/._

## Working title
Document Intake QC Agent

## The problem
SMEs and legal/ops teams receive messy file dumps — bad names, duplicates, wrong formats — and QC them by hand or not at all.

## The build (updated as phases close)
- _Planned approach:_ Python automation that takes a folder of messy mock client files, validates and classifies them, flags exceptions, and outputs an eDiscovery-grade QC report.
- **Phase 0 (done):** Built a realistic 41-file "client dump" test fixture — invoices, contracts, correspondence, reports, photos — with 21 deliberately planted problems across 9 categories (duplicates, misleading extensions, empty files, impossible dates, junk files, naming chaos). The fixture and its answer key are generated from the same script, so the test can't lie: every planted error is known, and the pipeline's catch rate can be measured exactly.

## Results & proof
- Phase 0 QC gate: 39 automated checks, 0 failures (`scripts/qc_phase0.py`). Angle for the case study: the *test harness itself* is eDiscovery-grade — you can't claim a QC tool works unless you know exactly what it should catch.

## What this demonstrates
- _Draft after Phase 1 — tie to Joel's positioning (data rigor + AI automation)._
