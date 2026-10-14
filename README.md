# PDF viewing optimizer

Create a separate PDF viewing copy while preserving handwriting geometry, search text, bookmarks and other navigation. The optimizer compares rendering cost as well as file size and publishes a copy only after its checks pass.

Supports vector, bitmap/scanned and mixed PDFs. BOOX handwriting has dedicated optimizations; unfamiliar drawing structures receive conservative storage optimizations. Smaller files are not necessarily faster to render.

Original files and existing output/report files are never overwritten. No personal PDFs are included in this repository; tests generate their own fixtures.

## Install

Use Python 3.10+, Node.js 22.13+ and an installed Zen or Firefox browser. Windows with Zen is the tested environment.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm install
$env:PDF_BROWSER = 'C:\Program Files\Zen Browser\zen.exe'
```

Keep the complete `pdfjs-dist` package, including its font, CMap, ICC and WASM resources. The renderer needs these to decode some PDFs correctly. `PDF_NODE`, `PDF_NODE_MODULES` and `PDF_BROWSER` can select alternate installed runtimes. Without local Node dependencies, the scripts can reuse Codex's bundled runtime.

## Create a viewing copy

```powershell
.\.venv\Scripts\python.exe optimize_pdf.py 'notes.pdf' --viewing-copy
```

The output is `notes.viewing.pdf`, with `.md` and `.json` reports beside it. You can provide an explicit output path as the second argument. If no candidate qualifies, the command saves reports with status **no improvement** and does not publish a replacement PDF.

For drag and drop, set `PDF_PYTHON` to the installed interpreter, then drop one PDF onto **Optimize Viewing PDF.cmd**. Keep the launcher and program files together.

Viewing-copy mode:

- Preserves stroke coordinates, widths, curves, dots and loops. It does not simplify or rasterize handwriting.
- Retains visible text, OCR/search text, image data, bookmarks, named destinations, links, attachments and protected interactive objects.
- May convert eligible static BOOX handwriting annotations to ordinary vector content. Those strokes are no longer individually editable as annotations in the viewing copy.
- Rejects encrypted or signed inputs, including empty signature fields.
- Uses a disposable browser profile. It does not change your browser preferences.

Run one document at a time. Large BOOX documents can take substantial time and memory because every candidate is checked before publication.

## Other modes

```powershell
python optimize_pdf.py input.pdf output.optimized.pdf
python optimize_pdf.py input.pdf output.inlined.pdf --inline-handwriting
python optimize_pdf.py input.pdf output.fast-vector.pdf --fast-handwriting
python optimize_pdf.py input.pdf output.no-ocr.pdf --remove-invisible-text
python optimize_pdf.py input.pdf output.compact.pdf --compact-structure
```

Default and inlining modes require identical tested pixels. `--fast-handwriting` permits its legacy edge-shading tolerance and has weaker acceptance checks than viewing-copy mode. `--remove-invisible-text` explicitly discards verified invisible OCR blocks, losing their search/copy text. It is never enabled automatically. Existing Python callers can continue to use `optimize_pdf.optimize(...)`.

## What is verified

Every candidate is checked against raw navigation structures and drawing/resource signatures, then rendered at 144 DPI in PDFium. Final candidates also undergo all-page PDF.js comparison, high-resolution and print samples, and browser interaction checks. Exact transformations require identical tested pixels. Edge-changing transformations must preserve geometry and meet a strict one-pixel edge-band limit.

The benchmark measures preparation, cold rendering and cached drawing separately in fresh processes. A **faster** classification requires at least 10% less aggregate median cold-render CPU and no significant sampled latency regression. A smaller exact copy can qualify without meeting the faster threshold, provided CPU stays within 5% of the original. Reports include individual runs, variability and rejected candidates.

See [design and acceptance rules](docs/design.md), [test instructions](docs/testing.md), [validation results and limitations](docs/validation.md), and the [complete mode reference](README.txt).

## Develop and package

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
.\.venv\Scripts\python.exe tools/build_release.py
```

The repository root is the maintained implementation. The packaging script copies those same files into a ZIP and a Codex skill, then compares their bytes. Generated releases go under `dist/` and are not committed. No duplicate implementation is maintained inside the skill source directory.

Tests cannot guarantee identical behavior in every viewer, zoom level or interactive state. Scripted/XFA forms are outside the viewing-copy acceptance harness, and external action targets are compared without opening them. Keep originals as the reference and editable copies.
