# Local optimizer comparisons

**Scope: the existing research runner.** For future improvement priorities,
candidate-selection policy and the implementation sequence, use the
[main improvement plan](roadmap.md). This guide documents the current fixed-profile
comparison workflow; it does not claim that the adaptive planner or automatic
resume already exists. The [original contract](optimizer-comparison-handoff.md)
is historical requirements context.

The comparison tools are separate from production optimization and its acceptance
rules. They collect differences rather than relaxing those rules. Reports describe
tested evidence, not semantic approval of altered handwriting.

## Progress and monitoring

The terminal prints the current document, optimizer/profile, browser operation,
page, image-analysis resolution and evidence region. A heartbeat every 15 seconds
shows total and current-stage elapsed time while an operation is waiting. The
same messages, including preflight messages, are saved in `progress.log` in the
new run directory. Optimizer stdout/stderr remain in each profile's own logs;
the progress message names those files. A heartbeat confirms the runner is alive,
not that a blocked external tool is making progress; timeouts still apply.

To follow an existing run from another PowerShell terminal:

```powershell
Get-Content -LiteralPath 'audit-output\YOUR_RUN\progress.log' -Tail 20 -Wait
```

## Setup

From the repository root, using Python 3.10+ and Node 22.13+:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-comparison.txt
npm ci
Copy-Item comparison-tools.example.json comparison-tools.local.json
```

Install Firefox and geckodriver locally; put geckodriver on PATH. Selenium may use
its driver manager if no driver is installed (public driver download only).
`SE_GECKODRIVER` can point directly to a local driver executable.
`SE_CACHE_PATH` selects its cache and `SE_AVOID_STATS=true` disables its usage
statistics. For restricted Windows accounts, set TEMP/TMP and MPLCONFIGDIR to
writable local directories. No tool installs competitors, buys Acrobat, uploads
PDFs, enables a cloud workflow or invokes an LLM.

Keep competitor environments separate. A Python competitor prefix can be
`["C:/Tools/competitor-env/Scripts/python.exe", "C:/Tools/competitor/cli.py"]`;
a Node prefix can be `["node", "C:/Tools/competitor/bin/cli.js"]`. Use absolute
paths because each process runs in a different directory. Windows npm `.cmd`
launchers, shell scripts, PowerShell and `cmd /c` are rejected. For pdfsizeopt use
a compatible Python interpreter with `pdfsizeopt.single`, or import a locally
generated output when its platform launcher needs shell interpretation.

## Configuration

The committed empty example runs the repository profiles and accounts for every
missing competitor/import. A configured executable needs its command prefix,
version pin, version-probe arguments and help-probe arguments. For example:

```json
{
  "tools": {
    "qpdf": {
      "command": ["C:/Tools/qpdf/bin/qpdf.exe"],
      "version": "12.4.2",
      "version_args": ["--version"],
      "help_args": ["--help=all"]
    },
    "mupdf": {
      "command": ["C:/Tools/mupdf/mutool.exe"],
      "version": "1.28.5",
      "version_args": ["-v"],
      "help_args": ["clean", "-h"]
    }
  },
  "imports": []
}
```

Use the actual installed version; these numbers are examples, not a claim that
those competitors were run during development. Tool keys: `boox-pdf`, `qpdf`,
`mupdf`, `pdfsizeopt`, `ocrmypdf`, `mattdesl`, `ghostscript`. Profiles are fixed in
`PROFILES`, not configurable flag strings. Help output must expose required flags;
an unavailable/unverified interface remains not tested. Repository provenance
includes commit and dirty status. Prefix executable/script hashes, actual version
output and exact commands are recorded. A changed upstream CLI requires reviewing
the fixed adapter; help token checks alone cannot prove identical tool semantics.

For a checked-out tool without a version flag, replace `version_args` with a direct
`version_command`, for example `["git", "-C", "C:/Tools/boox-pdf-optimizer", "rev-parse", "HEAD"]`,
and pin `version` to that commit. Keep the command entrypoint inside that checkout;
preflight records its script/executable hash as well. Shell wrappers are still rejected.
Alternatively supply an import. Every import declares the exact source hash:

```json
{
  "tools": {},
  "imports": [{
    "tool": "acrobat",
    "profile": "conservative",
    "source_sha256": "replace with the 64 lowercase hex characters of the original PDF SHA-256",
    "path": "C:/Local/comparison/acrobat-conservative.pdf",
    "version": "record the licensed local Acrobat version",
    "settings": {"images": "no downsampling", "fonts": "retain", "discard": "none; record every optimizer panel"}
  }]
}
```

Record all Acrobat optimizer settings yourself; the script cannot verify what
settings produced an imported PDF. BOOX Note Optimizer uses tool `boox-note`,
profile `default-export`, plus `note_path` and `note_sha256`. The matching note/PDF
relationship is user-declared. Export through the documented local interface;
this is an upstream workflow comparison. Other tool/profile outputs may also be
imported. Wrong source hashes are never compared. Duplicate imports are rejected.
Put personal configurations and outputs under ignored `audit-output/` or keep
the `comparison-tools.local.json` file ignored.

## Run

```powershell
.\.venv\Scripts\python.exe tools/compare_optimizers.py `
  --output audit-output/preflight-001 `
  --tools-config comparison-tools.example.json --preflight-only

