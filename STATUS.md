# STATUS.md — Document Intake QC Agent

**Repo:** https://github.com/iskow/doc-intake-qc.git (push here at each phase close)
**Active:** yes (priority 1 of 5 — see PROJECT-STANDARD.md build order)
**Current phase:** Phase 0 — COMPLETE, gate passed. Awaiting Joel's approval to start Phase 1.
**Last updated:** 2026-07-19 (Phase 0 session)

## Done
- Plan approved by Joel (his go on Phase 0 doubled as plan QC).
- `scripts/make_mock_data.py` — generates the fixture AND the answer key from one data structure (they can't drift). Rerunnable: delete `mock-intake/` and rerun.
- `mock-intake/` — 41 files (+2 inside the zip), 6 folders incl. 4-level nesting.
- `seeded-errors.md` / `.json` — 21 seeded errors across 9 types (exact-duplicate ×3 pairs, near-duplicate-name, naming-violation ×6, extension-mismatch ×3, zero-byte ×2, date-anomaly ×2, junk-file ×2, archive, deep-nesting).
- `scripts/qc_phase0.py` — the QC gate as a rerunnable script.
- Git: repo initialized, `.gitattributes` (`* -text` — protects fixture hashes from CRLF conversion), commit `7f7fd7e` pushed to origin/main.

## Next
- Joel approves Phase 0 → Phase 1 (inventory scanner `scan.py` → `manifest.csv`) in a fresh session.
- Phase 1 note: reuse the chunked-SHA-256 pattern from `qc_phase0.py`.

## Blockers
- None.

## QC gate results
| Phase | Result | Evidence |
|---|---|---|
| 0 | PASS | `python3 scripts/qc_phase0.py` → PASS: 39, FAIL: 0. Answer key complete (generated with the fixture, same source of truth); all 9 planned exception types present; fixture committed untouched (`7f7fd7e`, fsck clean). |

## Handoff notes for next agent
Read PLAN.md and DECISIONS.md before touching anything. Follow PROJECT-STANDARD.md (exit ritual is mandatory).
- **Never modify `mock-intake/` or the answer key by hand** — change `scripts/make_mock_data.py` and regenerate (then note that hashes rotate per run; dupe pairs still match within a run).
- After any fresh clone, rerun the generator to restore the two date-anomaly mtimes (git doesn't preserve them).
- Git from the sandbox works but can't delete its own lock/temp files on the mounted folder — if a commit leaves `.git/HEAD.lock` behind, remove it via Desktop Commander (runs on Joel's real PC). Pushing works via Desktop Commander (Joel's cached GitHub creds).
