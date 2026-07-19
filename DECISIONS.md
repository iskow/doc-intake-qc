# DECISIONS.md — Document Intake QC Agent

_Format per PROJECT-STANDARD.md. Newest first._

## 2026-07-19 — Kickoff decisions
**Decision:** Python CLI tool, non-destructive by design (works on copies), rule-based validation first with an AI classification layer added after core rules are proven.
**Why:** Deterministic rules are testable and defensible (eDiscovery mindset); AI adds value on top rather than being an unreliable foundation.
**Rejected:** See PLAN.md kickoff block for scope exclusions.

## Assumptions & risks
- [ASSUMPTION: open] All client data is mocked — no real client files anywhere in this project.
- [ASSUMPTION: open] Free-tier tools suffice for demo scale; paid tiers noted as information only.