.\.venv\Scripts\python.exe tools/compare_optimizers.py `
  --input "D:/Notes/lecture.pdf" `
  --output audit-output/comparison-001 `
  --tools-config comparison-tools.local.json `
  --firefox "C:/Program Files/Mozilla Firefox/firefox.exe"
```

`--input` is repeatable. The output directory must not exist, including for
preflight-only runs. Preflight never opens supplied personal input PDFs. Source
snapshots and per-profile disposable inputs are hash checked. Every output/log/
record is new. The default timeout is 30 minutes for optimization and 5 minutes
per page-render operation. Owned process trees are terminated on timeout or
interruption. Optimizers run sequentially; failures do not abort other profiles.
No automatic resume exists for this research runner; choose a new directory. Production viewing-copy
`no improvement` retains the original and never promotes rejected candidates.

## Read the evidence

`report.md` contains classes, checks, timings and the ten prioritized crops per
output. `results.json` contains provenance, raw Firefox observations, independent
checks, all-page/threshold metrics and every flagged region. `summary.csv` has one
row per document/tool/profile and worst-page foreground measurements.
`record-*.json` provides append-only per-operation records (record schema 2), with
document/output context and each page, detail, preservation check set or browser
summary saved once. Raw browser runs are saved in their measurement directories.
These records are diagnostics, not an automatic-resume interface. Earlier schema-1
runs used full snapshots, which could consume substantial space. Final
`results.json` still contains the full combined measurements. Operation JSON files and stdout/
stderr logs remain next to their outputs. Reports and images may contain private
content and paths: do not commit them.

PDFium compares every page at 144 and 288 DPI; standalone PDF.js compares every
page at width 1404. The script does not change renderer settings or align output
to hide displacement. True 576-DPI crops are rendered for flagged tiles, with a
representative source detail for exact outputs. Changed geometry is reported
without incompatible pixel comparisons. Each reader/render subprocess is released
between operations. Crops, overlays and heatmaps remain local.

Foreground masks at 200/225/250 retain every component, even a one-pixel dot.
Missing/added ink fractions use original foreground area, with null denominators
on blank originals; all missing ink reports fraction 1. Skeleton distances and
width estimates use actual rendered page dimensions in millimeters. Width changes
use nearest skeleton samples within .1 mm, a matching radius, not an acceptance
threshold. No alignment/warping is performed. Component overlap, skeleton endpoints,
junction pixels and holes flag possible shape changes; small translations and
antialiasing can also trigger them. Quantization limits precision to the raster.
Whole-page SSIM can hide local damage: inspect indexed local metrics and crops.

Current foreground measurements include printed text, grids and image foreground.
They explicitly report **foreground analysis; handwriting isolation uncertain**;
they must not be described as handwriting-only percentages. Original annotation
bounds, where recognized, are region evidence, not proof of exclusive handwriting.
Empty skeletons have null distances/width changes. Threshold-sensitive findings
retain all thresholds. Color/contrast are independent of binary ink measurements.

Feature loss is also reported in two separate groups:

- **PDF functions:** preserved, confirmed-change and unresolved categories for
  geometry/order, text, bookmarks, links, labels, attachments, forms, comments and
  layers. These are check counts, not a weighted quality score. Extra extracted
  trailing newlines are disclosed without claiming deleted text. Ink annotation
  editability changes are separate because flattening may be permitted.
- **Visible source features:** recognized BOOX image-stamp rectangles are mapped
  from the original PDF into the same rendered coordinates, including crop and
  quarter-turn rotation. Each region is unchanged, changed, completely blanked
  (every output RGB pixel is white), or not visible in the original. The report
  records changed pixels outside these rectangles too. No recognized regions
  means an undefined rate, not perfect preservation. This coverage does not
  isolate every image, symbol or overlapping handwriting stroke. A changed region
  is not automatically a lost feature or a change of meaning.

Known noninteractive BOOX image stamps are separated from comments. Their removal
as annotation objects is a representation change until appearance is assessed;
flattening alone never proves image loss. All prior reports remain immutable;
new metrics require a new report or a separate audit against hash-matched files.

Exact structural signatures prove equality only for supported representations.
Differences usually remain unproven; page geometry/labels, attachment bytes,
missing annotation/link counts, and text-character changes independently agreed
by pypdf and MuPDF are confirmed differences. Reordered extracted text remains
unproven. A feature-loss diagnostic is measured, never recommended. Annotation
editability loss is disclosed; there is no external conversion-manifest shortcut.

Native Firefox uses disposable headless profiles, fixed viewport/device scale,
bundled-viewer events and browser monotonic timestamps. The smoke fixture must
observe real text and annotation layers, render events, cached pages and viewer
version. Unsupported interfaces and launch failures produce explicit unavailable
measurements. Standalone PDF.js data is never substituted for native results.
Original exploratory first-render timings freeze pages 1/7/29 plus the three
slowest originals. Repetition order rotates/reverses; three runs extend to five
for noisy/close timings. Timed passes run before image analysis, after optimizers.
Fresh sessions retain OS caches; owned-tree RSS samples include geckodriver and
are not peak memory. Cached page-readiness times and draw times are distinct
operations. Different operations may have different winners.

Zoom samples first prepare the page at 200%, then measure page width, 100% and
200% in that order. They require an actual canvas/detail redraw and ready text
and annotation layers. CSS-scaled preview events cannot complete a measurement.
Two stable animation frames validate readiness; their delay is excluded from the
reported browser-event duration. Detail-only redraws may reuse finished layers.
An already-requested zoom is an unresolved no-op, not a measured redraw.

Plots appear only where native timings resolve: separate speed-versus-missing-ink,
displacement, thickness and shape flags for each operation, with preservation class
labels. Nondominated sets are reported independently by operation/axis. No combined
quality score or minimum speedup is used. No speed recommendation is established
when native timings are unavailable. Native instrumentation was verified outside
the command sandbox. Firefox can exit before WebDriver connects inside a restricted
command sandbox; use a normal terminal or an approved command outside that sandbox.
Do not disable Firefox's own sandbox to work around the command environment.

## Validation

The dated records below describe completed development checks, not live status
for a personal PDF. For an active run, inspect its progress/operation records;
for a verdict, require the completed measurements and preservation report.
Future test priorities belong in [roadmap.md](roadmap.md#ordered-delivery-checklist).

Progress/zoom verification on 2026-10-17: all 107 tests passed. The additional
checks cover early-stage log retention, heartbeat output, exclusive log creation,
CSS previews, delayed detail canvases, layer completion timestamps, no-op zooms,
timeouts and render errors. Fresh 27-page compatibility and three-page interaction
fixtures passed their PDFium, standalone PDF.js and Firefox browser checks.
The updated main runner's native Firefox smoke passed all zoom redraws.

The suite includes nine tests for separate PDF-function and source-image-region
measurements. These cover
flattening without lost appearance, complete blanking, faint retained content,
rotation/cropping, empty denominators, unresolved checks and independently
confirmed removal of searchable text. Production optimizer behavior is unchanged.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_comparison.py'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
.\.venv\Scripts\python.exe tests/run_compatibility.py fixtures --output audit-output/compat-comparison-001
.\.venv\Scripts\python.exe tests/run_compatibility.py browser --output audit-output/compat-comparison-001
```

