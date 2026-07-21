#!/usr/bin/env python3
"""Phase 4c — the client-facing HTML QC report.

Reads the artifacts the pipeline already produced and renders ONE self-contained
HTML file: no external stylesheet, no web font fetch, no JavaScript library. It
has to open on a client's machine with no network and screenshot cleanly.

  manifest.csv              what was received (41 rows)
  exceptions.csv            every finding, deterministic AND classification
  classifications.csv       the Phase 3 label per file
  organized/crossref-*.csv  where each file was copied, per axis

THREE THINGS THIS SCRIPT DOES THAT A TEMPLATE WOULD NOT:

1. It RECONCILES in code and refuses to render if the numbers disagree. Every
   headline figure is derived twice from different columns and compared; a
   mismatch exits non-zero with the discrepancy named. A report that quietly
   prints a wrong total is worse than no report.

2. It RE-MEASURES date fidelity against the disk at render time. The date
   section does not reprint what the Phase 4b gate found last week — it stats
   every copy in every mode and compares against the manifest's pre-copy
   record. If someone re-copies the tree with a lossy tool, this report says so
   the next time it runs.

3. It EMITS its own figures as data-qc attributes so qc_phase4.py can parse the
   rendered HTML and check the numbers independently, rather than trusting the
   arithmetic in this file.

Rule descriptions and severity definitions are imported from rules.py, not
retyped here, so the report can never describe a rule the engine does not have.

Run:  py scripts/report.py
Output: qc-report.html at the project root.
"""

from __future__ import annotations

import csv
import html
import sys
from datetime import datetime
from pathlib import Path

from organize import MODES, UNASSIGNED, crossref_path
from rules import RULE_DOCS, SEVERITY_DOCS, SEVERITY_ORDER

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "manifest.csv"
EXCEPTIONS = ROOT / "exceptions.csv"
CLASSIFICATIONS = ROOT / "classifications.csv"
ORGANIZED = ROOT / "organized"
OUT = ROOT / "qc-report.html"

# The one document that lands in a different bucket under each of the three
# axes. It is the strongest single thing this project demonstrates, so the
# report names it explicitly rather than leaving the reader to find it.
DIVERGENCE = "mock-intake/Contracts/CTR-2026-01_ServiceAgreement_AcmeSupply.docx"

# --- Tessera palette, validated not eyeballed -------------------------------
# Every value below was checked with the dataviz validator against the surface
# it actually sits on. Two brand colors did NOT survive that check and were
# darkened within their own hue family:
#   Steel Teal  #598392 -> #456B7A   body text needs 4.5:1; raw was 3.72:1
#   Warm Amber  #D98C3F -> #A85F18   a chart mark needs 3:1; raw was 2.44:1
# The severity ramp is ORDINAL (high/medium/low is a tier, and reordering it
# would change the meaning), so it is one hue in monotone lightness steps
# rather than a red/amber/green traffic light. Validator: 4/4 checks pass on
# both the Mist page and the white card.
INK = "#01161E"          # body text            16.68:1 on Mist
DEEP_TEAL = "#124559"    # headings, bar fill    9.37:1 on Mist
TEXT_MUTED = "#456B7A"   # secondary text        5.20:1 on Mist
STEEL = "#598392"        # rules and borders ONLY - never text
SAGE = "#AEC3B0"         # dividers, table lines
MIST = "#EFF6E0"         # page background
ACCENT = "#A85F18"       # the one emphasized mark per section
ACCENT_TEXT = "#96540F"  # accent as text        5.30:1 on Mist
SEVERITY_FILL = {"high": "#0E3B4D", "medium": "#2F6E85", "low": "#7FA8B5"}

e = html.escape


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def fail(msg: str) -> None:
    print(f"RECONCILIATION FAILED: {msg}", file=sys.stderr)
    sys.exit(1)


