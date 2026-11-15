# BOOX renderer source

The optional `.note` converter uses a patched fork of
[niw/boox-notes-renderer](https://github.com/niw/boox-notes-renderer).
`source.json` pins the upstream commit, patch checksum and resulting Rust sources.
`fork.patch` contains the strict conversion, width and preservation repairs.
The upstream MIT copyright and license are retained in `LICENSE`.

Prebuilt executables are local build artifacts and are not committed or included
in the optimizer ZIP. This avoids distributing an opaque binary without a complete
transitive dependency notice inventory. Rust dependencies remain pinned by the
upstream Cargo.lock. Binary redistribution needs a separate license/notice review.

From the optimizer repository, with Git and stable Rust/Cargo installed:

```powershell
.\.venv\Scripts\python.exe tools/build_boox_renderer.py `
  --work audit-output/renderer-source-NEW `
  --output audit-output/renderer-NEW.exe
$env:BOOX_NOTES_RENDERER = (Resolve-Path audit-output/renderer-NEW.exe).Path
```

Both destinations must be new. The builder fetches public source, checks out the
exact pin, checks and applies the patch, verifies every listed source hash and
builds with `--locked`. It does not copy private notebooks or machine configuration.
The converter also accepts `--renderer PATH`. End users of ordinary PDF modes
need neither Rust nor this optional renderer.
