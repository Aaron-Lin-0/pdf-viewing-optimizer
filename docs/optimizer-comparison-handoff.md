# Original PDF comparison contract (historical)

**Planning status: historical requirements reference.** The
[main improvement plan](roadmap.md) is the active roadmap and work queue.
This file retains the original comparison requirements; it does not describe
current implementation progress or authorize a new run. Use
[comparison.md](comparison.md) for current usage and known limitations, and
[design.md](design.md) for production acceptance rules.

Reference reviewed commit: `b7ad64fc09a5ae769fbd7bd0dbd19b9da8697e76`.
The chat handoff requested a separate Python comparison system, without changing
production transformations, thresholds, public CLI or `optimize_pdf.optimize`.
This file preserves that contract; [comparison.md](comparison.md) documents the
implementation, setup and current verification limits.

## Decisions and boundaries

Compare all 33 profiles in `tools/compare_optimizers.py`, including conservative
and lossy methods. Use only copies of PDFs authorized by the user in the current
work context; this historical document does not track those authorizations.
Keep originals, existing outputs and
reports untouched. Reject encrypted and signed inputs. Do not upload PDFs, buy
software, enable cloud integrations, or silently substitute profiles.

Firefox's built-in PDF viewer is the primary performance target. Native
instrumentation must first pass a generated fixture smoke test; failure means
unavailable measurements, never relabeled standalone PDF.js measurements.
Choose slow-page samples from original-page timings, never the production
density sampler. Preserve exact commands, versions, source/output hashes, logs,
durations, exit codes and raw observations. Run tools and documents sequentially,
keep environments separate, terminate owned trees on timeout, and retain a new
JSON record after operations. No automatic resume is requested.

Recommendations require existing Unicode/math/search text, bookmarks and zoom
targets, links and hit areas, page labels and attachments. Record forms, comments,
layers and other interaction changes separately. Handwriting annotation editability
may be lost in viewing copies. Never certify the meaning of altered handwriting.
Structural/extraction differences alone do not establish content loss. Use pass,
confirmed difference, unproven and check failed independently for each check.

## Matrix

| Tool | Profiles |
|---|---|
| Repository | viewing-copy, default, compact, inline, inline+compact, fast-handwriting, fast-handwriting+compact |
| JingJerYen/boox-pdf-optimizer | precision 6/2/1, color rasterization 300/200/150 DPI, grayscale 150 DPI |
| qpdf | Flate recompression + object streams, with/without linearization; no image optimization or normalization |
| MuPDF | duplicate objects/streams + compression + retained metadata/structure, with/without object packing |
| pdfsizeopt | documented default |
| OCRmyPDF | optimization 1/2/3, PDF output, skip existing text, no forced OCR |
| mattdesl/pdf-optimizer | default, quality 95 with no downsampling/off-page blanking, quality 85 at 200 DPI with no off-page blanking |
| Ghostscript | default pdfwrite, prepress, ebook, screen |
| Adobe Acrobat | imported conservative, 300-DPI, 150-DPI; recorded local settings and licensed installation |
| BOOX Note Optimizer | default optimization/export from matching `.note`; separate upstream workflow, local output/import |

Missing prerequisites must remain visible as not tested. Imported outputs must
declare source SHA-256, tool/profile, settings, version and path. Never compare
an import against a different source hash. Commands use trusted local prefixes
as argument arrays and `shell=False`; reject shell/batch wrappers.

## Measurement contract

Render every page using PDFium at 144 DPI and standalone PDF.js at width 1404.
Compare each renderer only against itself, without alignment, warping or resizing.
Report geometry changes separately. Recheck changed/representative content at
288 DPI and produce true 576-DPI crops. Release readers and arrays between pages.

Use source-derived handwriting regions when recognizable. Otherwise label the
analysis foreground content with uncertain handwriting isolation. Never discard
small components as noise. At thresholds 200/225/250 report missing and added ink
separately at 0/.025/.05/.1 mm tolerance; bidirectional skeleton distances
(median/p95/max); spatially matched thickness estimates; components, endpoints,
junctions and holes; foreground contrast/color and local SSIM. Empty masks must
retain undefined measurements as null; complete ink removal must report 100%
missing ink. Keep whole-page summaries and worst local damage. Topology changes
are potential defects subject to rasterization/antialiasing uncertainty.

Produce aligned before/after crops, overlays and heatmaps, with every flagged
region indexed and the ten highest-priority regions in the main gallery. Prefer
potential missing components/breaks/merges/loops, then displacement, thickness,
contrast and color. Include coordinates, page, metrics, resolutions and thresholds.

Measure native navigation completion, first visits on all pages, backward/forward
revisits, fresh sessions and page-width/100%/200% redraws on pages 1/7/29 when
present plus the original's three slowest pages. Freeze this sample for outputs.
Use browser monotonic timestamps and detect already rendered pages. Record browser,
geckodriver and bundled PDF.js versions, fixed viewport/scale/preferences, and
sampled owned-process-tree memory. Three balanced repetitions extend to five for
range/median > .25 or competing medians within 10%; zero timings are unresolved.
Exploratory timings are separate. No timed pass runs alongside optimization or
image analysis. Fresh sessions do not imply cold OS caches; sampled RSS is not peak.

## Delivery and verification

Deliver the CLI, native Firefox helper, pinned comparison requirements, focused
tests, setup documentation and example configuration. Reuse production readers,
rendering, hashing and signatures without fabricated external conversion manifests.
Generate report.md, results.json, summary.csv, separate speed-versus-quality plots,
review galleries, outputs and logs. Report nondominated choices per browser
operation without one combined quality score or minimum speedup requirement.

Recommendation classes: strict preservation candidate (supported checks pass,
tested appearance exact); potential visual tradeoff (required features retained,
changed appearance needs review); feature-loss diagnostic (confirmed required
feature differences); unproven; unavailable/failed. Disclose editability changes.

Calibrate with original-vs-original, tiny dots/decimals/short strokes, lost ink,
loops/breaks/merges/added ink, translations/thickness/faded/color changes,
rounding/rasterization/compression, thresholds/resolutions/empty masks, small local
damage, missing OCR/math/navigation/attachments/pages, equivalent rewrites,
execution errors/timeouts/interruption/isolation, source/output integrity, and
native viewer readiness/cache/page/zoom. Then run the full repository suite and
fresh generated compatibility/interaction fixtures. No private corpus or release
package is needed. Real-note calibration and semantic review await authorized files.
