# Design and acceptance rules

**Scope: current production behavior.** This document defines the existing
optimizer's transformations and publication gates. The
[main improvement plan](roadmap.md) describes future adaptive tool selection;
those proposals do not change these rules. The separate research runner and its
native Firefox protocol are documented in [comparison.md](comparison.md).

## Direct profile (CLI token: quick)

The GUI and drag-and-drop launcher default to **Direct optimization (no benchmarks)**.
Existing CLI/API callers retain thorough behavior unless they select
`--viewing-profile quick` / `viewing_profile='quick'`. The objective is to retain
useful transformations and storage savings without exhaustive comparisons or speed
benchmarks. Thirty seconds is an aspiration, not an acceptance gate.

With the default adaptive engine, `quick_lossless.py` runs one chain: exact supported
handwriting inlining, safe resource deduplication, current-document cleanup and one
qpdf packing step. No eligible handwriting means deduplication/cleanup still run;
unexpected transform errors prevent publication. The chain does not flatten live
annotations, batch/simplify strokes, rasterize handwriting, remove OCR or downsample
images. Unreachable editor/recovery objects may be omitted from this separate
viewing copy; the original remains intact. Reachable private data, interactive
identities and navigation retain the established preservation checks.

The transformed intermediate is packed with qpdf 12.4.2, preserving existing stream
encodings and unreferenced data in that intermediate. Strict graph/stream equivalence
must prove packing preserves it. Failed or non-shrinking packing retains the
transformed copy. This storage step does not create an optimizer tournament.

A smaller selected copy must pass qpdf syntax validation, ordered geometry/resource
and navigation checks, exact all-page PDFium at 144 DPI and PDF.js at width 1404,
plus PDFium high-resolution (288 DPI), print (144 DPI) and detail (576 DPI) checks.
High-resolution/print sampling uses first/middle/last pages; the detail check uses a
crop selected from those pages. Verification uses at most two workers and releases
PDFium workers before PDF.js verification. Publication also requires unchanged
source/candidate hashes and exclusive output/report creation with rollback.

No rendering-speed benchmarks, candidate-selection timing or native browser launch
runs in direct jobs. Reports record actual coverage, browser checks not run and
rendering performance not measured. A smaller-lossless result makes no rendering
speed or performance-regression claim. Unsupported/failed/no-benefit jobs produce
reports only, without an automatic thorough retry. Native-browser development
qualification remains open in this environment.

Direct setup requires pypdf 6.10.0, pypdfium2 5.14.0, Pillow 12.3.0, Node/PDF.js
6.4.299 and bundled qpdf 12.4.2. It needs neither PyMuPDF nor a browser. Jobs bind
profile, recipe, code/tool/renderer identities and source/output hashes. Interrupted
jobs restart; completed recovery requires matching context and hashes. Direct batch
records use schema 2, isolating them from older thorough-only recovery.

### Explicit compression-only engine

`--viewing-profile quick --viewing-optimizer qpdf` retains the narrower compression-only
lane. It requires pypdf/qpdf, preserves and compares the entire current graph plus
unreferenced non-storage objects, and performs no page rendering. Its strict
comparator is also used to prove the direct pipeline's packing step.

`lossless_verify.py` establishes a bijection for indirect identities, aliases/cycles,
exact decimal/scalar/string values and ordered arrays. Fingerprints only schedule
full comparison. Metadata, custom entries, fonts, OCR, annotations, forms, actions,
navigation, layers and attachments participate. Storage exceptions are enumerated:
xref/object-stream layout, permitted document-ID updates and proven lossless
Flate/ASCII/PNG transport changes. Encoded JPEG/JPX/CCITT data remain exact.

Both direct input preflight and this comparator decline encryption/signatures
(including unreachable signature objects), XFA, incremental history, warnings and
unaccounted trailing data. Strict comparison bounds include 512 MiB per file,
500,000 objects, depth 256, 256 MiB per simple decoded stream, 64 MiB per changed
multi-filter/predictor stream and 1 GiB cumulative decoded work. These are work
bounds, not a process-wide peak RAM guarantee. Unresolved preservation never
publishes. The thorough policy below remains unchanged.

## Thorough candidate pipeline

`optimize_pdf.py` keeps the legacy CLI and Python API. `viewing_copy.py` builds candidates, checks preservation, measures rendering cost and publishes only a qualifying result. `annotation_ink.py` handles eligible BOOX appearances; `handwriting.py` handles supported drawing containers and batching. `geometry.py` and `navigation.py` compare saved structures independently of rendering.

