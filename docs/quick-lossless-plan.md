# Direct viewing copies: implementation plan

Status: implemented; qualification is in progress. Native-browser acceptance
remains open. The [roadmap](roadmap.md) is the project index and
[design.md](design.md) defines the acceptance policies.

## Goal

Keep the useful transformations and comparable storage savings while removing
exhaustive optimizer comparisons and rendering-speed benchmarks from routine jobs.
Thirty seconds for a short PDF is an aspirational direction, not a timeout,
publication gate or reason to remove useful transformations. Page count alone
does not predict processing cost. Record actual elapsed time without claiming a
viewing-speed improvement.

The earlier compression-only implementation did not satisfy this objective. It
remains available through an explicit qpdf engine selection, but is not the GUI's
default direct recipe.

## One transformation chain

1. Validate input, snapshot its hash and reserve unused output/report paths.
   Reject encryption, signatures/fields, XFA, incremental revisions, unresolved
   parser/tool warnings and unsupported structures.
2. Inline supported handwriting Form calls exactly. Preserve drawing order,
   coordinates, widths, transforms and resources. If no eligible calls exist,
   continue with deduplication and cleanup; unexpected transformation errors fail.
3. Reuse safe resource deduplication and current-document cleanup. Preserve
   navigation, interactive identities and protected structures. Unreachable
   editor/recovery objects may be omitted from this separate viewing copy.
4. Pack that transformed copy once with bundled qpdf 12.4.2. Preserve existing
   stream encodings. Require strict graph/stream equivalence between transformed
   and packed copies. If packing fails or does not shrink it, retain the verified
   transformed copy. Do not generate a tournament of alternative optimizers.
5. For a smaller result, check syntax, ordered geometry/resources and navigation.
   Require exact all-page PDFium and PDF.js appearance, plus high-resolution,
   print and detail checks. Verification uses up to two bounded workers; this is
   preservation checking, not a speed benchmark. An unresolved check prevents
   publication. Browser interaction qualification remains a development gate.
6. Recheck source/output hashes, publish exclusively and write honest reports.
   Roll back owned partial outputs when publication/reporting fails.

Do not flatten live annotations, simplify/batch strokes, rasterize handwriting,
remove OCR, downsample images or introduce lossy codecs. No candidate-selection
benchmark, rendering CPU race, native-browser timing or automatic thorough retry
runs in this path. Originals and existing outputs remain untouched.

## Interfaces and recovery

The GUI label is **Direct optimization (no benchmarks)**. The existing
`--viewing-profile quick` token selects this path with the default adaptive engine;
`--viewing-optimizer qpdf` explicitly selects compression-only preservation of all
objects, including unreferenced payloads. Existing CLI/API callers retain thorough
defaults. The drag-and-drop launcher selects the direct profile.

Direct setup needs pypdf, PDFium, Pillow, Node/PDF.js and bundled qpdf. It does not
require PyMuPDF or a browser. `Setup Dependencies.cmd --quick` installs this subset.
The thorough path and its performance/browser gates remain unchanged.

Reports distinguish smaller-lossless, no size benefit, unsupported verification
and failed verification. They record transformation counts, packing choice,
verification coverage, elapsed stages and source/output hashes. Viewing performance
is explicitly not measured. Interrupted direct jobs restart; completed recovery
binds profile, recipe, code/tool/renderer identities and source/output hashes.

## Delivery and evidence

- [x] Restore exact inlining, deduplication, cleanup and packing in one chain.
- [x] Keep the strict compression comparator for the packing step and explicit
  qpdf-only lane, including exact numeric/stream/alias/orphan mutation coverage.
- [x] Add regression cases proving transformations remain, no benchmark or
  tournament runs, errors fail closed, packing has a safe fallback and elapsed
  time above thirty seconds does not reject an otherwise qualifying result.
- [x] Align GUI defaults, scoped setup, CLI validation and recovery identity.
- [x] Run focused/full regression tests and regenerated mixed/interactive fixtures.
- [x] Compare one complete direct run per authorized real input with its previous
  selected output. Compare savings first and report elapsed stages descriptively.
  Do not run an exhaustive optimizer comparison or require repeated thirty-second
  benchmark trials. Keep private paths and detailed results in ignored audit output.
- Package fresh previews after validation; keep byte-verification and extracted
  ZIP/installed-skill runtime smoke-test evidence with the ignored run records.
- [ ] Complete native-browser/interaction qualification when normal browser
  startup is available. Do not bypass browser security or claim this check passed.

## Next speed work

Use stage elapsed times to identify costs inside this chain. Investigate reusing
loaded documents and parsed signatures within a job, and reduce repeated renderer
startup while bounding memory and preserving cancellation. Keep renderer/resolution/
print settings distinct; cached evidence must match its exact verification context.
Validate changes with complete direct jobs and unchanged preservation coverage.
Do not restore an exhaustive optimizer comparison or discard useful transformations
to make a short-document timing target pass.
