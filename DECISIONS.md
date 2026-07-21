# DECISIONS.md — Document Intake QC Agent

_Format per PROJECT-STANDARD.md. Newest first._

## 2026-07-21 — Phase 3 model is `gemma4:12b`, measured before any pipeline code was written
**Decision:** The Phase 3 classifier calls `gemma4:12b` (11.9B parameters, Q4_K_M quantization, 7.04 GB) via Ollama at `http://127.0.0.1:11434`. The model was chosen and **measured first**, before `classify.py` existed.
**Why:** STATUS.md named this the load-bearing step — build only around a model already proven on the fixture. A throwaway spike classified 12 documents from text alone and scored **12/12**, including both traps (`scan_001.pdf`, a contract under a scanner-default name, and `report_final.pdf`, a report that is plain text wearing a `.pdf` extension). At ~1.0s per document, a 41-file run takes under a minute. It fits the 12,227 MiB VRAM ceiling with room to spare. The full-fixture gate later confirmed **32/32** on every classifiable file. The prior assumption ("an 8B-class local model clears the gate — untested") is now **retired, confirmed**.
**Rejected:** Escalating to a larger local model (unnecessary — accuracy is already 100% on the fixture, and a bigger model costs VRAM and speed for no measurable gain); reopening the hosted-API question (no evidence justified it).

## 2026-07-21 — The classifier never sees the filename or the folder
**Decision:** `classify.py` sends the model the document's extracted **text only**. The folder a file sits in is recorded separately as `folder_hint` in `classifications.csv` and is never part of the prompt.
**Why:** Putting `Invoices/INV-2026-001.pdf` in the prompt hands the model the answer, and any accuracy score after that measures our hint, not the document. Keeping them apart buys two things: the accuracy number is honest, and the folder becomes **independent evidence** — which is what makes the `CLASS_CONFLICT` rule possible at all. Two independent signals that must agree is the eDiscovery second-reviewer pattern; if the model just echoed the folder back, the rule could never fire. This is also the finding a client cares about most: "this document is filed in the wrong place."
**Rejected:** Including the filename to boost accuracy (inflates the score and destroys the cross-check — the fixture's traps exist precisely because names lie); classifying by folder alone (the two traps prove folders are evidence, not gospel).

## 2026-07-21 — Confidence alone cannot gate anything, so a folder cross-check gates alongside it
**Decision:** Three rules route classification problems into `exceptions.csv`: `UNCLASSIFIED` (no readable text, or the model failed), `CLASS_CONFLICT` (content label disagrees with the folder), and `LOW_CONFIDENCE` (below a 0.75 floor). All three live in `rules.py`, not `classify.py` — that script gathers evidence, this one decides what counts as a problem.
**Why:** Measurement killed the original plan. The model reports confidence **0.9–1.0 on every document it can read**, including the Office lock file. Sampling it three times at temperature 0.8 produced 100% self-agreement everywhere. A "low confidence routes to exceptions" gate built on that number can never fire, so it would pass **vacuously** — the exact failure the Phase 2 negative test exists to catch. `CLASS_CONFLICT` supplies a signal the model does not grade itself on. `LOW_CONFIDENCE` is kept anyway: it costs nothing, and a real client set will contain documents this fixture does not.
**Rejected:** Self-consistency (N samples, agreement rate as confidence) — more defensible in principle, but this fixture already showed 100% agreement, so it would have tripled runtime for no new information; shipping the threshold alone (leaves the low-confidence path untested and the gate empty).
**Also:** On this fixture `CLASS_CONFLICT` and `LOW_CONFIDENCE` fire **zero times** — the model agrees with the folder everywhere and is never unsure. That is a real result, not a broken rule, and from the outside the two look identical. Both were therefore proven with a **negative test**: planting a contract label on a file in `Invoices/` and a 0.40 confidence made each rule fire, then the fixture was restored and re-verified clean.

## 2026-07-21 — Document classes are generated into the answer key, not hand-listed in the gate
**Decision:** `make_mock_data.py` gained a `DOC_CLASSES` table and writes a `classes` map into `seeded-errors.json`. Genuinely ambiguous files carry a **list** of acceptable labels; files with no readable text carry `null`.
**Why:** The project rule is "never hand-edit the answer key — change the generator and regenerate," and a class map is part of the answer key. Two set-difference checks in the generator fail loudly if a fixture file has no class or a class names a missing file, so the two cannot drift. The list-valued entries matter for honesty: `notes.txt` is a client handover note that a reviewer could defensibly file as correspondence **or** other, and scoring the model against one arbitrary pick would be fake precision. `null` entries encode the stronger requirement — a photo has no correct label, and inventing one is a failure.
**Rejected:** A `CLASS_TRUTH` dict inside `qc_phase3.py` (less invasive, but it is a hand-maintained key that can drift from the fixture content — the thing this project explicitly forbids); folder names as ground truth (the two traps make folders wrong by construction).

## 2026-07-21 — Phase 2's gate is scoped to Phase 2's rules
**Decision:** `qc_phase2.py` filters `exceptions.csv` to its own nine rule IDs before checking anything.
**Why:** Once Phase 3 runs, `exceptions.csv` also carries classification rows. A site photo has no readable text and so earns an `UNCLASSIFIED` row, but it carries no *seeded* error — so Phase 2's "no clean file was flagged" check would read it as a false positive and fail a gate that is not about it. Scoping keeps each gate responsible for its own phase. Verified: Phase 2 still passes 22/0 with classification rows present, and Phase 1 still passes 13/0.
**Rejected:** Writing classification findings to a separate file (splits the client-facing exceptions list in two — the client should read one queue); loosening Phase 2's false-positive check (that check is the precision guarantee and the case study's strongest claim).