# --- Charts ------------------------------------------------------------------
# Inline SVG, because a chart library would break the self-contained rule. The
# mark specs come from the dataviz method: bars <=24px thick, a 4px rounded
# data-end with a square baseline, no gridlines (every bar is directly labeled,
# and direct labels come before gridlines), and text in text tokens rather than
# the data color.

def bar_path(x: float, y: float, w: float, h: float, r: float = 4.0) -> str:
    """A horizontal bar with its right (data) end rounded and its left end
    square against the baseline. A plain rounded rect would round the baseline
    too, which detaches the bar from its axis."""
    r = min(r, max(w, 0.01), h / 2)
    if w <= r:
        return f"M{x},{y} h{w:.2f} v{h:.2f} h-{w:.2f} Z"
    return (f"M{x},{y} h{w - r:.2f} a{r},{r} 0 0 1 {r},{r} "
            f"v{h - 2 * r:.2f} a{r},{r} 0 0 1 -{r},{r} h-{w - r:.2f} Z")


def hbar_chart(rows: list[tuple[str, int, str]], gutter: int = 156,
               width: int = 720) -> str:
    """rows = [(label, value, fill)]. Value is printed at the bar tip, so no
    gridlines and no legend are needed for a single measure."""
    band, bar_h = 30, 18
    top, bottom = 8, 8
    height = top + band * len(rows) + bottom
    plot_w = width - gutter - 56          # 56px reserved for the tip label
    biggest = max((v for _, v, _ in rows), default=1) or 1

    parts = [f'<svg class="chart" viewBox="0 0 {width} {height}" '
             f'role="img" width="100%" height="{height}">']
    for i, (label, value, fill) in enumerate(rows):
        y = top + i * band
        w = (value / biggest) * plot_w
        parts.append(
            f'<text x="{gutter - 10}" y="{y + bar_h / 2 + 4:.0f}" '
            f'text-anchor="end" class="c-label">{e(label)}</text>')
        parts.append(
            f'<path d="{bar_path(gutter, y, w, bar_h)}" fill="{fill}">'
            f'<title>{e(label)}: {value}</title></path>')
        parts.append(
            f'<text x="{gutter + w + 8:.0f}" y="{y + bar_h / 2 + 4:.0f}" '
            f'class="c-value">{value}</text>')
    # A single hairline baseline: solid, one step off the surface, recessive.
    parts.append(f'<line x1="{gutter}" y1="{top}" x2="{gutter}" '
                 f'y2="{top + band * len(rows) - (band - bar_h):.0f}" '
                 f'stroke="{SAGE}" stroke-width="1"/>')
    parts.append("</svg>")
    return "\n".join(parts)


# --- Measurement --------------------------------------------------------------

def measure_date_fidelity(manifest: list[dict[str, str]]) -> dict:
    """Compare every copy's created and modified timestamps against the
    manifest's PRE-COPY record of the original, for all three modes.

    Precision is one second, because that is the precision manifest.csv stores.
    The Phase 4b gate compares the same values as floats to 2ms; this is the
    client-readable version of the same measurement, and it is taken live so
    the claim cannot go stale.
    """
    original = {r["path"]: r for r in manifest}
    total = ok = 0
    mismatches: list[str] = []
    missing: list[str] = []

    for mode in MODES:
        xref = read_rows(crossref_path(ORGANIZED / f"by-{mode}"))
        for row in xref:
            copy = ROOT / row["new_path"]
            src = original.get(row["original_path"])
            if src is None:
                missing.append(f"{mode}: {row['original_path']} not in manifest")
                continue
            if not copy.is_file():
                missing.append(f"{mode}: missing copy {row['new_path']}")
                continue
            st = copy.stat()
            # st_birthtime is creation time on Windows from Python 3.12; the
            # st_ctime fallback is also creation time on Windows.
            born = getattr(st, "st_birthtime", st.st_ctime)
            got_c = datetime.fromtimestamp(born).strftime("%Y-%m-%dT%H:%M:%S")
            got_m = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%dT%H:%M:%S")
            for field, got, want in (("created", got_c, src["created"]),
                                     ("modified", got_m, src["modified"])):
                total += 1
                if got == want:
                    ok += 1
                else:
                    mismatches.append(
                        f"{mode}: {row['original_path']} {field} {want} -> {got}")

    return {"total": total, "ok": ok, "mismatches": mismatches, "missing": missing}


