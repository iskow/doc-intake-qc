# STATUS.md — Document Intake QC Agent

**Repo:** https://github.com/iskow/doc-intake-qc.git (push here at each phase close)
**Active:** yes (priority 1 of 5 — see PROJECT-STANDARD.md build order)
**Current phase:** Phase 3 — COMPLETE, gate passed (PASS 17 / FAIL 0), approved by Joel and **pushed** (`d66ff3d`, `2e25917..d66ff3d`). Phase 4 not started — run it in a fresh session.
**Last updated:** 2026-07-21 (Phase 3 built and gated: local classification with `gemma4:12b`, 32/32 accuracy)

## Done
- Plan approved by Joel (his go on Phase 0 doubled as plan QC).
- `scripts/make_mock_data.py` — generates the fixture AND the answer key from one data structure (they can't drift). Rerunnable: delete `mock-intake/` and rerun.
- `mock-intake/` — 41 files (+2 inside the zip), 6 folders incl. 4-level nesting.
- `seeded-errors.md` / `.json` — 21 seeded errors across 9 types (exact-duplicate ×3 pairs, near-duplicate-name, naming-violation ×6, extension-mismatch ×3, zero-byte ×2, date-anomaly ×2, junk-file ×2, archive, deep-nesting).
- `scripts/qc_phase0.py` — the Phase 0 QC gate as a rerunnable script.
- **Phase 1: `scripts/scan.py`** — read-only walk of `mock-intake/` → `manifest.csv` (41 rows). Columns: path, name, extension, true_type (magic-byte detection), size_bytes, sha256, created, modified. Archives listed as single files (not expanded). Reuses the chunked-SHA-256 pattern and grabs the type-sniff head in the same pass.
- **Phase 1: `scripts/qc_phase1.py`** — gate script: runs scan, then checks row count vs disk vs answer key, path-set absence check, dupe-pair hash match, no blank cells, archive-not-expanded, and a before/after read-only fingerprint.
- Git: repo initialized, `.gitattributes` (`* -text` — protects fixture hashes from CRLF conversion), commit `7f7fd7e` pushed to origin/main. `manifest.csv` added to `.gitignore` (generated artifact, hashes rotate per run).
- **Phase 2: `scripts/rules.py`** — reads `manifest.csv` with pandas, applies **nine** rules, writes `exceptions.csv` (path, name, rule_id, severity, detail). One row per (file, rule). Result on the fixture: **30 exceptions across 23 files; 18 files clean; 41 total.** Rules: EXACT_DUP, EXT_MISMATCH, ZERO_BYTE (high); NEAR_DUP_NAME, DATE_ANOMALY, JUNK_FILE, ARCHIVE (medium); NAMING, DEEP_NESTING (low). Nine not six — see DECISIONS 2026-07-21.
- **Phase 2: `scripts/qc_phase2.py`** — gate script: runs scan → rules, then absence-checks every seeded error against its owning rule, asserts **zero false positives** on the 18 clean files, reconciles totals, verifies the author column against the answer key, and checks every exception row is well-formed. Read-only proven by before/after fingerprint.
- **Phase 2: author column in `scan.py`** — `read_author()` reads PDF `/Author` (pypdf), DOCX/XLSX creator (python-docx/openpyxl); `Unassigned` where the format can't carry one or the file won't parse as its claimed type. Verified **0 mismatches** vs the answer key's `authors` map (41/41). pypdf's warning logger silenced (expected noise on the text-as-PDF file).
- **Phase 2 gate proven with a negative test:** disabling ZERO_BYTE made the gate fail with 4 failures naming both empty files (exit 1); reverted and re-passed. The gate has teeth.
- Phase 1 gate re-run after the `scan.py` change: still PASS 13 / FAIL 0 — no regression.
- **2026-07-20 — Document-author enrichment (Phase 0 amendment):** `make_mock_data.py` now embeds a document author (PDF `/Author`, DOCX/XLSX creator) — 22 files authored, 19 Unassigned. Answer key gained an `authors` map. One divergence file (`CTR-2026-01_..._AcmeSupply.docx`) authored by Meridian to prove author ≠ party ≠ custodian. Fixed reportlab's `"anonymous"` default. Verified: embedded authors match the answer key (0 mismatches); `qc_phase0.py` still PASS 39/0. See DECISIONS 2026-07-20. Not yet committed/pushed.

- **2026-07-21 — Phases 1–2 pushed.** Both gates re-run green on the working tree first (Phase 2: PASS 22 / FAIL 0; Phase 1: PASS 13 / FAIL 0, no regression), then two phase-sized commits: `d7de3e5` (Phase 0 author amendment + Phase 1 scanner) and `0b152bc` (Phase 2 rules engine + docs). Pushed `6ef6525..0b152bc`; working tree clean. Stray 0-byte `.__wtest` (write-probe orphan, unreferenced by any script) removed, not committed.
- **2026-07-21 — Phase 3 LLM decided:** local open-source instruct model via **Ollama**, not a hosted API. Full reasoning and the rejected options (DeepSeek V4, hosted free tiers) in DECISIONS 2026-07-21.

- **2026-07-21 — Phase 3 complete (this session).** Built in the order STATUS demanded: measure first, build second.
  - **Load-bearing step done first.** Two throwaway spikes before any pipeline code. Spike 1: `gemma4:12b` scored **12/12** on text alone, both traps caught, ~1.7s/doc. Spike 2 probed the ambiguous and no-text files and produced the session's two key findings — (a) confidence is pinned at 0.9–1.0 on everything, so it cannot gate anything, and (b) the draft text extractor was scraping binary garbage out of PNGs (541 chars from a solid-color image) and feeding it to the model.
  - **Phase 0 amendment — class ground truth.** `make_mock_data.py` gained `DOC_CLASSES` (41 files) and writes a `classes` map into `seeded-errors.json`; ambiguous files carry a list of acceptable labels, no-text files carry `null`. Two set-difference checks fail the generator loudly if the table and fixture drift. Fixture regenerated; Phase 0/1/2 gates re-run green first (39/0, 13/0, 22/0).
  - **`scripts/classify.py`** — reads `manifest.csv`, extracts text (dispatching on `true_type`, **no** catch-all fallback), calls `gemma4:12b` via stdlib `urllib` at `127.0.0.1:11434` (temperature 0, `format=json`), writes `classifications.csv` (path, name, chars, label, confidence, folder_hint, status, reason). **The prompt contains document text only — never the filename or folder.** 41 files in ~43s.
  - **`scripts/rules.py` — three new rules** (`UNCLASSIFIED`, `CLASS_CONFLICT`, `LOW_CONFIDENCE`), running only when `classifications.csv` exists so Phase 2 still stands alone without Ollama. Result: **39 exceptions across 25 files** (30 deterministic + 9 UNCLASSIFIED).
  - **`scripts/qc_phase2.py`** scoped to its own nine rule IDs — otherwise a site photo's `UNCLASSIFIED` row reads as a false positive. Still 22/0.
  - **`scripts/qc_phase3.py`** — 17 checks: coverage, accuracy vs the answer key, no-guessing, no-silent-drops, an independently recomputed exceptions absence check, well-formedness, determinism sampling, loopback-endpoint (=$0), and a read-only fingerprint.
  - `classifications.csv` added to `.gitignore` (generated artifact, same reasoning as `manifest.csv`).

## Next
**Phase 4 — QC report + non-destructive organize. Not started. Do this in a fresh session.**
- Read PLAN.md Phase 4. `organize.py --by class|author|custodian`; `class` now has a real source (`classifications.csv`), `author` comes from the manifest, `custodian` still needs its own name/pattern→custodian mapping table.
- **Phase 4 is where the client-facing single view gets built.** `exceptions.csv` already carries both deterministic and classification findings in one list, so the report reads one queue.
- The 9 `unclassified` files (5 photos, the archive, `Thumbs.db`, 2 empty files) must land in the report's `Unassigned/` bucket, never guessed.
- Run the full chain in order: `scan.py` → `classify.py` → `rules.py`. `rules.py` reads `classifications.csv` if it is present, so classify must run before rules.

## Blockers
- None.

## Environment note
- Python on Joel's PC: use the **`py`** launcher (3.14.5). The bare `python`/`python3` names hit a Microsoft Store stub and fail. Run scripts via Desktop Commander: `py scripts\scan.py`. The sandbox Bash has no Python at all.
- pandas **is now installed** (3.0.3, with numpy 2.5.1) — done in the Phase 2 session.
- Installed this session (were missing under `py`): `python-docx`, `openpyxl`, `reportlab` (generator deps — restored) and `pypdf` (for reading PDF authors in Phase 2). `pillow` was already present.
- **Hardware (verified 2026-07-21, for the Phase 3 local-model decision):** NVIDIA RTX 5070, **12,227 MiB VRAM** (`nvidia-smi`; note `Win32_VideoController` misreports this as 4 GB — the known 32-bit WMI overflow, don't trust it), 31.6 GB system RAM, Ryzen 5 7500F (6 cores). Comfortably runs an 8B-class model; cannot run DeepSeek V4 (284B/1.6T).
- **Ollama is installed and running** (verified 2026-07-21). Model in use: **`gemma4:12b`** — 11.9B params, Q4_K_M, 7.04 GB. `ollama` is **not on the PATH** of a non-interactive shell; the binary lives at `%LOCALAPPDATA%\Programs\Ollama\ollama.exe`. Easiest check that it's up: `Invoke-RestMethod http://127.0.0.1:11434/api/tags`.
- `classify.py` and `qc_phase3.py` need Ollama running. `scan.py`, `rules.py`, and the Phase 0/1/2 gates do **not** — Phase 2 still stands alone on a machine with no model installed.

## QC gate results
| Phase | Result | Evidence |
|---|---|---|
| 0 | PASS | `python3 scripts/qc_phase0.py` → PASS: 39, FAIL: 0. Answer key complete (generated with the fixture, same source of truth); all 9 planned exception types present; fixture committed untouched (`7f7fd7e`, fsck clean). |
| 2 | PASS | `py scripts/qc_phase2.py` → PASS: 22, FAIL: 0 (exit 0). 41 files → 30 exceptions across 23 files, 18 clean. Every one of the 21 seeded errors caught by its owning rule (absence check vs `seeded-errors.json`); **zero false positives** — no clean file flagged; flagged + clean = 41 reconciles with disk and answer key; author column 0 mismatches across 41; all rule_ids/severities valid, no blank details, no phantom or duplicate rows; fixture byte-for-byte unchanged. **Negative test:** disabling ZERO_BYTE produced FAIL 4 (exit 1) naming both empty files — gate proven to detect misses, then reverted. |
| 3 | PASS | `py scripts/qc_phase3.py` → PASS: 17, FAIL: 0 (exit 0). Runs scan → classify → rules fresh. **Accuracy 32/32** on every classifiable file vs the generated `classes` answer key, including both traps. All 9 no-text files reported `unclassified` — no label invented; inversely, no readable file left unclassified. The exceptions absence check is **recomputed independently** from `classifications.csv` rather than trusted from `rules.py` (9 rows, exact match). Labels all on-list, confidence in 0.0–1.0, no file labeled without ≥20 chars of text. Determinism: 8 sampled documents reclassified to identical labels. Endpoint asserted loopback → API cost $0. Fixture byte-for-byte unchanged. **Negative tests (3):** planting a wrong-folder label and a 0.40 confidence made `CLASS_CONFLICT` and `LOW_CONFIDENCE` fire (both are silent on the real fixture, so both needed proving); reintroducing the naive text fallback made the gate **fail with exit 1**, naming all 5 photos and the archive as invented labels with `unclassified` dropping 9→3. All reverted and re-verified green. |
| 1 | PASS | `py scripts/qc_phase1.py` → Scanned 41 files; PASS: 13, FAIL: 0. Re-run after the Phase 2 `scan.py` change — no regression. Row count 41 == disk == answer-key file_count; path-set exact match (no missed/phantom rows); all 3 exact-dupe pairs share a hash; no blank hashes/types; zip not expanded; fixture byte-for-byte unchanged before/after scan (read-only proven). |

## Handoff notes for next agent
Read PLAN.md and DECISIONS.md before touching anything. Follow PROJECT-STANDARD.md (exit ritual is mandatory).
- **Never modify `mock-intake/` or the answer key by hand** — change `scripts/make_mock_data.py` and regenerate (then note that hashes rotate per run; dupe pairs still match within a run).
- After any fresh clone, rerun the generator to restore the two date-anomaly mtimes (git doesn't preserve them).
- Git from the sandbox works but can't delete its own lock/temp files on the mounted folder — if a commit leaves `.git/HEAD.lock` behind, remove it via Desktop Commander (runs on Joel's real PC). Pushing works via Desktop Commander (Joel's cached GitHub creds).
