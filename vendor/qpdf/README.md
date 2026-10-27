# Bundled qpdf runtime

This directory contains the Windows x64 runtime files from
`qpdf-12.4.2-msvc64.zip` (archive SHA-256
`DB87077E683630C1217E0E8F9A20A9749D952AB676E881C3689187763A5DE25D`).
Only the runtime `bin/` directory is bundled; headers, libraries and the full manual
are excluded. The applicable qpdf license is preserved in `LICENSE.html`.

The optimizer prefers `bin/qpdf.exe` on Windows. `PDF_QPDF` remains an explicit
override for testing or a separately managed qpdf installation.
