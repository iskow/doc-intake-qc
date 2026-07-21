# STATUS.md — Document Intake QC Agent

**Repo:** https://github.com/iskow/doc-intake-qc.git (push here at each phase close)
**Active:** yes (priority 1 of 5 — see PROJECT-STANDARD.md build order)
**Current phase:** Phase 2 — COMPLETE, gate passed (PASS 22 / FAIL 0), **pushed**. Phase 3 approved by Joel and scoped (local Ollama classification — see DECISIONS 2026-07-21); build not yet started.
**Last updated:** 2026-07-21 (Phase 2 pushed; Phase 3 LLM decision logged)

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

## Next
**Phase 3 — AI classification layer. Not started. Do this in a fresh session.**
- Classify each doc (invoice / contract / correspondence / report / other) from extracted text; confidence per doc; low confidence routes into the exceptions list, never silently accepted.
- **Load-bearing step first — do this before writing any pipeline code:** install Ollama (**not currently installed** — verified 2026-07-21), pull one candidate small instruct model, and classify ~10 fixture docs against the folder-name ground truth. Only build `classify.py` around a model that has already been measured. If it flunks, escalate to a larger local model or reopen the hosted-API question with evidence.
- Pick the model at install time from what's actually current in the Ollama library — do not take a model tag from an agent's memory.
- Text extraction: `pypdf` is already installed for PDF text; DOCX text via python-docx.
- Ground-truth traps to watch: `scan_001.pdf` sits in `Contracts/` and `report_final.pdf` is text at the root — folder name is *independent* evidence, not gospel.

## Blockers
- None.

## Environment note
- Python on Joel's PC: use the **`py`** launcher (3.14.5). The bare `python`/`python3` names hit a Microsoft Store stub and fail. Run scripts via Desktop Commander: `py scripts\scan.py`. The sandbox Bash has no Python at all.
- pandas **is now installed** (3.0.3, with numpy 2.5.1) — done in the Phase 2 session.
- Installed this session (were missing under `py`): `python-docx`, `openpyxl`, `reportlab` (generator deps — restored) and `pypdf` (for reading PDF authors in Phase 2). `pillow` was already present.
- **Hardware (verified 2026-07-21, for the Phase 3 local-model decision):** NVIDIA RTX 5070, **12,227 MiB VRAM** (`nvidia-smi`; note `Win32_VideoController` misreports this as 4 GB — the known 32-bit WMI overflow, don't trust it), 31.6 GB system RAM, Ryzen 5 7500F (6 cores). Comfortably runs an 8B-class model; cannot run DeepSeek V4 (284B/1.6T).
- **Ollama is NOT installed** as of 2026-07-21 — first Phase 3 action.

## QC gate results
| Phase | Result | Evidence |
|---|---|---|
| 0 | PASS | `python3 scripts/qc_phase0.py` → PASS: 39, FAIL: 0. Answer key complete (generated with the fixture, same source of truth); all 9 planned exception types present; fixture committed untouched (`7f7fd7e`, fsck clean). |
| 2 | PASS | `py scripts/qc_phase2.py` → PASS: 22, FAIL: 0 (exit 0). 41 files → 30 exceptions across 23 files, 18 clean. Every one of the 21 seeded errors caught by its owning rule (absence check vs `seeded-errors.json`); **zero false positives** — no clean file flagged; flagged + clean = 41 reconciles with disk and answer key; author column 0 mismatches across 41; all rule_ids/severities valid, no blank details, no phantom or duplicate rows; fixture byte-for-byte unchanged. **Negative test:** disabling ZERO_BYTE produced FAIL 4 (exit 1) naming both empty files — gate proven to detect misses, then reverted. |
| 1 | PASS | `py scripts/qc_phase1.py` → Scanned 41 files; PASS: 13, FAIL: 0. Re-run after the Phase 2 `scan.py` change — no regression. Row count 41 == disk == answer-key file_count; path-set exact match (no missed/phantom rows); all 3 exact-dupe pairs share a hash; no blank hashes/types; zip not expanded; fixture byte-for-byte unchanged before/after scan (read-only proven). |

## Handoff notes for next agent
Read PLAN.md and DECISIONS.md before touching anything. Follow PROJECT-STANDARD.md (exit ritual is mandatory).
- **Never modify `mock-intake/` or the answer key by hand** — change `scripts/make_mock_data.py` and regenerate (then note that hashes rotate per run; dupe pairs still match within a run).
- After any fresh clone, rerun the generator to restore the two date-anomaly mtimes (git doesn't preserve them).
- Git from the sandbox works but can't delete its own lock/temp files on the mounted folder — if a commit leaves `.git/HEAD.lock` behind, remove it via Desktop Commander (runs on Joel's real PC). Pushing works via Desktop Commander (Joel's cached GitHub creds).
