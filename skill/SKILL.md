---
name: optimize-pdfs
description: "Optimize user-specified vector, bitmap/scanned and mixed PDFs, including BOOX notes, for measured viewing cost or lossless storage while preserving handwriting and navigation."
---

# Optimize PDFs

Run the bundled [optimizer](scripts/optimize_pdf.py) locally on user-specified PDFs. For faster viewing requests, use `--viewing-copy`: it selects candidates by measured rendering cost while preserving vector geometry and navigation. For storage-only requests, keep the existing default mode.

## Inputs and outputs

- Accept local paths, available PDF attachments, and local `file:///` URLs. Decode URL escapes such as `%20`; on Windows, a file URL's `System.Uri.LocalPath` gives the filesystem path. Quote paths containing spaces.
- Process the files the user names, or the PDF files in a folder they explicitly select for batch processing. If the intended files are unclear, ask for the missing paths.
- Use the user's requested output folder, or the current task's designated deliverables folder. Pass an explicit output path to the optimizer. Viewing copies use `NAME.viewing.pdf` plus `.json`/`.md` reports. Legacy default filenames are `NAME.optimized.pdf`; use a new numbered name if that path already exists, including collisions between inputs with the same basename.
- Preserve originals and existing outputs. The script refuses overwrites, encrypted PDFs, and PDFs containing signatures or signature fields.

## Run

1. Locate Python and the dependencies. In Codex desktop, prefer the Python returned by `mcp__codex_app__load_workspace_dependencies`; do not hard-code a dated runtime path. Otherwise use an available Python 3.10+ interpreter. The script needs `pypdf>=6.10`, `pypdfium2>=5.14`, and `Pillow`. If dependencies are missing, use a task-local virtual environment instead of altering an unrelated Python installation.
2. Resolve the helper relative to this skill directory, not a previous chat's output folder. Run one file at a time to limit memory use:

   ```powershell
   & $pdfPython (Join-Path $skillFolder 'scripts\optimize_pdf.py') $sourcePdf $outputPdf
   ```

   `$skillFolder` is the directory containing this `SKILL.md`. The three path variables should contain actual filesystem paths, not file URLs. The Windows drag-and-drop launcher is unnecessary when invoking the helper directly.

3. Allow verification to finish. Default, OCR-removal and inlining modes require matching page counts, rendered dimensions, and pixel hashes for every page at 144 DPI in PDFium, including annotations and form appearances. Fast handwriting permits its documented edge-shading guard; viewing-copy mode uses the stronger checks below. These renderings are checks only; the output retains its vector and image content. Keep the helper's per-page document reopening: retaining PDFium documents across this BOOX export caused severe verification slowdown.
4. In legacy modes, treat a file as successful only after exit code zero and an existing output file. Viewing-copy mode may exit successfully with reports only and status `no improvement`; retain the original in that case. If a file fails, report its error and continue with other requested files. Resolve ordinary path or dependency errors, but do not bypass a visual mismatch, signature restriction, or encryption restriction to force a result.

## Report

Link each successful optimized copy and report its original/output sizes, percentage change, page count, and verification result. For several files, use a compact table and identify any failures. If a file did not shrink, say so.

Describe the check as identical rendering at 144 DPI in PDFium, not a guarantee for every renderer, zoom level, or interactive state. Do not promise faster Firefox scrolling: unique handwriting strokes remain, and speed must be measured separately.

## Viewing-copy mode

Use an explicit output path in the current deliverables folder and run one document
at a time. Add `--viewing-copy` without other mode flags. `--parallel-candidates`
may build independent built-in candidates concurrently when extra RAM and disk
bandwidth are available; it does not parallelize documents, benchmarks or publication. Keep all bundled helper
Python files, `pdfjs_benchmark.mjs` and `browser_harness.html` beside the optimizer.
This mode additionally requires PyMuPDF >=1.28, Node.js 22.13+, pdfjs-dist 6.4.299,
@napi-rs/canvas and installed Zen/Firefox. The Windows skill includes qpdf 12.4.2
under `scripts/vendor/qpdf`; `PDF_QPDF` may select another executable. Locate available runtimes; use a task-local
dependency folder/environment when a dependency is missing. Set PDF_NODE,
PDF_NODE_MODULES or PDF_BROWSER only when selecting alternative installed tools.
Never substitute an unchecked PDF when a dependency or renderer fails.
Keep pdfjs-dist's cmaps, standard_fonts, iccs and wasm folders alongside its code;
these are required for complete font and image decoding. Do not disable parsing
errors to obtain a passing render. Bitmap data remains unchanged; no resampling
or lossy image recompression is used.

Static eligible BOOX annotations may become ordinary vector page content. Explain
that those strokes lose annotation editing in the viewing copy. OCR/search text
remains. No stroke simplification, rasterization or automatic OCR removal is used.
Unsupported appearances/flags/transparency and interactive annotations remain.
Known BOOX broken layer references are normalized only with verification and a
recorded report; other unresolved references block processing.

The program checks ordered drawing geometry, raw navigation/actions, shared-resource
safety, retained annotations, layers and private metadata. Unsafe object merging
falls back without merging. Appearance preflight restores original annotation pages
that fail PDFium/PDF.js checks. Final publication requires all-page checks in both
renderers, high-resolution/print samples and isolated-browser navigation/layer checks.
Exact passes require identical tested pixels; edge-changing passes additionally require unchanged geometry, a one-pixel original-ink edge band, peak <=96/255 and page mean <=1.5/255. Never weaken a failed guard. Browser checks use a disposable profile; do not alter
the user's live Zen profile. In sandboxed environments, a native browser may require
the platform's normal permission mechanism because it cannot launch in the sandbox.
Ordinary form controls, read-only/hidden flags, comments and internal links are
exercised; layer on/off checks cover every page. XFA and scripted interaction
cannot be accepted by this harness, so retain the original. External website/file
targets are compared without opening them. The harness uses installed PDF.js in
Zen; do not describe it as a test of every built-in viewer version or extension.

Benchmarks separate load, operator preparation, cold rendering and cached drawing;
never add overlapping phases. Use fresh sequential processes, alternating order,
three runs (five if noisy), including the densest page. A faster classification
requires at least 10% aggregate median cold-render CPU reduction and no sampled
cold/cached latency regression beyond max(10%,50ms). Smaller exact candidates may
qualify without a faster claim when CPU stays within 5% of the original.

Read the JSON/readable report before claiming success or speed. Link successful PDFs
and reports; when no candidate qualifies, report `no improvement` and leave the
original as the viewing file. State the tested renderers/resolutions, actual
navigation coverage, regressions and variability. Never describe tested equivalence
as a guarantee for all renderers, zoom levels or browser extensions.