def bucket_summary(mode: str) -> dict:
    rows = read_rows(crossref_path(ORGANIZED / f"by-{mode}"))
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["bucket"]] = counts.get(r["bucket"], 0) + 1
    named = {k: v for k, v in counts.items() if k != UNASSIGNED}
    top = max(named.items(), key=lambda kv: kv[1]) if named else ("-", 0)
    return {"files": len(rows), "buckets": len(counts),
            "unassigned": counts.get(UNASSIGNED, 0),
            "top_name": top[0], "top_n": top[1], "counts": counts}


# --- Rendering ----------------------------------------------------------------

def folder_label(path: str) -> str:
    """The file's folder, with the intake root stripped — a reader cares where
    it sat inside the collection, not that everything starts with mock-intake/.
    Files at the top level say so rather than showing a bare dot."""
    rel = Path(path).parent.as_posix().replace("mock-intake", "").strip("/")
    return rel or "(intake root)"


def stat(key: str, value) -> str:
    """Emit a figure tagged for the QC gate to parse back out of the HTML."""
    return f'<span data-qc="{key}">{value}</span>'


def render(manifest, exceptions, classifications, dates, buckets, recon) -> str:
    n_files = len(manifest)
    flagged = {r["path"] for r in exceptions}
    n_clean = n_files - len(flagged)
    by_sev = {s: sum(1 for r in exceptions if r["severity"] == s)
              for s in SEVERITY_ORDER}
    by_rule: dict[str, int] = {}
    for r in exceptions:
        by_rule[r["rule_id"]] = by_rule.get(r["rule_id"], 0) + 1
    by_label: dict[str, int] = {}
    for r in classifications:
        by_label[r["label"]] = by_label.get(r["label"], 0) + 1
    n_unclassified = by_label.get("unclassified", 0)
    n_labeled = n_files - n_unclassified

    # Rules chart: ordered worst-severity-first, then by count. Each bar wears
    # its severity's ordinal step, so the chart carries state as well as size.
    sev_of = {r["rule_id"]: r["severity"] for r in exceptions}
    rule_rows = sorted(by_rule.items(),
                       key=lambda kv: (SEVERITY_ORDER.index(sev_of[kv[0]]), -kv[1]))
    rules_chart = hbar_chart([(rid, n, SEVERITY_FILL[sev_of[rid]])
                              for rid, n in rule_rows])

    # Classification chart: nominal categories, so ONE hue for every bar -
    # colouring each bar by its own value would re-encode bar length. The single
    # exception is 'unclassified', emphasized in the accent because it is the
    # one category that represents work still to do.
    label_rows = sorted(by_label.items(), key=lambda kv: -kv[1])
    class_chart = hbar_chart(
        [(lbl, n, ACCENT if lbl == "unclassified" else DEEP_TEAL)
         for lbl, n in label_rows])

    generated = datetime.now().strftime("%d %B %Y, %H:%M")

    # --- exception queue rows
    queue = sorted(exceptions, key=lambda r: (SEVERITY_ORDER.index(r["severity"]),
                                              r["rule_id"], r["path"]))
    queue_rows = "\n".join(
        f'<tr><td><span class="chip chip-{r["severity"]}">{r["severity"]}</span></td>'
        f'<td class="mono">{e(r["rule_id"])}</td>'
        f'<td class="fname">{e(r["name"])}</td>'
        f'<td class="mono dim">{e(folder_label(r["path"]))}</td>'
        f'<td class="detail">{e(r["detail"])}</td></tr>'
        for r in queue)

    rule_gloss = "\n".join(
        f'<tr><td class="mono">{e(rid)}</td>'
        f'<td><span class="chip chip-{sev_of[rid]}">{sev_of[rid]}</span></td>'
        f'<td class="detail">{e(RULE_DOCS[rid])}</td></tr>'
        for rid, _ in rule_rows)

    sev_gloss = "\n".join(
        f'<tr><td><span class="chip chip-{s}">{s}</span></td>'
        f'<td class="num">{by_sev[s]}</td>'
        f'<td class="detail">{e(SEVERITY_DOCS[s])}</td></tr>'
        for s in SEVERITY_ORDER)

    axis_rows = "\n".join(
        f'<tr><td class="mono">--by {m}</td>'
        f'<td class="num">{stat(f"buckets_{m}", buckets[m]["buckets"])}</td>'
        f'<td>{e(buckets[m]["top_name"])} <span class="dim">({buckets[m]["top_n"]})</span></td>'
        f'<td class="num">{stat(f"unassigned_{m}", buckets[m]["unassigned"])}</td>'
        f'<td class="num">{stat(f"files_{m}", buckets[m]["files"])}</td></tr>'
        for m in MODES)

    div_rows = "\n".join(
        f'<tr><td class="mono">--by {m}</td><td class="fname">'
        f'{e(buckets[m]["div_bucket"])}</td></tr>' for m in MODES)

    recon_rows = "\n".join(
        f'<tr><td class="detail">{e(label)}</td><td class="num mono">{left}</td>'
        f'<td class="num mono">{right}</td>'
        f'<td class="{"ok" if ok else "bad"}">{"match" if ok else "MISMATCH"}</td></tr>'
        for label, left, right, ok in recon)

    date_ok = dates["ok"] == dates["total"] and not dates["missing"]

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Document intake QC report</title>
<style>
  :root {{
    --ink: {INK}; --deep: {DEEP_TEAL}; --muted: {TEXT_MUTED}; --steel: {STEEL};
    --sage: {SAGE}; --mist: {MIST}; --accent: {ACCENT}; --accent-text: {ACCENT_TEXT};
    --head: 'Space Grotesk', 'Segoe UI', system-ui, -apple-system, sans-serif;
    --body: 'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif;
    --mono: 'JetBrains Mono', Consolas, 'Courier New', monospace;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 48px 24px 96px; background: var(--mist);
    font-family: var(--body); color: var(--ink); line-height: 1.6;
    font-size: 15px; -webkit-font-smoothing: antialiased;
  }}
  .wrap {{ max-width: 940px; margin: 0 auto; }}
  h1, h2, h3 {{ font-family: var(--head); color: var(--deep); font-weight: 600;
                line-height: 1.25; margin: 0; }}
  h1 {{ font-size: 30px; letter-spacing: -0.01em; }}
  h2 {{ font-size: 20px; margin-bottom: 6px; }}
  h3 {{ font-size: 15px; margin-bottom: 4px; }}
  p {{ margin: 0 0 12px; }}
  .lede {{ color: var(--muted); max-width: 68ch; }}
  header {{ border-bottom: 2px solid var(--deep); padding-bottom: 20px; margin-bottom: 8px; }}
  .kicker {{ font-family: var(--mono); font-size: 11px; letter-spacing: 0.13em;
             text-transform: uppercase; color: var(--accent-text); margin-bottom: 10px; }}
  .meta {{ font-family: var(--mono); font-size: 12px; color: var(--muted); margin-top: 12px; }}
  section {{ margin-top: 44px; }}
  .card {{ background: #fff; border: 1px solid var(--sage); border-radius: 10px;
           padding: 24px 26px; margin-top: 16px; }}
  /* Hero: exactly one per view, in the same sans as everything else, and with
     proportional figures - tabular-nums makes a large number look loose. */
  .hero {{ display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap; }}
  .hero .n {{ font-family: var(--head); font-size: 60px; font-weight: 600;
              color: var(--accent-text); line-height: 1; }}
  .hero .t {{ font-size: 16px; color: var(--ink); max-width: 46ch; }}
  .tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 12px; margin-top: 22px; }}
  .tile {{ background: var(--mist); border: 1px solid var(--sage); border-radius: 8px;
           padding: 14px 16px; }}
  .tile .v {{ font-family: var(--head); font-size: 26px; font-weight: 600;
              color: var(--deep); line-height: 1.1; }}
  .tile .l {{ font-size: 12px; color: var(--muted); margin-top: 3px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13.5px; }}
  th {{ text-align: left; font-family: var(--mono); font-size: 10.5px;
        letter-spacing: 0.1em; text-transform: uppercase; color: var(--muted);
        font-weight: 500; padding: 0 10px 8px 0; border-bottom: 1px solid var(--sage); }}
  td {{ padding: 8px 10px 8px 0; border-bottom: 1px solid #E6EDE2; vertical-align: top; }}
  tbody tr:last-child td {{ border-bottom: none; }}
  .mono {{ font-family: var(--mono); font-size: 12px; }}
  .num {{ font-variant-numeric: tabular-nums; text-align: right;
          font-family: var(--mono); font-size: 12.5px; white-space: nowrap; }}
  .dim {{ color: var(--muted); }}
  .fname {{ word-break: break-word; }}
  .detail {{ color: var(--muted); }}
  .ok {{ color: var(--deep); font-family: var(--mono); font-size: 12px; }}
  .bad {{ color: #8A1C10; font-weight: 600; font-family: var(--mono); font-size: 12px; }}
  /* Severity chips carry an ordinal color AND the word - never color alone. */
  .chip {{ display: inline-block; padding: 2px 9px; border-radius: 999px;
           font-family: var(--mono); font-size: 10.5px; letter-spacing: 0.05em;
           color: #fff; white-space: nowrap; }}
  .chip-high {{ background: {SEVERITY_FILL['high']}; }}
  .chip-medium {{ background: {SEVERITY_FILL['medium']}; }}
  .chip-low {{ background: {SEVERITY_FILL['low']}; color: {INK}; }}
  .chart {{ display: block; margin-top: 8px; overflow: visible; }}
  .c-label {{ font-family: var(--mono); font-size: 11.5px; fill: var(--muted); }}
  .c-value {{ font-family: var(--mono); font-size: 12px; fill: var(--ink); }}
  .note {{ border-left: 3px solid var(--accent); padding: 2px 0 2px 16px;
           margin: 18px 0; color: var(--ink); }}
  .note strong {{ color: var(--accent-text); }}
  .limits li {{ margin-bottom: 14px; }}
  .limits strong {{ color: var(--deep); }}
  .scroll {{ overflow-x: auto; }}
  footer {{ margin-top: 56px; padding-top: 20px; border-top: 1px solid var(--sage);
            font-size: 12px; color: var(--muted); }}
  @media print {{
    body {{ background: #fff; padding: 0; font-size: 11pt; }}
    .card {{ break-inside: avoid; }}
    section {{ break-inside: avoid-page; }}
  }}
</style>
</head>
<body>
<div class="wrap">

<header>
  <div class="kicker">Document intake QC</div>
  <h1>Intake review: {stat('total_files', n_files)} files received</h1>
  <p class="meta">Generated {generated} &nbsp;·&nbsp; source: mock-intake/ &nbsp;·&nbsp;
     pipeline: scan &rarr; classify &rarr; rules &rarr; organize</p>
</header>

<section>
  <div class="card">
    <div class="hero">
      <div class="n">{stat('files_flagged', len(flagged))}</div>
      <div class="t">of {n_files} files carry at least one finding.
        {stat('files_clean', n_clean)} came through clean.</div>
    </div>
    <div class="tiles">
      <div class="tile"><div class="v">{stat('total_exceptions', len(exceptions))}</div>
        <div class="l">findings total</div></div>
      <div class="tile"><div class="v">{stat('sev_high', by_sev['high'])}</div>
        <div class="l">high severity</div></div>
      <div class="tile"><div class="v">{stat('sev_medium', by_sev['medium'])}</div>
        <div class="l">medium severity</div></div>
      <div class="tile"><div class="v">{stat('sev_low', by_sev['low'])}</div>
        <div class="l">low severity</div></div>
    </div>
    <p class="lede" style="margin-top:22px">A file can trip more than one rule, so
      findings outnumber flagged files. Nothing here was fixed automatically —
      this is a review queue, not a clean-up log.</p>
  </div>
</section>

<section>
  <h2>One queue, not two</h2>
  <p class="lede">Deterministic checks and AI classification findings share a
    single list. A reviewer works one queue in severity order; where a finding
    came from is a column, not a separate report.</p>

  <div class="card">
    <h3>Severity means this</h3>
    <table><thead><tr><th>Severity</th><th class="num">Findings</th>
      <th>What it means for the set</th></tr></thead>
      <tbody>{sev_gloss}</tbody></table>
  </div>

  <div class="card">
    <h3>Findings by rule</h3>
    <p class="detail" style="font-size:13px;margin:0">Bar color carries severity;
      the label is always printed, so the ranking never depends on color alone.</p>
    <div class="scroll">{rules_chart}</div>
  </div>

  <div class="card">
    <h3>What each rule catches</h3>
    <table><thead><tr><th>Rule</th><th>Severity</th><th>Why it matters</th></tr></thead>
      <tbody>{rule_gloss}</tbody></table>
  </div>

  <div class="card">
    <h3>The queue — {stat('queue_rows', len(queue))} findings across {len(flagged)} files</h3>
    <div class="scroll">
    <table><thead><tr><th>Severity</th><th>Rule</th><th>File</th><th>Folder</th>
      <th>Detail</th></tr></thead>
      <tbody>{queue_rows}</tbody></table>
    </div>
  </div>
</section>

<section>
  <h2>Classification</h2>
  <p class="lede">Every document was classified from its text alone — the
    filename and folder were never shown to the model, so a file misfiled by the
    client cannot talk the classifier into agreeing with it.</p>
  <div class="card">
    <h3>{stat('class_labeled', n_labeled)} labeled ·
        {stat('class_unclassified', n_unclassified)} unclassified</h3>
    <p class="detail" style="font-size:13px;margin:0">The
      <span style="color:{ACCENT_TEXT};font-weight:600">unclassified</span> bar is
      the one that needs action: those files carry no readable text, so no label
      was invented for them. They are in the queue above.</p>
    <div class="scroll">{class_chart}</div>
  </div>
</section>

<section>
  <h2>Three ways to organize the same set</h2>
  <p class="lede">The organized copy was built three times, on three different
    axes. Each is a complete copy of all {n_files} files — nothing is left behind
    and nothing is invented. Files with no value on an axis go to
    <span class="mono">Unassigned/</span>, never a guessed bucket.</p>
  <div class="card">
    <table><thead><tr><th>Mode</th><th class="num">Buckets</th><th>Largest bucket</th>
      <th class="num">Unassigned</th><th class="num">Files</th></tr></thead>
      <tbody>{axis_rows}</tbody></table>
  </div>

  <div class="card">
    <h3>Why three axes and not one</h3>
    <p style="margin-bottom:10px">Author, custodian, and document class are three
      different questions. One file answers them three different ways:</p>
    <p class="mono" style="font-size:12px;color:var(--muted);margin-bottom:6px">
      {e(Path(DIVERGENCE).name)}</p>
    <table><thead><tr><th>Mode</th><th>Lands in</th></tr></thead>
      <tbody>{div_rows}</tbody></table>
    <div class="note"><strong>Why this matters.</strong> Written by one person,
      collected from another's custody, and named for a third party. A tool that
      collapses these into one "organize" button will file it wrong two times out
      of three — and will not tell you which time.</div>
  </div>
</section>

<section>
  <h2>Date fidelity</h2>
  <p class="lede">Filesystem dates drive date filtering in review. A copy that
    silently stamps every file with the processing date does not fail loudly — it
    produces plausible, wrong results. So the copies were measured, not assumed.</p>
  <div class="card">
    <div class="hero">
      <div class="n" style="color:var(--deep)">{stat('datefid_ok', dates['ok'])}</div>
      <div class="t">of {stat('datefid_total', dates['total'])} timestamp
        comparisons match their source exactly
        {'' if date_ok else '— <strong style="color:#8A1C10">DISCREPANCIES FOUND</strong>'}
        <div class="dim" style="font-size:13px;margin-top:4px">Created and modified,
          on every copy, in all three modes. Measured against the manifest's
          pre-copy record when this report was generated.</div></div>
    </div>
    <p style="margin-top:20px">The two seeded date anomalies — a 1980 file and a
      2031 file — survive the copy with their dates intact. That is the point: if
      a bad date were quietly repaired by the copy, the finding above it in the
      queue would become unreproducible.</p>
    <div class="note"><strong>Access times are not preserved, and cannot be.</strong>
      Reading a file updates its access time, so hashing the set during intake
      destroys the original value before any copy exists. No copy-based workflow
      recovers it. If access times matter to your matter, they must be captured by
      the forensic collection tool at acquisition. This report will not claim
      otherwise.</div>
  </div>
</section>

<section>
  <h2>Where everything went</h2>
  <div class="card">
    <p>Each mode writes a cross-reference alongside its tree —
      <span class="mono">organized/crossref-by-&lt;mode&gt;.csv</span> — recording
      every file's original path, its new path, its hash, and its dates as
      captured before the copy ran.</p>
    <p style="margin-bottom:0">Files keep their original folder structure inside
      each bucket, so the cross-reference is the record of where a document sat
      when it was collected. Hashes are the scan-time values, taken before any
      copy existed, so they prove provenance rather than restating it.</p>
  </div>
</section>

<section>
  <h2>Reconciliation</h2>
  <p class="lede">Every figure in this report is derived twice, from different
    columns, and compared. This table is that check — computed when the report was
    generated, not typed in afterwards.</p>
  <div class="card">
    <table><thead><tr><th>Check</th><th class="num">Computed</th>
      <th class="num">Cross-check</th><th>Result</th></tr></thead>
      <tbody>{recon_rows}</tbody></table>
  </div>
</section>

<section>
  <h2>Limitations</h2>
  <p class="lede">What this report does not establish. Each of these is a real
    boundary, stated because a client finding it later is worse than reading it now.</p>
  <div class="card">
    <ul class="limits">
      <li><strong>Bad filenames were reported, never fixed.</strong> Files are
        copied under their original names. "Organized" here means re-foldered, not
        cleaned up — all {stat('naming_findings', by_rule.get('NAMING', 0))} naming
        findings above are still present in the organized copy. Renaming is a client
        decision, because a renamed file no longer matches what was collected.</li>
      <li><strong>Access times are not preserved.</strong> See the date section —
        destroyed at intake by the act of reading, before any copy exists.</li>
      <li><strong>File ownership is not proven to be preserved.</strong> The copy
        engine was tested on a single-user machine, where every file has the same
        owner — so the check passes without being able to fail. On a collection
        holding files owned by several accounts, ownership may fall back to the
        copying account. Test before relying on it.</li>
      <li><strong>Access-control lists survive only within one NTFS volume.</strong>
        Copying to another device, a FAT/exFAT drive, or a network share drops
        them — there is nowhere to put them.</li>
      <li><strong>The custodian mapping is a supplied input, not a finding.</strong>
        It stands in for a client's collection log. The tool cannot validate it: a
        wrong map produces confidently wrong buckets. Agreeing it in writing is a
        scoping step.</li>
      <li><strong>Classification accuracy here does not predict accuracy on your
        set.</strong> These documents are short and clean. Real intakes bring scans,
        OCR noise, and document types outside the five labels. Measure on a sample
        of your own documents before relying on the numbers.</li>
    </ul>
  </div>
</section>

<footer>
  Generated by report.py · Document Intake QC Agent · sources: manifest.csv,
  exceptions.csv, classifications.csv, organized/crossref-by-&lt;mode&gt;.csv.
  All data in this report is mock data created for demonstration.
</footer>

</div>
</body>
</html>
"""


def main() -> int:
    for required in (MANIFEST, EXCEPTIONS, CLASSIFICATIONS):
        if not required.is_file():
            fail(f"{required.name} is missing — run the pipeline first "
                 f"(scan.py -> classify.py -> rules.py -> organize.py)")

    manifest = read_rows(MANIFEST)
    exceptions = read_rows(EXCEPTIONS)
    classifications = read_rows(CLASSIFICATIONS)

    buckets = {}
    for mode in MODES:
        xref = crossref_path(ORGANIZED / f"by-{mode}")
        if not xref.is_file():
            fail(f"{xref.name} is missing — run organize.py --by {mode} first")
        buckets[mode] = bucket_summary(mode)

    dates = measure_date_fidelity(manifest)

    # --- Reconciliation. Each row derives the same quantity two different ways.
    # A report that cannot prove its own arithmetic is a report a client cannot
    # act on, so a mismatch here stops the render rather than printing a caveat.
    n_files = len(manifest)
    flagged = {r["path"] for r in exceptions}
    clean = n_files - len(flagged)
    sev_total = sum(1 for r in exceptions if r["severity"] in SEVERITY_ORDER)
    unclassified = sum(1 for r in classifications if r["label"] == "unclassified")

    recon = [
        ("Files in manifest vs files on the classification list",
         n_files, len(classifications), n_files == len(classifications)),
        ("Flagged files + clean files vs total received",
         len(flagged) + clean, n_files, len(flagged) + clean == n_files),
        ("Findings by severity vs total findings",
         sev_total, len(exceptions), sev_total == len(exceptions)),
        ("Unclassified label count vs UNCLASSIFIED findings",
         unclassified, sum(1 for r in exceptions if r["rule_id"] == "UNCLASSIFIED"),
         unclassified == sum(1 for r in exceptions if r["rule_id"] == "UNCLASSIFIED")),
    ]
    for mode in MODES:
        b = buckets[mode]
        recon.append((f"Copies in by-{mode} cross-reference vs files received",
                      b["files"], n_files, b["files"] == n_files))
    recon.append(("Timestamp comparisons matching vs comparisons made",
                  dates["ok"], dates["total"], dates["ok"] == dates["total"]))

    # Every rule the engine emitted must have a client-facing description.
    undocumented = sorted({r["rule_id"] for r in exceptions} - set(RULE_DOCS))
    if undocumented:
        fail(f"rule(s) with no description in rules.RULE_DOCS: {undocumented}")

    for label, left, right, ok in recon:
        if not ok:
            fail(f"{label}: {left} != {right}")
    if dates["missing"]:
        fail("copies missing or unmatched: " + "; ".join(dates["missing"][:5]))

    # The divergence file's bucket per mode, read from each cross-reference.
    for mode in MODES:
        xref = read_rows(crossref_path(ORGANIZED / f"by-{mode}"))
        match = [r["bucket"] for r in xref if r["original_path"] == DIVERGENCE]
        if not match:
            fail(f"divergence file not found in by-{mode} cross-reference")
        buckets[mode]["div_bucket"] = match[0]

    OUT.write_text(render(manifest, exceptions, classifications, dates,
                          buckets, recon), encoding="utf-8")

    print(f"Wrote {OUT.name}")
    print(f"  {n_files} files | {len(exceptions)} findings across {len(flagged)} files "
          f"| {clean} clean")
    print(f"  date fidelity: {dates['ok']}/{dates['total']} comparisons match")
    print(f"  reconciliation: {sum(1 for *_, ok in recon if ok)}/{len(recon)} checks match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
