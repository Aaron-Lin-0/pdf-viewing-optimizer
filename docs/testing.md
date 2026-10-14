# Testing

Install `requirements-dev.txt` and the Node packages before running tests. Tests generate PDFs in temporary directories; no private notes or downloaded fixtures are required.

```powershell
python -m unittest discover -s tests -p 'test_*.py'
```

The unit/regression suite covers geometry, navigation mutations, annotation conversion, shared resources, transparency, malformed references, output collisions, interrupted writes, signatures, dependency failures, memory ownership and candidate selection. One test decodes a JPEG2000 image and checks its colored pixels, preventing two blank renderings from passing as equivalent.

## Generated compatibility matrix

```powershell
python tests/run_compatibility.py fixtures
$env:PDF_BROWSER = 'C:\Program Files\Zen Browser\zen.exe'
python tests/run_compatibility.py browser
```

Results go into ignored `audit-output/`. Fixture generation refuses to overwrite an existing matrix. Use `--output` with another folder to repeat it. The browser step reads the fixtures in that folder and starts only disposable browser profiles.

The 27-page matrix includes RGB/grayscale/CMYK JPEG, JPEG2000, Flate RGB/grayscale/1-bit, CCITT Group 4, indexed color, ICC color, soft/stencil/color-key masks, predictors, inline images, mixed vector/raster/OCR, rotation/cropping/UserUnit, transparency, shading, tiling patterns, transformed/clipped Forms, standard/Type3/embedded TrueType/CID fonts, shared resources and a blank page. Every page has a bookmark; named targets, labels and an attachment are also present.

A separate three-page fixture covers internal-link zoom, comments, editable/read-only/hidden fields, checkboxes, radio buttons, choices and a layer that changes only page three. Browser assertions check actual state changes, including opening a comment and switching the layer on/off.

To exercise the complete selection and publication pipeline on the generated matrix:

```powershell
python optimize_pdf.py audit-output/matrix.pdf audit-output/matrix.viewing.pdf --viewing-copy
```

A reports-only **no improvement** outcome is valid. Never force publication when the checks reject all candidates.

## Real documents and timing

Run one document at a time with `--viewing-copy`. Keep the `.md` and `.json` reports; they include source/output hashes, candidate decisions, preservation results and raw timing runs. The source is never rewritten.

Compare medians and variability using the same machine, dependency versions, annotation settings and page samples. Do not benchmark alongside other renderers or heavy tests. Operator preparation, cold rendering, cached drawing and browser page sequences are different measurements. Avoid adding their timings or describing a smaller file as faster without a measured result.

The private corpus results are summarized in [validation.md](validation.md). Personal PDFs and raw local paths are deliberately excluded from the repository. The original corpus cannot be reconstructed from this checkout.
