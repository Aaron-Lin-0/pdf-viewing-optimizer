# Overnight optimizer and viewing-cost experiment

This is a local research harness, separate from production acceptance. Run it
from a normal terminal; Codex does not need to remain open. It never uploads a
PDF, changes the public optimizer's defaults, or replaces an original. Supply
the actual documents you want tested. Generated PDFs check the harness and known
geometry; their results do not establish a winner for your notes.
The executable research tools require the repository checkout and comparison
dependencies; they are not part of the ordinary installed optimizer/skill runtime.

If strict inventory rejects a NeoPdf/BOOX export, the runner attempts a separate
current-revision snapshot with qpdf. The independent qpdf check must accept the
original without warnings. Encryption, signatures, XFA, unsupported producers,
other parser warnings and resource limits still block it. The saved snapshot
must pass ordinary strict input checks, and the full logical-graph/stream
comparator must prove preservation against the source's current revision. Only
the exact non-zero-index warning is recorded as a source-reader exception;
other warnings still fail. Historical revision layout is not retained in this
research copy. The source is unchanged. This is separate research preprocessing;
production Direct/Thorough acceptance remains unchanged.

The preprocessing operation has its own time, hashes and proof in `results.json`.
Verified NeoPdf snapshots use first/middle/last timing pages from the proved page
count. A separate expensive density/small-writing inventory does not block these
inputs; its missing coverage is disclosed. Each treatment still inspects the full
document and must complete its own preservation checks. Approximate trials are
not predeclared eligible on this basis. No-op reduction trials discard their own
scratch PDFs after checks, retaining the diagnostic manifest rather than large
unused copies.
Candidates are generated from the verified snapshot; the original viewing-time
reference remains the actual original source. Preprocessing cost is additional
to candidate-generation timings. Failure leaves the document explicitly
unavailable rather than silently treating it as verified.

The ordered improvement plan remains [roadmap.md](roadmap.md), especially P16.
This harness implements exploratory point-reduction comparisons, **not P16-D/E
qualification**: neighborhood-gap protection, general topology preservation,
small-writing/semantic acceptance and real-document holdout review remain open.

## Run locally

Install comparison dependencies once, in this checkout's environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-comparison.txt
npm ci
```

First check the installation with actual generated PDF files:

```powershell
.\.venv\Scripts\python.exe tools/benchmark_overnight.py --smoke --skip-native --output audit-output/overnight-smoke-NEW
```

For an overnight run, replace the two example input paths with selected local
PDFs. Choose a new output name. Quoted positional paths are the only personal
documents the runner reads; it does not search your notes folders.

```powershell
.\.venv\Scripts\python.exe tools/benchmark_overnight.py `
  "D:/Notes/first-document.pdf" "D:/Notes/second-document.pdf" `
  --fixtures --hours 8 --disk-gb 20 `
  --output audit-output/overnight-NEW
```

Default design: 18 treatments plus original, three fresh generation/screen
repetitions, five fresh PDF.js rendering repetitions per sampled page, separate
operator preparation, optional native Firefox measurements, then image analysis
and a separate processing profile. The eight-hour limit is a maximum, not a
promise to occupy the whole night or finish every document. Short/no-op inputs
can finish much earlier; long inputs may leave incomplete work. Timing is
sequential; do not run games, other benchmarks or other heavy work alongside it.

