# PLAN.md — Document Intake QC Agent

**Priority 1 of 5 — ACTIVE.** Follows PROJECT-STANDARD.md: QC-gated phases, hard stop after each, one phase per session.

## Kickoff block
- **Job behind the task:** Prove Joel can turn eDiscovery-grade data rigor into an AI automation a client would pay for. The deliverable is credibility, not just a script.
- **Consumer:** Prospective clients (SMEs, legal/ops, bookkeepers) judging Joel's capability via the portfolio case study and demo.
- **Success criteria:** One command runs the full pipeline on the mock intake folder; every seeded error is caught; QC report totals reconcile with the manifest; case study reads client-ready.
- **Out of scope:** Real client data (mock only). GUI (CLI is fine; Tkinter is the PDF-OCR project's territory). Cloud deployment. Handling every file type on earth — the mock set defines the universe.
- **Stack:** Python (pathlib, hashlib, pandas), free LLM tier for classification (decision logged in Phase 3). No paid tools.
- **Assumptions:** [ASSUMPTION: Claude/Gemini free tier is enough for ~50-file classification demo.]

## Phases

### Phase 0 — Mock data + repo skeleton
Build the messy "client dump": ~40–60 files — PDFs, DOCX, XLSX, images, a ZIP, exact + near duplicates, bad/inconsistent names, a zero-byte file, wrong-extension files, nested folders. Keep a `seeded-errors.md` answer key listing every planted problem.
**QC gate:** Answer key complete; every planned exception type present in the mock set; folder committed untouched as the permanent test fixture.

### Phase 1 — Inventory scanner
`scan.py`: walk the intake folder (read-only), build `manifest.csv` — path, name, extension, true file type, size, hash (SHA-256), created/modified dates.
**QC gate:** Manifest row count = file count (verified in code). Hash-identical files share hashes. Ran on the fixture and watched it work.

### Phase 2 — Validation rules engine
Rule checks over the manifest: exact dupes (hash), near-dupe names, naming-convention violations, extension/content mismatch, zero-byte, date anomalies. Output `exceptions.csv` with rule ID + severity. **Author enrichment:** also read each file's embedded **document author** (PDF `/Author` via `pypdf`; DOCX/XLSX core.xml creator) into a new `author` manifest column — Unassigned where the format can't carry one or the file won't parse as its claimed type. (Author is distinct from custodian; see DECISIONS 2026-07-20.)
**QC gate:** Every seeded error appears in exceptions.csv (absence check against the answer key). Zero false "clean" verdicts. Extracted `author` column matches the `authors` map in `seeded-errors.json` (0 mismatches). Rules documented in SOP-DRAFT.md.

### Phase 3 — AI classification layer
Classify each document (invoice / contract / correspondence / report / other) using a free-tier LLM on extracted text snippets. Confidence per doc; low confidence routes to exceptions, never silently accepted.
**QC gate:** Spot-check 10 classifications by hand; all low-confidence docs in the exceptions list; API cost = $0. Decision on which LLM logged in DECISIONS.md.

### Phase 4 — QC report + non-destructive organize
Generate the deliverable: an HTML/MD QC report (summary stats, exceptions by severity, classification breakdown) plus a non-destructive organized copy of the intake. `organize.py` takes a mode flag — `--by class|author|custodian` — and copies files (renamed to convention) into `organized/<value>/…`; originals never modified. `class` uses the Phase 3 label; `author` uses the Phase 2 embedded-author column; `custodian` uses a separate name/pattern→custodian mapping table (the collection-source axis, distinct from author). Files with no value for the chosen axis go to an `Unassigned/` bucket, never guessed.
**QC gate:** Report totals reconcile with manifest (in code). Organized copy is complete — file count matches, hashes match originals (re-hash before/after proves originals untouched). Each `--by` mode buckets correctly and routes blanks to Unassigned. Report passes the client-ready test.

### Phase 5 — Demo polish + case study final
README with one-command demo, sample report screenshot, optional Loom walkthrough. Finalize case study via the case-study skill.
**QC gate:** Fresh-eyes run: follow the README from scratch and it works. qc-gate skill pass on the case study. Zero typos.
