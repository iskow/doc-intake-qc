# CASE-STUDY-DRAFT.md — Document Intake QC Agent

_Running draft — update at every phase close (PROJECT-STANDARD.md exit ritual). Final pass uses the case-study skill → portfolio/._

## Working title
Document Intake QC Agent

## The problem
SMEs and legal/ops teams receive messy file dumps — bad names, duplicates, wrong formats — and QC them by hand or not at all.

## The build (updated as phases close)
- _Planned approach:_ Python automation that takes a folder of messy mock client files, validates and classifies them, flags exceptions, and outputs an eDiscovery-grade QC report.
- **Phase 0 (done):** Built a realistic 41-file "client dump" test fixture — invoices, contracts, correspondence, reports, photos — with 21 deliberately planted problems across 9 categories (duplicates, misleading extensions, empty files, impossible dates, junk files, naming chaos). The fixture and its answer key are generated from the same script, so the test can't lie: every planted error is known, and the pipeline's catch rate can be measured exactly.
- **Phase 1 (done):** Built the inventory scanner — a read-only walk of the folder that produces a `manifest.csv`: one row per file with its true type (read from the file's actual bytes, not its name), size, SHA-256 fingerprint, and dates. This is the evidentiary base every later check builds on. It already exposes the traps a name-based sort would miss: a PNG wearing a `.jpg` name, plain text renamed `.pdf`, empty files, and copy-paste duplicates that share a fingerprint.

- **Phase 2 (done):** Built the validation rules engine — nine deterministic checks that turn the inventory into an exceptions report. It finds byte-identical duplicates, files whose extension lies about their content, empty files, near-duplicate names where two versions disagree (which one is authoritative?), impossible dates, OS junk, unexpanded archives, naming-convention breaks, and files buried too deep to survive a manual review. It also reads each document's **embedded author** — distinct from the party named in the filename and from the custodian who supplied it, a distinction most tools blur. Every finding carries a severity and a plain-English reason.

## Results & proof
- **Phase 2 QC gate: 22 automated checks, 0 failures** (`scripts/qc_phase2.py`). On the 41-file dump: **30 exceptions across 23 files, 18 files clean.** All 21 planted errors were caught by the rule responsible for them — and, just as important, **not one clean file was falsely flagged.** Author extraction matched the answer key on all 41 files. Totals reconcile in code against both the folder and the answer key.
- **The gate itself was tested.** A rule was deliberately switched off to confirm the QC gate *fails* when something is missed — it did, naming the exact two files that slipped through. A passing test only means something if it can fail.
- Phase 0 QC gate: 39 automated checks, 0 failures (`scripts/qc_phase0.py`). The *test harness itself* is eDiscovery-grade — you can't claim a QC tool works unless you know exactly what it should catch.
- Phase 1 QC gate: 13 automated checks, 0 failures (`scripts/qc_phase1.py`). Row count reconciles with the folder and the answer key; every file is accounted for (nothing missed, nothing invented); duplicate pairs share a hash; and the scan is proven read-only by fingerprinting every file before and after — the originals are never touched.

## What this demonstrates
- **Data rigor first, AI second.** The tool identifies files by their content bytes and a cryptographic hash, not by trusting the filename — the same defensible, evidence-first approach used in eDiscovery. Duplicates are caught by fingerprint, not guesswork.
- **Chain-of-custody mindset.** The scanner is provably non-destructive (verified in code), so a client's originals are safe — the credibility a legal/ops or bookkeeping client needs before handing over a data set.
- **Precision matters as much as recall.** Catching every problem is easy if you flag everything. This engine caught 21 of 21 planted errors while leaving all 18 clean files untouched — so the exceptions list is a work queue, not noise a client has to re-sort by hand.
- **Findings are explainable, not black-box.** Every flag names the rule, a severity, and a concrete reason ("extension '.doc' but content is 'text'"). Deterministic rules come first precisely because they're defensible; the AI layer is added on top, never underneath.
- _Classification + AI angle drafts after Phase 3._
