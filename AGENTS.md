# PDF viewing optimizer

## Project and source of truth

This repository creates verified viewing copies of vector, bitmap/scanned and mixed PDFs. Reducing rendering work is the priority; file size is secondary. Read `docs/design.md` before changing PDF transformations or acceptance rules. `docs/validation.md` records measured results and limits, not promises for every PDF.

**Main improvement plan: [docs/roadmap.md](docs/roadmap.md).** Start future
improvement work there and maintain its ordered milestones. It is the primary
roadmap for adaptive optimizer selection; the old comparison handoff is historical
requirements context, not an active task list. Planned features are not current
behavior: `docs/design.md` still defines production acceptance, and
`docs/comparison.md` describes the existing research runner.

Private assessment snapshots and run progress belong under ignored `audit-output/`.
Read a run's actual status, completed records and hashes before making claims;
partial timings or readable outputs do not establish preservation or a winner.
Do not copy live run status into the roadmap or change executable files during
an active comparison. Documentation-only maintenance may proceed.

Maintain the implementation in the repository root:

- `optimize_pdf.py`: public CLI, legacy Python API, input checks and publication.
- `batch_workflow.py`: reviewed batch planning, source identity and sequential child lifecycle.
- `viewing_copy_gui.py`: thin Windows multi-file picker and lifecycle display.
- `viewing_copy.py`: candidate generation, preservation checks and performance selection.
- `handwriting.py`, `annotation_ink.py`, `invisible_text.py`: drawing transformations and explicit OCR removal.
- `navigation.py`, `geometry.py`: preservation signatures.
- `pdfjs_benchmark.mjs`, `browser_check.py`, `browser_harness.html`: rendering measurements and browser checks.
- `tools/compare_optimizers.py`, `tools/firefox_measure.py`, `tools/comparison_metrics.py`, `tools/feature_loss.py`: separate comparison execution, native-viewer measurements and descriptive evidence.
- `tests/`: regression tests and generated compatibility fixtures.
- `tools/build_release.py`: ZIP and installed-skill packaging from the maintained source.

Do not edit generated copies in `dist/` or an installed skill as an independent implementation. Keep personal PDFs, local reports, credentials and machine-specific paths out of commits. The private corpus is not included in this checkout.

## Development environment

Use Python 3.10+ and Node.js 22.13+. Windows with Zen is the tested platform. From the repository root, create `.venv`, install `requirements-dev.txt`, and run `npm ci`; see `README.md` for commands. Use `.venv/Scripts/python.exe` on Windows or `.venv/bin/python` on POSIX, rather than changing global Python packages.

The Node dependencies belong in the root `node_modules/`. The Windows release includes the pinned qpdf runtime under `vendor/qpdf/`; `PDF_QPDF` may select another executable. `PDF_NODE`, `PDF_NODE_MODULES` and `PDF_BROWSER` can select installed runtimes; do not hard-code a developer's absolute paths. Browser checks use disposable profiles, never the user's everyday profile. Activation is optional when invoking the virtual-environment interpreter directly.

## Preservation requirements

- Never overwrite originals or existing outputs/reports. Keep encrypted/signed-input rejection, exclusive publication and temporary-file cleanup intact.
- Preserve handwriting coordinates, widths, curves, dots, loops, order, transforms, clipping and layers. No stroke simplification or rasterization in viewing-copy mode.
- Retain visible text, OCR/search text, image data, bookmarks/table of contents, link targets and hit areas, page labels, attachments and protected interactive data.
- OCR removal remains a separate explicit option. Annotation conversion must pass the eligibility and ordered-prefix safeguards; converted handwriting loses annotation editability only in the viewing copy.
- Copy shared resources before modifying them. Unsupported or unresolved content must not silently lose data.
- Do not relax geometry, navigation, rendering or performance checks to make a candidate pass. A reports-only `no improvement` result is valid.
- Preserve existing CLI modes and `optimize_pdf.optimize(...)` callers. Compare rendering cost independently of storage savings and report regressions honestly.

## Checks and delivery

Run focused regression tests for the changed behavior, then the suite when changing executable code:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
```

For renderer, resource or interaction changes, follow `docs/testing.md` for generated fixtures, all-page comparisons and browser checks. Fixtures refuse overwrites; choose a fresh ignored output directory when repeating them. Use only user-authorized real PDFs, one document at a time. Release readers/renderers between passes and do not benchmark alongside other heavy work.

Package with `.\.venv\Scripts\python.exe tools/build_release.py --output dist/NEW_NAME`, using a destination that does not exist. The builder checks ZIP and skill bytes against the same maintained files. Keep README mode descriptions, tests, launchers and packaging aligned with any interface change. Changes limited to documentation or project setup do not require rerunning the private corpus or publishing another release.
