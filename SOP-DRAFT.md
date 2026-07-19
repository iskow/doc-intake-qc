# SOP-DRAFT.md — Document Intake QC Agent

_Draft — grows as the build makes the process real. Final pass uses the sop-builder skill._

## Purpose
The repeatable service process behind this build — what Joel would run for a paying client.

## When to use
SMEs and legal/ops teams receive messy file dumps — bad names, duplicates, wrong formats — and QC them by hand or not at all.

## Process (draft)
1. Receive intake folder (never work on originals — copy first).
2. Run inventory scan (manifest + hashes).
3. Run validation rules (naming, dupes, type mismatch, zero-byte).
4. Run AI classification; route low-confidence to exceptions.
5. Generate QC report; review exceptions.
6. Apply renames/organization to the copy; deliver report + organized set.

## Tools
- See PLAN.md kickoff block (stack). Update here when tools firm up.

## QC checks
- See PLAN.md phase gates. Promote the ones that survive into permanent SOP checks.

## Open questions
- _Log here as they come up during the build._
