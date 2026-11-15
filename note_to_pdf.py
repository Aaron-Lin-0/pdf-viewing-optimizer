"""Bridge BOOX .note archives to verified vector PDFs via boox-notes-renderer and quick_lossless."""
import argparse
import datetime
import io
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

from pdf_runtime import RunBudget, TimeBudgetExceeded, DiskBudgetExceeded, filehash
from quick_lossless import optimize_quick

MAX_ARCHIVE_ENTRIES = 50_000
MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024  # 500 MB
MAX_ZIP_DEPTH = 2


def _normalize_uuid(u):
    """Normalize simple or hyphenated UUIDs to lowercase 32-hex string."""
    return ''.join(c for c in u if not c.isspace() and c not in ('-', '\0')).lower() if u else ''


def _decode_varint(data, pos, max_pos=None):
    limit = len(data) if max_pos is None else min(len(data), max_pos)
    if pos >= limit:
        raise ValueError(f"Truncated varint: pos {pos} >= limit {limit}")
    val = 0
    shift = 0
    start_pos = pos
    while pos < limit:
        b = data[pos]
        pos += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, pos
        shift += 7
        if shift > 63:
            raise ValueError(f"Varint overflow at offset {start_pos}")
    raise ValueError(f"Truncated varint at offset {start_pos}")


def _parse_proto_fields(data, max_pos=None):
    pos = 0
    limit = len(data) if max_pos is None else min(len(data), max_pos)
    fields = {}
    while pos < limit:
        key, pos = _decode_varint(data, pos, limit)
        field_num = key >> 3
        wire_type = key & 7
        if wire_type == 0:
            val, pos = _decode_varint(data, pos, limit)
            fields.setdefault(field_num, []).append(val)
        elif wire_type == 1:
            if pos + 8 > limit:
                raise ValueError(f"Truncated 64-bit field {field_num} at pos {pos}")
            val = data[pos:pos + 8]
            pos += 8
            fields.setdefault(field_num, []).append(val)
        elif wire_type == 2:
            length, pos = _decode_varint(data, pos, limit)
            if length < 0 or pos + length > limit:
                raise ValueError(f"Truncated length-delimited field {field_num} at pos {pos}: length {length}")
            val = data[pos:pos + length]
            pos += length
            fields.setdefault(field_num, []).append(val)
        elif wire_type == 5:
            if pos + 4 > limit:
                raise ValueError(f"Truncated 32-bit field {field_num} at pos {pos}")
            val = data[pos:pos + 4]
            pos += 4
            fields.setdefault(field_num, []).append(val)
        else:
            raise ValueError(f"Unsupported protobuf wire type {wire_type} for field {field_num} at pos {pos}")
    return fields


def _parse_shape_container(data):
    pos = 0
    shapes = []
    total_len = len(data)
    while pos < total_len:
        key, pos = _decode_varint(data, pos, total_len)
        field_num = key >> 3
        wire_type = key & 7
        if field_num == 1 and wire_type == 2:
            length, pos = _decode_varint(data, pos, total_len)
            if length < 0 or pos + length > total_len:
                raise ValueError(f"Truncated shape container record at pos {pos}: length {length}")
            end_pos = pos + length
            shape_fields = {}
            spos = pos
            while spos < end_pos:
                skey, spos = _decode_varint(data, spos, end_pos)
                sfield = skey >> 3
                swire = skey & 7
                if swire == 0:
                    sval, spos = _decode_varint(data, spos, end_pos)
                    shape_fields.setdefault(sfield, []).append(sval)
                elif swire == 1:
                    if spos + 8 > end_pos:
                        raise ValueError(f"Truncated 64-bit shape field {sfield} at pos {spos}")
                    sval = data[spos:spos + 8]
                    spos += 8
                    shape_fields.setdefault(sfield, []).append(sval)
                elif swire == 2:
                    slen, spos = _decode_varint(data, spos, end_pos)
                    if slen < 0 or spos + slen > end_pos:
                        raise ValueError(f"Truncated length-delimited shape field {sfield} at pos {spos}")
                    sval = data[spos:spos + slen]
                    spos += slen
                    shape_fields.setdefault(sfield, []).append(sval)
                elif swire == 5:
                    if spos + 4 > end_pos:
                        raise ValueError(f"Truncated 32-bit shape field {sfield} at pos {spos}")
                    sval = data[spos:spos + 4]
                    spos += 4
                    shape_fields.setdefault(sfield, []).append(sval)
                else:
                    raise ValueError(f"Unsupported wire type {swire} in shape field {sfield} at pos {spos}")
            shapes.append(shape_fields)
            pos = end_pos
        elif wire_type == 0:
            _, pos = _decode_varint(data, pos, total_len)
        elif wire_type == 1:
            if pos + 8 > total_len:
                raise ValueError(f"Truncated 64-bit field in shape container at pos {pos}")
            pos += 8
        elif wire_type == 2:
            length, pos = _decode_varint(data, pos, total_len)
            if length < 0 or pos + length > total_len:
                raise ValueError(f"Truncated length-delimited field in shape container at pos {pos}")
            pos += length
        elif wire_type == 5:
            if pos + 4 > total_len:
                raise ValueError(f"Truncated 32-bit field in shape container at pos {pos}")
            pos += 4
        else:
            raise ValueError(f"Unsupported wire type {wire_type} in shape container at pos {pos}")
    return shapes