Choose fresh fixture destinations. Keep benchmark runs separate from tests or
other heavy work. Real-note calibration and semantic approval need user-supplied
documents. No private corpus or release package is required for this work.

Development verification on 2026-10-15: all 96 tests passed, including 23 comparison
tests. Fresh compatibility fixtures passed all 27 PDFium/PDF.js page checks and
the three-page interaction fixture's structural checks. A full comparison run
accounted for all 33 profiles, retained source integrity, and analyzed all 27 pages
of both the source and usable repository default output without page-analysis
errors. A separate changed-PDF run produced and visually checked a 576-DPI overlay.
Preflight-only, malformed fake tools, failure isolation, hash mismatch, output
collisions, interruption and owned-descendant timeout cleanup were exercised.
Native Firefox smoke and the existing Firefox interaction/compatibility-browser
checks passed outside the command sandbox. The native run used Firefox 157.0.1,
bundled PDF.js 6.3.335 and geckodriver 0.37.1, and covered render/layer events,
cached revisits, zoom and direct-page fresh sessions. Fresh-session samples open
their requested page in the initial URL rather than first rendering page 1.
First-draw measurements retain native event durations even when Firefox has
prefetched the page before the navigation request; this basis is explicit in raw
observations. Zero-resolution cached timings remain unresolved.

