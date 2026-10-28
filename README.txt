PDF optimizer

Extract the ZIP and keep its scripts and launchers together. Originals and
existing output/report files are never overwritten. Encrypted PDFs and PDFs
with signatures or signature fields are rejected.

Supports vector, bitmap/scanned, and mixed pages. Image data is preserved without
resampling or recompression to a lossy format. Drawing-call optimizations target
recognized BOOX structures; other PDFs can benefit from verified object/storage
compression, but a smaller file is not necessarily faster to draw.

Viewing copy (recommended for measured rendering cost)

After extracting the ZIP, open Setup Dependencies.cmd once. It creates a private
.venv and installs the pinned Python and PDF.js packages. Python 3.10+, Node.js
22.13+ and Zen/Firefox must already be installed; missing components are explained
without changing any PDF. All launchers prefer the private environment afterward.
In a Git checkout, Update PDF Viewing Optimizer.cmd performs a clean fast-forward
pull and reruns setup. It stops when local changes exist. Extracted releases must be
updated by installing a newer release into a separate folder.

On Windows, open Create Viewing Copies.cmd and select one or more PDFs. After each
picker, choose whether to add PDFs from another folder; canceling a later picker
keeps the current selection. Files run one at a time after you review and confirm
each source-to-output mapping. Duplicate
selections collapse to one job. The Optimizer selector defaults to the repository's
PDF Viewing Optimizer (recommended). qpdf-only and PyMuPDF-only runs remain available
and use the same preservation, timing and publication gates. Stop after current preserves the active result and
skips queued files. Each run keeps its accepted copy, reports and checkpoint in one
adjacent NAME.optimized-YYYY-MM-DD folder. Same-day repeats use -2 and higher rather
than overwriting, so loose artifacts do not accumulate beside the original.
Failures and no-improvement results do not stop later files.
The reviewed source hash is rechecked at child startup and before publication.
Interrupted jobs reuse only exact completed generation, preservation and timing
stages from the checkpoint inside that run folder. Final browser acceptance and publication
always run again. The GUI shows stage age and applies a six-hour/10-GiB limit per file.
It checks required components before selection. Keyboard shortcuts: Alt+S setup,
Alt+O optimizer, Alt+P choose PDFs, Alt+G parallel generation, Alt+T stop after current, Ctrl+L activity log. Long paths remain
available in a resizable, scrollable review window.
The progress bar advances by completed reviewed files; the status line shows the
active file and stage without inventing a percentage for variable-length checks.
Choose 1 through the detected logical CPU count for verification before review.
The default is two (or one on a one-CPU system). Startup and review show total and
available RAM plus a conservative 4 GiB + 0.5 GiB/worker planning estimate. Workers
may use multiple CPU threads; the selection neither reserves nor caps CPU cores.
Parallel candidate generation is optional and off by default. When enabled, independent
built-in candidates use at most the reviewed worker count; packing, verification,
benchmarks, documents and publication remain ordered.

Drag one original PDF onto Optimize Viewing PDF.cmd, or run:
  python optimize_pdf.py "input.pdf" --viewing-copy

The default creates NAME.optimized-YYYY-MM-DD\NAME.optimized.pdf plus reports and
checkpoint data. Supplying an explicit output path retains caller-controlled placement.

Use --verification-workers N to select page-verification processes and
--parallel-candidates to concurrently build independent built-in candidates. CLI-only limits
are available as --time-budget-seconds and --disk-budget-bytes;
--checkpoint selects a checkpoint directory beside the chosen output.
Use --viewing-optimizer adaptive, qpdf or pymupdf to select the engine; parallel
candidate generation applies only to adaptive.

Creates readable .md and machine-readable .json reports beside the chosen output.
If no candidate meets the preservation/performance rules, only reports are saved.
OCR/search text, navigation, attachments, comments and form fields are retained.
Static BOOX ink annotations may become vector page drawings; those strokes lose
individual annotation editing in the viewing copy. Keep the original for editing.
No rasterization, stroke simplification or automatic OCR removal is performed.

The program recognizes each page's structure, rather than the filename. It tests
static BOOX appearances, shared drawing containers and same-width segment batching.
It preserves literal paths, stroke coordinates, clipping, transforms, pen states,
layer membership and paint order. Transformed and unfamiliar static appearance bodies keep their original Form calls.
Viewing-copy batching conservatively skips documents with transparency because
inherited masks can make separate paint operations significant; exact inlining
and storage candidates remain available.
Unsupported appearances, transparency groups,
state-dependent appearances, special display flags and protected annotations stay
unchanged. Conversion stops at a retained painted annotation to preserve overlaps.
Pages that fail the PDFium or PDF.js appearance preflight keep original annotations.
Only verified known BOOX dangling layer references may normalize to absent layers;
other unresolved references block processing. Normalizations are recorded.

