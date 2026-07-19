# DECISIONS.md — Document Intake QC Agent

_Format per PROJECT-STANDARD.md. Newest first._

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
