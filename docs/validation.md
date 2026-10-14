# Implementation audit

Audit date: 2026-10-14. The source repository contains generated tests and these results; personal PDFs and local filesystem paths are excluded.

## Verified results

- 72 unit/regression tests pass both in the original workspace and from the repository layout.
- All 84 published pages (ME303, ME505 and Goodnotes) were rechecked: identical pixels in PDFium at 144 DPI and PDF.js at a 1404-pixel page width; strengthened geometry/resource and raw navigation comparisons pass.
- The 27-page generated vector/bitmap/mixed matrix renders identically in both engines. Its browser navigation checks pass, including all 27 bookmarks.
- The three-page interaction fixture passes internal-link zoom, editable/read-only/hidden fields, checkboxes, radio buttons, choices, comment popup and every-page layer toggles.
- The earlier 149-page corpus run included all 65 ME304 pages. Published copies also passed 288 DPI, print-mode and 576 DPI detail-crop checks; ME303 retains 13 bookmarks, ME505 retains 24, and Goodnotes retains six URL link annotations.

## Refreshed rendering measurements

Sequential fresh processes, alternating source/candidate order; three runs, extended to five for noisy CPU measurements. Positive reduction means less CPU. All three results remain **storage-only**, below the 10% faster threshold.

| Document | Original MB | Copy MB | Cold-render CPU reduction | Latency regression gate |
| --- | ---: | ---: | ---: | --- |
| ME303 | 165.860 | 75.167 | 6.0% | Pass |
| ME505 | 259.299 | 105.170 | 7.7% | Pass |
| Goodnotes | 1.393 | 1.389 | -1.8% | Pass |

Goodnotes used 1.8% more aggregate median cold-render CPU in this rerun. Its size reduction is only about 0.3%; it should not be described as a speed improvement. Individual page regressions and variability remain in the data below.

| Document / version / page | Runs | Preparation median (s) | Cold wall median [range] (s) | Cold CPU median (s) | Cached wall median (s) | RSS median (MiB) |
| --- | ---: | ---: | --- | ---: | ---: | ---: |
| ME303 / original / 1 | 5 | 0.141 | 0.237 [0.226, 0.242] | 0.375 | 0.058 | 382.4 |
| ME303 / original / 7 | 5 | 0.294 | 0.480 [0.448, 0.486] | 0.719 | 0.130 | 428.0 |
| ME303 / original / 14 | 5 | 4.197 | 6.219 [6.082, 6.587] | 7.422 | 1.711 | 1407.5 |
| ME303 / original / 29 | 5 | 3.837 | 4.298 [4.274, 4.376] | 5.406 | 0.393 | 1278.3 |
| ME303 / copy / 1 | 5 | 0.061 | 0.175 [0.169, 0.183] | 0.312 | 0.055 | 281.4 |
| ME303 / copy / 7 | 5 | 0.134 | 0.335 [0.320, 0.338] | 0.485 | 0.119 | 346.5 |
| ME303 / copy / 14 | 5 | 3.884 | 5.736 [5.711, 6.118] | 7.077 | 1.647 | 1362.9 |
| ME303 / copy / 29 | 5 | 3.672 | 4.143 [4.087, 4.165] | 5.218 | 0.378 | 1138.3 |
| ME505 / original / 1 | 3 | 1.808 | 2.339 [2.332, 2.353] | 3.141 | 0.414 | 945.4 |
| ME505 / original / 7 | 3 | 6.198 | 7.342 [7.321, 7.669] | 9.109 | 0.940 | 1904.9 |
| ME505 / original / 29 | 3 | 5.552 | 7.148 [6.942, 7.519] | 8.766 | 1.117 | 1792.1 |
| ME505 / original / 31 | 3 | 1.483 | 2.966 [2.956, 3.012] | 3.735 | 1.066 | 964.7 |
| ME505 / copy / 1 | 3 | 1.620 | 2.135 [2.091, 2.148] | 3.156 | 0.389 | 865.9 |
| ME505 / copy / 7 | 3 | 5.826 | 6.930 [6.871, 6.967] | 8.468 | 0.847 | 1708.3 |
| ME505 / copy / 29 | 3 | 5.195 | 6.475 [6.426, 6.588] | 8.156 | 1.021 | 1557.9 |
| ME505 / copy / 31 | 3 | 1.091 | 2.417 [2.417, 2.455] | 3.077 | 1.028 | 879.4 |
| Goodnotes / original / 1 | 5 | 0.030 | 0.123 [0.119, 0.125] | 0.234 | 0.022 | 172.5 |
| Goodnotes / original / 2 | 5 | 0.033 | 0.138 [0.137, 0.147] | 0.265 | 0.023 | 173.1 |
| Goodnotes / original / 3 | 5 | 0.031 | 0.135 [0.131, 0.140] | 0.234 | 0.022 | 172.2 |
| Goodnotes / original / 6 | 5 | 0.022 | 0.098 [0.097, 0.104] | 0.141 | 0.019 | 161.0 |
| Goodnotes / copy / 1 | 5 | 0.029 | 0.122 [0.119, 0.126] | 0.218 | 0.022 | 172.3 |
| Goodnotes / copy / 2 | 5 | 0.032 | 0.139 [0.135, 0.140] | 0.265 | 0.023 | 173.3 |
| Goodnotes / copy / 3 | 5 | 0.031 | 0.133 [0.129, 0.137] | 0.219 | 0.022 | 171.8 |
| Goodnotes / copy / 6 | 5 | 0.022 | 0.097 [0.095, 0.104] | 0.188 | 0.019 | 161.0 |

Preparation and cold rendering are measured in different processes and must not be added. RSS is sampled process memory, not peak memory. Browser forward/back sequences are functional checks, not a repeated scrolling benchmark. Raw individual runs, load timings, operation and annotation counts, and hashes are in [validation.json](validation.json).

## Defects fixed during the audit

- Complete PDF.js CMap, standard-font, ICC and WASM resource paths; strict parsing and a positive JPEG2000 pixel test prevent accepting blank/incomplete decoding.
- Image decoding parameters, Form matrices/layers, original container resources and converted appearance placement now participate in preservation checks.
- Signature detection includes orphan page widgets; empty inputs and XFA documents are rejected appropriately. Unknown broken layer references cannot use the BOOX exception.
- Private page data, retained annotation appearances and orphan field parents protect referenced handwriting objects from conversion. Private Form metadata keeps its original Form call.
- Viewing-copy batching checks nested direct dictionaries as well as indirect objects for transparency; inherited masks retain separate paint operations.
- Candidate builders release page snapshots and readers between phases. A regression test proves that a previous writer is collectable before another writer is built.
- Source hashes are captured before preflight; failed final candidates cannot leave successful acceptance fields in a no-improvement report.
- Browser navigation waits for rendering, reports the stalled phase, tests layer states on every page and uses the correct scrolling container for mixed-size pages.
- Repository-relative tests, local Node dependency lookup, portable launchers and one-source release/skill packaging remove workspace-specific requirements.

## ME304 and remaining limits

The earlier ME304 annotation/consolidation candidates retained geometry and passed PDFium, but failed the strict PDF.js one-pixel edge-band rule. Smaller packed originals regressed in rendering. No ME304 replacement was published. The current full stress rerun is pending; this report does not claim it has completed.

JBIG2-specific fixtures, every malformed export, live Zen profile/extensions, Zen bundled viewer behavior, and other platforms were not tested. Scripted/XFA forms remain unsupported by acceptance; external URI/file targets are compared structurally without opening them. Keep originals as the reference and editable copies.
