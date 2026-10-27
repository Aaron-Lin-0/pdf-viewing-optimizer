# PDF viewing optimizer

Create a separate PDF viewing copy while preserving handwriting geometry, search text, bookmarks and other navigation. The optimizer compares rendering cost as well as file size and publishes a copy only after its checks pass.

Supports vector, bitmap/scanned and mixed PDFs. BOOX handwriting has dedicated optimizations; unfamiliar drawing structures receive conservative storage optimizations. Smaller files are not necessarily faster to render.

Original files and existing output/report files are never overwritten. No personal PDFs are included in this repository; tests generate their own fixtures.

## Documentation and main improvement plan

**The main improvement plan is [adaptive optimizer selection](docs/roadmap.md).**
It defines the next iteration and its ordered milestones. It is a roadmap, not a
claim that automatic selection across external tools is already available.

| Document | Purpose |
|---|---|
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

Use Python 3.10+, Node.js 22.13+ and an installed Zen or Firefox browser. Windows with Zen is the tested environment.

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
$env:PDF_BROWSER = 'C:\Program Files\Zen Browser\zen.exe'
```

Keep the complete `pdfjs-dist` package, including its font, CMap, ICC and WASM resources. The renderer needs these to decode some PDFs correctly. `PDF_NODE`, `PDF_NODE_MODULES` and `PDF_BROWSER` can select alternate installed runtimes. Without local Node dependencies, the scripts can reuse Codex's bundled runtime. The Windows release uses its bundled qpdf 12.4.2 for a lossless Flate/object-stream candidate; set `PDF_QPDF` to select another executable.

## Create a viewing copy

On Windows, open **Create Viewing Copies.cmd** and select one or more PDFs. After each
picker, choose whether to add PDFs from another folder; canceling a later picker keeps
the files already selected. Leave the window open while they are processed sequentially. Review each exact source-to-output
mapping in the confirmation dialog before starting. Duplicate selections collapse
to one job. Use **Stop after current** to keep the active result and skip the queue. Accepted copies and all
reports are saved beside each source as `NAME.optimized.*`; existing names receive
`-2`, `-3`, and so on. A failed or unchanged file does not stop the remaining files.
Interrupted jobs resume only exact completed candidate-generation, preservation and
timing stages from the adjacent `.checkpoint` directory. Final browser acceptance
and publication always run again. The window shows the current stage and its age;
each file has a six-hour and 10-GiB working-data limit in the GUI.
The app checks required local components before selection; **Check setup** repeats
that check. Keyboard shortcuts are Alt+S for setup, Alt+P to choose PDFs, Alt+G to
toggle parallel generation, Alt+T to stop after the current file, and Ctrl+L to focus the activity log. Long mappings are
shown in a resizable, scrollable review window before anything starts.
Choose **Verification workers** before review (default: two, or one on a one-CPU
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

The output is `notes.viewing.pdf`, with `.md` and `.json` reports beside it. You can provide an explicit output path as the second argument. If no candidate qualifies, the command saves reports with status **no improvement** and does not publish a replacement PDF.
CLI callers may set `--verification-workers N`, `--parallel-candidates`,
`--time-budget-seconds`, `--disk-budget-bytes`, and an adjacent
`--checkpoint` directory. Omitting the budget options retains the existing unlimited
single-file behavior.

For drag and drop, set `PDF_PYTHON` to the installed interpreter, then drop one PDF onto **Optimize Viewing PDF.cmd**. Keep the launcher and program files together.

Viewing-copy mode:

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

## Viewing-copy verification

Viewing-copy candidates are checked against raw navigation structures and drawing/resource signatures, then rendered at 144 DPI in PDFium. Source signatures and lossless source renders are reused within the invocation; page comparisons use the selected spawned workers and remain fail-closed. Final candidates also undergo all-page PDF.js comparison with persistent verification-only workers, high-resolution and print samples, and browser interaction checks. All verification workers stop before fresh sequential benchmarks, browser checks, and publication. Exact transformations require identical tested pixels. Edge-changing transformations must preserve geometry and meet a strict one-pixel edge-band limit. The legacy modes have the different checks described above and in the mode reference.

The benchmark measures preparation, cold rendering and cached drawing separately in fresh processes. Operator preparation is recorded once per page/version; the selection-driving render pass retains three alternating runs and extends to five when noisy. A **faster** classification requires at least 10% less aggregate median cold-render CPU and no significant sampled latency regression. A smaller exact copy can qualify without meeting the faster threshold, provided CPU stays within 5% of the original. Reports include individual runs, variability and rejected candidates.

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

Choose a release directory that does not already exist. The repository root is the maintained implementation. The packaging script copies those same files into a ZIP and a Codex skill, then compares their bytes. Generated releases go under `dist/` and are not committed. No duplicate implementation is maintained inside the skill source directory. `.venv/`, `node_modules/` and local test results are also excluded from Git. Codex uses your existing model and permission settings; this project does not override them.

Tests cannot guarantee identical behavior in every viewer, zoom level or interactive state. Scripted/XFA forms are outside the viewing-copy acceptance harness, and external action targets are compared without opening them. Keep originals as the reference and editable copies.