A real-input 200% zoom check exposed detail-only redraws reusing completed text
and annotation layers. The tracker now preserves that readiness for detail
canvases while requiring new readiness for full page redraws. A regression test
covers both cases; real-input page/revisit/zoom checks passed after the fix.
Selenium's HTTP client timeout also exceeds the configured browser-script limit,
so the shorter client timeout cannot silently truncate a valid page operation.

Local generated execution checks passed for all 14 configured qpdf, MuPDF,
BOOX PDF Optimizer and mattdesl profiles. BOOX's Python prefix uses `-X utf8` to
avoid a Windows encoding failure in its final status message. These execution
checks establish readable output, not preservation or performance recommendations.
At that generated-fixture checkpoint, other competitor prerequisites and personal
PDFs had not been tested. Later local runs have their own status and evidence;
that historical statement is not a current corpus verdict. Machine paths,
installed tools, local configuration and detailed run records remain ignored.

The unmodified mattdesl lockfile pins sharp 0.35.1; npm audit reported high-severity
[libheif](https://github.com/advisories/GHSA-rgj7-g3m4-5g8c) and
[librsvg](https://github.com/advisories/GHSA-wq5f-xc86-pv6w) dependency advisories.
Its checked codec uses sharp's raw-pixel API. This is not a general security
exemption, and the upstream dependency set was not silently replaced for testing.

## Adapter references

Interfaces checked against primary upstream documentation on 2026-10-15:
[BOOX PDF](https://github.com/JingJerYen/boox-pdf-optimizer),
[qpdf](https://qpdf.readthedocs.io/en/stable/cli.html),
[MuPDF clean](https://mupdf.readthedocs.io/en/latest/tools/mutool-clean.html),
[pdfsizeopt](https://github.com/pts/pdfsizeopt),
[OCRmyPDF](https://ocrmypdf.readthedocs.io/en/stable/optimizer.html),
[mattdesl](https://github.com/mattdesl/pdf-optimizer),
[Ghostscript pdfwrite](https://ghostscript.readthedocs.io/en/master/VectorDevices.html),
[Acrobat settings](https://helpx.adobe.com/acrobat/desktop/create-documents/optimize-pdfs/pdf-optimizer-settings.html),
[BOOX Note](https://github.com/nrontsis/boox-note-optimizer).
Live local preflight still controls whether each configured profile can execute.
