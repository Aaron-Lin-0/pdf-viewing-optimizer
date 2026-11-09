# Testing

This is the current verification guide. Planned adaptive-selection milestones
and their exit criteria live in the [main improvement plan](roadmap.md).
Comparison-specific dependencies, calibration and native-viewer smoke coverage
are documented in [comparison.md](comparison.md#validation).

Install `requirements-dev.txt` and the Node packages before running tests. Tests generate PDFs in temporary directories; no private notes or downloaded fixtures are required.

```powershell
python -m ruff check .
python -m unittest discover -s tests -p 'test_*.py'
```

`ruff.toml` limits linting to definite errors and likely bugs; it does not enforce a code style.

The unit/regression suite covers geometry, navigation mutations, annotation conversion, shared resources, transparency, malformed references, output collisions, interrupted writes, signatures, dependency failures, memory ownership and candidate selection. One test decodes a JPEG2000 image and checks its colored pixels, preventing two blank renderings from passing as equivalent.

The Windows batch tests additionally cover duplicate/reordered selections, complete
output-set collisions, preflight and launch rejection, source mutation, output races,
zero-launch cancellation, process-start failure, sequential failure isolation,
report/hash validation, reports-only results and stop/close boundaries. Before a
release, use `Create Viewing Copies.cmd` to select at least two fresh generated
fixtures, confirm the displayed source-to-output mappings and verify both terminal
results and adjacent report sets.

For release acceptance, extract the ZIP into a clean folder and run
`Setup Dependencies.cmd`. Verify that it creates `.venv`, installs `requirements.txt`
and the lockfile packages, that every launcher prefers `.venv`, and that missing or
old Python/Node versions stop with an actionable message. Browser installation stays
explicit; setup must not download or change a browser profile.

The P12 native smoke uses keyboard shortcuts to open the setup report and system
picker, selects the fresh matrix and interaction fixtures together, reviews every
long source/output mapping in the scrollable dialog, and starts them sequentially.
Repeat the picker from a second folder and confirm both folders remain in the single
review; canceling a later picker must retain the earlier selection.
Use Alt+G to toggle parallel candidate generation and confirm the review names the
selected generation mode before starting.
Verify one terminal result before the next launch, report/output hashes, an honest
reports-only result when appropriate, and isolated-browser acceptance for any
published copy. Resize the main and review windows to their minimum sizes and confirm
that paths wrap or scroll without hiding controls.
For progress, assert that a reviewed two-file batch begins at 0/2, advances to 1/2
after the first terminal result (including failure/skip), and reaches 2/2 only after
the second terminal result. Do not derive a fake within-file percentage from log text.

Checkpoint tests run the same generated source twice through one explicit checkpoint
and assert that completed generation, preservation and timing do not execute again.
The evidence-cache tests change every identity field, corrupt and remove completion
markers, and verify one immutable entry per exact context. Batch tests reconstruct a
terminal job only from hash-validated reports, resume an incomplete job at the same
reviewed output, and keep timeout and disk-budget results distinct. Final browser
acceptance and publication remain uncached by construction and by interrupted-write
tests.

P15 follow-up regressions additionally cover newest-first incremental xref-stream
trailer precedence (including mixed classic/stream revisions), direct signature
widgets and signed parent dictionaries, stop/close preservation of timeout and
disk-budget retry records, and Alt+S during blocked batch planning. The signature
fixture must be direct inside `/Annots`; making it an indirect writer object exercises
the separate inventory scan instead. Retry tests must confirm the saved status and
the exact output/checkpoint paths after reselection, not only the displayed skipped
state. The GUI shortcut test must invoke the root binding while setup is disabled
and then prove the original review callback occurs exactly once.

Browser destination waits may correct delayed post-navigation reflow only after the
requested target has already been observed. `/XYZ` correction uses PDF.js's viewport
conversion and is bounded to two attempts; final acceptance still requires rendered
canvas/text/annotation layers, a visible target, 100 ms stable viewport and exact
source/candidate navigation evidence. The generated interaction fixture exercises
this path with a page-2 link that changes zoom to 150%.

Verification-worker tests compare identical acceptance evidence with one and two
spawned PDFium workers, assert one source render/signature per setting across
candidates, exercise ordered persistent PDF.js results and missing-result failure,
and cover worker-count propagation plus unavailable resource detection. The generated
matrix and interaction fixtures remain the cross-renderer check. Before timing or
browser acceptance, assert the verification pools have shut down; never overlap
these phases when collecting performance evidence.
Parallel-generation tests must prove every independent base candidate is submitted
before results are collected, the reviewed worker cap reaches the child command, and
sequential mode remains the default. Packing and all acceptance phases stay ordered.

## Generated compatibility matrix

Direct optimization has a separate qualification runner. Runtime direct jobs
verify preservation in PDFium/PDF.js, without speed benchmarks. These additional
development checks cover all generated high-resolution/print pages and browser
interactions (the browser step requires normal native browser startup):

```powershell
.\.venv\Scripts\python.exe tests/run_quick_compatibility.py fixtures --output audit-output/QUICK_NEW_NAME
.\.venv\Scripts\python.exe tests/run_quick_compatibility.py render --output audit-output/QUICK_NEW_NAME
.\.venv\Scripts\python.exe tests/run_quick_compatibility.py browser --output audit-output/QUICK_NEW_NAME
```

Fixture creation requires a fresh directory; later steps refuse to replace their
reports. The quick regression suite also mutates exact stream/image/font/text
data, hit areas, annotations, bookmarks, metadata, layers, attachments, private
entries, decimal precision and shared identities. It checks all PNG row filters,
unreferenced payload preservation, warning/incremental/signature rejection,
decoder limits, source races, exclusive publication/rollback and profile recovery.
The direct regression cases prove useful inlining/cleanup survives, no benchmark
or tournament runs, packing failure retains a verified intermediate, transform
errors prevent publication and thirty seconds is not a gate. Direct dependency
tests require renderer probes but no browser or PyMuPDF. The explicit qpdf-only
lane still runs without renderers. The GUI defaults to Direct optimization,
disables thorough-only controls and rechecks setup when changing mode.
The Direct revision regression replaces the source at its original path,
inserts a page, and edits another page. Only the shifted unchanged page reuses
its 144-DPI PDFium comparison. Altering prior reports disables reuse, and every
page still receives fresh PDF.js verification.

The commands below retain the thorough-mode qualification path.

```powershell
python tests/run_compatibility.py fixtures
$env:PDF_BROWSER = 'C:\Program Files\Mozilla Firefox\firefox.exe'
python tests/run_compatibility.py browser
```

Results go into ignored `audit-output/`. Fixture generation refuses to overwrite an existing matrix. Use `--output` with another folder to repeat it. The browser step reads the fixtures in that folder and starts only disposable browser profiles.

The 27-page matrix includes RGB/grayscale/CMYK JPEG, JPEG2000, Flate RGB/grayscale/1-bit, CCITT Group 4, indexed color, ICC color, soft/stencil/color-key masks, predictors, inline images, mixed vector/raster/OCR, rotation/cropping/UserUnit, transparency, shading, tiling patterns, transformed/clipped Forms, standard/Type3/embedded TrueType/CID fonts, shared resources and a blank page. Every page has a bookmark; named targets, labels and an attachment are also present.

The thorough fixture runner also generates a four-page mixed-transparency PDF
with opaque handwriting, an unrelated transparency group, and a Form shared by
safe and inherited-alpha callers. Its batching report records changed/skipped
Forms and segment counts. All-page PDFium/PDF.js, high-resolution, print, detail
and browser checks preserve the same gates. Browser qualification probes the
source once and compares a fresh candidate trace with that invocation-local trace.

A separate three-page fixture covers internal-link zoom, comments, editable/read-only/hidden fields, checkboxes, radio buttons, choices and a layer that changes only page three. Browser assertions check actual state changes, including opening a comment and switching the layer on/off.

To exercise the complete selection and publication pipeline on the generated matrix:

```powershell
python optimize_pdf.py audit-output/matrix.pdf audit-output/matrix.viewing.pdf --viewing-copy
```

A reports-only **no improvement** outcome is valid. Never force publication when the checks reject all candidates.

## Planned approximate-handwriting procedure

The ordered implementation and testing procedure is maintained in
[roadmap P16](roadmap.md#p16-approximate-handwriting-research-and-conditional-product-option),
including the legacy transparency repair, experiment/baseline manifest, tiny-writing
and deliberately damaged controls, independently bounded point reduction, local
review, native-viewer performance gates and conditional curve/product work.
It is planned research, not an implemented option or a change to exact acceptance.
The compatibility commands above test today's exact/direct behavior; they do not
qualify a future simplifier. P16 requires its own calibrated generated fixtures and
versioned quality policy while reusing the existing function/resource checks.

Generate calibration controls in a fresh or empty directory with
`python tools/calibration_controls.py --generate-fixtures audit-output/NEW_CALIBRATION`.
The damaged controls share the reference layout. The tests check the expected
defect for each pair and require identical pixels for the zero-tolerance control.
When running `tools/experiment_manifest.py`, an optional `--exact-baseline` PDF
must have its adjacent production JSON report. The manifest checks exact
preservation evidence and both PDF hashes before accepting it as the comparator.

For recognition/retypesetting research, use
[roadmap P17](roadmap.md#p17-notes-to-latex-study-copy-research). Its generated
prose/math/diagram references, compile-success-but-wrong-content controls,
source-region review and portable `.tex` rebuild checks are separate from P16's
bounded geometry tests. Exact pixel comparisons and equal page numbers cannot
certify a reflowed study copy. No handwriting recognizer or LaTeX mode is currently
qualified by the existing compatibility commands.

## Real documents and timing

Run one document at a time. For direct mode add `--viewing-profile quick`; keep its reports with transformation counts, preservation coverage, hashes and descriptive stage elapsed times. Compare size savings against prior selected outputs. Thirty seconds is not a pass/fail criterion. The source is never rewritten.

The remaining timing guidance applies to thorough mode, whose reports also include candidate decisions and raw rendering-speed measurements.

Confirm that bundled qpdf appears as `qpdf-flate`, either measured or explicitly
rejected in the report. `PDF_QPDF` may select another executable. It receives the same
all-page and final renderer/browser checks as repository-generated candidates.

Compare medians and variability using the same machine, dependency versions, annotation settings and page samples. Do not benchmark alongside other renderers or heavy tests. Operator preparation, cold rendering, cached drawing and browser page sequences are different measurements. Avoid adding their timings or describing a smaller file as faster without a measured result.

The private corpus results are summarized in [validation.md](validation.md). Personal PDFs and raw local paths are deliberately excluded from the repository. The original corpus cannot be reconstructed from this checkout.