Raw navigation comparisons include bookmark hierarchy, destinations/zoom/styles,
named destinations, page labels, link hit areas and chained URI/file actions,
opening actions, viewer preferences, embedded attachments, retained annotations,
layers and catalog/private metadata. Comparisons tolerate renumbered PDF objects,
but reject merges of independently interactive identities. Unsafe deduplication
falls back to the unmerged candidate. Shared resources are copied before editing.

Every candidate must pass PDFium on every page at 144 DPI. Final candidates must
also pass PDF.js on every page at a 1404-pixel viewing width, sampled 288 DPI and
576 DPI crops, sampled print rendering, and navigation/layer checks in an isolated
headless Zen/Firefox profile. Your browser profile/preferences are never changed.
Browser checks exercise ordinary text fields, read-only/hidden fields, checkboxes,
radio buttons, choices, comments, links, and each layer on every page.
Scripted/XFA form behavior is unsupported by the viewing-copy acceptance harness;
those documents retain their originals. External links/file actions are compared
without opening their destinations.
Exact transformations require identical pixels. Edge-changing passes require
unchanged geometry, differences only in a one-pixel band around the original ink
mask (gray threshold 250), peak channel difference <=96 and page mean <=1.5/255.
These strict bounds can reject copies that look indistinguishable to a person.
Within one run, lossless source renders and source geometry are reused across
candidates. Selected spawned workers compare PDFium pages; verification-only
PDF.js workers retain both documents across assigned pages. Workers stop before
the unchanged sequential fresh-process benchmarks, browser checks and publication.

Benchmarks use sequential fresh processes and alternating file order. Render timing
uses three runs per page/version, extended to five for noisy CPU results; operator
preparation is recorded once because it does not select the winner. Samples include
page 1 plus the three highest-density non-first pages. Operator preparation, cold rendering (without prior operator preparation), and
cached drawing on a fresh canvas are separate measurements. Do not sum them.
Reports include wall/CPU time, variability, sampled RSS, operations, annotations
and sizes; browser forward/back rendering and layer construction are separate.
A faster result requires >=10% aggregate median cold-render CPU reduction and no
sampled cold/cached latency regression beyond max(10%,50ms). Within 5% CPU, prefer
exact appearance then smaller size. A smaller exact candidate may qualify without
a faster classification, provided CPU does not regress >5%. Size growth requires
measured speed improvement. Failed final checks fall back to verified candidates.

Other modes (preserved for existing callers)

  python optimize_pdf.py "input.pdf" "output.optimized.pdf"
  python optimize_pdf.py "input.pdf" "output.fast-vector.pdf" --fast-handwriting
  python optimize_pdf.py "input.pdf" "output.inlined.pdf" --inline-handwriting
  python optimize_pdf.py "input.pdf" "output.no-ocr.pdf" --remove-invisible-text
  python optimize_pdf.py "input.pdf" "output.compact.pdf" --compact-structure

Matching drag-and-drop launchers are included. Default deduplication, inlining and
OCR-removal modes require pixel-identical PDFium 144 DPI page renders. Fast mode
retains exact segments but permits edge shading differences with its legacy
peak/mean guard; it does not have viewing-copy mode's cross-renderer/performance
gates. OCR removal is explicit and loses search/copy from verified invisible text
blocks. Visible or unrecognized text remains. Structure packing is optional with
legacy modes; viewing-copy mode selects its own passes and cannot combine flags.

Dependencies (Python 3.10+, Node.js 22.13+, PDF.js 6.4.299)

  python -m pip install "pypdf>=6.10" "pypdfium2>=5.14" Pillow "pymupdf>=1.28"
  npm install pdfjs-dist@6.4.299 @napi-rs/canvas@1.0.13

Legacy non-packing modes do not need PyMuPDF/Node/browser. Viewing-copy mode needs
all dependencies and installed Zen/Firefox. Set PDF_PYTHON to select the launcher's Python interpreter. Otherwise it tries
the current user's bundled Codex runtime, then Python on PATH. Install the
dependencies in the interpreter you choose.
Use PDF_NODE, PDF_NODE_MODULES or PDF_BROWSER to select other installed runtimes.
The Windows package includes qpdf 12.4.2 and uses it for a lossless
Flate/object-stream candidate. Set PDF_QPDF to select another qpdf executable.
Missing dependencies produce an error and never publish an unchecked PDF.
Keep the complete pdfjs-dist package: its cmaps, standard_fonts, iccs and wasm
directories are needed for image/font decoding. The harness stops on parsing
errors instead of accepting a comparison of two incomplete/blank renderings.

See docs/validation.md for the latest compatibility audit and measurements.
See docs/testing.md for the reproducible generated fixtures and test commands.
Historical benchmark files used different
measurement/cache behavior; do not compare their drawing timings to the new harness.
No tested sample proves behavior in every renderer, zoom, print setting or browser
extension. Originals remain the reference copies.
