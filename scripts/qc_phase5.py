#!/usr/bin/env python3
"""Phase 5 QC gate — the one-command demo, the README, and the screenshots.

Phase 5 ships documentation and a picture, which is exactly the kind of
deliverable that rots without anyone noticing. Code that breaks throws; a
README that has drifted from the code keeps rendering beautifully and lies to
every reader. So the three things this gate exists to prevent are:

  1. the demo not actually running from a clean checkout
  2. the README stating numbers the pipeline no longer produces
  3. the committed screenshot showing a report the code no longer generates

Each is checked by recomputation, never by reading. The README's results table
is parsed and every figure re-derived from the live pipeline; the screenshots
are checked against the figures the report renders right now; the documented
commands are compared against `run_demo.STEPS`, which is the code the runner
actually executes.

Two claims are proven by making them fail, in the house style: the runner must
STOP at a failing step rather than carrying on, and the fresh-clone date guard
must fire on a fixture whose planted dates have been reset. Both are exercised
against synthetic inputs, so neither touches the real fixture.

Run:  py scripts/qc_phase5.py
Requires: Ollama (it runs the full demo end to end), Chrome only if you need to
re-capture screenshots first.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import run_demo
from run_demo import STEPS, command_line

ROOT = Path(__file__).resolve().parents[1]
INTAKE = ROOT / "mock-intake"
SCRIPTS = ROOT / "scripts"
DOCS = ROOT / "docs"
README = ROOT / "README.md"
REPORT = ROOT / "qc-report.html"
SNAPSHOT = DOCS / "report-snapshot.json"
HERO = DOCS / "qc-report-hero.png"
FULL = DOCS / "qc-report-full.png"

# Generated artifacts. Deleted before the fresh run, because "it works" has to
# mean "it works from nothing", not "it works over yesterday's output".
GENERATED = ["manifest.csv", "exceptions.csv", "classifications.csv",
             "qc-report.html", "organized/"]

# Scripts that are NOT pipeline stages, so their absence from STEPS is correct.
NON_PIPELINE = {"make_mock_data.py", "run_demo.py", "capture_screenshots.py"}

# The README's results table, mapped to the report's own data-qc figures. Both
# sides of this mapping are asserted complete below: a row in the README with no
# mapping, or a mapping with no row, fails. Otherwise a figure could be quietly
# deleted from the README and the check would pass by simply not looking.
RESULTS_TABLE = {
    "Files received": "total_files",
    "Files with at least one finding": "files_flagged",
    "Files clean": "files_clean",
    "Findings total": "total_exceptions",
    "High severity": "sev_high",
    "Medium severity": "sev_medium",
    "Low severity": "sev_low",
    "Documents classified": "class_labeled",
    "Sent to manual review, unlabeled": "class_unclassified",
    "Date comparisons matching": "datefid_ok",
}

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


def git_ignored(path: str | Path) -> bool:
    """True if git would ignore this path. Exit 0 means ignored, 1 means not.

    Takes a string as well as a Path because the trailing slash is load-bearing:
    the rule `organized/` matches directories only, and when the folder is not
    on disk git cannot tell that it is one. `Path` silently drops the slash, so
    a directory rule would read as "not ignored" purely because the directory
    had been deleted — which is exactly the state this gate runs in.
    """
    result = subprocess.run(["git", "check-ignore", "-q", str(path)],
                            cwd=ROOT, capture_output=True)
    return result.returncode == 0


def report_figures() -> dict[str, str]:
    doc = REPORT.read_text(encoding="utf-8") if REPORT.is_file() else ""
    return {k: v.strip()
            for k, v in re.findall(r'data-qc="([^"]+)">([^<]*)<', doc)}


def readme_section(text: str, heading: str) -> str:
    """The body under one `## heading`, up to the next `## `."""
    hit = re.search(rf"^## {re.escape(heading)}\s*$(.*?)(?=^## |\Z)",
                    text, re.M | re.S)
    return hit.group(1) if hit else ""


def png_size(path: Path) -> tuple[int, int]:
    """Width and height from the PNG IHDR chunk. No pillow needed for this."""
    data = path.read_bytes()[16:24]
    return int.from_bytes(data[:4], "big"), int.from_bytes(data[4:], "big")


def main() -> int:
    started = time.perf_counter()

    # --- 1. THE CHAIN IS CORRECT AND COMPLETE --------------------------------
    # STEPS is the code the runner executes. Everything downstream — the README
    # check, the ordering claims — is measured against it rather than against
    # prose describing it.
    scripts_in_steps = [s for s, _, _, _ in STEPS]
    check((SCRIPTS / "run_demo.py").is_file(), "runner: scripts/run_demo.py exists")
    check(scripts_in_steps[0] == "scan.py", "chain: scan.py runs first")
    check(scripts_in_steps[-1] == "report.py",
          f"chain: report.py runs LAST (it reads all three cross-references), "
          f"got '{scripts_in_steps[-1]}'")
    check(scripts_in_steps.index("classify.py") < scripts_in_steps.index("rules.py"),
          "chain: classify.py runs before rules.py, so the AI findings land in "
          "the same exceptions.csv")

    organize_modes = [a[1] for s, a, _, _ in STEPS if s == "organize.py"]
    check(sorted(organize_modes) == ["author", "class", "custodian"],
          f"chain: all three organize axes run ({organize_modes})")
    last_organize = max(i for i, (s, _, _, _) in enumerate(STEPS) if s == "organize.py")
    check(last_organize < len(STEPS) - 1,
          "chain: every organize mode runs before the report")

    missing_scripts = [s for s in scripts_in_steps if not (SCRIPTS / s).is_file()]
    check(not missing_scripts,
          f"chain: every scripted step exists on disk ({len(set(scripts_in_steps))} scripts)")
    for s in missing_scripts:
        failures.append(f"  -> step names a missing script: {s}")

    # Absence check: a future pipeline stage added to scripts/ but never wired
    # into the demo would leave a runner that silently does less than the tool.
    on_disk = {p.name for p in SCRIPTS.glob("*.py")
               if not p.name.startswith("qc_phase") and p.name not in NON_PIPELINE}
    orphans = sorted(on_disk - set(scripts_in_steps))
    check(not orphans,
          f"chain: no pipeline script is missing from the demo ({len(on_disk)} checked)")
    for o in orphans:
        failures.append(f"  -> pipeline script never runs in the demo: {o}")

    # --no-ai must drop exactly the steps that cannot run without the model's
    # output, and keep everything that can.
    no_ai = [(s, a) for s, a, needs_ai, _ in STEPS if not needs_ai]
    no_ai_scripts = {s for s, _ in no_ai}
    check("classify.py" not in no_ai_scripts and "report.py" not in no_ai_scripts,
          "--no-ai: drops classify.py and report.py")
    check(not any(a == ["--by", "class"] for _, a in no_ai),
          "--no-ai: drops the class axis, which reads classifications.csv")
    check(any(a == ["--by", "author"] for _, a in no_ai) and
          any(a == ["--by", "custodian"] for _, a in no_ai),
          "--no-ai: keeps the author and custodian axes, which need no model")
    check({"scan.py", "rules.py"} <= no_ai_scripts,
          "--no-ai: keeps scan.py and rules.py, so Phase 2 stands alone")

    # --- 2. THE RUNNER BEHAVES -----------------------------------------------
    # From scratch, not over yesterday's artifacts: a pipeline that only works
    # when its own output is already present is not a demo, it is a re-run.
    for name in GENERATED:
        target = ROOT / name.rstrip("/")
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    check(not any((ROOT / n.rstrip("/")).exists() for n in GENERATED),
          "fresh run: every generated artifact cleared before the run")

    before = fixture_fingerprint()
    demo = subprocess.run([sys.executable, str(SCRIPTS / "run_demo.py")],
                          capture_output=True, text=True)
    after = fixture_fingerprint()

    check(demo.returncode == 0,
          f"fresh run: py scripts/run_demo.py exits 0 (got {demo.returncode})")
    if demo.returncode != 0:
        failures.append(f"  -> stderr: {demo.stderr.strip()[:400]}")

    produced = [p for _, _, _, p in STEPS]
    for artifact in produced:
        target = ROOT / artifact.rstrip("/")
        check(target.exists(), f"fresh run: produced {artifact}")

    check(before == after,
          "fresh run: the intake is byte-for-byte unchanged by the whole demo")
    for path in sorted(set(before) | set(after)):
        if before.get(path) != after.get(path):
            failures.append(f"  -> intake file changed: {path}")

    check("Pipeline complete" in demo.stdout,
          "fresh run: the runner reports completion")
    check("WARNING: the fixture's seeded date anomalies are gone" not in demo.stderr,
          "fresh run: the fixture's planted date anomalies are intact")

    # Negative test 1: a failing step must STOP the chain. Proven on a synthetic
    # three-step chain in a temp folder — the real scripts are never broken. The
    # marker file is the evidence: if step 3 ran, the runner carried on past a
    # failure and every later artifact is built on a step that did not finish.
    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp)
        marker = fake / "ran.marker"
        (fake / "ok.py").write_text("print('ok')\n", encoding="utf-8")
        (fake / "boom.py").write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
        (fake / "after.py").write_text(
            "from pathlib import Path\n"
            f"Path(r'{marker}').write_text('ran')\n", encoding="utf-8")
        probe = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, r'%s')\n"
             "import run_demo\n"
             "from pathlib import Path\n"
             "run_demo.SCRIPTS = Path(r'%s')\n"
             "run_demo.STEPS = [('ok.py', [], False, 'a'), "
             "('boom.py', [], False, 'b'), ('after.py', [], False, 'c')]\n"
             "sys.argv = ['run_demo.py', '--no-ai']\n"
             "sys.exit(run_demo.main())" % (SCRIPTS, fake)],
            capture_output=True, text=True, cwd=ROOT)
        check(probe.returncode == 3,
              f"negative test: a failing step aborts the run with its own exit "
              f"code (got {probe.returncode}, wanted 3)")
        check(not marker.exists(),
              "negative test: no step runs after a failure — the marker file "
              "the next step would have written is absent")
        check("Pipeline stopped" in probe.stderr,
              "negative test: the failure names the step and says it stopped")

    # Negative test 2: the fresh-clone date guard. Git does not preserve mtimes,
    # so this warning is the only thing standing between a cloner and a silently
    # different set of results. A guard that cannot fire is not a guard.
    check(run_demo.seeded_dates_intact() == [],
          "date guard: silent on the real fixture, whose planted dates are intact")
    with tempfile.TemporaryDirectory() as tmp:
        fake_root = Path(tmp)
        planted = fake_root / "reset_by_clone.doc"
        planted.write_text("x", encoding="utf-8")   # mtime = now, as a clone gives
        key = fake_root / "seeded-errors.json"
        key.write_text(json.dumps({"seeded_errors": [
            {"type": "date-anomaly", "paths": ["reset_by_clone.doc"]}]}),
            encoding="utf-8")
        real_root, real_key = run_demo.ROOT, run_demo.ANSWER_KEY
        try:
            run_demo.ROOT, run_demo.ANSWER_KEY = fake_root, key
            fired = run_demo.seeded_dates_intact()
        finally:
            run_demo.ROOT, run_demo.ANSWER_KEY = real_root, real_key
        check(fired == ["reset_by_clone.doc"],
              f"date guard: fires when a planted date has been reset to now "
              f"(got {fired})")

    # --- 3. THE README MATCHES THE CODE --------------------------------------
    check(README.is_file(), "README.md exists")
    readme = README.read_text(encoding="utf-8") if README.is_file() else ""

    check("py scripts/run_demo.py" in readme,
          "README: shows the one command")

    documented = [command_line(s, a) for s, a, _, _ in STEPS]
    positions = [readme.find(cmd) for cmd in documented]
    for cmd, pos in zip(documented, positions):
        check(pos >= 0, f"README documents the step: {cmd}")
    found = [p for p in positions if p >= 0]
    check(found == sorted(found),
          "README: the steps appear in the order the runner runs them")

    # Every relative path the README points at must exist. A README whose links
    # 404 is worse than one with no links - it advertises what is not there.
    targets = set(re.findall(r"\]\(([^)]+)\)", readme))
    targets |= {t for t in re.findall(r"`([^`]+)`", readme)
                if re.fullmatch(r"[\w./-]+\.(py|md|csv|html|png|json)", t)}
    broken = sorted(t for t in targets
                    if not t.startswith("http") and not (ROOT / t).exists())
    check(not broken, f"README: every referenced path exists ({len(targets)} checked)")
    for b in broken:
        failures.append(f"  -> README points at something that is not there: {b}")

    # Flags must be real. A documented flag the script would reject is a reader
    # hitting an error on their first command.
    flag_uses = re.findall(r"py scripts/(\w+\.py)((?: --?[\w-]+(?: \w+)?)*)", readme)
    bad_flags = []
    for script, tail in flag_uses:
        source = (SCRIPTS / script).read_text(encoding="utf-8") if (SCRIPTS / script).is_file() else ""
        for flag in re.findall(r"--[\w-]+", tail):
            if f'"{flag}"' not in source:
                bad_flags.append(f"{script} {flag}")
    check(not bad_flags, f"README: every documented flag is accepted by its script "
                         f"({len(flag_uses)} commands checked)")
    for b in bad_flags:
        failures.append(f"  -> README documents a flag the script does not define: {b}")

    for gate in sorted(SCRIPTS.glob("qc_phase*.py")):
        check(f"scripts/{gate.name}" in readme,
              f"README: lists the QC gate {gate.name}")

    # The results table, recomputed. This is the check that stops the README
    # aging: the numbers are re-derived from the run that just happened.
    live = report_figures()
    section = readme_section(readme, "Results on the mock intake")
    rows = dict(re.findall(r"^\| ([^|]+?) \| (\d+) \|$", section, re.M))

    check(set(rows) == set(RESULTS_TABLE),
          f"README results table has exactly the expected rows "
          f"({len(rows)} found, {len(RESULTS_TABLE)} expected)")
    for extra in sorted(set(rows) - set(RESULTS_TABLE)):
        failures.append(f"  -> unmapped row in the results table: {extra}")
    for gone in sorted(set(RESULTS_TABLE) - set(rows)):
        failures.append(f"  -> results table row missing from the README: {gone}")

    for label, key in sorted(RESULTS_TABLE.items()):
        stated, actual = rows.get(label), live.get(key)
        check(stated is not None and stated == actual,
              f"README figure '{label}': says {stated}, pipeline produced {actual}")

    # --- 4. THE SCREENSHOTS ARE REAL AND CURRENT -----------------------------
    check(HERO.is_file(), "screenshot: docs/qc-report-hero.png committed")
    check(FULL.is_file(), "screenshot: docs/qc-report-full.png committed")
    check(SNAPSHOT.is_file(), "screenshot: docs/report-snapshot.json committed")

    if HERO.is_file() and FULL.is_file() and SNAPSHOT.is_file():
        snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        hero_size, full_size = png_size(HERO), png_size(FULL)

        check(HERO.stat().st_size > 20_000,
              f"screenshot: the hero image has real content ({HERO.stat().st_size // 1024} KB)")
        check(FULL.stat().st_size > 100_000,
              f"screenshot: the full-page image has real content ({FULL.stat().st_size // 1024} KB)")
        check(list(hero_size) == snap["hero"]["size"],
              f"screenshot: hero dimensions match the capture record {hero_size}")
        check(list(full_size) == snap["full"]["size"],
              f"screenshot: full-page dimensions match the capture record {full_size}")
        check(full_size[1] > hero_size[1] * 3,
              f"screenshot: the full-page image is the WHOLE report, not another "
              f"crop of the top ({full_size[1]}px vs {hero_size[1]}px)")
        check(hero_size[0] == full_size[0],
              "screenshot: hero and full page came from the same render width")

        # The staleness guard. A screenshot is a photograph of a number, and it
        # does not update itself - if the pipeline now says 26 and the committed
        # image says 25, the repo is misinforming every reader who scrolls past.
        pinned = snap["figures"]
        drifted = {k: (v, live.get(k)) for k, v in pinned.items() if live.get(k) != v}
        check(not drifted,
              f"screenshot: all {len(pinned)} pinned figures still match what the "
              f"pipeline produces — the committed image is current")
        for key, (was, now) in sorted(drifted.items()):
            failures.append(f"  -> screenshot is stale: {key} was {was}, is now {now}. "
                            f"Re-run: py scripts/capture_screenshots.py")

    # Committing them is the entire point — if they were ignored, the repo could
    # not show its own deliverable, since qc-report.html is generated.
    for asset in (HERO, FULL, SNAPSHOT):
        check(not git_ignored(asset),
              f"screenshot: {asset.name} is not gitignored — the repo can show it")
    check("docs/qc-report-hero.png" in readme and "docs/qc-report-full.png" in readme,
          "README: embeds the hero image and links the full report")

    # --- 5. HYGIENE ----------------------------------------------------------
    # The generated artifacts must STAY ignored: they are rebuilt by one command
    # and every one of them churns on each run.
    for name in GENERATED:
        check(git_ignored(f"{ROOT.as_posix()}/{name}"),
              f"gitignore: {name} stays out of the repo (generated, rebuilt in one command)")

    for phase in range(6):
        check((SCRIPTS / f"qc_phase{phase}.py").is_file(),
              f"gate script present: qc_phase{phase}.py")

    # The marker words are assembled from fragments so that this file does not
    # match itself. Written out in full, the pattern IS an unfinished marker and
    # the check fails on its own source — which it did, on the first run.
    markers = ("TO" "DO", "FIX" "ME", "XX" "X")
    leftovers = []
    for script in ("run_demo.py", "capture_screenshots.py", "qc_phase5.py"):
        text = (SCRIPTS / script).read_text(encoding="utf-8")
        if re.search(r"\b(" + "|".join(markers) + r")\b", text):
            leftovers.append(script)
    check(not leftovers,
          "hygiene: no unfinished-work markers left in the Phase 5 scripts")
    for l in leftovers:
        failures.append(f"  -> unfinished marker in {l}")

    # This gate's own size, stated in the README, checked against reality — the
    # same rule the rest of the project follows about quoting its own numbers.
    # Detail lines (the "  ->" explanations under a failure) are not checks, so
    # they must not inflate the count the README is held to.
    total = len(passes) + len([f for f in failures if not f.startswith("  ->")]) + 1
    stated = re.search(r"`py scripts/qc_phase5\.py` \| (\d+) \|", readme)
    check(stated is not None and int(stated.group(1)) == total,
          f"README states this gate's check count correctly "
          f"(says {stated.group(1) if stated else 'nothing'}, actual {total})")

    elapsed = time.perf_counter() - started
    print()
    for label in passes:
        print(f"PASS  {label}")
    for label in failures:
        print(f"FAIL  {label}" if not label.startswith("  ->") else label)
    print(f"\nPASS: {len(passes)}, FAIL: {len([f for f in failures if not f.startswith('  ->')])} "
          f"({elapsed:.1f}s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