Firefox is discovered locally. `--firefox "C:/Program Files/Mozilla Firefox/firefox.exe"`
selects a binary explicitly; `--skip-native` disables that phase. Native startup
and driver failures remain unavailable evidence and do not prevent standalone
measurements. Install geckodriver before an offline run; Selenium's driver manager
may otherwise download the public driver. Disposable profiles are used. Browser
timing follows [the native comparison protocol](comparison.md#read-the-evidence).

Keep the computer plugged in. On Windows the runner requests that automatic
system sleep be deferred until it exits; lid-close, explicit sleep and power
policies can still interrupt it. Closing its terminal or
shutting down Windows interrupts the job. Every operation has a timeout, logs,
and owned-child cleanup; a hung optimizer cannot stall the entire night forever.
Time/disk-budget stops save a partial report. Disk usage is checked between
operations, not imposed as a filesystem quota. A `STOP` file placed inside the run
directory requests a stop between operations; Ctrl+C interrupts the active child.

Resume the same settings/code/tools using the same output directory:

```powershell
.\.venv\Scripts\python.exe tools/benchmark_overnight.py `
  --resume --hours 8 --output audit-output/overnight-NEW
```

Omit input paths and `--fixtures` on resume. Repeat non-default settings such as
`--skip-native`, `--repeats` or `--smoke` exactly. Frozen snapshots and completed
artifacts are hash-checked; changed code, tools, settings or evidence refuse reuse.
Failed/incomplete operations get a new attempt; prior evidence is retained.
Each invocation has a separate `session-NNN/report.md`, `report.csv`, `results.json` and
`progress.log`. Raw per-operation files remain under `tasks/`.

## Hypotheses registered before trials

For a focused stroke-chain experiment, select the exact comparator, zero-deviation
chaining control and bounded reductions, with a fresh output directory:

```powershell
.\.venv\Scripts\python.exe tools/benchmark_overnight.py "D:/Notes/dense.pdf" `
  --recipes pack-9 joined-rdp-0 joined-rdp-0.01 joined-rdp-0.025 joined-rdp-0.05 `
  --require-recipes joined-rdp-0 joined-rdp-0.025 `
  --optimizer-repeats 1 --repeats 5 --skip-native --hours 2 --disk-gb 8 `
  --output audit-output/NEW_STROKE_CHAIN_RUN
```

Original is always included. `--recipes` selects from the maintained registry,
not arbitrary commands. This example repeats rendering, generating each treatment
once. Native measurements are explicitly skipped. Resume requires the same
recipes/settings and code. Older runs remain readable but cannot resume across
these implementation changes.

`--require-recipes` prevents an exact-only timing run from masquerading as the
requested lossy experiment. Every named recipe must be selected, produce a
checked candidate, and (for approximate recipes) actually edit target bodies.
Otherwise the document is reported as preflight-blocked, and no original/exact
or candidate viewing benchmarks run for it. Generation diagnostics are retained.

Resource protection remains bounded: at most 100,000 structural object/reason
pairs across the scan and 2,000,000 reference/value visits in total. Terminal numeric/name/
string values cannot reference a Form and do not consume the structural-node
budget. The previous bound counted those terminal values too, which could reject
large font metadata before any simplification. Nested/shared Form references,
Type 3 fallback, masks, transparency and opacity safeguards remain in force.

The run's immutable `plan.json` records a hypothesis, named comparator and
falsifier for **every treatment** before any candidate results are inspected.
Every generation repetition also carries that hypothesis in its request.
These are predictions to test, not claims that the methods work on an input.

| Treatment | Prediction | Main counterevidence |
| --- | --- | --- |
| Original | Provides source viewing cost and appearance | A changed source/hash invalidates evidence |
| qpdf-only | Omitting drawing transformations saves generation time; viewing work resembles original | Little processing saving or a repeatable viewing difference needing explanation |
| Direct without packing | Avoids packing cost; output may be larger, inlining may reduce drawing overhead | Packing is negligible or storage/viewing gets worse |
| Packing level 1 | Faster generation than level 9 with more bytes | No generation saving or unexpectedly worse viewing |
| Packing level 6 | Intermediate storage/processing tradeoff | Level 1 or 9 dominates both dimensions |
| Packing level 9 | Current Direct-style chain may reduce overhead; verification may dominate processing | No viewing benefit or unacceptable total processing cost |
| Recompress Flate at level 9 | More processing for possible byte savings, little drawing improvement | Material repeatable viewing gain, or no byte saving |
| Existing qualified batching | Fewer paint calls may beat the Direct-style baseline | No useful viewing gain or visible edge damage |
| Zero reduction | Same path bytes/pixels; measures clone/packing overhead separately | Any path or tested-pixel change |
| RDP at 0.01, 0.025, 0.05 mm | Removing redundant existing vertices may reduce cold-render CPU; increasing tolerance may remove more | No eligible paths, no point reduction, no repeatable viewing gain, or unacceptable local loss |
| Midpoint splitting at the same three tolerances | More balanced subdivision may trade retained vertices against reduction cost | RDP dominates processing, drawing and quality |
| joined-rdp-0 | Exact-endpoint, identical-width segment chaining lowers paint-call overhead without centerline deviation; raster edge shading may change | No useful gain or unacceptable local shading/shape change |
| joined-rdp at 0.01, 0.025, 0.05 mm | Bounded vertex reduction inside these chains may improve drawing beyond the chaining control | No incremental gain, a failed independent bound, or unacceptable local loss |

qpdf explicitly documents that recompression can be much slower and that lower
compression levels trade bytes for writing speed. These are upstream mechanisms,
not results from this benchmark. [qpdf command reference](https://qpdf.readthedocs.io/en/latest/cli.html)

## What point reduction actually changes

Only already-connected, opaque, positive constant-width `m/l/S` paths in a
recognized pen Form are eligible. The Form keeps its resources, order, clipping,
layers, widths and literal retained coordinates. The first/last point, closed
paths, short paths, zero-length marks and sharp corners are protected. Tolerances
are physical millimeters, mapped through every supported invocation; the local
bound is also capped at 10% of stroke width. Shared unsafe contexts, transparency,
annotation appearances, Type 3 fallback, hairlines, unsupported grammar, shear,
nonuniform transforms and inherited dashes decline conservatively.

The original `rdp`/`midpoint` treatments still require already-connected paths.
The separate `joined-rdp` research treatment groups only adjacent nonzero
two-point segments whose endpoint and width literals match exactly. It never
bridges a gap or width change, reorders paint, or joins across a dot. Round caps
and joins and every invocation's opacity/transform/dash context must be proved.
This groups geometry, not recovered pen identity. Pressure is not averaged.

Zero-deviation chaining is its own approximate control: source centerlines and
widths are retained, but changing separately painted caps to one round-joined
path can change raster edge shading. It is **not** the old exact zero-tolerance
control. Bounded reduction retains endpoints, protected corners, tiny runs and
paths with repeated/closed vertices. Neighbor gaps, general crossings and small
writing remain unqualified and require review. All no-op/unsupported treatments
stay in the report.

Research staging retains exact decimal metadata through cloning. qpdf first
creates a classic-xref representation; only copies of identified physical
xref/object streams are removed before compaction. The unchanged strict
graph/stream comparator must prove equivalence to the input, including logical
orphan data. An application reference or logical payload prevents that proof if
data would be lost. Transport types are never blanket-ignored by the comparator.
Binary document-ID bytes are retained as bytes. Staging, unchanged-clone and
restoration files use an owned temporary directory removed on exit. Original,
candidate, manifest and check results remain the evidence.

The saved candidate is independently parsed to check literal vertex subsequences,
widths, endpoints and a continuous bidirectional centerline bound. Restoring only
declared Form bodies must pass the existing strict whole-graph comparator against
the source. Packing must independently preserve its input. This isolates
approximate target edits from accidental changes to text/OCR, images, navigation,
attachments, retained annotations and other data. Unsupported cloning or decimal
representation declines the treatment rather than bypassing the comparator.

**A path-distance bound does not protect meaning or neighboring topology.** The
prototype does not certify preserved gaps between letters, intersections,
clipping-edge details or readable math. Every altered-handwriting output stays
`review required`, even if all rendered metrics look good. See the separate
topology-preserving approach described by [CGAL](https://doc.cgal.org/latest/Polyline_simplification_2/)
for why neighborhood checks are an additional requirement. CGAL is a reference;
this project does not install or claim its guarantees. Source-run partitions
are independently checked: every original path must be covered in order, with
literal endpoint continuity, unchanged widths and retained dots. Round-stroke
envelope reasoning does not certify raster antialiasing, clipped visible-set
bounds or topology. Those remain descriptive review findings.

## Separate processing and viewing measurements

Generation records fresh-process worker wall time and Python worker CPU for each
repeat. Python CPU excludes external children such as qpdf. Startup/import time
is outside worker wall time; external-command logs separately retain total child
duration. Verification or preliminary-screen time is reported separately. The
sum is not automatically a production CLI completion time: the research runner
omits publication and compares candidates even when they grow. Its separate
`optimizer.prof`/`profile.txt` identifies Python call costs without contaminating
timed repetitions; external subprocess internals need their own profiling.

Viewing uses a fixed source-derived page sample and width 1404. Each repetition
uses a fresh renderer process, rotates/reverses treatment order, and retains raw
load, cold and second-draw CPU/wall times. Operator preparation is a separate
process, never added to cold rendering. Fresh processes do not flush OS caches;
this is not a cold-disk benchmark. One report number sums per-page median cold
CPU over the same sample; per-page regressions and raw spread remain visible.
Missing/zero-resolution measurements cannot establish a speedup.

Optional native follow-up uses original, the fastest measured exact comparator,
and the two fastest remaining candidates from standalone measurements. It measures bundled Firefox viewer operations, rather
than substituting Node timings for scrolling/zoom. Native success is not device
qualification for BOOX hardware. Changes must eventually be checked on the target
device if that is the intended viewer.

All optimizer/screen workers end before viewing timings. All viewing timings end
before expensive image metrics/profiling **within each document**. Documents run
sequentially. Three generation/five rendering repeats are fixed in advance; do
not cherry-pick the fastest attempt. Use a new preregistered run to investigate
noisy outcomes or changed tolerances.

## Quality evidence and next-morning decision

Exact treatments keep their existing applicable preservation checks. Altered
geometry first gets a gross all-page screen; this only catches unreadable shapes,
dimension changes and blanked pages. It does not certify handwriting. After
timing, every page of altered candidates gets local foreground analysis in
PDFium at 144/288 DPI and PDF.js at width 1404. Source, output, overlays and
heatmaps are retained for changed pages, together with changed tiles, missing/added
ink, displacement, widths, loops/components and threshold sensitivity. Foreground
can include printed text and images, not just handwriting.

Review changed writing against the actual source PDF, particularly decimal dots,
subscripts, equality/fraction bars, gaps and loops. Mark incomplete coverage and
unreadable/ambiguous changes unresolved. The harness does not publish a lossy
copy as verified or decide acceptable semantic loss for you. P16's higher-resolution
protected crops, print checks and calibrated neighborhood/semantic review remain
qualification work; these exploratory plots do not replace them.

Use the fastest fully checked exact treatment as the practical comparator, plus
the zero-reduction control to isolate geometry savings. Require repeatable gains
beyond raw timing spread, check every sampled cold/cached/native regression, and
compare processing cost with expected use:

`break-even viewing operations = extra processing seconds / seconds saved per matching viewing operation`

Only compute this for the same actual operation with positive measured savings.
For example, expensive reduction can still pay off for a document reopened many
times, while a faster packer can be preferable for one-time reading. Bytes, point
counts and paint calls explain results; none alone proves a speed improvement.
There may be different winners for generation time, cold drawing, cached drawing
and acceptable visual loss. The correct outcome can be no useful improvement.

## Follow-up hypotheses outside this first runnable matrix

- **Curve fitting/smoothing:** a few cubic Béziers may replace many line vertices,
  but evaluation/tessellation can offset operator savings and smoothing can erase
  corners. Compare at matched independently verified error and local quality,
  only if polyline reduction leaves a measured bottleneck. The
  [Schneider reference implementation](https://github.com/erich666/GraphicsGems/blob/master/gems/FitCurves.c)
  is an algorithm starting point, not a PDF-preservation checker. No curve-fitting
  trial is claimed by the current runner.
- **Raster-heavy inputs:** image decode/downsample or image-only background
  conversion may help when images dominate. Handwriting point reduction cannot
  improve scanned ink. Changing resolution/codecs needs an explicitly separate
  image-loss contract and actual scan/zoom tests; no image resampling runs here.
- **Optimizer internals:** profile repeated parsing, deduplication, graph walks,
  render reuse and packing before changing them. Existing scoped source reuse
  and one packing pass are useful controls. Any proposed cache/worker change
  must retain source/renderer identity checks and be tested with cold and reusable
  work separately. Removing verification can appear faster while changing the
  guarantee, so it is not an equivalent optimizer-speed treatment.
- **Linearization/object packing:** loading/layout may improve first-page access
  without reducing drawing work. Test separately if actual startup/load
  measurements, rather than cold drawing, dominate. No assumed file-size-to-speed
  relationship is used.