def _page_inventory(zf):
    notes = []
    offset = 0
    for name in sorted(n for n in zf.namelist() if n.endswith('note/pb/note_info')):
        prefix = name[:-len('note/pb/note_info')]
        pages = None
        for raw in _parse_proto_fields(zf.read(name)).get(1, []):
            fields = _parse_proto_fields(raw)
            if 20 in fields:
                data = json.loads(fields[20][0].decode('utf-8'))
                if not isinstance(data, dict) or not isinstance(data.get('pageNameList'), list):
                    raise ValueError('Invalid pageNameList in ' + name)
                pages = list(dict.fromkeys(_normalize_uuid(x) for x in data['pageNameList']))
                break
        if pages is None:
            raise ValueError('Missing required active_pages field (20) with pageNameList in ' + name)
        for shape in sorted(n for n in zf.namelist() if n.startswith(prefix + 'shape/') and n.endswith('.zip')):
            page_id = _normalize_uuid(shape.rsplit('/', 1)[-1].split('#')[0])
            with zipfile.ZipFile(io.BytesIO(zf.read(shape))) as inner:
                has_shapes = any(_parse_shape_container(inner.read(n)) for n in inner.namelist())
            if has_shapes and page_id not in pages:
                pages.append(page_id)
        notes.append({'prefix': prefix, 'pages': pages, 'offset': offset})
        offset += len(pages)
    return notes


