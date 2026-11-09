# PDF viewing optimizer

Create a separate PDF viewing copy while preserving handwriting geometry, search text, bookmarks and other navigation. The default Windows workflow keeps useful transformations without exhaustive optimizer comparisons or speed benchmarks, and publishes only after preservation checks pass. Thorough mode also compares rendering cost.

Supports vector, bitmap/scanned and mixed PDFs. BOOX handwriting has dedicated optimizations; unfamiliar drawing structures receive conservative storage optimizations. Smaller files are not necessarily faster to render.

Original files and existing output/report files are never overwritten. No personal PDFs are included in this repository; tests generate their own fixtures.

## Documentation and main improvement plan

**The main improvement plan is [adaptive optimizer selection](docs/roadmap.md).**
It defines the next iteration and its ordered milestones. It is a roadmap, not a
claim that automatic selection across external tools is already available.

| Document | Purpose |
|---|---|
| [v0.2.0 release notes](docs/release-v0.2.0.md) | Safer large-page verification, reviewed Windows workflow and upgrade notes |
| [Main roadmap](docs/roadmap.md) | Primary future improvement plan and decision flow |
| [Batch workflow ADR](docs/adr-batch-workflow.md) | Implemented GUI/controller boundary and safety invariants |
| [Contributor instructions](AGENTS.md) | Source map, preservation constraints and delivery rules |
| [Design](docs/design.md) | Current production transformations and acceptance rules |
| [Comparison guide](docs/comparison.md) | Existing research runner, setup, measurements and limitations |
| [Testing](docs/testing.md) | Current verification commands and generated fixtures |
| [Validation](docs/validation.md) | Dated historical production audit; not live run status |
| [Original comparison contract](docs/optimizer-comparison-handoff.md) | Historical requirements reference; not the active work queue |
| [Mode reference](README.txt) | Packaged CLI usage |

Local run reports and in-progress results stay in ignored `audit-output/`. A
partial assessment is evidence for a plan, not a completed optimization verdict.

## Install

Use Python 3.10+ and Node.js 22.13+. Thorough mode also requires Firefox or Zen. Firefox is the default; Zen remains supported through `PDF_BROWSER`.

For the packaged Windows version, extract the ZIP and open **Setup Dependencies.cmd**
once. It creates a private `.venv`, installs the pinned Python packages, and runs
`npm ci` for the pinned PDF.js packages. It does not silently install Python,
Node.js, or a browser; if one is missing, it shows the official download location
and leaves PDFs untouched. The release includes qpdf 12.4.2 and its license; setup
checks those bundled files. The launchers automatically use the private environment.

