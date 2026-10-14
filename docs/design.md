# Design and acceptance rules

## Candidate pipeline

`optimize_pdf.py` keeps the legacy CLI and Python API. `viewing_copy.py` builds candidates, checks preservation, measures rendering cost and publishes only a qualifying result. `annotation_ink.py` handles eligible BOOX appearances; `handwriting.py` handles supported drawing containers and batching. `geometry.py` and `navigation.py` compare saved structures independently of rendering.

Pages are recognized by their drawing structure, not their filename or producer label. Candidate passes include duplicate-object removal, static annotation conversion, consolidation of eligible appearances, exact inlining and same-width segment batching. Optional structure packing is compared separately. Already deduplicated candidates do not receive another all-stream duplicate scan during packing.

Annotation conversion preserves placement, appearance matrices, bounding-box clipping, colors, layer membership and paint order. Only a leading sequence of eligible painted annotations is converted. Links, comments, forms, attachments, action-bearing objects, replies and protected references remain. Missing/state-dependent appearances, special zoom/rotation flags, transparency groups and unsupported layer expressions are skipped. Unfamiliar or transformed appearances retain ordinary Form calls.

Inlining retains literal path commands. Batching keeps separate subpaths and round caps. Viewing-copy batching skips documents containing transparency because inherited masks can make separate painting operations significant. Duplicate handwriting is removed only when the complete referenced drawing graph passes the compatible opaque-content proof. OCR removal is a separate explicit legacy option.

## Preservation

Navigation comparisons include bookmark hierarchy, titles, styles, expansion, destinations and zoom; named destinations and page labels; link rectangles and chained actions; opening actions and viewer preferences; attachments; retained annotations; optional-content layers and private catalog/page metadata. References compare semantic relationships, not object numbers. Merging distinct interactive identities is rejected and retried without duplicate merging.

Geometry/resource checks cover paths, widths, color state, transforms, clipping, layer membership, image decoding parameters, fonts and original resource entries. Edited shared resources are copied before modification. Reachability cleanup discards editing data only after conversion and only when no retained object needs it.

Known BOOX dangling optional-content references may normalize to absence only in recognized, verified contexts; these changes are reported. Other unresolved references block the affected candidate. Input encryption, signatures and signature fields are rejected. Source hashes are checked before publication. Exclusive file creation prevents output collisions; interrupted publication removes partial files.

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

## Performance selection

Preparation and rendering run in separate fresh processes. Cold rendering has no preceding operator-list request. Cached drawing renders a second time onto a fresh canvas. These measurements overlap in meaning and are never added together.

Runs are sequential, with alternating source/candidate order: three runs per page/version, extended to five when CPU measurements are noisy. Samples include 1, 7 and 29 when present plus the densest page; six-page documents use 1, 3 and 6 plus the densest. Density includes image dimensions as well as drawing data. Reports retain wall time, process CPU, sampled RSS, drawing operations, annotation counts, sizes and individual runs. RSS sampling is not a peak-memory measurement.

- Faster: at least 10% lower aggregate median cold-render CPU.
- Regression gate: no sampled cold/cached latency increase above the greater of 10% or 50 ms.
- Smaller exact copy: must shrink and use at most 105% of baseline CPU.
- Candidates within 5% CPU: prefer exact appearance, then smaller size.
- Size growth: requires a measured speed improvement.
- No qualifier: save reports only and retain the original as the viewing file.

## Limits

The optimizer does not promise maximum compression, universal renderer equivalence or faster scrolling. Unique handwriting still requires drawing work. Bitmap content is not resampled or converted to a lossy codec. Unknown content is preserved or rejected conservatively.

Scripted and XFA forms are unsupported by browser acceptance. External URI/file targets are preserved structurally but are never opened. The tests do not cover every image codec, malformed export, browser extension, zoom or print setting. Windows/Zen is the tested platform; other platforms may require explicit runtime paths.
