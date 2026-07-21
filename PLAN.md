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
**AMENDED 2026-07-21 — split into 4a / 4b / 4c.** Joel identified a real-world requirement mid-phase: filesystem dates are used for date filtering in review and forensics, so a copy that loses them silently corrupts downstream culling. That moves metadata fidelity from "nice to have" to load-bearing, and it earns its own phase. See DECISIONS 2026-07-21.

- **4a — organize foundation.** ✅ DONE, gate 36/0. `organize.py --by class|author|custodian`, non-destructive, `Unassigned` routing, safety guard, idempotence.
- **4b — metadata fidelity.** ✅ DONE, gate 73/0. Preserve **creation** and **modified** timestamps, the NTFS ACL, the owner and alternate data streams on every copy — `shutil.copy2` preserves only modified, measured. Engine is batched `robocopy /COPY:DATSO` (DECISIONS 2026-07-21). Stop flattening: each file's original relative path is preserved *under* the bucket root, so folder structure and path metadata survive. The cross-reference CSV (original path → new path → hash) becomes **mandatory**, because re-foldering breaks the copy-as-is identity mapping that made it unnecessary in 4a. Filename collisions **stop the run** rather than auto-suffixing.
  **QC gate (AMENDED mid-phase):** created and modified match exactly on every copy; ACL, owner, ADS and all three timestamps proven on the copy engine by probe; original relative paths reconstructable; cross-reference complete and reconciling; negative tests proving each claim's check can fail.
  **The amendment:** this phase was planned to require "every copy's **three** timestamps match its source." That is not achievable and the requirement is withdrawn, not quietly failed. Reading a file updates its access time, so `scan.py` destroys the original in Phase 1; and NTFS defers access-time writes to disk, so source and copy legitimately disagree even when the copy was faithful. Measured both ways — see DECISIONS 2026-07-21. Access-time fidelity is proven on the engine in isolation instead, and the shortfall is a stated limitation in the 4c report rather than a passed check.
- **4c — the HTML QC report.** ✅ DONE, gate 131/0. `report.py` writes one self-contained `qc-report.html` (no external assets, no JS). Summary → one queue → classification → three-axis comparison → **date fidelity re-measured live at 246/246** → cross-reference → reconciliation → limitations. The gate checks the **rendered HTML**, not report.py's arithmetic, and enforces that the honest limitations stay in. Palette validated with the dataviz validator — two brand colors failed their contrast checks and were corrected. See DECISIONS 2026-07-21.

### Phase 4 (original scope, now spanning 4a–4c)
Generate the deliverable: an HTML/MD QC report (summary stats, exceptions by severity, classification breakdown) plus a non-destructive organized copy of the intake. `organize.py` takes a mode flag — `--by class|author|custodian` — and copies files (renamed to convention) into `organized/<value>/…`; originals never modified. `class` uses the Phase 3 label; `author` uses the Phase 2 embedded-author column; `custodian` uses a separate name/pattern→custodian mapping table (the collection-source axis, distinct from author). Files with no value for the chosen axis go to an `Unassigned/` bucket, never guessed.
**QC gate:** Report totals reconcile with manifest (in code). Organized copy is complete — file count matches, hashes match originals (re-hash before/after proves originals untouched). Each `--by` mode buckets correctly and routes blanks to Unassigned. Report passes the client-ready test.

### Phase 5 — Demo polish + case study final
**AMENDED 2026-07-21 — split into 5a / 5b** at Joel's call, for the same reason Phase 4 was split: the runnable demo and the portfolio close are separate deliverables with separate consumers, and each earns a full sitting.

- **5a — the one-command demo.** ✅ DONE, gate 85/0. `scripts/run_demo.py` runs the chain in the one order that works, holding it in code as `STEPS` so the README can be checked against it rather than trusted. `README.md` leads with the deliverable and carries a results table the gate recomputes from the live pipeline. `scripts/capture_screenshots.py` makes the screenshot reproducible, and `docs/report-snapshot.json` pins the figures so a stale image fails the build. See DECISIONS 2026-07-21.
  **QC gate (met):** the demo runs from a cleared tree and produces every declared artifact; the intake is unchanged; every documented command, path, flag and figure in the README is checked against the code; the committed screenshots are proven current; three negative tests, each isolating one claim.

- **5b — the portfolio close.** Not started. Finalize the case study via the case-study skill → `portfolio/doc-intake-qc.md`; qc-gate skill pass; `SOP-DRAFT.md` final pass. Optional Loom walkthrough — Joel records that himself.
  **QC gate:** qc-gate skill pass on the case study. Every number in it traceable to a gate result in STATUS. Zero typos.

**Original Phase 5 gate, for the record:** Fresh-eyes run: follow the README from scratch and it works. qc-gate skill pass on the case study. Zero typos. The fresh-eyes clause is now automated — `qc_phase5.py` deletes every generated artifact and runs the documented command.