## 2026-07-21 — Text extraction dispatches on true content type, with no catch-all fallback
**Decision:** `extract_text()` dispatches on the manifest's magic-byte `true_type`, and formats with no text layer return `""`. There is deliberately **no** "just read it as text" fallback.
**Why:** The first draft had one (`read_text(errors="ignore")`), and the spike caught it scraping **541 characters of binary garbage out of a PNG** — which the model then confidently labeled. A fallback that always returns something converts a loud failure into a quiet wrong answer. Dispatching on content rather than extension is also what reads `report_final.pdf` correctly: it is text wearing a `.pdf` name, and the PDF reader would simply fail on it. Proven by negative test: reintroducing the fallback made `qc_phase3.py` fail with exit 1, naming all five photos and the archive as files given invented labels, with `unclassified` silently dropping from 9 to 3.
**Rejected:** Keeping the fallback for robustness (robustness that produces wrong answers is worse than a clean empty result); OCR on the images (out of scope — the fixture's photos are solid colors with no text, and OCR is the PDF-OCR project's territory).

## 2026-07-21 — Phase 3 classifies with a local open-source model via Ollama, not a hosted API
**Decision:** The Phase 3 classification layer calls a small open-source instruct model running locally through Ollama (OpenAI-compatible endpoint at `127.0.0.1`). No API key, no account, no network call. The specific model is chosen at install time and logged here once measured — not picked from memory.
**Why:** Four reasons, heaviest first. (1) **The client story.** "Documents are classified locally — nothing leaves the machine" is the most sellable sentence this project can offer legal, compliance, and bookkeeping prospects, which is exactly the audience in the PLAN kickoff block. A hosted API throws that away for no gain. (2) **`$0` becomes literal**, not "free tier until the quota changes" — the Phase 3 gate requires API cost = $0, and DeepSeek is [retiring `deepseek-chat`/`deepseek-reasoner` on 2026-07-24](https://api-docs.deepseek.com/updates/), three days after this decision, which is exactly how hosted free tiers rot. (3) **Reproducible** — one command after a fresh clone, which the Phase 5 fresh-eyes gate depends on. (4) **The task is small**: five labels from a text snippet does not need a frontier model, and the fixture's folder names (`Invoices/`, `Contracts/`, `Correspondence/`, `Reports/`) give independent ground truth to *measure* accuracy rather than assume it.
**Rejected:** **DeepSeek V4** (Joel's initial suggestion) — real and strong, but sized wrong for local use: [V4-Pro is 1.6T parameters and V4-Flash 284B](https://api-docs.deepseek.com/news/news260424/), and this machine has 12,227 MiB VRAM (RTX 5070, verified via `nvidia-smi`) with 31.6 GB system RAM. Neither runs here, so "DeepSeek V4" in practice means their **paid** API — roughly $0.006 for the full 41-file run, trivial in absolute terms but non-zero, which breaks the gate and the free-first rule and adds a key to the README. There is also no free route: [every DeepSeek listing on OpenRouter is paid; the `:free` variant has no provider behind it](https://ofox.ai/blog/deepseek-v4-flash-free-zero-cost-paths-2026/). **Hosted free tiers** (Gemini, OpenRouter free models) — $0 today, but rate-limited, key-gated, and revocable without notice.
**Also:** Joel raised **OpenCode as the harness** in the same breath. Kept separate deliberately: the harness is which agent writes the code and never appears in the deliverable; the runtime LLM is what `classify.py` calls and is the thing this entry decides. OpenCode stays a worthwhile experiment on its own, not bolted onto a phase mid-build.

## 2026-07-21 — Phase 2 ships nine rules, not the six named in PLAN.md
**Decision:** `rules.py` implements nine rules — the six in PLAN.md Phase 2 (exact dupes, near-dupe names, naming, extension mismatch, zero-byte, date anomalies) plus `JUNK_FILE`, `ARCHIVE`, and `DEEP_NESTING`.
**Why:** The Phase 2 gate requires *every* seeded error to appear in `exceptions.csv`, and the answer key seeds **nine** exception types. The three extras were already named in the Phase 0 exception taxonomy (SOP-DRAFT open questions) — PLAN.md's prose list simply under-counted them. Six rules would have left three seeded types uncaught and failed the gate.
**Rejected:** Folding junk/archive/deep-nesting into `NAMING` (conflates "badly named" with "shouldn't be here" and "can't see inside" — different severities and different client actions); amending the gate to ignore the three types (would hide real findings to protect a plan).

## 2026-07-21 — Zero-byte files are excluded from the exact-duplicate rule
**Decision:** `EXACT_DUP` groups by SHA-256 only among files with `size_bytes > 0`.
**Why:** Every empty file has the *same* SHA-256 (`e3b0c442…`, the hash of nothing). The fixture has two unrelated empty files (`memo_draft.docx`, `empty_placeholder.pdf`); without this exclusion they'd be reported as content duplicates of each other, which is false. They're already caught by `ZERO_BYTE` — the accurate finding.
**Rejected:** Reporting them as duplicates (technically true of the hash, misleading about the files); special-casing the known empty hash (the size check is clearer and needs no magic constant).

## 2026-07-21 — Junk files are excluded from the extension-mismatch rule
**Decision:** `EXT_MISMATCH` skips files identified as junk (`~$` prefix, `Thumbs.db`, etc.).
**Why:** `~$ntract_temp.docx` is named `.docx` but its content is text, so the rule would flag it as an extension mismatch. The answer key classifies it as **junk**, and that's the useful finding — an Office lock file's extension isn't the point; its presence is. Reporting both would send the client chasing a type problem on a file that should simply be deleted.
**Rejected:** Letting it trip both rules (noise that misdirects the remediation).

## 2026-07-21 — A file gets one exception row per rule it trips
**Decision:** `exceptions.csv` is keyed on (file, rule), not one "worst problem" row per file.
**Why:** A file can be both a duplicate *and* badly named (e.g. `INV-2026-005_AcmeSupply (1).pdf`). Collapsing to one row hides findings; the eDiscovery standard is that every issue is separately visible and separately dispositioned. Totals still reconcile because the gate checks distinct-file counts, not row counts.
**Rejected:** One row per file with a concatenated issue list (harder to filter, sort, and count by severity in the Phase 4 report).

## 2026-07-21 — The Phase 2 gate is proven with a negative test
**Decision:** Beyond the 22 passing checks, the gate was validated by deliberately disabling the `ZERO_BYTE` rule, confirming it failed (4 failures naming both empty files, exit 1), then reverting.
**Why:** A gate that passes is only evidence if it *can* fail. Without the negative test, "PASS: 22" could mean the checks never actually inspect anything. This is the difference between recognition and verification.
**Rejected:** Trusting the pass count alone.

## 2026-07-20 — Seed document-author metadata into the fixture; keep author, party, and custodian as three distinct axes
**Decision:** `make_mock_data.py` now writes an embedded author into each generatable file (PDF `/Author`, DOCX/XLSX core.xml creator). 22 of 41 files are authored; 19 are Unassigned (formats that can't carry an author — txt/csv/png/zip/legacy .doc — plus scanned PDFs and empty files). The answer key gains an `authors` path→author|null map (`seeded-errors.json`) built from the same values, so it can't drift. One planted **divergence file** — `CTR-2026-01_ServiceAgreement_AcmeSupply.docx`, filename party = Acme, embedded author = Dana Cruz (Meridian) — proves the demo's point that **author ≠ party ≠ custodian**.
**Why:** Joel chose true document-author handling (Option C) over parsing the party token out of filenames. "Author" is a real embedded property; "custodian" is the collection source (a Phase 4 mapping concept); the filename party is a third thing. Keeping them separate is the eDiscovery-rigor signal for the case study — and the divergence file makes the distinction visible instead of theoretical.
**Also:** reportlab defaults `/Author` to the literal `"anonymous"` when unset — `make_pdf` now blanks it (`setAuthor("")`) for unauthored PDFs so they read back as None, not a misleading "anonymous". `pypdf` introduced to read PDF authors (reused in Phase 3 for text). Regenerating rotated all fixture hashes (expected; `manifest.csv` is gitignored). Terminology locked: we say **document author** for the metadata and reserve **custodian** for the Phase 4 mapping.
**Rejected:** Deriving custodian from the filename party token (conflates three axes; over-claims). Labeling the extracted field "custodian" (inaccurate — it's the author property). Leaving reportlab's "anonymous" default in place (contradicts the Unassigned ground truth).

## 2026-07-19 — Phase 1 writes the manifest with the stdlib `csv` module (pandas deferred to Phase 2)
**Decision:** `scan.py` builds `manifest.csv` using Python's built-in `csv` module. pandas (named in the PLAN stack) is introduced in Phase 2.
**Why:** Phase 1 is "walk files → write rows," which `csv.DictWriter` does with zero dependencies (pandas isn't installed on the machine). pandas earns its place in Phase 2, where the rule engine groups by hash and filters across the manifest — the natural moment to `pip install` and teach it. The `manifest.csv` output is identical either way, so this changes only when pandas enters, not the deliverable.
**Rejected:** Installing pandas in Phase 1 (unneeded dependency for a one-line CSV write); using pandas throughout (no benefit until Phase 2's grouping queries).

## 2026-07-19 — `manifest.csv` is a generated artifact, not committed
**Decision:** Add `manifest.csv` to `.gitignore`; `scan.py` is the committed deliverable.
**Why:** Same reasoning as the fixture hashes rotating per run — PDF bytes embed a creation timestamp, so the manifest's hashes change every time the fixture is regenerated. Committing it would create churn. It's reproducible in one command (`py scripts/scan.py`). A frozen sample can be captured for the case study in Phase 5.
**Rejected:** Committing `manifest.csv` (noisy diffs on every regeneration).

## 2026-07-19 — `created` column uses `st_ctime`; true type is content-based
**Decision:** The manifest's `created` timestamp is `os.stat().st_ctime` (creation time on Windows); `true_type` is detected from leading magic bytes, reporting the ZIP family (`zip`) for `.docx`/`.xlsx`/`.zip` alike.
**Why:** `st_ctime` is the creation time on Windows (the target platform); on Unix it is inode-change time — noted in code, and `created` is informational since only `mtime` is tampered in the fixture and Phase 2's date rule checks `mtime`. Magic-byte typing can't distinguish OOXML from a plain zip (all are zip containers), so reporting `zip` honestly and letting Phase 2's extension rule flag disagreements avoids over-claiming.
**Rejected:** Claiming a precise `docx`/`xlsx` type from bytes alone (impossible without opening the container — a Phase 2 concern).

## 2026-07-19 — GitHub remote
**Decision:** Repo lives at https://github.com/iskow/doc-intake-qc.git — push at each phase close. Pushes run via Desktop Commander on Joel's PC (cached credentials); the sandbox has none.
**Why:** Joel provided the repo; every future session needs to know where to push.
**Rejected:** Pushing from the sandbox (no credentials, and never store tokens in the project).

## 2026-07-19 — Fixture is generated, not hand-made
**Decision:** `scripts/make_mock_data.py` builds `mock-intake/` and the answer key (`seeded-errors.md` + `.json`) from one data structure; `seed()` is called at the moment each problem is planted.
**Why:** Answer key can't drift from the fixture; fixture is reproducible after a fresh clone (needed anyway — git doesn't preserve the seeded mtime anomalies).
**Rejected:** Hand-placing files (key goes stale; not reproducible); committing only the generator without the fixture (PLAN.md requires the fixture committed untouched as the permanent test fixture).

## 2026-07-19 — `.gitattributes` marks everything binary (`* -text`)
**Decision:** Disable git line-ending conversion for all files in this repo.
**Why:** CRLF conversion on a Windows checkout would silently rewrite text-file bytes, changing SHA-256 hashes and breaking the duplicate pairs the fixture depends on.
**Rejected:** Default autocrlf behavior.

## 2026-07-19 — QC gate is a script, not an eyeball check
**Decision:** `scripts/qc_phase0.py` verifies every answer-key claim in code (existence, counts, hash-identical dupe pairs, magic-byte mismatches, mtime anomalies, required-type coverage) and includes the inverse absence check: no *unintended* hash duplicates.
**Why:** PROJECT-STANDARD.md — numbers verified in code; rerunnable evidence for the gate table.
**Rejected:** Manual spot-checking.

## 2026-07-19 — Kickoff decisions
**Decision:** Python CLI tool, non-destructive by design (works on copies), rule-based validation first with an AI classification layer added after core rules are proven.
**Why:** Deterministic rules are testable and defensible (eDiscovery mindset); AI adds value on top rather than being an unreliable foundation.
**Rejected:** See PLAN.md kickoff block for scope exclusions.

## Assumptions & risks
- [ASSUMPTION: open] All client data is mocked — no real client files anywhere in this project.
- [ASSUMPTION: open] Free-tier tools suffice for demo scale; paid tiers noted as information only.
- [ASSUMPTION: **retired, confirmed** 2026-07-21] A small local instruct model clears the Phase 3 gate on this fixture. Measured before any pipeline code was written: `gemma4:12b` scored 12/12 on the spike and **32/32** on every classifiable file in the full gate, at ~1.0s per document. No escalation needed.
- [ASSUMPTION: open] Accuracy on this fixture does not predict accuracy on a real client set. The fixture's documents are short, clean, and generated; real intakes bring scans, OCR noise, mixed languages, and document types outside the five labels. Before any paid engagement, measure on a sample of that client's own documents rather than quoting the 32/32 figure.
- [RISK: open] `gemma4:12b` reports confidence 0.9–1.0 on everything it can read, so the `LOW_CONFIDENCE` rule is effectively dormant on this fixture. `CLASS_CONFLICT` carries the real cross-check. If a client set needs genuine uncertainty scoring, revisit self-consistency sampling (measured and rejected today for adding no information here).
- [ASSUMPTION: open] Date anomalies are seeded on the *modified* date only — creation time isn't settable from Python on most systems. Phase 2's date rule must check mtime.
- [ASSUMPTION: open] Fixture hashes rotate on each generator run (reportlab embeds a creation timestamp in PDF bytes). Invariant to rely on: within any one run, each duplicate pair is hash-identical. Phase 1/2 tests must not hard-code hash values.
- [RISK: open] Git run from the sandbox can't delete its own lock/temp files on the mounted folder. Mitigation in STATUS.md handoff notes (clean via Desktop Commander).
