# Design and acceptance rules

**Scope: current production behavior.** This document defines the existing
optimizer's transformations and publication gates. Development priorities and
research decisions live in the [roadmap](roadmap.md#current-work-queue).
The separate research runner and its native Firefox protocol are documented in
[comparison.md](comparison.md).

**Opt-in study-copy work:** on 2026-11-17 the user approved investigating
bounded appearance changes, point reduction and raster copies for faster reading.
Each study-copy profile has its own acceptance contract. Study-copy candidates
must declare geometry, resolution, feature losses, quality coverage and actual
performance evidence. Visual tolerance does not silently authorize loss of search, navigation, layers
or protected interactive data. Raster output has finite zoom/print resolution
and is not lossless preservation of the original drawing representation.

## Experimental bitmap study contract (`bitmap-study-1`)

The CLI profile `bitmap` and GUI **Bitmap study copy (experimental)** create
finite-resolution images of visible pages. Letter (612 × 792 points) at 300 DPI
is the initial preset; source-size paper and 450/600 DPI are explicit alternatives.
Pages fit centrally without stretching or clipping. Source rotation is baked into
the image, and physical page size accounts for the PDF's page unit. DPI applies
after fitting, so a large tablet canvas does not silently
produce a source-size 300-DPI bitmap when Letter was selected. Each page is capped
at 40 million rendered pixels. Windows batch review checks the selected paper
and DPI using the rendering geometry before offering to start a job. RGB is
retained unless all channels are equal;
images use lossless Flate transport after raster conversion. The raster pass
limits the full document to 256 MiB of encoded images and 1 GiB of decoded
image data. The saved-file verifier also enforces its 512 MiB file limit and
checks image bytes incrementally.
The existing reachability cleanup removes original drawing/resource graphs that
the bitmap pages no longer reference. One qpdf packing pass removes storage gaps;
all image, placement and retained-feature checks run on those final packed bytes.

By default Bitmap accepts annotation-free, font-free PDFs. **Flatten text**
(`--study-flatten-text`) explicitly permits rasterizing supported text. Visible
text becomes pixels and the copy loses text search, selection, copying and OCR.
Preflight resolves fonts selected by page drawing and graphics state. It supports
Type 1, Multiple Master Type 1, TrueType and Type 0 fonts with a resolved CID font.
Forms, links/comments, tagged content, named destinations/actions, positional
bookmarks, hidden/alternate/automatic layers and optional-content membership
expressions remain unsupported. Preflight rejects missing drawing resources and
cycles before rasterization. Flat and nested Fit bookmarks targeting existing
local pages are supported across exporters. Chained bookmark actions are declined.
Bookmarks, labels, attachments,
metadata and private navigation data are cloned and independently compared after
normalizing the declared geometry and layer changes. All-visible layers flatten;
layer switching and vector editing/zoom are explicitly lost. Unsupported features
produce a rejection, without an output PDF. Existing strict input rejection,
source-hash checks, budgets, exclusive publication and rollback apply.

Bitmap can verify a temporary current-revision snapshot for supported NeoPdf
index warnings and incremental saves using the same graph/stream proof as
adaptive Direct. It separately supports the exact paired qpdf warnings for a malformed
xref-stream terminator and its missing self-entry. A clean temporary snapshot
copies the entire object region byte-for-byte, then adds the missing xref self-entry
and terminator. Independent checks require identical object bytes, index bindings
and trailer values; orphans therefore remain intact. Only unfiltered xref streams
with the supported layout qualify. All other warnings remain blocking;
signature and encryption rejection remains in force. The report records the
original and snapshot hashes, warnings and proof. The original keeps its save history.

Every saved image payload, dimensions and placement program are checked, together
with qpdf syntax and retained feature signatures. This proves saved-image integrity,
not mathematical meaning, multi-renderer equivalence or a viewing-speed improvement.
Reports declare human review pending and performance unmeasured. Copies may be
larger than the source. Interrupted jobs restart. Schema-4 batch records bind paper,
DPI, text flattening, source hashes and code/tool identities before completed-result recovery.

Exact profiles use their own policy versions and gates. Vector study and any
strength control are conditional research in the [roadmap](roadmap.md#current-work-queue).

## Direct profile (CLI token: quick)

The GUI and drag-and-drop launcher default to **Direct (recommended)**.
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

For NeoPdf exports that trigger the recognized cross-reference warning or have
incremental saves, adaptive Direct can first make a temporary current-revision
snapshot. Review discloses this step. Clean qpdf checks and an exact comparison
of the current logical objects, reference identities and streams, including
unreferenced payloads, are required before using the snapshot. Other parser or
tool warnings, encryption, signature objects and fields, and XFA remain
blocking. The original retains its revision history. Reports bind the source
and snapshot hashes and record the preparation proof. Final geometry, navigation,
rendering and size checks compare the viewing copy against the original input.

The transformed intermediate is packed with qpdf 12.4.2, preserving existing stream
encodings and unreferenced data in that intermediate. Strict graph/stream equivalence
must prove packing preserves it. Failed or non-shrinking packing retains the
transformed copy. This storage step does not create an optimizer tournament.

Direct candidate construction uses one writer and the strict exact-decimal parser
so saving numeric dictionary operands cannot round coordinates, rectangles,
matrices or widths. The existing
strict parser warning policy also applies during construction.

A catalog/metadata/document-ID reachability walk removes unreachable graphs,
including cycles, before and after
deduplication. Saved unmerged and merged copies still receive the existing
navigation/resource comparisons; a failed merge retains the unmerged copy.
The selected saved bytes are copied exclusively instead of being parsed and
serialized by a second writer. Thorough candidates retain their existing rebuild
path and final preservation gates.

Inlining discovery skips Forms with no child XObjects before decoding their
content. Recognized pen Forms select the grouped or separate-segment grammar
from their first width command, then still require a complete literal-body match.
This changes parsing work, not supported geometry or acceptance tolerances.

A smaller selected copy must pass qpdf syntax validation, ordered geometry/resource
and navigation checks, exact all-page PDFium at 144 DPI and PDF.js at width 1404,
plus PDFium high-resolution (288 DPI), print (144 DPI) and detail (576 DPI) checks.
High-resolution/print sampling uses first/middle/last pages; the detail check uses a
crop selected from those pages. The direct high-resolution, print and detail
comparisons share one owned source/candidate PDFium document pair; pages and
bitmaps are released after each check and both documents close on failure.
The all-page PDFium workers compare paired strips in memory for Direct's single
candidate. Verification uses at most two workers and releases PDFium workers
before PDF.js verification. PDF.js accepts equal pixel buffers immediately and
uses the existing comparison for differences. Publication also requires unchanged
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

For a new revision at the same source path, Direct checks up to eight recent
adjacent checkpoints. Reuse requires a completed successful run, a matching
report hash, the same code and tool context, the same PDFium binary, and exact
raw render-input identities on both the source and candidate sides. These
identities cover page boxes, inherited resources, reference topology and encoded
stream bytes. Pages with annotations, fonts or unresolved render dependencies
receive fresh comparisons. The pipeline rebuilds the candidate from the revised
source and repeats geometry, navigation, all-page PDF.js, high-resolution,
print and detail checks. The report identifies each reused 144-DPI comparison.

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

Compression-only input preflight and the default comparator decline encryption/signatures
(including unreachable signature objects), XFA, incremental history, warnings and
unaccounted trailing data. Strict comparison bounds include 512 MiB per file,
500,000 objects, depth 256, 256 MiB per simple decoded stream, 64 MiB per changed
multi-filter/predictor stream and 1 GiB cumulative decoded work. These are work
bounds, not a process-wide peak RAM guarantee. Unresolved preservation never
publishes. The thorough policy below remains unchanged.

## Thorough candidate pipeline

`optimize_pdf.py` keeps the legacy CLI and Python API. `viewing_copy.py` builds candidates, checks preservation, measures rendering cost and publishes only a qualifying result. `annotation_ink.py` handles eligible BOOX appearances; `handwriting.py` handles supported drawing containers and batching. `geometry.py` and `navigation.py` compare saved structures independently of rendering.

The Windows batch GUI is a thin adapter over the headless controller in
`batch_workflow.py` and one subprocess per file: `optimize_pdf.py --viewing-copy`
for PDFs or `note_to_pdf.py` for BOOX notes. Planning collapses duplicate selections, rejects unsafe inputs,
snapshots the source SHA256 and reserves an unused adjacent
`NAME.optimized-YYYY-MM-DD[-N]` run folder before displaying every source-to-output
mapping for confirmation. When Windows path limits require it, planning uses a
compact adjacent output name with matching reports and checkpoint, or rejects the
path in review if the optimizer's working files still would not fit. An explicit
CLI/API destination remains caller-owned.
The picker can be reopened to accumulate files from multiple folders. The main
window retains the selection while the user adjusts settings. Opening batch review
snapshots those settings and plans the batch. Returning to settings keeps the
selection and a new review plans it with the new settings. The controller
rechecks sources and targets before each sequential launch, validates terminal reports
and hashes, isolates per-file failures and supports stop-after-current. Window close
is blocked while a child or its terminal result is being handled; closing between
jobs skips the remaining queue. The optimizer independently checks the confirmed
source hash at startup and before publication. The GUI does not implement a separate
transformation or acceptance path.

After **Inspect PDF**, **Review inspected PDF** uses the same review and run path
for that file and displays the drawing summary. The planning hash must match the
completed inspection. The selected mode still determines eligibility, candidate
selection and acceptance.

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
After at least one candidate passes preservation, a fresh source-only browser probe
runs before balanced timing. Failure produces reports only with an explicit source
browser blocker; no timing or candidate browser retry runs. Source-only probes create
their disposable profiles in system temporary storage, without requiring write
access beside the original PDF. A successful source
trace is reused solely for candidate comparisons in that invocation. Candidate
traces remain fresh and exact trace comparison is unchanged; no browser evidence
is checkpointed. Direct mode does not run this thorough-only probe.
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

The GUI checks dependencies for the selected mode. Thorough PDF requirements
remain visible when missing and are checked again during review. They do not
block BOOX note conversion. Review checks the optional BOOX converter separately. Blocking
diagnostics include a local corrective action and never bypass acceptance. Diagnostics
and PDF planning run on background workers while all pickers, review dialogs and
widget updates remain on the Tk thread. Settings are snapshotted before planning;
stale or post-close results are ignored. The native
review dialog is resizable and scrollable for long paths, and all primary controls
have keyboard access. Dynamic status is mirrored in the window title. The progress
bar uses an approximate share for the active file based on reported stages and
page checks. The completed-file count and per-file stage age are shown separately.

Pages are recognized by their drawing structure, not their filename or producer label. Candidate passes include duplicate-object removal, static annotation conversion, consolidation of eligible appearances, exact inlining and same-width segment batching. Optional structure packing is compared separately. The Windows package includes qpdf 12.4.2 and compares its lossless Flate/object-stream rewrite under the same preservation, rendering and publication gates; `PDF_QPDF` can select another executable. Already deduplicated candidates do not receive another all-stream duplicate scan during packing.

Annotation conversion preserves placement, appearance matrices, bounding-box clipping, colors, layer membership and paint order. Only a leading sequence of eligible painted annotations is converted. Links, comments, forms, attachments, action-bearing objects, replies and protected references remain. Missing/state-dependent appearances, special zoom/rotation flags, transparency groups and unsupported layer expressions are skipped. Unfamiliar or transformed appearances retain ordinary Form calls.

Inlining retains literal path commands. Batching keeps separate subpaths and round caps. Viewing-copy batching inventories reachable page/Form invocations and batches a recognized pen Form only when every invocation is proven opaque. Graphics-state stacks, groups, masks, blend modes, stroke/fill alpha and overprint participate. Shared safe/unsafe Forms stay unchanged; Forms referenced through annotation appearances, fonts, patterns or graphics-state resources are conservatively protected. Unresolved or bounded-out inventories reject batching before candidate rendering. The scoped pass retains duplicate layer draws; ordinary deduplication remains separate. Resettable graphics-state values are evaluated at each child invocation; enclosing transparency groups remain blocking. A successful batching pass may retain its Forms when no exact inlining container is supported; unexpected inlining errors still reject the candidate. Duplicate handwriting is removed only when the complete referenced drawing graph passes the compatible opaque-content proof. OCR removal is a separate explicit legacy option.

## Preservation

When Type 3 glyphs fall back to their caller's resources, the batching pass
protects the Forms in those resource scopes.

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
for 100 ms. The harness disables browser scroll anchoring during PDF.js zoom and
requires Fit destinations to reach the page top or the scroll limit. It compares
source/candidate scroll position, scale, visible pages,
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

## BOOX .note conversion contract (Milestone P18)

The BOOX conversion pipeline provides a separate, strict conversion path from
proprietary binary BOOX `.note` archives (`geo_layout` format) into standalone
vector PDF viewing copies. Conversion is architecturally decoupled from general PDF
optimization: it transforms archive-internal protobuf metadata, binary `#points`
stroke tables, and shape structures into an unoptimized standard vector PDF,
which is then optionally passed directly through the Direct profile (`quick_lossless`)
for inlining, deduplication, and stream packing. The published PDF is a static
vector viewing copy, not an editable BOOX notebook.

### Archive preflight and input validation

Conversion is guarded by fail-closed archive inspection before any transformation
or temporary extraction:

1. **Preflight inspection (`inspect_note`):** The input `.note` archive is validated
   as a standard ZIP container without extracting files to the host filesystem.
   Preflight parses top-level and nested protobuf records (`note_info`, `pageModel`,
   and `shape/*.zip`) via bounded in-memory decoders, inventorying page count,
   layer visibility, total shapes, pen type distributions, embedded images, and
   background template descriptors.
2. **Input immutability:** The SHA-256 digest of the source `.note` archive is
   recorded before execution and re-verified before final publication. Any
   modification to the source during conversion or optimization aborts the job
   immediately.
3. **Execution containment:** Conversion runs the pinned Rust binary
   (`boox-notes-renderer`) inside a private, disposable scratch directory
   (`tempfile.TemporaryDirectory`). The child process is invoked with argument
   arrays, execution timeouts, and strict disk/time budget limits (`RunBudget`).
   Intermediate PDF files are never created directly at the user-facing destination.
4. **Exclusive publication and collision refusal:** The published PDF and its
   companion reports (`.json` and `.md`) are written using exclusive file creation
   modes (`xb` / `x`). If any destination file or report exists or is a symlink,
   processing halts with `FileExistsError` without modifying existing artifacts.
5. **Rollback on failure:** Any abnormal termination, timeout, budget exhaustion,
   or verification failure unlinks all partially created destination artifacts,
   leaving no orphaned or corrupted files.

### Strict vector pen support matrix

In strict mode (`--strict`, default in `note_to_pdf.py`), all supported handwriting
pens are preserved with 100% vector fidelity and zero geometric decimation.
Upstream decimation passes (e.g. Douglas-Peucker point filtering or distance-based
sample dropping) are bypassed. Raw point sequences, sample timestamps, pressure,
and tilt values are retained.

| Pen Type | Name / Semantics | Geometry and PDF Representation |
| --- | --- | --- |
| `2` | Ballpoint | Constant nominal width; drawn as continuous stroked polylines with round caps and joins (`1 J 1 j`). All recorded points are preserved. |
| `2000`–`2999` | Boundary / History copy | Constant nominal width; identical to Pen 2. Generated by BOOX when duplicating boundary-crossing strokes across adjacent tiles or recording edit history. |
| `5` | Fountain pen | Pressure-sensitive width: `width = max(2.0, 0.964 · (nominal + 3) · p^0.599)` where `p = pressure / 4095`. Smoothed via zero-phase forward-backward EMA (α ≈ 0.2) to eliminate discrete stepping while preserving stroke envelope. Rendered as short round-capped segments coalesced at consecutive width steps. |
| `15` | Highlighter / Marker | Nominal constant width; drawn as a single stroked polyline of raw points at alpha 127/255 (0.49803922) with `Multiply` blend mode (`/BM /Multiply`). Optional `--flat-marker` mode converts each stroke into a single nonzero-winding fill of its capsule union to prevent viewer-specific dark self-crossings. |
| `21` | nBrush (Neo Brush) | Dynamic brush modeling: two-phase velocity accumulator (`velAccum`) during dwell and move phases, combined with square-root pressure scaling (`width = velAccum + 2 · nominal · √p`). Evaluated at fixed dt over raw undecimated points to preserve characteristic entry/exit tapering and dwell blobs. |
| `60` | Calligraphy (+45°) | Fixed chisel nib rotated at +45° (`width(dir) = L·\|cos(dir - π/4)\| + w·\|sin(dir - π/4)\|`). Drawn by filling swept nib polygon quads (nonzero winding); stroke ends exhibit the flat angled edge. |
| `61` | Calligraphy (-45°) | Fixed chisel nib rotated at -45° (`width(dir) = L·\|cos(dir + π/4)\| + w·\|sin(dir + π/4)\|`). Geometry and rendering identical to Pen 60 with opposite nib angle. |

Supported non-pen shape items in strict mode:
- **Text boxes (pens `6` and `16`):** Plain text (field 10) or rich-text HTML (field 22)
  with font size, alignment, and line spacing from `text_style_json`. Resolved against
  host system fonts via `crates/system-fonts` with exact per-character fallback and
  TrueType/OpenType glyph subsetting embedded in the PDF.
- **Embedded images (pen `19`):** JPEG or PNG assets from `resource/data/` placed at
  the shape's bounding box without lossy re-encoding or resampling.
- **Shape-tool geometry (pen `40`):** GeoJSON `featureCollection` primitives
  (open `LineString`, closed `Polygon` rings, `Oval`, quadratic Bézier `Curve`,
  elliptical `Arc`, `Bracket`, and `DirectionLine` arrowheads) transformed by
  the shape's 3×3 affine matrix, with optional stroke dash intervals.

### Strict rejection policies

Strict conversion rejects incomplete or unrepresented document features:

1. **Rejection of charcoal raster pens (pen `22`):** Pen 22 uses tilt-driven width
   and pressure-driven grain density, rasterizing strokes into bitmap texture stamps.
   Strict mode rejects Pen 22 (`Error::UnsupportedPen(22)`) because rasterization
   violates the vector preservation contract. No silent raster fallback or lossy
   vector approximation is permitted.
2. **Rejection of active templates and backgrounds:** BOOX notes frequently reference
   proprietary or tablet-local background templates (lined, grid, dotted, or planner
   backgrounds). Strict mode rejects archives containing active templates (non-empty
   `resName`, non-empty `fillPath`, non-zero `displayFillColor`, or embedded background
   images). Only blank/empty/none templates are accepted; unbundled template assets
   cannot be rendered faithfully without cross-platform asset divergence.
3. **Rejection of hidden layers:** Archives containing layers marked invisible
   (`check_hidden_layers`) are rejected (`Error::UnsupportedFeature`). This prevents
   silent loss of user drawings that cannot yet be represented in PDF Optional
   Content Groups (OCG).
4. **Rejection of missing or corrupt assets:** Missing stroke point streams,
   malformed tile geometries, corrupt embedded images, or unresolvable font glyphs
   fail closed with explicit typed errors (`Error::MissingContent`, `Error::Malformed`),
   preventing partial or blank PDF generation.
5. **Rejection of unknown pens and legacy containers:** Any pen type outside the
   supported matrix triggers immediate rejection. Legacy SQLite-based `.note`
   archives are rejected at container opening.

### Configurable stroke width modeling

To provide flexible visual density control for thin pens while preventing regression:

1. **CLI options & precedence**:
   - `--stroke-mode <upstream|density-scaled|fixed|scale> (compatibility alias: `device-native`)`: Selects width calculation (default: `upstream`).
   - `--fixed-stroke-width <width>`: Constant width with optional unit (`mm`, `pt`, `px`, or raw float in `0.001`..`100.0 pt`), e.g. `0.10mm` (0.2835 pt), `0.20mm` (0.5669 pt), `0.5pt`, `2px`.
   - `--stroke-scale <factor>`: Positive float linear multiplier applied across stroke widths.
   - **Precedence**: `--fixed-stroke-width` > `--stroke-scale` > `--stroke-mode`.
2. **Drawing floors and custom dot radius**:
   - **Upstream baseline**: Preserves exact original drawing minimums: `dot()` 0.3 pt radius, `draw_segments()` 0.2 pt floor, `draw_calligraphy()` 0.3 pt clamp, and `MIN_STEP = 0.6 pt` decimation. Guarantees zero regression for default callers.
   - **Custom modes (`Fixed`, `DeviceNative`, `Scale`)**:
     * Custom dot radius is strictly **half the requested stroke width** (e.g. $0.005\text{ pt}$ radius for a $0.01\text{ pt}$ dot), removing the 0.3 pt clamp.
     * `draw_segments()` floor lowered to 0.001 pt; `draw_calligraphy()` 0.3 pt clamp removed.
     * Dynamic decimation in non-strict mode: `MIN_STEP = min(0.6, width * 1.0)`. In strict mode: decimation is bypassed.
     * Applied across all pen types (2, 5, 15, 21, 60/61, 2000–2999, lone dots).
3. **Qualification status**:
   - Density scaling (`0.24` ratio = 72/300 pt/px) is documented as **experimental**.
   - Tablet hardware rasterizer fidelity acceptance (Milestone P18-D) remains open / in progress pending empirical calibration.

### Hierarchical bookmarks and Table of Contents (TOC)

BOOX notebooks store outline bookmark structures in `toc/pb/*`:

1. **Protobuf schema**: Repeated `TocItem` records containing:
   - Tag 1 (string): Item UUID.
   - Tag 2 (uint64): Creation timestamp.
   - Tag 3 (uint64): Update timestamp.
   - Tag 4 (string): Target `page_uuid`.
   - Tag 5 (string): Bookmark title text.
   - Tag 6 (uint32): Target page index (0-based).
   - Tag 8 (string, optional): Parent Item UUID.
2. **Validation and cycle detection**:
   - Target `page_uuid` is verified against document `pageNameList`; invalid destinations are dropped with warnings.
   - Iterative parent traversal detects cycles in linear time and severs them with warnings. Trees deeper than 64 levels (roots at level one) are rejected before PDF construction.
3. **Single-page export remapping (`--page N`)**:
   - When exporting a single page $N$ (1-based), only bookmarks targeting page $N$ are retained.
   - Destinations are remapped to PDF page index 0; orphan children whose parents target other pages are promoted to root nodes.
4. **PDF outline injection**:
   - Injected into converted PDFs via `pypdf.PdfWriter.add_outline_item(title, page_idx, parent=parent_handle)`.
   - Direct optimization (`quick_lossless`) navigation checks verify bookmark preservation.
   - Can be bypassed using `--no-toc`.

### Direct optimization integration (`note_to_pdf.py`)

`note_to_pdf.py` coordinates conversion and optional Direct optimization in a
unified two-stage pipeline:

- **Stage 1 (Strict Vector Conversion):** Invokes `boox-notes-renderer` with
  `--strict`, writing an unoptimized vector PDF to the private scratch directory.
  If conversion fails or strict validation rejects the archive, an audit report is
  written to `audit-output/` and execution halts with `conversion_verdict: 'rejected'`.
- **Stage 2 (Direct Lossless Optimization):** The scratch PDF is fed to
  `quick_lossless.optimize_quick(..., optimizer='adaptive')`. This applies exact
  handwriting inlining (merging eligible opaque Form XObjects into page content),
  resource deduplication, catalog/xref reachability cleanup, and single-pass
  qpdf 12.4.2 object-stream packing.
- **Appearance verification:** The candidate must satisfy Direct profile appearance
  gates:
  - Exact all-page PDFium comparison at 144 DPI.
  - Exact all-page PDF.js comparison at 1404px width. For multi-page notebooks
    exceeding 50 pages (e.g., 91-page `ME 505.note`), verification is split into
    sequential page chunks of at most 30 pages to bound Node.js worker heap usage
    and prevent out-of-memory worker termination.
  - High-resolution (288 DPI), print (144 DPI), and detail (576 DPI) PDFium checks
    on sample pages.
- **Lossless fallback guarantee:** If Direct optimization fails verification,
  exhausts budgets, or does not reduce file size, the pipeline retains and publishes
  the verified direct converted PDF from Stage 1 (`optimization_verdict: 'retained direct converted PDF'`).
  If optimization succeeds and shrinks the file, it publishes the optimized copy
  (`optimization_verdict: 'smaller-lossless'`). Conversion verdict and optimization
  verdict are tracked as distinct diagnostic outcomes.
- **Reference export audit:** When a matching tablet-exported PDF is provided
  (`--reference-pdf`), the pipeline evaluates page count alignment, bounding box
  dimensions (within 1.0 pt), ink density ratios, mean/max grayscale pixel differences,
  and storage savings against the device export. Note that tablet exports (such as `ME 505.pdf`)
  contain vector handwriting represented as individual `/Stamp` annotations with vector
  appearance streams (`/Annots` -> `/Stamp` -> `/AP` -> `/N`), not raster handwriting
  (the 91 embedded page images occupy only ~698 KB combined and serve as background templates).
  The 440 MB size of the tablet export stems from per-stroke annotation dictionary and
  appearance stream bloat (60,575 objects). Structural optimization of the tablet PDF
  (inlining/flattening annotations into page content streams, resource deduplication,
  and stream packing) is a viable and valuable optimization target.
- **Traceability:** Every run produces structured `<output>.json` and human-readable
  `<output>.md` reports detailing input/output SHA-256 hashes, preflight inventory,
  renderer arguments, stage timings, and verification results.

## Limits

The optimizer does not promise maximum compression, universal renderer equivalence or faster scrolling. Unique vector handwriting still requires drawing work. Exact profiles do not resample bitmap content or use lossy codecs. The opt-in bitmap contract above instead rasterizes visible pages at the declared resolution. Unknown content is preserved or rejected conservatively.

Scripted and XFA forms are unsupported by browser acceptance. External URI/file targets are preserved structurally but are never opened. The tests do not cover every image codec, malformed export, browser extension, zoom or print setting. Windows/Zen is the tested platform; other platforms may require explicit runtime paths.


### Bookmark and custom-width repair contract

Notebook prefixes are sorted, then each notebook uses its first metadata record
with a valid page list, normalized and deduplicated in declaration order. Content-only
pages are appended in renderer order. Preflight retains flat `pages` and adds `notes`
with each prefix, ordered pages and global offset. Bookmark and parent IDs are
normalized and scoped to their notebook. Conflicting normalized records fail.

Missing TOC data is allowed. Malformed TOC or injection errors reject conversion
and publish audit diagnostics only; `--no-toc` is the explicit bypass. Invalid page
targets retain the documented warning/drop behavior. Saved outline titles,
hierarchy, destinations and page count are checked before accepting conversion.

Custom widths recalculate single-canvas bounds with a shared conservative pen
envelope and the existing 24-point margin. Per-page dimensions and upstream bounds
retain their existing behavior. Density scaling remains experimental; P18-D remains open.


### Mixed GUI batches

The Tk GUI dispatches PDFs through the existing optimizer and notes through the
strict conversion adapter. Frozen note options and reviewed source/renderer hashes
bind each launch and its `boox-gui-1` report. Note jobs use separate schema-3
checkpoints, restart conversion after interruption and never reuse intermediate
conversion evidence. Note result validation checks source, renderer, settings,
output identity and completed Direct proof for any optimization claim. Converter
availability is a per-note planning check; it cannot block eligible PDFs.