When running from a Git checkout, **Update PDF Viewing Optimizer.cmd** performs only
a clean fast-forward pull, then reruns setup. It refuses to update a checkout with
local tracked or untracked changes. Extracted release copies do not self-update;
install a newer release into a separate folder.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm ci
$env:PDF_BROWSER = 'C:\Program Files\Mozilla Firefox\firefox.exe'
```

Keep the complete `pdfjs-dist` package, including its font, CMap, ICC and WASM resources. The renderer needs these to decode some PDFs correctly. `PDF_NODE`, `PDF_NODE_MODULES` and `PDF_BROWSER` can select alternate installed runtimes. Without local Node dependencies, the scripts can reuse Codex's bundled runtime. The Windows release uses its bundled qpdf 12.4.2 for a lossless Flate/object-stream candidate; set `PDF_QPDF` to select another executable.

## Create a viewing copy

On Windows, open **Create Viewing Copies.cmd** and select one or more PDFs. After each
picker, choose whether to add PDFs from another folder; canceling a later picker keeps
the files already selected. Leave the window open while they are processed sequentially. Review each exact source-to-output
mapping in the confirmation dialog before starting. Duplicate selections collapse
to one job. The **Mode** selector defaults to **Direct optimization (no benchmarks)**:
exact supported handwriting inlining, safe deduplication, current-document cleanup
and one lossless packing step. It retains useful transformations without a candidate
tournament or rendering-speed benchmarks. A smaller copy must pass geometry,
navigation, exact all-page PDFium/PDF.js and high-resolution/print/detail checks.
Live annotations remain editable; unreachable editor/recovery data may be omitted
from the separate viewing copy. Originals remain intact. Batch review shows
preflight rejections before processing, including the Thorough option for PDFs
with incremental revisions. Those rejections produce no report. Jobs that reach
verification write reports for unresolved checks, and verification failures count
as failures in the batch summary.
Choose **Thorough optimization** for the existing candidate comparison, rendering
benchmarks and visual/browser acceptance. Its **Optimizer** selector supports the
adaptive pipeline, qpdf or PyMuPDF, all under the original thorough gates. Use **Stop
after current** to keep the active result and skip the queue.
Each run normally keeps its copy, reports and checkpoint together in an adjacent
`NAME.optimized-YYYY-MM-DD[-N]` folder. If Windows path limits require shorter
names, the copy, reports and checkpoint share a compact name beside the source.
Review flags paths that still need a shorter source folder. A failed or unchanged
file does not stop the remaining files.
Thorough interrupted jobs resume only exact completed candidate-generation, preservation and
timing stages from the run folder's `.checkpoint` directory. Final browser acceptance
and publication always run again. Quick interrupted jobs restart their single pass;
completed results are recovered only after report, context and hash validation.
When you replace an export with a revised PDF at the same path, Direct starts a
new output and looks for recent checkpoints beside the source. It can reuse the
144-DPI PDFium comparison for an unchanged, self-contained page, even if the
page moved. The report counts the comparisons reused. Keep the prior run folder
to enable this on the next revision. Earlier runs without page evidence start
with a fresh verification pass.
Profiles never reuse each other's results. The window shows the current stage and its age;
each file has a six-hour and 10-GiB working-data limit in the GUI.
The app checks the selected profile's local components before selection; quick mode
requires pypdf 6.10.0, pypdfium2 5.14.0, Pillow 12.3.0, Node/PDF.js 6.4.299
and bundled qpdf 12.4.2. It does not require PyMuPDF or a browser.
For a quick-only installation, run `Setup Dependencies.cmd --quick` in a terminal;
running setup without that argument installs the full thorough dependencies.
**Check setup** repeats that check. Keyboard shortcuts are Alt+M for mode, Alt+S for setup, Alt+O for the optimizer, Alt+P to choose PDFs, Alt+I to inspect drawing opportunities, Alt+G to
toggle parallel generation, Alt+T to stop after the current file, and Ctrl+L to focus the activity log. Long mappings are
shown in a resizable, scrollable review window before anything starts.
In thorough mode, choose **Verification workers** before review (default: two, or one on a one-CPU
system). Startup and review show logical CPUs, total/available RAM, and the
conservative planning estimate of 4 GiB plus 0.5 GiB per worker. This is not a
measured peak or limit, and renderer workers may use multiple CPU threads.
Enable **Parallel candidate generation** to build independent built-in candidates
concurrently, capped by the reviewed worker count. It can reduce generation time on
large files but raises simultaneous RAM and disk traffic; dependent packing,
verification, timing, documents and publication remain ordered.
The determinate progress bar reports completed reviewed files—such as “1 of 2 files
complete”—while the status line separately identifies the active file and stage.

For the command line:

```powershell
.\.venv\Scripts\python.exe optimize_pdf.py 'notes.pdf' --viewing-copy
```

Existing CLI and Python callers keep thorough behavior by default. For quick copies:

```powershell
.\.venv\Scripts\python.exe optimize_pdf.py 'notes.pdf' --viewing-copy --viewing-profile quick
```

Thirty seconds for a short PDF is an aspirational target, not a publication gate
or a reason to remove useful transformations. Direct mode aims to retain comparable
storage savings while removing exhaustive comparisons and benchmarks. Processing
time still depends on document complexity and preservation checks; it does not
measure or guarantee faster viewing. Native-browser interaction qualification is
still pending in this environment; direct jobs do not launch a browser.

Incremental revision histories, encryption/signatures, XFA, parser/tool warnings
and unresolved preservation prevent publication. Direct mode checks the current
document; unreachable editor/recovery objects may be omitted. To retain all
unreferenced objects too, explicitly use `--viewing-optimizer qpdf` with the quick
profile. That narrower compression-only lane uses strict graph/stream equivalence
and no page rendering, so it may save substantially less space. Its input/output
limit is 512 MiB, with bounded object/decoder work. Both lanes retain originals
and produce reports only when preservation is unresolved or no smaller copy results.

The default run folder is `notes.optimized-YYYY-MM-DD`, containing
`notes.optimized.pdf`, `.md`/`.json` reports and the checkpoint. A same-day repeat
uses `-2`, `-3`, and so on without overwriting. You can provide an explicit output
path as the second argument to retain caller-controlled placement. If no candidate
qualifies, the run folder contains the reports and checkpoint but no replacement PDF.
CLI callers may set `--verification-workers N`, `--parallel-candidates`,
`--time-budget-seconds`, `--disk-budget-bytes`, and a same-folder
`--checkpoint` directory. Omitting the budget options retains the existing unlimited
single-file behavior.
Use `--viewing-optimizer adaptive|qpdf|pymupdf` to make the same choice from the
command line. Parallel candidate generation applies only to `adaptive`.
These worker controls apply to thorough mode. Direct mode uses its single adaptive
chain with up to two verification workers and rejects parallel candidates, explicit
worker counts or PyMuPDF. An explicit qpdf choice selects compression-only mode.

For drag and drop, set `PDF_PYTHON` to the installed interpreter, then drop one PDF onto **Optimize Viewing PDF.cmd** for a quick lossless copy. Keep the launcher and program files together.

Thorough viewing-copy mode:

- Preserves stroke coordinates, widths, curves, dots and loops. It does not simplify or rasterize handwriting.
- Retains visible text, OCR/search text, image data, bookmarks, named destinations, links, attachments and protected interactive objects.
- May convert eligible static BOOX handwriting annotations to ordinary vector content. Those strokes are no longer individually editable as annotations in the viewing copy.
- Rejects encrypted or signed inputs, including empty signature fields.
- Uses a disposable browser profile. It does not change your browser preferences.

Run one document at a time. Large BOOX documents can take substantial time and memory because every candidate is checked before publication.

## Other modes

```powershell
python optimize_pdf.py input.pdf output.optimized.pdf
python optimize_pdf.py input.pdf output.inlined.pdf --inline-handwriting
python optimize_pdf.py input.pdf output.fast-vector.pdf --fast-handwriting
python optimize_pdf.py input.pdf output.no-ocr.pdf --remove-invisible-text
python optimize_pdf.py input.pdf output.compact.pdf --compact-structure
```

Default and inlining modes require identical tested pixels. `--fast-handwriting` permits its legacy edge-shading tolerance and has weaker acceptance checks than viewing-copy mode. `--remove-invisible-text` explicitly discards verified invisible OCR blocks, losing their search/copy text. It is never enabled automatically. Existing Python callers can continue to use `optimize_pdf.optimize(...)`.

The batch controller snapshots each reviewed source hash and passes it through the
additive `--expected-source-sha256` viewing-copy option. The optimizer checks that
identity at startup and again before publishing any copy or report. Existing CLI
and Python callers that omit the constraint keep their current behavior.
Checkpoint entries bind the source, candidate bytes, maintained code, tool and
renderer versions, and stage context. Incomplete or corrupt entries are ignored.

## Thorough viewing-copy verification

Viewing-copy candidates are checked against raw navigation structures and drawing/resource signatures, then rendered at 144 DPI in PDFium. Source signatures and lossless source renders are reused within the invocation; page comparisons use the selected spawned workers and remain fail-closed. Final candidates also undergo all-page PDF.js comparison with persistent verification-only workers, high-resolution and print samples, and browser interaction checks. All verification workers stop before fresh sequential benchmarks, browser checks, and publication. Exact transformations require identical tested pixels. Edge-changing transformations must preserve geometry and meet a strict one-pixel edge-band limit. The legacy modes have the different checks described above and in the mode reference.

The benchmark measures preparation, cold rendering and cached drawing separately in fresh processes. Operator preparation is recorded once per page/version; the selection-driving render pass retains three alternating runs and extends to five when noisy. A **faster** classification requires at least 10% less aggregate median cold-render CPU and no significant sampled latency regression. A smaller exact copy can qualify without any speedup, provided CPU stays within 5% of the original and sampled latency checks pass. It is published as a **storage-only** optimized PDF after all preservation and final acceptance checks pass. Reports include individual runs, variability and rejected candidates.

These production selection measurements use the installed PDF.js harness. The
separate comparison runner measures Firefox's bundled viewer; its findings do
not replace production acceptance rules.

See [design and acceptance rules](docs/design.md), [test instructions](docs/testing.md), [validation results and limitations](docs/validation.md), and the [complete mode reference](README.txt).

## Develop and package

For local comparisons with other optimizers, see [comparison setup and limitations](docs/comparison.md).
The separate comparison CLI records preservation, foreground changes and native
Firefox timings (or explicit instrumentation failure), without changing production
acceptance rules. Start with `comparison-tools.example.json` and a fresh ignored output folder.

For Codex, add this Git checkout as a local project named **PDF Viewing Optimizer**. Select the folder containing `optimize_pdf.py` and `AGENTS.md` as the primary folder. Project instructions, preservation requirements and the source map are in [AGENTS.md](AGENTS.md). See the [Codex project guide](https://learn.chatgpt.com/docs/projects?surface=app) for the folder controls.

Set up an isolated development environment from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
npm ci
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
.\.venv\Scripts\python.exe tools/build_release.py --output dist/RELEASE_NAME
```

Install `requirements-comparison.txt` in the same environment to include the calibration tests.

Choose a release directory that does not already exist. The repository root is the maintained implementation. The packaging script copies those same files into a ZIP and a Codex skill, then compares their bytes. Generated releases go under `dist/` and are not committed. No duplicate implementation is maintained inside the skill source directory. `.venv/`, `node_modules/` and local test results are also excluded from Git. Codex uses your existing model and permission settings; this project does not override them.

Tests cannot guarantee identical behavior in every viewer, zoom level or interactive state. Scripted/XFA forms are outside the viewing-copy acceptance harness, and external action targets are compared without opening them. Keep originals as the reference and editable copies.
