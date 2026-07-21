#!/usr/bin/env python3
"""Phase 4 QC gate (4a organize + 4b metadata fidelity) — prove the organized
copy is complete, faithful, correctly bucketed, and metadata-preserving.

Runs the full chain fresh (scan -> classify -> rules -> organize x3), then:
  1. READ-ONLY — the intake is byte-for-byte unchanged after everything, and
     still holds all 41 originals. Copying must never mutate or move a source.
  2. COMPLETE — every mode's tree holds exactly one copy of every manifest
     file: no misses, no phantoms, no silent overwrites.
  3. FAITHFUL — every copy's SHA-256 equals its original's, and its CREATED
     and MODIFIED timestamps match exactly (4b), so the seeded date anomalies
     survive the copy instead of being quietly reset to the run date.
  4. BUCKETED — every file sits in the folder its axis dictates, recomputed
     here from each axis's own source of truth (the generated answer key for
     author and class) rather than trusted from organize.py.
  5. UNASSIGNED — files with no value on an axis land in Unassigned/, exactly
     and only those. Nothing is guessed into a real bucket.
  6. AXES ARE INDEPENDENT — named files that must land in different buckets
     under different modes actually do. If the three modes agreed everywhere,
     the feature would be one axis wearing three names.
  7. SAFETY GUARD — organize() refuses to write inside the intake folder.
  8. IDEMPOTENT — running a mode twice yields an identical tree.
  9. STRUCTURE PRESERVED (4b) — each copy sits at <bucket>/<its original path
     under the intake>, and that original path is reconstructable by stripping
     the bucket and finding the file back in the intake.
 10. CROSS-REFERENCE (4b) — crossref-by-<mode>.csv accounts for every file and
     reconciles with the manifest and with what is actually on disk.
 11. ENGINE PROBE (4b) — the copy engine is proven on a synthetic file to carry
     all three timestamps, the ACL, the owner, and an alternate data stream.
 12. COLLISION PREFLIGHT (4b) — check_collisions() stops a colliding plan and
     passes a clean one.
 13. THE QC REPORT (4c) — every figure is recomputed here from the CSVs and
     compared against the value read back out of the RENDERED HTML, so the gate
     never trusts report.py's own arithmetic. Plus: required sections all
     present, the honest-limitation statements still there (their absence is
     the failure mode), every fired rule explained, and no external asset that
     would break the report on a client machine with no network.

WHY ACCESSED TIME IS NOT ASSERTED ON THE FIXTURE (measured 2026-07-21):
reading a file updates its access time, so scan.py destroys the original in
Phase 1 before organize.py exists; and NTFS defers access-time writes to disk,
so source and copy legitimately disagree by milliseconds even when the copy was
faithful. Check 11 proves the engine carries atime in a controlled setting
where nothing else reads the file. Claiming it end-to-end would be false.

Exit code 0 = gate passed; 1 = failures (listed).
Run:  py scripts/qc_phase4.py        (Ollama must be running for classify.py)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from organize import (CUSTODIAN_MAP, MODES, UNASSIGNED, WINDOWS, check_collisions,
                      crossref_path, load_custodian_rules, match_custodian,
                      organize, plan_copies, robocopy_files, safe_folder)
from rules import RULE_DOCS

ROOT = Path(__file__).resolve().parents[1]
INTAKE = ROOT / "mock-intake"
MANIFEST = ROOT / "manifest.csv"
CLASSIFICATIONS = ROOT / "classifications.csv"
ORGANIZED = ROOT / "organized"
REPORT = ROOT / "qc-report.html"

# Files whose custodian is asserted BY NAME rather than by replaying the
# mapping code. These are the cases that carry the design claims: a specific
# rule beating the folder rule it sits inside, a sibling still getting the
# folder rule, a root-level system export, and a file no rule covers.
CUSTODIAN_CASES = {
    "mock-intake/Reports/RPT-2026-Q1_Financials.xlsx": "P. Bautista (Finance)",
    "mock-intake/Reports/RPT-2026-Q1_Operations.pdf": "L. Ferrer (Operations)",
    "mock-intake/data_export.csv": "ERP Export (non-custodial source)",
    "mock-intake/Old Files/2019 archive/deep/nested/legacy_notes.doc": UNASSIGNED,
}

# The divergence file: written by Meridian's Dana Cruz, collected from Legal,
# named for Acme Supply. Three axes, three different answers, one document —
# this is the case study's central claim, so the gate checks it literally.
DIVERGENCE = "mock-intake/Contracts/CTR-2026-01_ServiceAgreement_AcmeSupply.docx"

failures: list[str] = []
passes: list[str] = []


def check(ok: bool, label: str) -> None:
    (passes if ok else failures).append(label)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def fixture_fingerprint() -> dict[str, str]:
    return {p.relative_to(ROOT).as_posix(): sha256(p)
            for p in INTAKE.rglob("*") if p.is_file()}


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    import csv
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def tree_state(root: Path) -> dict[str, str]:
    """Every file under root as {relative path -> sha256}."""
    return {p.relative_to(root).as_posix(): sha256(p)
            for p in root.rglob("*") if p.is_file()}


def created_time(path: Path) -> float:
    """Creation time. st_birthtime is present on Windows from Python 3.12; the
    st_ctime fallback is also creation time on Windows (it is inode-change time
    on Unix, where this Windows-only gate does not run)."""
    st = path.stat()
    return getattr(st, "st_birthtime", st.st_ctime)


def powershell(command: str) -> str:
    """Run one PowerShell command and return stdout. Used only by the engine
    probe, to read back NTFS facts (ACL, owner) that Python cannot see without
    pywin32 — which this project will not add for a QC check."""
    return subprocess.run(["powershell", "-NoProfile", "-Command", command],
                          capture_output=True, text=True).stdout.strip()


def expected_buckets(mode: str, key: dict, manifest: list[dict[str, str]],
                     cls: list[dict[str, str]]) -> dict[str, str]:
    """Recompute what bucket each file BELONGS in, from that axis's own source.

    author and class are recomputed from the generated answer key, which is
    built by make_mock_data.py alongside the fixture and so cannot drift from
    it — genuinely independent of anything organize.py did.

    custodian has no generated ground truth (it is a declared input, not a
    property of the files), so its mapping is replayed here and the *semantics*
    are pinned separately by CUSTODIAN_CASES below. What this function proves
    for custodian is placement: the file physically sits where the map says.
    """
    if mode == "author":
        return {p: (a or UNASSIGNED) for p, a in key["authors"].items()}

    if mode == "class":
        label_by_path = {r["path"]: r for r in cls}
        out = {}
        for path, accepted in key["classes"].items():
            rec = label_by_path[path]
            if not accepted:
                # No correct label exists for this file (a photo, an empty
                # file, the zip). It must go to Unassigned, never a class.
                out[path] = UNASSIGNED
            else:
                # Two-sided: the label must be one the answer key accepts AND
                # the file must be filed under that label.
                if rec["label"] not in accepted:
                    failures.append(f"  -> label not accepted by answer key: {path} "
                                    f"got '{rec['label']}', expected one of {accepted}")
                out[path] = rec["label"] if rec["status"] == "ok" else UNASSIGNED
        return out

    rules = load_custodian_rules()
    return {r["path"]: match_custodian(r["path"], rules) for r in manifest}


def main() -> int:
    key = json.loads((ROOT / "seeded-errors.json").read_text(encoding="utf-8"))

    before = fixture_fingerprint()
    for script in ("scan.py", "classify.py", "rules.py"):
        subprocess.run([sys.executable, str(ROOT / "scripts" / script)], check=True)
    for mode in MODES:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "organize.py"),
                        "--by", mode], check=True)
    after = fixture_fingerprint()

    manifest = read_csv_rows(MANIFEST)
    cls = read_csv_rows(CLASSIFICATIONS)
    disk_count = sum(1 for p in INTAKE.rglob("*") if p.is_file())

    # --- 1. READ-ONLY --------------------------------------------------------
    check(before == after,
          "read-only: intake byte-for-byte unchanged after scan+classify+rules+organize x3")
    for path in sorted(set(before) | set(after)):
        if before.get(path) != after.get(path):
            failures.append(f"  -> intake file changed: {path}")
    check(len(after) == len(manifest) == disk_count == 41,
          f"all originals still in place ({disk_count} files, none moved or consumed)")

    originals = {r["path"]: r for r in manifest}
    bucket_of: dict[str, dict[str, str]] = {}

    for mode in MODES:
        out_root = ORGANIZED / f"by-{mode}"
        expected = expected_buckets(mode, key, manifest, cls)
        bucket_of[mode] = expected

        copies = [p for p in out_root.rglob("*") if p.is_file()]

        # --- 2. COMPLETE -----------------------------------------------------
        check(len(copies) == len(manifest),
              f"[{mode}] organized copy is complete: {len(copies)} files == manifest ({len(manifest)})")

        # Where each original MUST land: its bucket, then its own path relative
        # to the intake root. Computed here from the answer key rather than read
        # back from organize.py, so a wrong destination rule cannot agree with
        # itself. This one mapping settles completeness, bucketing and structure
        # at once — a file at the wrong path simply is not where it must be.
        want = {(out_root / safe_folder(expected[p])
                 / Path(p).relative_to(INTAKE.name)).relative_to(out_root).as_posix(): p
                for p in originals}
        on_disk = {c.relative_to(out_root).as_posix() for c in copies}

        placed = {want[rel]: out_root / rel for rel in want if rel in on_disk}
        phantom = sorted(on_disk - set(want))
        missing = sorted(want[rel] for rel in want if rel not in on_disk)

        check(not phantom,
              f"[{mode}] every copy sits at the exact path its axis dictates "
              f"(unexpected paths: {len(phantom)})")
        for ph in phantom[:5]:
            failures.append(f"  -> [{mode}] copy at an unexpected path: {ph}")

        check(not missing, f"[{mode}] no file was dropped ({len(originals)} accounted for)")
        for m in missing[:5]:
            failures.append(f"  -> [{mode}] not found where expected: {m} "
                            f"-> {safe_folder(expected[m])}/"
                            f"{Path(m).relative_to(INTAKE.name).as_posix()}")

        # --- 3. FAITHFUL -----------------------------------------------------
        # 4b: created AND modified must match exactly. Tolerance is 2ms rather
        # than 0 only to absorb float rounding on the epoch conversion — the
        # measured difference is zero to the tick. Accessed time is deliberately
        # not compared; see the module docstring for the two measured reasons.
        bad_hash, bad_mtime, bad_ctime = [], [], []
        for path, copy in placed.items():
            src = ROOT / path
            if sha256(copy) != originals[path]["sha256"]:
                bad_hash.append(path)
            if abs(copy.stat().st_mtime - src.stat().st_mtime) > 0.002:
                bad_mtime.append(path)
            if abs(created_time(copy) - created_time(src)) > 0.002:
                bad_ctime.append(path)
        check(not bad_hash,
              f"[{mode}] every copy is byte-identical to its original ({len(placed)} hashes)")
        for b in bad_hash[:5]:
            failures.append(f"  -> [{mode}] hash mismatch: {b}")
        check(not bad_mtime,
              f"[{mode}] modified timestamps preserved on all {len(placed)} copies")
        for b in bad_mtime[:5]:
            failures.append(f"  -> [{mode}] mtime not preserved: {b}")
        check(not bad_ctime,
              f"[{mode}] CREATION timestamps preserved on all {len(placed)} copies (4b)")
        for b in bad_ctime[:5]:
            src_c, cpy_c = created_time(ROOT / b), created_time(placed[b])
            failures.append(f"  -> [{mode}] creation time not preserved: {b} "
                            f"(src {src_c:.3f} vs copy {cpy_c:.3f})")

        # --- 9. STRUCTURE PRESERVED (4b) -------------------------------------
        # Independent of `want`: strip the bucket off each copy's path and the
        # remainder must name a real file back in the intake. This asks the
        # filesystem, not our own mapping, whether the original path survived.
        unreconstructable = [rel for rel in sorted(on_disk)
                             if not (INTAKE / Path(rel).relative_to(Path(rel).parts[0])).is_file()]
        check(not unreconstructable,
              f"[{mode}] every copy's original intake path is reconstructable from where it sits")
        for u in unreconstructable[:5]:
            failures.append(f"  -> [{mode}] path does not lead back to the intake: {u}")

        # Prove the tree is actually NESTED, not flattened. Without this the
        # structure check above passes vacuously on a flat tree of root-level
        # files. The deepest fixture file is 4 folders down.
        deep = [rel for rel in on_disk if len(Path(rel).parts) >= 5]
        check(bool(deep),
              f"[{mode}] nesting survived: {len(deep)} copies sit 4+ folders deep, not flattened")

        # --- 5. UNASSIGNED ---------------------------------------------------
        should_be = {p for p, v in expected.items() if v == UNASSIGNED}
        unassigned_dir = out_root / UNASSIGNED
        actually = {p for p, c in placed.items()
                    if c.relative_to(out_root).parts[0] == UNASSIGNED}
        check(should_be == actually,
              f"[{mode}] Unassigned holds exactly the {len(should_be)} files with no value on this axis")
        for miss in sorted(should_be - actually)[:5]:
            failures.append(f"  -> [{mode}] should be Unassigned but was bucketed: {miss}")
        for extra in sorted(actually - should_be)[:5]:
            failures.append(f"  -> [{mode}] wrongly dumped in Unassigned: {extra}")
        # Counted recursively: Unassigned/ now holds mirrored client subfolders
        # (4b), so counting its immediate children would count folders, not
        # files, and would pass while hiding an extra file two levels down.
        check(not unassigned_dir.exists() or
              sum(1 for p in unassigned_dir.rglob("*") if p.is_file()) == len(should_be),
              f"[{mode}] Unassigned/ folder holds nothing else")

        # --- 10. CROSS-REFERENCE (4b) ----------------------------------------
        # Mandatory from 4b on: re-foldering broke the copy-as-is identity
        # mapping, so this file is the only surviving record of where each
        # document sat in the client's environment.
        xref_file = crossref_path(out_root)
        check(xref_file.is_file(), f"[{mode}] cross-reference written: {xref_file.name}")
        if xref_file.is_file():
            xref = read_csv_rows(xref_file)
            check(len(xref) == len(manifest),
                  f"[{mode}] cross-reference accounts for every file "
                  f"({len(xref)} rows == manifest {len(manifest)})")
            check(len({r["original_path"] for r in xref}) == len(xref),
                  f"[{mode}] cross-reference has no duplicate originals")
            check({r["original_path"] for r in xref} == set(originals),
                  f"[{mode}] cross-reference originals match the manifest exactly")

            bad_xref = []
            for r in xref:
                orig = originals.get(r["original_path"])
                new = ROOT / r["new_path"]
                if orig is None:
                    bad_xref.append(f"unknown original: {r['original_path']}")
                elif r["sha256"] != orig["sha256"]:
                    bad_xref.append(f"hash disagrees with manifest: {r['original_path']}")
                elif not new.is_file():
                    bad_xref.append(f"new_path does not exist: {r['new_path']}")
                elif sha256(new) != orig["sha256"]:
                    bad_xref.append(f"file at new_path is not the original: {r['new_path']}")
                elif r["bucket"] != safe_folder(expected[r["original_path"]]):
                    bad_xref.append(f"bucket disagrees with the axis: {r['original_path']}")
            check(not bad_xref,
                  f"[{mode}] every cross-reference row reconciles: original -> "
                  f"new path on disk -> scan-time hash")
            for b in bad_xref[:5]:
                failures.append(f"  -> [{mode}] cross-reference: {b}")

            # The point of the file: where the document sat in the client's
            # environment must be recoverable from it alone, even if the
            # organized tree were flattened tomorrow.
            bad_folder = []
            for r in xref:
                parent = Path(r["original_path"]).relative_to(INTAKE.name).parent
                want_folder = parent.as_posix() if parent.parts else ""
                if r["original_folder"] != want_folder:
                    bad_folder.append(f"{r['original_path']}: recorded "
                                      f"'{r['original_folder']}', actual '{want_folder}'")
            check(not bad_folder,
                  f"[{mode}] every row records the folder the file actually came from")
            for b in bad_folder[:5]:
                failures.append(f"  -> [{mode}] cross-reference folder: {b}")

            nested = [r for r in xref if r["original_folder"]]
            check(all((INTAKE / r["original_folder"] / Path(r["original_path"]).name).is_file()
                      for r in nested),
                  f"[{mode}] every recorded original folder resolves back to a real "
                  f"intake file ({len(nested)} nested rows)")

    # --- 4b. CUSTODIAN SEMANTICS (named cases, not replayed code) ------------
    for path, want in CUSTODIAN_CASES.items():
        got = bucket_of["custodian"][path]
        check(got == want, f"custodian mapping: {Path(path).name} -> {want}")
        if got != want:
            failures.append(f"  -> got '{got}' instead")

    rules = load_custodian_rules()
    check(len(rules) == len(read_csv_rows(CUSTODIAN_MAP)) > 0,
          f"custodian map loaded ({len(rules)} rules, first match wins)")

    # --- 6. AXES ARE INDEPENDENT --------------------------------------------
    three = {mode: bucket_of[mode][DIVERGENCE] for mode in MODES}
    check(len(set(three.values())) == 3,
          f"the divergence file lands in 3 different buckets: "
          f"class='{three['class']}', author='{three['author']}', custodian='{three['custodian']}'")

    author_vals = set(bucket_of["author"].values()) - {UNASSIGNED}
    custodian_vals = set(bucket_of["custodian"].values()) - {UNASSIGNED}
    check(not (author_vals & custodian_vals),
          "no value appears as both an author and a custodian — the axes stay distinguishable")

    # --- 7. SAFETY GUARD -----------------------------------------------------
    # The guard is the whole non-destructive promise, so prove it fires rather
    # than assuming it would. Aimed at a path inside the intake folder.
    try:
        organize("author", INTAKE / "would-be-destroyed", dry_run=True)
        guard_held = False
    except SystemExit:
        guard_held = True
    check(guard_held, "safety guard: organize() refuses to write inside mock-intake/")
    check(not (INTAKE / "would-be-destroyed").exists(),
          "safety guard: nothing was created in the intake folder")

    # --- 11. ENGINE PROBE (4b) -----------------------------------------------
    # The fixture carries no alternate data streams and no interesting ACLs, so
    # checking those on it would pass vacuously — the failure this project keeps
    # catching. Instead, plant a file that has all of them and run the REAL
    # production copy function over it. Same technique the --out guard uses.
    if WINDOWS:
        probe = Path(tempfile.mkdtemp(prefix="qc4b_"))
        try:
            psrc, pdst = probe / "src", probe / "dst"
            psrc.mkdir()
            pfile = psrc / "probe file (v2).docx"     # spaces and parens on purpose
            pfile.write_text("probe payload", encoding="utf-8")
            # An alternate data stream. Python can open one directly on Windows.
            with open(f"{pfile}:qc.provenance", "w", encoding="utf-8") as f:
                f.write("collected-from=Legal")
            # Three timestamps, none of them "now", so a fresh stamp is obvious.
            os.utime(pfile, (1_010_000_000, 315_532_800))       # atime, mtime
            powershell(f"$i = Get-Item -LiteralPath '{pfile}'; "
                       f"$i.CreationTime = [datetime]'1998-03-04 01:02:03'")
            # Break inheritance and add an explicit ACE, so the ACL is distinct
            # from whatever the destination folder would hand out on its own.
            subprocess.run(["icacls", str(pfile), "/inheritance:d"],
                           capture_output=True, text=True)
            subprocess.run(["icacls", str(pfile), "/grant", "*S-1-1-0:(R)"],
                           capture_output=True, text=True)

            src_c, src_m, src_a = created_time(pfile), pfile.stat().st_mtime, pfile.stat().st_atime
            src_acl = powershell(f"(Get-Acl -LiteralPath '{pfile}').Sddl")
            src_owner = powershell(f"(Get-Acl -LiteralPath '{pfile}').Owner")

            robocopy_files(psrc, pdst, [pfile.name])          # the production call
            pcopy = pdst / pfile.name

            check(pcopy.is_file(), "engine probe: the copy engine produced a file")
            if pcopy.is_file():
                check(abs(created_time(pcopy) - src_c) <= 0.002,
                      "engine probe: CREATION time carried")
                check(abs(pcopy.stat().st_mtime - src_m) <= 0.002,
                      "engine probe: MODIFIED time carried")
                check(abs(pcopy.stat().st_atime - src_a) <= 0.002,
                      "engine probe: ACCESSED time carried (provable only here — "
                      "nothing else reads this file)")
                check(powershell(f"(Get-Acl -LiteralPath '{pcopy}').Sddl") == src_acl,
                      "engine probe: NTFS ACL carried (SDDL identical)")
                # Honest caveat, proven by negative test: dropping /COPY:O does
                # NOT fail this check, because every file on this machine is
                # owned by the same user. It confirms the owner is not CLOBBERED;
                # it cannot confirm a foreign owner would be carried across.
                check(powershell(f"(Get-Acl -LiteralPath '{pcopy}').Owner") == src_owner,
                      "engine probe: owner not clobbered (cannot prove more on a "
                      "single-user machine — see DECISIONS)")
                try:
                    with open(f"{pcopy}:qc.provenance", encoding="utf-8") as f:
                        ads = f.read()
                except OSError:
                    ads = None
                check(ads == "collected-from=Legal",
                      "engine probe: alternate data stream carried")
                check(sha256(pcopy) == sha256(pfile), "engine probe: bytes identical")
        finally:
            shutil.rmtree(probe, ignore_errors=True)
    else:
        check(False, "engine probe: SKIPPED — not Windows, copies are copy2 only")

    # --- 12. COLLISION PREFLIGHT (4b) ----------------------------------------
    # Preserving original relative paths makes a real collision impossible, so
    # this check can never fire on the fixture. A check that cannot fire is
    # indistinguishable from a broken one, so prove it BOTH ways: it must stop
    # a colliding plan and it must stay silent on the real one.
    fake_row = dict(manifest[0])
    colliding = [(ROOT / "a.txt", ORGANIZED / "x" / "same.txt", "x", fake_row),
                 (ROOT / "b.txt", ORGANIZED / "x" / "same.txt", "x", dict(manifest[1]))]
    try:
        check_collisions(colliding)
        stopped = False
    except SystemExit:
        stopped = True
    check(stopped, "collision preflight: a plan writing two files to one path is refused")

    real_plan = plan_copies(manifest, bucket_of["custodian"], ORGANIZED / "by-custodian")
    try:
        check_collisions(real_plan)
        clean_ok = True
    except SystemExit:
        clean_ok = False
    check(clean_ok, "collision preflight: the real plan passes — it discriminates, "
                    "rather than refusing everything")

    # --- 8. IDEMPOTENT -------------------------------------------------------
    mode = "custodian"
    out_root = ORGANIZED / f"by-{mode}"
    first = tree_state(out_root)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "organize.py"),
                    "--by", mode], check=True)
    second = tree_state(out_root)
    check(first == second,
          f"idempotent: rerunning --by {mode} reproduces an identical tree ({len(first)} files)")

    # --- 13. THE QC REPORT (4c) ----------------------------------------------
    # The report is generated LAST, after the pipeline above has rebuilt every
    # artifact it reads, so it is checked against the same run everything else
    # was checked against.
    #
    # The rule this section follows: never check the report against report.py's
    # own arithmetic. Every figure is recomputed here from the CSVs, then read
    # back out of the RENDERED HTML via its data-qc attributes and compared. If
    # both sides shared a bug they would agree and the check would be worthless,
    # so the two sides derive their numbers by different routes.
    subprocess.run([sys.executable, str(ROOT / "scripts" / "report.py")], check=True)

    check(REPORT.is_file(), "report: qc-report.html was generated")
    doc = REPORT.read_text(encoding="utf-8") if REPORT.is_file() else ""

    def figure(key: str) -> str | None:
        """Read one data-qc figure back out of the rendered HTML."""
        hit = re.search(rf'data-qc="{re.escape(key)}">([^<]*)<', doc)
        return hit.group(1).strip() if hit else None

    exceptions = read_csv_rows(ROOT / "exceptions.csv")
    flagged = {r["path"] for r in exceptions}
    sev_counts: dict[str, int] = {}
    for r in exceptions:
        sev_counts[r["severity"]] = sev_counts.get(r["severity"], 0) + 1
    n_unclassified = sum(1 for r in cls if r["label"] == "unclassified")
    naming = sum(1 for r in exceptions if r["rule_id"] == "NAMING")

    expected_figures = {
        "total_files": len(manifest),
        "files_flagged": len(flagged),
        "files_clean": len(manifest) - len(flagged),
        "total_exceptions": len(exceptions),
        "queue_rows": len(exceptions),
        "sev_high": sev_counts.get("high", 0),
        "sev_medium": sev_counts.get("medium", 0),
        "sev_low": sev_counts.get("low", 0),
        "class_unclassified": n_unclassified,
        "class_labeled": len(manifest) - n_unclassified,
        "naming_findings": naming,
        # 41 files x 3 modes x 2 fields (created, modified)
        "datefid_total": len(manifest) * len(MODES) * 2,
        "datefid_ok": len(manifest) * len(MODES) * 2,
    }
    for mode in MODES:
        xref = read_csv_rows(crossref_path(ORGANIZED / f"by-{mode}"))
        expected_figures[f"files_{mode}"] = len(xref)
        expected_figures[f"unassigned_{mode}"] = sum(
            1 for r in xref if r["bucket"] == UNASSIGNED)
        expected_figures[f"buckets_{mode}"] = len({r["bucket"] for r in xref})

    for key, want in sorted(expected_figures.items()):
        got = figure(key)
        check(got == str(want),
              f"report figure {key}: HTML says {got}, recomputed {want}")

    # Totals must RECONCILE, not merely be present: flagged + clean == received,
    # and the severity buckets must account for every finding with none left over.
    check(len(flagged) + (len(manifest) - len(flagged)) == len(manifest),
          "report: flagged + clean reconciles with files received")
    check(sum(sev_counts.values()) == len(exceptions),
          f"report: severities account for all {len(exceptions)} findings, none uncounted")

    # The report's own reconciliation table must be visibly green. A report that
    # rendered a failed row and shipped anyway would be worse than one that
    # refused to render.
    #
    # Matched on the rendered CELL, not the bare word: the rule id EXT_MISMATCH
    # contains "MISMATCH", so a substring test fires on a rule name and reports
    # a failure that isn't there. Caught by this gate on its first run.
    check('class="bad"' not in doc,
          "report: its own reconciliation table shows no failed rows")
    check("DISCREPANCIES FOUND" not in doc,
          "report: date-fidelity section reports no discrepancies")

    # Absence check: every section PLAN.md and STATUS.md require must exist. A
    # report missing its limitations section still looks finished.
    for heading in ("One queue, not two", "Classification",
                    "Three ways to organize the same set", "Date fidelity",
                    "Where everything went", "Reconciliation", "Limitations"):
        check(f">{heading}<" in doc, f"report section present: {heading}")

    # The claims the project is contractually honest about. These are checked as
    # STRINGS because their absence is the failure mode — a future edit that
    # tidies away the access-time caveat would make the report over-claim.
    check("Access times are not preserved" in doc,
          "report: states plainly that access times are NOT preserved")
    check("ownership may fall back" in doc,
          "report: does not claim owner fidelity (states it is unproven)")
    check("never fixed" in doc and "Renaming is a client" in doc,
          "report: states the naming gap — findings reported, never remediated")
    check("crossref-by-" in doc,
          "report: tells the client the cross-reference exists")

    # Every rule that fired must be explained in the report, in client language.
    for rule_id in sorted({r["rule_id"] for r in exceptions}):
        check(rule_id in doc, f"report: explains rule {rule_id}")
    check(not ({r["rule_id"] for r in exceptions} - set(RULE_DOCS)),
          "report: no rule fired without a description in rules.RULE_DOCS")

    # The divergence file must appear under three DIFFERENT buckets. This is the
    # case study's central claim, so the report has to actually show it.
    div_buckets = {bucket_of[m][DIVERGENCE] for m in MODES}
    check(len(div_buckets) == 3, "report: divergence file shown in 3 distinct buckets")
    for value in div_buckets:
        check(value in doc, f"report: names the divergence bucket {value!r}")

    # Self-contained: it must open on a client machine with no network. Any
    # external fetch would silently degrade to an unstyled page offline.
    for pattern, what in ((r'<script', "no <script> tag"),
                          (r'src\s*=\s*["\']http', "no remote src"),
                          (r'<link[^>]+href\s*=\s*["\']http', "no remote stylesheet"),
                          (r'@import', "no CSS @import"),
                          (r'url\(\s*["\']?http', "no remote url() asset")):
        check(not re.search(pattern, doc, re.I), f"report is self-contained: {what}")

    # --- Report --------------------------------------------------------------
    print()
    for mode in MODES:
        vals = bucket_of[mode]
        n_buckets = len(set(safe_folder(v) for v in vals.values()))
        n_unassigned = sum(1 for v in vals.values() if v == UNASSIGNED)
        print(f"by-{mode:<10} {len(vals)} files | {n_buckets} buckets | "
              f"{n_unassigned} Unassigned")
    print(f"Divergence check ({Path(DIVERGENCE).name}):")
    for mode in MODES:
        print(f"  {mode:<10} -> {bucket_of[mode][DIVERGENCE]}")
    print()
    print(f"PASS: {len(passes)}  FAIL: {len([f for f in failures if not f.startswith('  ->')])}")
    for f in failures:
        print(f"  FAIL: {f}" if not f.startswith("  ->") else f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
