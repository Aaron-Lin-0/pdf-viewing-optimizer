# Testing

This is the current verification guide. Planned adaptive-selection milestones
and their exit criteria live in the [main improvement plan](roadmap.md).
Comparison-specific dependencies, calibration and native-viewer smoke coverage
are documented in [comparison.md](comparison.md#validation).

Install `requirements-dev.txt` and the Node packages before running tests. Tests generate PDFs in temporary directories; no private notes or downloaded fixtures are required.

```powershell
python -m unittest discover -s tests -p 'test_*.py'
```

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

Verification-worker tests compare identical acceptance evidence with one and two
spawned PDFium workers, assert one source render/signature per setting across
candidates, exercise ordered persistent PDF.js results and missing-result failure,
and cover worker-count propagation plus unavailable resource detection. The generated
matrix and interaction fixtures remain the cross-renderer check. Before timing or
browser acceptance, assert the verification pools have shut down; never overlap
these phases when collecting performance evidence.

## Generated compatibility matrix

```powershell
python tests/run_compatibility.py fixtures
$env:PDF_BROWSER = 'C:\Program Files\Zen Browser\zen.exe'
python tests/run_compatibility.py browser
```

Results go into ignored `audit-output/`. Fixture generation refuses to overwrite an existing matrix. Use `--output` with another folder to repeat it. The browser step reads the fixtures in that folder and starts only disposable browser profiles.

The 27-page matrix includes RGB/grayscale/CMYK JPEG, JPEG2000, Flate RGB/grayscale/1-bit, CCITT Group 4, indexed color, ICC color, soft/stencil/color-key masks, predictors, inline images, mixed vector/raster/OCR, rotation/cropping/UserUnit, transparency, shading, tiling patterns, transformed/clipped Forms, standard/Type3/embedded TrueType/CID fonts, shared resources and a blank page. Every page has a bookmark; named targets, labels and an attachment are also present.

A separate three-page fixture covers internal-link zoom, comments, editable/read-only/hidden fields, checkboxes, radio buttons, choices and a layer that changes only page three. Browser assertions check actual state changes, including opening a comment and switching the layer on/off.

To exercise the complete selection and publication pipeline on the generated matrix:

```powershell
python optimize_pdf.py audit-output/matrix.pdf audit-output/matrix.viewing.pdf --viewing-copy
```

A reports-only **no improvement** outcome is valid. Never force publication when the checks reject all candidates.

## Real documents and timing

Run one document at a time with `--viewing-copy`. Keep the `.md` and `.json` reports; they include source/output hashes, candidate decisions, preservation results and raw timing runs. The source is never rewritten.

When qpdf is installed or selected with `PDF_QPDF`, confirm that `qpdf-flate` appears
as a measured candidate or an explicit rejection in the report. It receives the same
all-page and final renderer/browser checks as repository-generated candidates.

Compare medians and variability using the same machine, dependency versions, annotation settings and page samples. Do not benchmark alongside other renderers or heavy tests. Operator preparation, cold rendering, cached drawing and browser page sequences are different measurements. Avoid adding their timings or describing a smaller file as faster without a measured result.

The private corpus results are summarized in [validation.md](validation.md). Personal PDFs and raw local paths are deliberately excluded from the repository. The original corpus cannot be reconstructed from this checkout.
