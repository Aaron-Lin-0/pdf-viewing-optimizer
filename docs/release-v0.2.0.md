# PDF Optimizer v0.2.0 — 2026-10-27

This release makes viewing-copy creation safer on unusually large PDF pages and
turns the Windows workflow into a reviewed, restartable batch process. It does not
relax any preservation, appearance, navigation or performance acceptance rule.

## Highlights

- **Bounded-memory page verification.** PDFium renders above roughly 8 million
  pixels are compared in horizontal strips instead of as one full-page bitmap.
  One-row halos preserve the existing ink-edge guard at strip boundaries. The
  legacy optimizer, viewing-copy checks, high-DPI checks, print checks, detail-crop
  selection and reusable source renders all use the bounded path. Ordinary Letter
  and A4 pages remain single renders.
- **Reviewed Windows batch workflow.** The native picker accepts PDFs from multiple
  folders, displays every source/output mapping before launch and processes files
  sequentially. Each default viewing-copy run receives a dated folder containing
  its PDF, reports and checkpoint; explicit CLI/API destinations are unchanged.
- **Optimizer selection.** Reviewed runs may use the repository's adaptive pipeline,
  bundled qpdf lossless compression or PyMuPDF structural packing. The selection is
  bound to restart metadata and reports. Optional parallel generation applies only
  to independent adaptive candidates; verification, timing, browser acceptance and
  publication remain ordered.
- **Safer interruption and recovery.** Checkpoints are bound to source, maintained
  code, tools, renderers and operation settings. Concurrent-run claims, source-hash
  rechecks, time/disk budgets and exclusive publication continue to fail closed.
  Final browser acceptance and publication are always fresh.
- **More supportable setup.** The Windows package includes qpdf 12.4.2, PDF.js
  6.4.299 and the pinned canvas runtime. Startup diagnostics identify missing or
  mismatched local components. Damaged or incomplete PDFs now receive a clearer
  rejection message.
- **Bug-focused linting.** Ruff checks definite errors and likely bugs in local and
  GitHub Actions validation without imposing a new formatting style.

## Compatibility and unchanged safeguards

- Existing CLI modes and `optimize_pdf.optimize(...)` callers remain supported; new
  controls are optional.
- Originals and existing outputs/reports are never overwritten.
- Encrypted PDFs, signed PDFs and signature fields remain rejected.
- Handwriting is not simplified or rasterized. Text/OCR, images, navigation, links,
  labels, attachments, layers and protected interactive data retain the same gates.
- A reports-only `no improvement` result remains valid. Smaller output is not called
  faster without measured rendering evidence.

## Validation for this release

- Ruff passed with the repository's bug-focused rules.
- All 165 unit and regression tests passed; one Windows directory-symlink fixture
  was skipped where the environment could not create that fixture.
- The fresh 27-page generated compatibility matrix passed all-page PDFium and PDF.js
  comparisons. The three-page interaction fixture and matrix passed isolated
  Zen/Firefox checks using disposable profiles.
- Release packaging compared every ZIP and installed-skill runtime byte with the
  maintained source files.

Private PDFs and the historical private corpus were not rerun for this release.
The measurements in [validation.md](validation.md) remain dated evidence, not a
universal speed, memory or compatibility promise.

## Upgrade

- **Existing repository checkout:** pull `v0.2.0`, rerun `Setup Dependencies.cmd`,
  then use `Create Viewing Copies.cmd` or the existing CLI commands.
- **Downloaded release:** extract the ZIP into a new folder and run
  `Setup Dependencies.cmd`. Do not copy it over an older extracted release.

See [design.md](design.md) for current acceptance rules, [testing.md](testing.md) for
reproducible checks and [roadmap.md](roadmap.md) for planned work that is not part of
this release.