The Windows batch GUI is a thin adapter over the headless controller in
`batch_workflow.py` and a separate `optimize_pdf.py --viewing-copy` process for
each selected file. Planning collapses duplicate selections, rejects unsafe inputs,
snapshots the source SHA256 and reserves an unused adjacent
`NAME.optimized-YYYY-MM-DD[-N]` run folder before displaying every source-to-output
mapping for confirmation. The selected PDF, JSON/Markdown reports and checkpoint
all stay inside that folder; an explicit CLI/API destination remains caller-owned.
The picker can
be reopened to accumulate files from multiple folders before that single review. The controller
rechecks sources and targets before each sequential launch, validates terminal reports
and hashes, isolates per-file failures and supports stop-after-current. Window close
is blocked while a child or its terminal result is being handled; closing between
jobs skips the remaining queue. The optimizer independently checks the confirmed
source hash at startup and before publication. The GUI does not implement a separate
transformation or acceptance path.

The reviewed optimizer selector defaults to the repository's adaptive pipeline.
It may restrict a run to the bundled qpdf Flate/object-stream candidate or the
PyMuPDF structural-packing candidate. The selected engine is stored with restart
metadata and included in the report; changing it starts a distinct reviewed run.
All choices retain the same preservation, performance and publication gates.
Parallel candidate generation is available only to the adaptive pipeline.

Production checkpoints reuse completed candidate generation, full preservation
verification and balanced timing only when their source, candidate, maintained-code,
tool, renderer and operation contexts match exactly. Completion markers are written
last and payload hashes are rechecked. A restarted batch reconstructs a terminal job
only from validated reports and otherwise resumes an incomplete job at its reviewed
output path. Timing identity includes the PDF.js version and hashes of the loaded
PDF.js/canvas native modules, so changing `PDF_NODE_MODULES` cannot reuse another
renderer installation's measurements. Final PDF.js/browser acceptance and publication
are never cached.
Independent built-in base candidates may be generated in separate processes when the
reviewed parallel-generation toggle is enabled, capped by the verification-worker
count. Dependent packed candidates, preservation, timing, documents and publication
remain ordered. Candidate failures stay isolated and use the same cache identities.
Within one document, run-local source geometry and lossless 144-DPI PDFium renders
are reused across candidate checks. PDFium and final PDF.js page comparisons use
the reviewed verification-worker count; each worker owns its renderer handles and
all workers stop before performance timing, browser checks and publication. Final
acceptance remains fresh. Documents and all benchmark runs remain sequential.
Persistent PDF.js verification releases page/shared rendering caches and runs
garbage collection between page comparisons while keeping both documents open.
When available RAM is known, verification may raise Node's old-space heap limit
above 4096 MiB, capped at 8192 MiB per process and half of currently available RAM
divided among workers. Unknown or insufficient RAM retains Node's default.
This is a heap allowance, not a cap on total process memory or a RAM reservation;
the GUI's PDFium planning estimate does not describe the PDF.js heap allowance.
If a persistent PDF.js verification worker exhausts its heap, all workers are
released and every page is retried sequentially in a fresh process using the same
appearance gates and remaining run budget. A single-page failure still blocks
publication; reducing concurrency alone does not resolve retained page caches.
The GUI displays stage age and reports time and disk budget exhaustion as distinct
terminal states.

Before selection, the GUI checks the pinned Python packages, Node/PDF.js/canvas and
configured Zen/Firefox executable and bundled qpdf runtime once. Blocking
diagnostics include a local corrective action and never bypass acceptance. Diagnostics
and PDF planning run on background workers while all pickers, review dialogs and
widget updates remain on the Tk thread. Settings are snapshotted before planning;
stale or post-close results are ignored. The native
review dialog is resizable and scrollable for long paths, and all primary controls
have keyboard access. Dynamic status is mirrored in the window title. Batch progress
is determinate and counts terminal reviewed files; per-file stage age remains separate
because candidate and browser work varies by document.

Pages are recognized by their drawing structure, not their filename or producer label. Candidate passes include duplicate-object removal, static annotation conversion, consolidation of eligible appearances, exact inlining and same-width segment batching. Optional structure packing is compared separately. The Windows package includes qpdf 12.4.2 and compares its lossless Flate/object-stream rewrite under the same preservation, rendering and publication gates; `PDF_QPDF` can select another executable. Already deduplicated candidates do not receive another all-stream duplicate scan during packing.

Annotation conversion preserves placement, appearance matrices, bounding-box clipping, colors, layer membership and paint order. Only a leading sequence of eligible painted annotations is converted. Links, comments, forms, attachments, action-bearing objects, replies and protected references remain. Missing/state-dependent appearances, special zoom/rotation flags, transparency groups and unsupported layer expressions are skipped. Unfamiliar or transformed appearances retain ordinary Form calls.

Inlining retains literal path commands. Batching keeps separate subpaths and round caps. Viewing-copy batching skips documents containing transparency because inherited masks can make separate painting operations significant. Duplicate handwriting is removed only when the complete referenced drawing graph passes the compatible opaque-content proof. OCR removal is a separate explicit legacy option.

## Preservation