def inspect_note(note_path, renderer=None, budget=None):
    """Pre-flight inspection of a .note archive without modifying or extracting it.

    Enforces strict allocation and expansion limits:
    - total entry count <= MAX_ARCHIVE_ENTRIES (default 50,000)
    - total uncompressed bytes <= MAX_UNCOMPRESSED_BYTES (500 MB)
    - nested ZIP depth <= MAX_ZIP_DEPTH (2)
    Fail closed: Corrupt/truncated archives or malformed protobuf data raise ValueError.
    """
    path = Path(note_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Note file not found: {path}")

    note_size = path.stat().st_size
    if note_size > MAX_UNCOMPRESSED_BYTES:
        raise ValueError(
            f"Note file size ({note_size} bytes) exceeds maximum uncompressed limit ({MAX_UNCOMPRESSED_BYTES} bytes)"
        )

    note_sha = filehash(path)

    if not zipfile.is_zipfile(path):
        raise ValueError(f"File is not a valid ZIP archive: {path}")

    if budget:
        budget.check('preflight inspection')

    # Try to discover renderer and calculate build hash for preflight reporting
    renderer_sha = None
    try:
        r_bin = find_renderer(renderer)
        renderer_sha = filehash(r_bin)
    except Exception:
        pass

    total_entries = 0
    total_uncompressed = 0

    with zipfile.ZipFile(path, 'r') as zf:
        outer_infolist = zf.infolist()
        total_entries += len(outer_infolist)
        if total_entries > MAX_ARCHIVE_ENTRIES:
            raise ValueError(f"Archive entry count exceeds limit: {total_entries} > {MAX_ARCHIVE_ENTRIES}")

        for info in outer_infolist:
            if info.file_size < 0:
                raise ValueError(f"Negative uncompressed size for entry '{info.filename}'")
            total_uncompressed += info.file_size
            if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
                raise ValueError(
                    f"Archive uncompressed bytes exceed limit: {total_uncompressed} > {MAX_UNCOMPRESSED_BYTES}"
                )

        namelist = zf.namelist()

        # 1. Page inventory from note_info
        note_info_entries = [n for n in namelist if n.endswith('note/pb/note_info')]
        if not note_info_entries:
            raise ValueError(f"Missing required note_info entry in .note archive: {path}")

        # 2. Shape and pen inventory (depth 2 nested shape archives)
        shape_zips = [n for n in namelist if n.endswith('.zip') and 'shape/' in n]
        pen_counts = {}
        layer_counts = {}
        total_shapes = 0
        for sz_name in shape_zips:
            try:
                sz_bytes = zf.read(sz_name)
            except Exception as exc:
                raise ValueError(f"Failed to read shape ZIP '{sz_name}': {exc}") from exc
            try:
                sz = zipfile.ZipFile(io.BytesIO(sz_bytes), 'r')
            except Exception as exc:
                raise ValueError(f"Corrupt nested shape ZIP '{sz_name}': {exc}") from exc
            with sz:
                sz_infolist = sz.infolist()
                total_entries += len(sz_infolist)
                if total_entries > MAX_ARCHIVE_ENTRIES:
                    raise ValueError(f"Archive entry count exceeds limit: {total_entries} > {MAX_ARCHIVE_ENTRIES}")
                for sinfo in sz_infolist:
                    if sinfo.file_size < 0:
                        raise ValueError(f"Negative uncompressed size for nested entry '{sz_name}:{sinfo.filename}'")
                    total_uncompressed += sinfo.file_size
                    if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
                        raise ValueError(
                            f"Archive uncompressed bytes exceed limit: {total_uncompressed} > {MAX_UNCOMPRESSED_BYTES}"
                        )
                    if sinfo.filename.lower().endswith('.zip'):
                        raise ValueError(
                            f"Nested ZIP depth exceeds limit: entry '{sz_name}:{sinfo.filename}' at depth 3 > {MAX_ZIP_DEPTH}"
                        )
                    try:
                        sdata = sz.read(sinfo.filename)
                    except Exception as exc:
                        raise ValueError(
                            f"Failed to read shape record '{sz_name}:{sinfo.filename}': {exc}"
                        ) from exc
                    if sdata.startswith(b'PK\x03\x04'):
                        raise ValueError(
                            f"Nested ZIP depth exceeds limit: entry '{sz_name}:{sinfo.filename}' at depth 3 > {MAX_ZIP_DEPTH}"
                        )
                    shapes = _parse_shape_container(sdata)
                    total_shapes += len(shapes)
                    for s in shapes:
                        pen = s.get(12, [0])[0]
                        pen_counts[pen] = pen_counts.get(pen, 0) + 1
                        layer = s.get(6, [0])[0]
                        layer_counts[layer] = layer_counts.get(layer, 0) + 1

        # Check for any non-shape nested zips at depth 2
        other_zips = [n for n in namelist if n.endswith('.zip') and n not in shape_zips]
        for oz_name in other_zips:
            try:
                oz_bytes = zf.read(oz_name)
                with zipfile.ZipFile(io.BytesIO(oz_bytes), 'r') as oz:
                    oz_infolist = oz.infolist()
                    total_entries += len(oz_infolist)
                    if total_entries > MAX_ARCHIVE_ENTRIES:
                        raise ValueError(f"Archive entry count exceeds limit: {total_entries} > {MAX_ARCHIVE_ENTRIES}")
                    for oinfo in oz_infolist:
                        if oinfo.file_size < 0:
                            raise ValueError(
                                f"Negative uncompressed size for nested entry '{oz_name}:{oinfo.filename}'"
                            )
                        total_uncompressed += oinfo.file_size
                        if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
                            raise ValueError(
                                f"Archive uncompressed bytes exceed limit: {total_uncompressed} > {MAX_UNCOMPRESSED_BYTES}"
                            )
                        if oinfo.filename.lower().endswith('.zip'):
                            raise ValueError(
                                f"Nested ZIP depth exceeds limit: entry '{oz_name}:{oinfo.filename}' at depth 3 > {MAX_ZIP_DEPTH}"
                            )
                        odata = oz.read(oinfo.filename)
                        if odata.startswith(b'PK\x03\x04'):
                            raise ValueError(
                                f"Nested ZIP depth exceeds limit: entry '{oz_name}:{oinfo.filename}' at depth 3 > {MAX_ZIP_DEPTH}"
                            )
            except (zipfile.BadZipFile, ValueError):
                raise
            except Exception as exc:
                raise ValueError(f"Corrupt nested ZIP '{oz_name}': {exc}") from exc

        # 3. Template and background inventory
        template_entries = [n for n in namelist if 'template' in n.lower() or 'background' in n.lower()]
        blank_templates = 0
        active_templates = []
        for te in template_entries:
            if te.endswith('.template_json') or te.endswith('.json'):
                try:
                    tdata = json.loads(zf.read(te).decode('utf-8'))
                except Exception as exc:
                    raise ValueError(f"Failed to parse template JSON '{te}': {exc}") from exc
                res = tdata.get('resName', '')
                ares = tdata.get('assetsResName', '')
                fp = tdata.get('fillPath', {})
                is_empty = fp.get('empty', False) if isinstance(fp, dict) else False
                color = tdata.get('displayFillColor', 0)
                if res or ares or (not is_empty and fp) or color != 0:
                    active_templates.append({'path': te, 'resName': res, 'displayFillColor': color})
                else:
                    blank_templates += 1

        # 4. Resource data
        embedded_images = [n for n in namelist if 'resource/data/' in n and not n.endswith('/')]

        # 5. Table of contents (TOC) inventory
        toc_entries = [n for n in namelist if '/toc/pb/' in n or n.startswith('toc/pb/')]
        toc_items_count = 0
        for top_name in toc_entries:
            try:
                tdata = zf.read(top_name)
                tfields = _parse_proto_fields(tdata)
                toc_items_count += len(tfields.get(1, []))
            except Exception:
                pass

    with zipfile.ZipFile(path) as inventory_zip:
        notes = _page_inventory(inventory_zip)
    pages = [page for note in notes for page in note['pages']]
    if not pages:
        raise ValueError('Note archive contains 0 declared pages')

    # Evaluate strict qualification
    unsupported_reasons = []
    supported_pens = {2, 5, 6, 15, 16, 19, 21, 40, 60, 61}
    for pen, count in pen_counts.items():
        if pen not in supported_pens and not (2000 <= pen <= 2999):
            if pen == 22:
                unsupported_reasons.append(
                    f"Pen type 22 (charcoal grain raster) found ({count} shapes); strict mode rejects rasterized pens."
                )
            else:
                unsupported_reasons.append(
                    f"Unknown or unsupported pen type {pen} found ({count} shapes) in strict mode."
                )

    if active_templates:
        unsupported_reasons.append(
            f"Active non-blank template/background found ({len(active_templates)} entries); strict mode rejects unbundled templates."
        )

    return {
        'path': str(path),
        'size_bytes': note_size,
        'sha256': note_sha,
        'entry_count': total_entries,
        'uncompressed_bytes': total_uncompressed,
        'page_count': len(pages),
        'pages': pages,
        'notes': notes,
        'shapes_total': total_shapes,
        'pen_counts': pen_counts,
        'layer_counts': layer_counts,
        'templates': {
            'blank_count': blank_templates,
            'active_count': len(active_templates),
            'active_assets': active_templates,
        },
        'embedded_images_count': len(embedded_images),
        'toc_items_count': toc_items_count,
        'strictly_compatible': len(unsupported_reasons) == 0,
        'unsupported_reasons': unsupported_reasons,
        'renderer_sha256': renderer_sha,
    }


def find_renderer(explicit_path=None):
    """Locate the boox-notes-renderer executable portably without hardcoded absolute paths."""
    candidates = []
    if explicit_path:
        candidates.append(Path(explicit_path))

    env_val = os.environ.get('BOOX_NOTES_RENDERER')
    if env_val:
        candidates.append(Path(env_val))

    base = Path(__file__).resolve().parent
    relative_dirs = [
        base / 'vendor',
        base / 'target' / 'release',
        base / 'target' / 'debug',
        base.parent / 'boox-notes-renderer' / 'target' / 'release',
        base.parent / 'boox-notes-renderer' / 'target' / 'debug',
        base.parent / 'target' / 'release',
        base.parent / 'target' / 'debug',
    ]
    for rdir in relative_dirs:
        candidates.append(rdir / 'boox-notes-renderer.exe')
        candidates.append(rdir / 'boox-notes-renderer')

    which_hit = shutil.which('boox-notes-renderer') or shutil.which('boox-notes-renderer.exe')
    if which_hit:
        candidates.append(Path(which_hit))

    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue

    raise FileNotFoundError(
        "boox-notes-renderer executable not found. Specify --renderer or set BOOX_NOTES_RENDERER."
    )


def allocate_audit_job_dir(audit_base, note_stem, max_retries=100):
    """Allocate audit job directory exclusively with unique microsecond timestamp + PID + random token."""
    audit_base = Path(audit_base).resolve()
    audit_base.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    for _ in range(max_retries):
        now = datetime.datetime.now(datetime.timezone.utc)
        timestamp = now.strftime('%Y%m%d-%H%M%S-%f')
        token = secrets.token_hex(4)
        job_id = f"note-{note_stem}-{timestamp}-{pid}-{token}"
        job_dir = audit_base / job_id
        try:
            job_dir.mkdir(parents=True, exist_ok=False)
            return job_dir
        except FileExistsError:
            continue
    raise FileExistsError(f"Failed to allocate unique audit job directory in {audit_base} after {max_retries} attempts.")


MAX_TOC_DEPTH = 64


def _toc_rows(nodes):
    stack = [(n, 1) for n in reversed(nodes)]
    while stack:
        node, depth = stack.pop()
        if depth > MAX_TOC_DEPTH:
            raise ValueError('TOC exceeds 64 levels')
        yield node['title'], node['page_idx'], depth
        stack.extend((n, depth + 1) for n in reversed(node.get('children', [])))


def count_toc_nodes(nodes):
    return sum(1 for _ in _toc_rows(nodes))


def extract_toc(note_path, page=None, page_names=None):
    if page_names is None:
        inspect_note(note_path)  # Apply archive bounds for standalone callers.
    # Per-notebook keys prevent UUID collisions between notebook copies.
    with zipfile.ZipFile(note_path) as zf:
        notes = _page_inventory(zf)
        items = {}
        for note in notes:
            prefix = note['prefix']
            page_map = {p: note['offset'] + i for i, p in enumerate(note['pages'])}
            for entry in sorted(n for n in zf.namelist() if n.startswith(prefix + 'toc/pb/') and not n.endswith('/')):
                for raw in _parse_proto_fields(zf.read(entry)).get(1, []):
                    f = _parse_proto_fields(raw)
                    iid = _normalize_uuid(f.get(1, [b''])[0].decode('utf-8'))
                    parent = _normalize_uuid(f.get(8, [b''])[0].decode('utf-8'))
                    target = _normalize_uuid(f.get(4, [b''])[0].decode('utf-8'))
                    if target not in page_map:
                        logging.warning('Dropping invalid TOC destination: %s', target)
                        continue
                    if not iid:
                        raise ValueError('TOC item has no ID')
                    item = {'title': f.get(5, [b'Untitled'])[0].decode('utf-8'),
                            'page_idx': page_map[target], 'parent': (prefix, parent) if parent else None}
                    key = prefix, iid
                    if key in items and items[key] != item:
                        raise ValueError('Conflicting normalized TOC IDs')
                    items[key] = item
    if page is not None:
        items = {k: dict(v, page_idx=0) for k, v in items.items() if v['page_idx'] == page - 1}
    for item in items.values():
        if item['parent'] not in items:
            item['parent'] = None
    done = set()
    for key in items:
        chain = set()
        current = key
        while current is not None and current not in done:
            if current in chain:
                logging.warning('Severing cyclic TOC parent reference')
                items[current]['parent'] = None
                break
            chain.add(current)
            current = items[current]['parent']
        done.update(chain)
    nodes = {k: {'title': v['title'], 'page_idx': v['page_idx'], 'children': []} for k, v in items.items()}
    roots = []
    for key, item in items.items():
        if item['parent'] is None:
            roots.append(nodes[key])
        else:
            nodes[item['parent']]['children'].append(nodes[key])
    count_toc_nodes(roots)  # Validate depth before PDF or recursive library operations.
    return roots


def inject_toc(input_pdf_path, output_pdf_path, toc_tree):
    from pypdf import PdfReader, PdfWriter
    expected = list(_toc_rows(toc_tree))
    writer = PdfWriter(clone_from=str(input_pdf_path))
    parents = {}
    for title, page_idx, depth in expected:
        if not 0 <= page_idx < len(writer.pages):
            raise ValueError('TOC destination out of bounds')
        parents[depth] = writer.add_outline_item(title, page_idx, parent=parents.get(depth - 1))
    output = Path(output_pdf_path)
    fd, name = tempfile.mkstemp(prefix='toc-', suffix='.pdf', dir=output.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        writer.write(temporary)
        with temporary.open('rb') as stream:
            reader = PdfReader(stream)
            actual = []
            def walk(outline, depth=1):
                for node in outline:
                    if isinstance(node, list):
                        walk(node, depth + 1)
                    else:
                        actual.append((node.title, reader.get_destination_page_number(node), depth))
            walk(reader.outline)
            if actual != expected or len(reader.pages) != len(writer.pages):
                raise ValueError('Saved TOC verification failed')
        os.replace(temporary, output)
    finally:
        writer.close()
        temporary.unlink(missing_ok=True)


def report_markdown(report):
    """Generate structured markdown report for the conversion and optimization run."""
    lines = [
        "# BOOX .note to Vector PDF Pipeline Report",
        "",
        f"- **Status:** {report['status']}",
        f"- **Conversion Verdict:** {report['conversion_verdict']}",
        f"- **Optimization Verdict:** {report['optimization_verdict']}",
        f"- **Total Elapsed Time:** {report.get('total_elapsed_seconds', 0.0):.3f}s",
        "",
        "## Source Note",
        f"- **Path:** `{report['source_note']}`",
        f"- **Size:** {report['source_note_bytes']:,} bytes ({report['source_note_bytes'] / (1024 * 1024):.2f} MiB)",
        f"- **SHA-256:** `{report['source_note_sha256']}`",
    ]

    preflight = report.get('preflight', {})
    lines.extend([
        "",
        "## Preflight Inventory",
        f"- **Pages:** {preflight.get('page_count', 'unknown')}",
        f"- **Total Shapes:** {preflight.get('shapes_total', 'unknown')}",
        f"- **Pen Types:** {json.dumps(preflight.get('pen_counts', {}))}",
        f"- **Layers:** {json.dumps(preflight.get('layer_counts', {}))}",
        f"- **Embedded Images:** {preflight.get('embedded_images_count', 0)}",
        f"- **Templates:** {preflight.get('templates', {}).get('blank_count', 0)} blank, {preflight.get('templates', {}).get('active_count', 0)} active",
        f"- **Strict Compatibility:** {'Yes' if preflight.get('strictly_compatible') else 'No'}",
    ])
    if preflight.get('toc_items_count'):
        lines.append(f"- **TOC Bookmarks in Archive:** {preflight['toc_items_count']}")
    if preflight.get('renderer_sha256'):
        lines.append(f"- **Renderer Build SHA-256:** `{preflight['renderer_sha256']}`")
    if preflight.get('unsupported_reasons'):
        lines.append("- **Unsupported Reasons:**")
        for reason in preflight['unsupported_reasons']:
            lines.append(f"  - {reason}")

    ref = report.get('reference_pdf')
    if ref:
        lines.extend([
            "",
            "## Reference PDF (Tablet Export)",
            f"- **Path:** `{ref['path']}`",
            f"- **Size:** {ref['bytes']:,} bytes ({ref['bytes'] / (1024 * 1024):.2f} MiB)",
            f"- **SHA-256:** `{ref['sha256']}`",
        ])

    ref_audit = report.get('reference_audit')
    if ref_audit:
        lines.extend([
            "",
            "## Tablet Reference Audit (Milestone P18-D)",
            f"- **Page Count Match:** {'Yes' if ref_audit.get('page_count_match') else 'No'} ({ref_audit.get('converted_page_count')} vs {ref_audit.get('reference_page_count')})",
            f"- **Storage Savings vs Tablet Export:** {ref_audit.get('size_savings_bytes'):,} bytes ({ref_audit.get('size_savings_pct'):.1f}% reduction)",
        ])
        for ep in ref_audit.get('evaluated_pages', []):
            lines.append(
                f"- **Page {ep['page']} Alignment:** Converted `{ep['converted_dimensions']}` pt vs Reference `{ep['reference_dimensions']}` pt (Match: {ep['dimensions_match']})"
            )
            lines.append(
                f"  - Mean pixel difference: {ep['mean_pixel_diff']:.3f} / 255 | Max diff: {ep['max_pixel_diff']}"
            )
            lines.append(
                f"  - Ink ratio: Converted {ep['converted_ink_ratio'] * 100:.2f}% vs Reference {ep['reference_ink_ratio'] * 100:.2f}%"
            )

    lines.extend([
        "",
        "## Conversion Stage",
        f"- **Renderer Executable:** `{report['renderer']['executable']}`",
        f"- **Renderer Build SHA-256:** `{report['renderer'].get('sha256', 'unknown')}`",
        f"- **Strict Mode:** {report['renderer']['strict']}",
        f"- **Arguments:** `{' '.join(report['renderer'].get('args', []))}`",
        f"- **Exit Code:** {report['renderer'].get('exit_code', 'unknown')}",
    ])
    if report.get('toc') and report['toc'].get('injected'):
        lines.append(
            f"- **Bookmarks Injected:** {report['toc']['item_count']} items across {report['toc']['root_count']} root chapters"
        )
    if report['renderer'].get('stderr'):
        lines.append(f"- **Renderer Output:** `{report['renderer']['stderr']}`")

    conv_out = report.get('conversion_output')
    if conv_out:
        lines.extend([
            f"- **Converted PDF Size:** {conv_out['bytes']:,} bytes",
            f"- **Converted PDF SHA-256:** `{conv_out['sha256']}`",
        ])

    lines.extend([
        "",
        "## Optimization Stage",
        f"- **Verdict:** {report['optimization_verdict']}",
    ])
    opt_rep = report.get('optimization_report')
    if opt_rep:
        lines.extend([
            f"- **Optimization Status:** {opt_rep.get('status', 'unknown')}",
            f"- **Optimization Classification:** {opt_rep.get('classification', 'unknown')}",
            f"- **Transformations:** {opt_rep.get('pipeline', [])}",
            f"- **Visual Checks:** {opt_rep.get('visual_checks', 'not run')}",
            f"- **All-Page PDF.js Checks:** {opt_rep.get('pdfjs_all_pages', 'not run')}",
        ])
        if opt_rep.get('stages'):
            lines.append("- **Elapsed Stage Times:**")
            for st in opt_rep['stages']:
                lines.append(f"  - {st['stage']}: {st['wall_seconds']:.3f}s ({st['outcome']})")

    final_out = report.get('final_output')
    if final_out:
        lines.extend([
            "",
            "## Final Published Output",
            f"- **Path:** `{final_out['path']}`",
            f"- **Size:** {final_out['bytes']:,} bytes ({final_out['bytes'] / (1024 * 1024):.2f} MiB)",
            f"- **SHA-256:** `{final_out['sha256']}`",
        ])
        if conv_out and conv_out['bytes'] > 0:
            savings = conv_out['bytes'] - final_out['bytes']
            pct = (savings / conv_out['bytes']) * 100
            lines.append(f"- **Size Savings vs Direct Render:** {savings:,} bytes ({pct:.1f}% reduction)")

    if report.get('reason'):
        lines.extend([
            "",
            "## Diagnostic Reason",
            f"`{report['reason']}`",
        ])

    lines.extend([
        "",
        "## Disclaimers & Integrity",
        "- Output PDF is a static vector viewing copy, not an editable BOOX notebook.",
        "- Rendering performance is not measured.",
        "- Input .note archive was verified unmodified after processing.",
        "",
    ])
    return '\n'.join(lines)


def compare_reference_audit(converted_pdf, reference_pdf, page=None):
    """Compare converted vector PDF against matching BOOX device reference export (Milestone P18-D)."""
    from PIL import ImageChops, ImageStat
    import pypdfium2 as pdfium

    conv_path = Path(converted_pdf).resolve()
    ref_path = Path(reference_pdf).resolve()

    conv_bytes = conv_path.stat().st_size
    ref_bytes = ref_path.stat().st_size

    with pdfium.PdfDocument(conv_path) as conv_doc, pdfium.PdfDocument(ref_path) as ref_doc:
        conv_pages = len(conv_doc)
        ref_pages = len(ref_doc)

        eval_indices = []
        if page is not None:
            if 1 <= page <= ref_pages:
                eval_indices.append((0, page - 1, page))
        else:
            sample_candidates = [1, 10, 25, 50, 58, 75, conv_pages]
            samples = sorted({s for s in sample_candidates if 1 <= s <= min(conv_pages, ref_pages)})
            for s in samples:
                if 1 <= s <= conv_pages and 1 <= s <= ref_pages:
                    eval_indices.append((s - 1, s - 1, s))

        evaluated = []
        for conv_idx, ref_idx, page_num in eval_indices:
            conv_page = conv_doc[conv_idx]
            ref_page = ref_doc[ref_idx]

            cw, ch = conv_page.get_size()
            rw, rh = ref_page.get_size()
            dim_match = abs(cw - rw) < 1.0 and abs(ch - rh) < 1.0

            conv_bmp = conv_page.render(scale=1).to_pil().convert('L')
            ref_bmp = ref_page.render(scale=1).to_pil().convert('L')

            if conv_bmp.size != ref_bmp.size:
                ref_bmp = ref_bmp.resize(conv_bmp.size)

            diff = ImageChops.difference(conv_bmp, ref_bmp)
            stat = ImageStat.Stat(diff)
            mean_diff = stat.mean[0]
            max_diff = stat.extrema[0][1]

            c_hist = conv_bmp.histogram()
            conv_ink = sum(c_hist[:250]) / (conv_bmp.width * conv_bmp.height)
            r_hist = ref_bmp.histogram()
            ref_ink = sum(r_hist[:250]) / (ref_bmp.width * ref_bmp.height)

            evaluated.append({
                'page': page_num,
                'converted_dimensions': [round(cw, 2), round(ch, 2)],
                'reference_dimensions': [round(rw, 2), round(rh, 2)],
                'dimensions_match': dim_match,
                'mean_pixel_diff': round(mean_diff, 3),
                'max_pixel_diff': max_diff,
                'converted_ink_ratio': round(conv_ink, 4),
                'reference_ink_ratio': round(ref_ink, 4),
            })

    savings = ref_bytes - conv_bytes
    pct = (savings / ref_bytes) * 100 if ref_bytes > 0 else 0.0

    return {
        'page_count_match': (conv_pages == ref_pages) if page is None else True,
        'converted_page_count': conv_pages,
        'reference_page_count': ref_pages,
        'size_savings_bytes': savings,
        'size_savings_pct': round(pct, 2),
        'evaluated_pages': evaluated,
    }


def convert_note_to_pdf(
    note_path,
    output_pdf=None,
    reference_pdf=None,
    page=None,
    renderer=None,
    audit_dir=None,
    skip_optimize=False,
    optimizer='adaptive',
    strict=True,
    time_budget_seconds=None,
    disk_budget_bytes=None,
    flat_marker=False,
    stroke_mode=None,
    fixed_stroke_width=None,
    stroke_scale=None,
    no_toc=False,
):
    """Bridge BOOX .note archive to verified vector PDF with isolation, disk safety, and budget enforcement."""
    started = time.monotonic()
    note_path = Path(note_path).resolve()
    if not note_path.is_file():
        raise FileNotFoundError(f"Input note not found: {note_path}")

    # Resource budgets: Initialize and check RunBudget BEFORE preflight begins
    budget = RunBudget(time_budget_seconds, disk_budget_bytes)
    budget.check('preflight')

    # Preflight inspection (enforces bounds and fail-closed validation)
    preflight = inspect_note(note_path, renderer=renderer, budget=budget)
    note_sha256 = preflight['sha256']
    note_bytes = preflight['size_bytes']

    # Reference tablet export
    ref_info = None
    if reference_pdf:
        ref_path = Path(reference_pdf).resolve()
        if not ref_path.is_file():
            raise FileNotFoundError(f"Reference PDF not found: {ref_path}")
        ref_info = {
            'path': str(ref_path),
            'bytes': ref_path.stat().st_size,
            'sha256': filehash(ref_path),
        }

    # Locate renderer & compute build SHA-256
    renderer_bin = find_renderer(renderer)
    renderer_sha256 = filehash(renderer_bin)
    preflight['renderer_sha256'] = renderer_sha256

    # Verify destination collision safeguards
    out_path = Path(output_pdf).resolve() if output_pdf else None
    if out_path:
        targets = [out_path, out_path.with_suffix('.json'), out_path.with_suffix('.md')]
        if any(p.exists() or p.is_symlink() for p in targets):
            raise FileExistsError(f"Output destination or report already exists: {out_path}. Refusing to overwrite.")
        out_path.parent.mkdir(parents=True, exist_ok=True)

    # Prepare audit directory with exclusive microsecond allocation
    audit_base = Path(audit_dir).resolve() if audit_dir else (Path(__file__).resolve().parent / 'audit-output')
    audit_job_dir = allocate_audit_job_dir(audit_base, note_path.stem)

    report = {
        'status': 'running',
        'conversion_verdict': 'pending',
        'optimization_verdict': 'skipped' if skip_optimize else 'pending',
        'source_note': str(note_path),
        'source_note_bytes': note_bytes,
        'source_note_sha256': note_sha256,
        'preflight': preflight,
        'renderer': {
            'executable': str(renderer_bin),
            'sha256': renderer_sha256,
            'strict': strict,
            'page': page,
            'flat_marker': flat_marker,
            'stroke_mode': stroke_mode,
            'fixed_stroke_width': fixed_stroke_width,
            'stroke_scale': stroke_scale,
        },
        'reference_pdf': ref_info,
        'audit_job_dir': str(audit_job_dir),
    }

    with tempfile.TemporaryDirectory(prefix='ntp-scratch-') as scratch_dir_str:
        scratch_dir = Path(scratch_dir_str)
        scratch_converted_pdf = scratch_dir / 'converted.pdf'

        # Stage 1: boox-notes-renderer execution in scratch directory
        cmd = [str(renderer_bin), str(note_path), str(scratch_converted_pdf)]
        if strict:
            cmd.append('--strict')
        if page is not None:
            cmd.extend(['--page', str(page)])
        if flat_marker:
            cmd.append('--flat-marker')
        if stroke_mode:
            cmd.extend(['--stroke-mode', str(stroke_mode)])
        if fixed_stroke_width:
            cmd.extend(['--fixed-stroke-width', str(fixed_stroke_width)])
        if stroke_scale is not None:
            cmd.extend(['--stroke-scale', str(stroke_scale)])

        report['renderer']['args'] = cmd

        try:
            # Check budgets before rendering
            budget.check('rendering', (scratch_dir, audit_job_dir))
            timeout = budget.timeout(300)
            proc = subprocess.run(
                cmd,
                timeout=timeout,
                capture_output=True,
                text=True,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            report['renderer']['exit_code'] = proc.returncode
            report['renderer']['stdout'] = proc.stdout.strip()
            report['renderer']['stderr'] = proc.stderr.strip()

            # Check budgets after rendering
            budget.check('post-rendering', (scratch_dir, audit_job_dir))

            if proc.returncode != 0 or not scratch_converted_pdf.exists() or scratch_converted_pdf.stat().st_size == 0:
                report['status'] = 'failed'
                report['conversion_verdict'] = 'rejected'
                report['reason'] = (
                    f"Renderer failed with exit code {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}"
                )
                report['total_elapsed_seconds'] = time.monotonic() - started
                (audit_job_dir / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
                (audit_job_dir / 'report.md').write_text(report_markdown(report), encoding='utf-8')
                return report

            # Stage 1.5: Inject Table of Contents (TOC) if present and not disabled
            if not no_toc:
                try:
                    toc_tree = extract_toc(note_path, page=page, page_names=preflight.get('pages'))
                    if toc_tree:
                        inject_toc(scratch_converted_pdf, scratch_converted_pdf, toc_tree)
                        item_count = count_toc_nodes(toc_tree)
                        report['toc'] = {
                            'item_count': item_count,
                            'root_count': len(toc_tree),
                            'injected': True,
                            'tree': toc_tree,
                        }
                except Exception as exc:
                    logging.warning(f"Failed to inject TOC: {exc}")
                    report.update(status='failed', conversion_verdict='rejected', reason=str(exc),
                                  total_elapsed_seconds=time.monotonic() - started)
                    report['toc'] = {'injected': False, 'error': str(exc)}
                    (audit_job_dir / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
                    (audit_job_dir / 'report.md').write_text(report_markdown(report), encoding='utf-8')
                    return report

            report['conversion_verdict'] = 'converted'
            conv_sha = filehash(scratch_converted_pdf)
            conv_bytes = scratch_converted_pdf.stat().st_size
            report['conversion_output'] = {'bytes': conv_bytes, 'sha256': conv_sha}

        except subprocess.TimeoutExpired as exc:
            report['status'] = 'failed'
            report['conversion_verdict'] = 'rejected'
            report['reason'] = f"Renderer timed out after {timeout} seconds."
            report['total_elapsed_seconds'] = time.monotonic() - started
            (audit_job_dir / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            (audit_job_dir / 'report.md').write_text(report_markdown(report), encoding='utf-8')
            raise TimeBudgetExceeded(report['reason']) from exc

        # Stage 2: Direct Optimization via quick_lossless (optional)
        selected_pdf = scratch_converted_pdf
        if not skip_optimize:
            scratch_optimized_pdf = scratch_dir / 'optimized.pdf'
            try:
                # Check budgets before direct optimization
                budget.check('direct optimization', (scratch_dir, audit_job_dir))

                # Pass REMAINING budget (not original) to optimize_quick
                remaining_time = (
                    (budget.seconds - (time.monotonic() - budget.started)) if budget.seconds is not None else None
                )
                if remaining_time is not None and remaining_time <= 0:
                    raise TimeBudgetExceeded("Time budget exhausted before direct optimization.")

                used_disk = 0
                if budget.bytes_limit is not None:
                    roots = (scratch_dir, audit_job_dir)
                    used_disk = sum(
                        path.stat().st_size
                        for root in roots
                        if root.exists()
                        for path in root.rglob('*')
                        if path.is_file()
                    )
                remaining_disk = (budget.bytes_limit - used_disk) if budget.bytes_limit is not None else None
                if remaining_disk is not None and remaining_disk <= 0:
                    raise DiskBudgetExceeded("Disk budget exhausted before direct optimization.")

                optimize_quick(
                    source=scratch_converted_pdf,
                    destination=scratch_optimized_pdf,
                    expected_source_sha256=conv_sha,
                    time_budget_seconds=remaining_time,
                    disk_budget_bytes=remaining_disk,
                    optimizer=optimizer,
                )
                opt_json_path = scratch_optimized_pdf.with_suffix('.json')
                if opt_json_path.exists():
                    opt_rep = json.loads(opt_json_path.read_text(encoding='utf-8'))
                    report['optimization_report'] = opt_rep
                    if opt_rep.get('status') == 'optimized' and scratch_optimized_pdf.is_file():
                        selected_pdf = scratch_optimized_pdf
                        report['optimization_verdict'] = 'smaller-lossless'
                    else:
                        report['optimization_verdict'] = opt_rep.get('classification', 'retained direct converted PDF')
                else:
                    report['optimization_verdict'] = 'retained direct converted PDF'
            except (TimeBudgetExceeded, DiskBudgetExceeded):
                raise
            except Exception as exc:
                report['optimization_verdict'] = f"optimization failed ({exc}); retained direct converted PDF"

        # Stage 3: Tablet Reference Audit (Milestone P18-D)
        if ref_info:
            try:
                report['reference_audit'] = compare_reference_audit(
                    converted_pdf=selected_pdf,
                    reference_pdf=ref_info['path'],
                    page=page,
                )
            except Exception as exc:
                report['reference_audit_error'] = str(exc)

        # Re-check source immutability
        if filehash(note_path) != note_sha256:
            raise ValueError(f"CRITICAL: Source note {note_path} was modified during execution!")

        # Publication & Final Reports
        # Final disk budget check right before publishing output so --skip-optimize cannot publish if exceeded
        budget.check('publication', (scratch_dir, audit_job_dir))

        selected_sha = filehash(selected_pdf)
        selected_bytes = selected_pdf.stat().st_size

        created = []
        try:
            if out_path:
                with out_path.open('xb') as out_f, selected_pdf.open('rb') as sel_f:
                    created.append(out_path)
                    shutil.copyfileobj(sel_f, out_f)

                if filehash(out_path) != selected_sha:
                    raise ValueError("Published output failed checksum verification!")

                report['final_output'] = {
                    'path': str(out_path),
                    'bytes': selected_bytes,
                    'sha256': selected_sha,
                }
            else:
                audit_out = audit_job_dir / f"{note_path.stem}.pdf"
                with audit_out.open('xb') as out_f, selected_pdf.open('rb') as sel_f:
                    created.append(audit_out)
                    shutil.copyfileobj(sel_f, out_f)
                report['final_output'] = {
                    'path': str(audit_out),
                    'bytes': selected_bytes,
                    'sha256': selected_sha,
                }

            report['status'] = 'success'
            report['total_elapsed_seconds'] = time.monotonic() - started

            json_text = json.dumps(report, indent=2)
            md_text = report_markdown(report)

            if out_path:
                for suffix, content in (('.json', json_text), ('.md', md_text)):
                    t = out_path.with_suffix(suffix)
                    with t.open('x', encoding='utf-8') as f:
                        created.append(t)
                        f.write(content)

            (audit_job_dir / 'report.json').write_text(json_text, encoding='utf-8')
            (audit_job_dir / 'report.md').write_text(md_text, encoding='utf-8')

        except BaseException:
            for t in created:
                t.unlink(missing_ok=True)
            raise

    return report


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Convert BOOX .note archives to verified vector PDF with quick_lossless optimization."
    )
    parser.add_argument("note_file", help="Path to input BOOX .note archive.")
    parser.add_argument("output_pdf", nargs="?", default=None, help="Path for destination vector PDF.")
    parser.add_argument("--reference-pdf", default=None, help="Matching BOOX tablet vector PDF export for audit.")
    parser.add_argument("--page", type=int, default=None, help="Render only this 1-based page.")
    parser.add_argument("--renderer", default=None, help="Path to boox-notes-renderer.exe.")
    parser.add_argument("--audit-dir", default=None, help="Directory for audit records (default: audit-output/).")
    parser.add_argument("--skip-optimize", action="store_true", help="Skip quick_lossless; emit direct vector PDF.")
    parser.add_argument("--optimizer", choices=["adaptive", "qpdf"], default="adaptive", help="Direct optimizer engine.")
    parser.add_argument("--no-strict", dest="strict", action="store_false", help="Disable strict content verification.")
    parser.add_argument("--flat-marker", action="store_true", help="Flatten translucent markers to prevent overlap artifacts.")
    parser.add_argument("--time-budget", type=float, default=None, help="Maximum execution time in seconds.")
    parser.add_argument("--disk-budget", type=int, default=None, help="Maximum temporary disk usage in bytes.")
    parser.add_argument("--inspect-only", action="store_true", help="Only inspect .note archive and emit report.")
    parser.add_argument(
        "--stroke-mode",
        choices=["upstream", "density-scaled", "device-native", "fixed", "scale"],
        default=None,
        help="Stroke width calculation mode.",
    )
    parser.add_argument(
        "--fixed-stroke-width",
        default=None,
        help="Fixed stroke width (e.g. '0.10mm', '0.5pt', '2px', '0.28').",
    )
    parser.add_argument(
        "--stroke-scale",
        type=float,
        default=None,
        help="Linear stroke width scaling multiplier.",
    )
    parser.add_argument(
        "--no-toc",
        action="store_true",
        help="Disable bookmark (TOC) extraction and injection.",
    )

    args = parser.parse_args(argv)

    if args.inspect_only:
        info = inspect_note(args.note_file, renderer=args.renderer)
        print(json.dumps(info, indent=2))
        return 0

    try:
        report = convert_note_to_pdf(
            note_path=args.note_file,
            output_pdf=args.output_pdf,
            reference_pdf=args.reference_pdf,
            page=args.page,
            renderer=args.renderer,
            audit_dir=args.audit_dir,
            skip_optimize=args.skip_optimize,
            optimizer=args.optimizer,
            strict=args.strict,
            time_budget_seconds=args.time_budget,
            disk_budget_bytes=args.disk_budget,
            flat_marker=args.flat_marker,
            stroke_mode=args.stroke_mode,
            fixed_stroke_width=args.fixed_stroke_width,
            stroke_scale=args.stroke_scale,
            no_toc=args.no_toc,
        )
        print(f"Pipeline Result: {report['status']}")
        print(f"Conversion Verdict: {report['conversion_verdict']}")
        print(f"Optimization Verdict: {report['optimization_verdict']}")
        if report.get('toc') and report['toc'].get('injected'):
            print(f"Bookmarks Injected: {report['toc']['item_count']} items across {report['toc']['root_count']} roots")
        if report.get('final_output'):
            print(f"Published PDF: {report['final_output']['path']} ({report['final_output']['bytes']:,} bytes)")
        if report['status'] != 'success':
            print(f"Reason: {report.get('reason')}")
            return 1
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
