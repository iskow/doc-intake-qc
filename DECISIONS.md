# DECISIONS.md — Document Intake QC Agent

_Format per PROJECT-STANDARD.md. Newest first._

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
- [ASSUMPTION: open] Date anomalies are seeded on the *modified* date only — creation time isn't settable from Python on most systems. Phase 2's date rule must check mtime.
- [ASSUMPTION: open] Fixture hashes rotate on each generator run (reportlab embeds a creation timestamp in PDF bytes). Invariant to rely on: within any one run, each duplicate pair is hash-identical. Phase 1/2 tests must not hard-code hash values.
- [RISK: open] Git run from the sandbox can't delete its own lock/temp files on the mounted folder. Mitigation in STATUS.md handoff notes (clean via Desktop Commander).