Navigation comparisons include bookmark hierarchy, titles, styles, expansion, destinations and zoom; named destinations and page labels; link rectangles and chained actions; opening actions and viewer preferences; attachments; retained annotations; optional-content layers, private catalog/page metadata and application-defined trailer entries from classic or cross-reference-stream trailers. File-layout trailer keys and permitted document-ID updates remain storage exceptions. References compare semantic relationships, not object numbers. Merging distinct interactive identities is rejected and retried without duplicate merging.

Geometry/resource checks cover paths, widths, color state, transforms, clipping, layer membership, image decoding parameters, fonts and original resource entries. Edited shared resources are copied before modification. Reachability cleanup discards editing data only after conversion and only when no retained object needs it.

Known BOOX dangling optional-content references may normalize to absence only in recognized, verified contexts; these changes are reported. Other unresolved references block the affected candidate. Input encryption, signatures and signature fields are rejected. Signature validation inventories all current objects, including unreferenced and object-stream objects, before cleanup; `/Sig`, signature fields and `/ByteRange` plus `/Contents` all fail closed. Source hashes are checked before publication. Exclusive file creation prevents output collisions; interrupted publication removes partial files.

## Appearance gates

| Check | Scope |
| --- | --- |
| PDFium at 144 DPI | Every page of every candidate |
| PDF.js at 1404-pixel page width | Every page of a final candidate |
| PDFium at 288 DPI and print mode at 144 DPI | Benchmark sample pages |
| PDFium at 576 DPI | A detail-rich crop selected from sample pages |
| Isolated Zen/Firefox with installed PDF.js | Bookmark/link destinations, page labels, text/annotation layers, ordinary form/comment interactions, optional layers on every page, forward/back page sequence |

Exact candidates require identical tested pixels. Edge-changing candidates additionally require exact geometry, differences confined to a one-pixel band around the original ink boundary (gray threshold 250), peak channel difference at most 96/255 and page mean at most 1.5/255. A failed check never loosens its own tolerance. Failed final candidates fall back to the next verified candidate.

The Node and browser harnesses load the complete PDF.js font/image resources and stop on parsing errors. See the [PDF.js API](https://mozilla.github.io/pdf.js/api/draft/api.js.html) for resource options and render/annotation settings. Browser checks use the installed PDF.js package inside Zen/Firefox, rather than the browser's bundled PDF viewer.

Bookmark and internal-link checks await destination navigation, target rendering
and text/annotation layers, then require a visible target and unchanged viewport
for 100 ms. They compare source/candidate scroll position, scale, visible pages,
current page and target rectangle exactly. The current-page label need not equal
the destination page when scrolling is clamped at the document end. Timeout
diagnostics include that viewport and the normalized destination. Structural
navigation equality remains independently required.
After page-resource cleanup, a bookmark that leaves scroll position and scale
unchanged explicitly requests rendering of visible pages. Otherwise PDF.js can
leave the reset destination unrendered because no scroll/scale update occurs.
Normal navigation retains its own rendering schedule.

## Performance selection

Preparation and rendering run in separate fresh processes. Cold rendering has no preceding operator-list request. Cached drawing renders a second time onto a fresh canvas. These measurements overlap in meaning and are never added together.

Runs are sequential, with alternating source/candidate order: render timing uses three runs per page/version, extended to five when CPU measurements are noisy. Operator preparation is descriptive and runs once per page/version. Samples include page 1 plus the three highest-density non-first pages. Density includes decoded drawing bytes, nested Forms, image dimensions and annotation appearances. Reports retain wall time, process CPU, sampled RSS, drawing operations, annotation counts, sizes and individual runs. RSS sampling is not a peak-memory measurement.

- Faster: at least 10% lower aggregate median cold-render CPU.
- Regression gate: no sampled cold/cached latency increase above the greater of 10% or 50 ms.
- Smaller exact copy: must shrink and use at most 105% of baseline CPU.
- Candidates within 5% CPU: prefer exact appearance, then smaller size.
- Size growth: requires a measured speed improvement.
- No qualifier: save reports only and retain the original as the viewing file.

If qualifying candidates all fail final acceptance, reports classify the result as
`no publishable candidate: final acceptance failed`. The legacy reports-only
`no improvement` status remains for CLI/API and batch recovery compatibility.

If cold-render CPU rounds to zero for the baseline, the report marks the reduction as unresolved. It cannot classify a speedup; a smaller exact copy may qualify only if its measured CPU is also zero and the latency gate passes.

## Limits

The optimizer does not promise maximum compression, universal renderer equivalence or faster scrolling. Unique handwriting still requires drawing work. Bitmap content is not resampled or converted to a lossy codec. Unknown content is preserved or rejected conservatively.

Scripted and XFA forms are unsupported by browser acceptance. External URI/file targets are preserved structurally but are never opened. The tests do not cover every image codec, malformed export, browser extension, zoom or print setting. Windows/Zen is the tested platform; other platforms may require explicit runtime paths.
