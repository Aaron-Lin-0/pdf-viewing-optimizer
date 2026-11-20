"""Opt-in finite-resolution study copies; exact profiles never call this module."""
import hashlib
from contextlib import closing
import importlib.metadata
import json
import math
from pathlib import Path
import re
import shutil
import zlib

from lossless_verify import MAX_DECODED_BYTES, MAX_FILE_BYTES, _stream_digest, strict_parsing, strict_reader
from pdf_runtime import RunBudget, check_qpdf, filehash, qpdf_identity, temporary_directory, _run, build_qpdf_candidate

CONTRACT = 'bitmap-study-1'
MAX_ENCODED_IMAGE_BYTES = MAX_FILE_BYTES // 2
LOSSES = ('Visible pages become finite-resolution images; vector zoom and layer switching are lost. '
          'This is an experimental study copy. Mathematical meaning requires human review. '
          'Search text, annotations and positional navigation are currently unsupported and rejected.')


def settings(paper='letter', dpi=300):
    if paper not in ('letter', 'source') or dpi not in (300, 450, 600):
        raise ValueError('Bitmap study copies support letter/source paper and 300/450/600 DPI.')
    return {'paper': paper, 'dpi': dpi}


def acceptance_context(options, identity=None):
    root = Path(__file__).resolve().parent
    names = ('bitmap_study.py', 'optimize_pdf.py', 'pdf_runtime.py', 'lossless_verify.py', 'navigation.py', 'viewing_copy.py')
    return {'contract': CONTRACT, 'settings': options,
            'code_sha256': hashlib.sha256(b''.join((root/name).read_bytes() for name in names)).hexdigest(),
            'tools': {name: importlib.metadata.version(name) for name in ('pypdf', 'pypdfium2', 'Pillow')},
            'qpdf': list((identity or qpdf_identity())[1:])}



DRAWING_RESOURCES = {
    b'Do': '/XObject', b'gs': '/ExtGState', b'sh': '/Shading',
    b'CS': '/ColorSpace', b'cs': '/ColorSpace',
    b'SCN': '/Pattern', b'scn': '/Pattern', b'BDC': '/Properties', b'DP': '/Properties',
}
DEVICE_SPACES = {'/DeviceGray': '/DefaultGray', '/DeviceRGB': '/DefaultRGB', '/DeviceCMYK': '/DefaultCMYK'}
DEVICE_OPERATORS = {b'g': '/DeviceGray', b'G': '/DeviceGray', b'rg': '/DeviceRGB',
                    b'RG': '/DeviceRGB', b'k': '/DeviceCMYK', b'K': '/DeviceCMYK'}


def check_drawing_resources(page, reader):
    """Resolve resources used by page drawing before a renderer can omit them."""
    from pypdf.generic import ArrayObject, ContentStream, DictionaryObject, NameObject, NullObject, StreamObject

    def resolve(resources, category, name):
        entries = resources.get(category, DictionaryObject()).get_object()
        if not isinstance(entries, DictionaryObject) or not isinstance(name, NameObject) or name not in entries:
            raise ValueError(f'Unresolved drawing resource {category} {name}.')
        value = entries[name]
        if isinstance(value, NullObject):
            raise ValueError(f'Unresolved drawing resource {category} {name}.')
        return value

    def color(space, resources, active=(), default=True):
        space = space.get_object()
        if id(space) in active or len(active) >= 64:
            raise ValueError('Cyclic or excessively nested drawing color space.')
        nested = (*active, id(space))
        if isinstance(space, NameObject):
            if space in DEVICE_SPACES:
                entries = resources.get('/ColorSpace', DictionaryObject()).get_object()
                if not isinstance(entries, DictionaryObject):
                    raise ValueError('Invalid drawing color-space resources.')
                if default and DEVICE_SPACES[space] in entries:
                    value = resolve(resources, '/ColorSpace', NameObject(DEVICE_SPACES[space]))
                    color(value, resources, nested, default=False)
            elif space != '/Pattern':
                color(resolve(resources, '/ColorSpace', space), resources, nested, default)
        elif isinstance(space, ArrayObject) and space and isinstance(space[0], NameObject):
            kind = space[0]
            if kind in ('/Indexed', '/Separation', '/DeviceN') or (kind == '/Pattern' and len(space) > 1):
                base = 2 if kind in ('/Separation', '/DeviceN') else 1
                if len(space) <= base:
                    raise ValueError('Drawing color space has no base or alternate space.')
                color(space[base], resources, nested, default)
            elif kind == '/ICCBased':
                if len(space) != 2 or not isinstance(space[1].get_object(), StreamObject):
                    raise ValueError('Unresolved drawing ICC profile.')
                profile = space[1].get_object()
                if '/Alternate' in profile:
                    color(profile['/Alternate'], resources, nested, default)
            elif kind not in ('/CalGray', '/CalRGB', '/Lab', '/Pattern'):
                raise ValueError('Unsupported drawing color space.')
        else:
            raise ValueError('Unresolved drawing color space.')

    def shading(value, resources):
        if not isinstance(value, DictionaryObject) or '/ShadingType' not in value or '/ColorSpace' not in value:
            raise ValueError('Unresolved drawing shading.')
        color(value['/ColorSpace'], resources)

    def image(value, resources, active=()):
        if not isinstance(value, StreamObject) or value.get('/Subtype') != '/Image':
            raise ValueError('Unresolved drawing image or mask.')
        if id(value) in active or len(active) >= 64:
            raise ValueError('Cyclic or excessively nested drawing image mask.')
        if '/ColorSpace' in value:
            color(value['/ColorSpace'], resources)
        for key in ('/Mask', '/SMask'):
            if key in value:
                mask = value[key]
                if (key == '/Mask' and isinstance(mask, ArrayObject)) or mask == '/None':
                    continue
                image(mask, resources, (*active, id(value)))

    page_resources = page.get('/Resources', DictionaryObject()).get_object()
    visited = set()

    def walk(stream, resources, active=()):
        if not isinstance(resources, DictionaryObject):
            raise ValueError('Unresolved drawing resource dictionary.')
        if id(stream) in active or len(active) >= 64:
            raise ValueError('Cyclic or excessively nested drawing stream.')
        key = (id(stream), id(resources))
        if key in visited:
            return
        nested = (*active, id(stream))
        content = stream if isinstance(stream, ContentStream) else ContentStream(stream, reader)
        for operands, op in content.operations:
            if op in DEVICE_OPERATORS:
                color(NameObject(DEVICE_OPERATORS[op]), resources)
                continue
            if op == b'INLINE IMAGE':
                settings = operands['settings']
                if '/CS' in settings or '/ColorSpace' in settings:
                    space = settings.get('/CS', settings.get('/ColorSpace'))
                    aliases = {'/G': '/DeviceGray', '/RGB': '/DeviceRGB', '/CMYK': '/DeviceCMYK'}
                    if isinstance(space, NameObject) and space in aliases:
                        space = NameObject(aliases[space])
                    color(space, resources)
                continue
            category = DRAWING_RESOURCES.get(op)
            if category is None:
                continue
            if op in (b'BDC', b'DP', b'SCN', b'scn'):
                name = operands[-1] if operands else None
                if op in (b'SCN', b'scn') and not isinstance(name, NameObject):
                    continue
                if op in (b'BDC', b'DP') and len(operands) == 2 and isinstance(name, DictionaryObject):
                    continue
                expected_count = 2 if op in (b'BDC', b'DP') else len(operands)
            else:
                name = operands[0] if len(operands) == 1 else None
                expected_count = 1
            if len(operands) != expected_count or not isinstance(name, NameObject):
                raise ValueError(f'Invalid drawing resource operands for {op.decode()}.')
            if category == '/ColorSpace':
                color(name, resources)
                continue
            value = resolve(resources, category, name)
            if category == '/XObject':
                if isinstance(value, StreamObject) and value.get('/Subtype') == '/Form':
                    form(value, nested)
                else:
                    image(value, resources)
            elif not isinstance(value, DictionaryObject):
                raise ValueError(f'Invalid drawing resource {category} {name}.')
            elif category == '/ExtGState' and '/SMask' in value and value['/SMask'] != '/None':
                mask = value['/SMask']
                if not isinstance(mask, DictionaryObject) or '/G' not in mask:
                    raise ValueError('Unresolved drawing soft mask.')
                form(mask['/G'], nested)
            elif category == '/Shading':
                shading(value, resources)
            elif category == '/Pattern':
                if value.get('/PatternType') == 1 and isinstance(value, StreamObject) and '/Resources' in value:
                    walk(value, value['/Resources'], nested)
                elif value.get('/PatternType') == 2 and '/Shading' in value:
                    shading(value['/Shading'], resources)
                else:
                    raise ValueError('Unresolved drawing pattern.')
        visited.add(key)

    def form(value, active):
        if not isinstance(value, StreamObject) or value.get('/Subtype') != '/Form':
            raise ValueError('Unresolved drawing Form.')
        resources = value['/Resources'] if '/Resources' in value else page_resources
        if '/Group' in value and '/CS' in value['/Group']:
            color(value['/Group']['/CS'], resources)
        walk(value, resources, active)

    contents = page.get_contents()
    if contents is not None:
        walk(contents, page_resources)


def inventory(reader):
    from navigation import _current_objects
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject
    root = reader.trailer['/Root']
    # shortcut: positional navigation needs a calibrated coordinate mapper before support expands.
    if any(key in root for key in ('/AcroForm', '/OpenAction', '/AA', '/Dests', '/StructTreeRoot')):
        raise ValueError('Study copy does not yet support forms, actions, named targets or tagged content.')
    names = root['/Names'] if '/Names' in root else {}
    if any(key != '/EmbeddedFiles' for key in names):
        raise ValueError('Study copy supports attachment names only; other named features are unsupported.')
    for obj in _current_objects(reader):
        if not isinstance(obj, DictionaryObject): continue
        if obj.get('/Type') == '/OCMD':
            raise ValueError('Optional-content membership expressions need separate study-copy qualification.')
        if obj.get('/Type') == '/Font' or obj.get('/Font'):
            raise ValueError('Font-bearing input may contain search text; study copy currently declines it.')
    outline = reader.outline
    bookmark = root['/Outlines'].get('/First') if outline else None
    for item in outline:
        node = bookmark.get_object()
        if isinstance(item, list) or '/First' in node or item.typ != '/Fit':
            raise ValueError('Study copy currently supports flat, whole-page bookmarks only.')
        if '/A' in node:
            action = node['/A']
            if ('/Dest' in node or action['/S'] != '/GoTo' or
                    set(action) - {'/Type', '/S', '/D'}):
                raise ValueError('Study copy does not support bookmark actions beyond a local page jump.')
            destination = action['/D']
        else:
            destination = node['/Dest'] if '/Dest' in node else None
        if (not isinstance(destination, ArrayObject) or len(destination) != 2 or
                not isinstance(destination[0], IndirectObject) or reader.get_destination_page_number(item) is None):
            raise ValueError('Study copy requires bookmarks to target an existing page directly.')
        bookmark = node.get('/Next')
    layers = root.get('/OCProperties')
    groups = []
    if layers:
        layers = layers.get_object()
        groups = layers['/OCGs'] if '/OCGs' in layers else []
        default = layers['/D'] if '/D' in layers else {}
        if (layers.get('/Configs') or default.get('/BaseState', '/ON') != '/ON' or
                any(default.get(k) for k in ('/OFF', '/AS', '/RBGroups')) or
                any(set(group.get_object().get('/Usage', {})) - {'/CreatorInfo'} for group in groups)):
            raise ValueError('Hidden, alternate or automatic layer states are unsupported in study copies.')
    for page in reader.pages:
        if page.get('/Annots') or page.get('/AA'):
            raise ValueError('Study copy does not yet support links, comments or other annotations.')
        check_drawing_resources(page, reader)
    return {'pages': len(reader.pages), 'flattened_layers': len(groups),
            'bookmarks': len(outline), 'attachments': list(reader.attachments)}


def preflight(source):
    with strict_parsing(), strict_reader(source) as reader:
        return inventory(reader)


def feature_signature(reader):
    from navigation import navigation_fingerprint
    root = reader.trailer['/Root']
    root.pop('/OCProperties', None)
    for page in reader.pages:
        for key in ('/MediaBox', '/CropBox', '/TrimBox', '/BleedBox', '/ArtBox', '/Rotate', '/UserUnit', '/Group'):
            page.pop(key, None)
    return navigation_fingerprint(reader)


def prepare_source(source, working, identity, budget):
    """Resolve only the known malformed xref wrapper, with complete logical proof."""
    import subprocess
    result = _run([identity[0], '--check', str(source)], 1800, budget, 'source syntax',
                  capture_output=True, text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode == 0 and not result.stderr.strip(): return source, None
    warnings = result.stderr.strip().splitlines()
    prefix = 'WARNING: '+str(source)
    if (result.returncode != 3 or len(warnings) != 3 or
            not re.fullmatch(re.escape(prefix)+r' \(xref stream: object \d+ \d+, offset \d+\): expected endobj', warnings[0]) or
            warnings[1] != prefix+': xref entry for the xref stream itself is missing - a common error handled correctly by qpdf and most other applications' or
            warnings[2] != 'qpdf: operation succeeded with warnings'):
        raise ValueError('Unresolved source syntax warnings: '+result.stderr[-1500:])
    snapshot = working/'source-snapshot.pdf'
    from pypdf.generic import ArrayObject, DecodedStreamObject, NameObject, NumberObject
    from pypdf.generic import IndirectObject
    with strict_parsing(), strict_reader(source) as reader:
        if len(reader.quick_xref_ids) != 1: raise ValueError('Expected one source xref stream.')
        number, generation = next(iter(reader.quick_xref_ids))
        offset = reader._startxref
        original = reader.get_object(IndirectObject(number, generation, reader))
        widths = list(original['/W'])
        index = list(original.get('/Index', [0, original['/Size']]))
        if (generation != 0 or original.get('/Filter') or original.get('/DecodeParms') or
                len(widths) != 3 or any(not 1 <= w <= 8 for w in widths) or len(index) % 2 or
                any(start <= number < start+count for start, count in zip(index[::2], index[1::2])) or
                any(value >= offset for rows in reader.xref.values() for n, value in rows.items() if n and value)):
            raise ValueError('Unsupported xref wrapper layout.')
        raw = original._data
        if len(raw) != sum(index[1::2])*sum(widths) or len(raw) > 16*1024**2:
            raise ValueError('Invalid or oversized xref stream.')
        wrapper = DecodedStreamObject()
        wrapper.update({NameObject(k): v for k, v in original.items() if k != '/Length'})
        wrapper[NameObject('/Index')] = ArrayObject([NumberObject(v) for v in [*index, number, 1]])
        wrapper[NameObject('/Size')] = NumberObject(max(original['/Size'], number+1))
        wrapper.set_data(raw + b''.join(v.to_bytes(w, 'big') for v, w in zip((1, offset, 0), widths)))
        with source.open('rb') as incoming, snapshot.open('xb') as output:
            remaining = offset
            while remaining:
                budget.check('xref byte copy', (working,))
                block = incoming.read(min(1024**2, remaining))
                if not block: raise ValueError('Source ended before its xref stream.')
                output.write(block); remaining -= len(block)
            output.write(f'{number} 0 obj\n'.encode())
            wrapper.write_to_stream(output)
            output.write(f'\nendobj\nstartxref\n{offset}\n%%EOF\n'.encode())
        check_qpdf(snapshot, identity, budget)
        proof = verify_xref_snapshot(source, snapshot, reader, number, offset, budget)
    return snapshot, {'policy': 'study-xref-wrapper-1', 'source_sha256': filehash(source),
                      'snapshot_sha256': filehash(snapshot), 'warnings': warnings, 'preservation': proof}


def verify_xref_snapshot(source, snapshot, reader, number, offset, budget):
    from io import BytesIO
    def trailer_bytes(document):
        from pypdf.generic import DictionaryObject
        output = BytesIO()
        DictionaryObject({k: v for k, v in document.trailer.items() if k != '/Size'}).write_to_stream(output)
        return output.getvalue()
    with source.open('rb') as before, snapshot.open('rb') as after:
        remaining = offset
        while remaining:
            budget.check('xref byte proof', (snapshot,))
            count = min(1024**2, remaining)
            if before.read(count) != after.read(count): raise ValueError('Original object bytes changed.')
            remaining -= count
    with strict_reader(snapshot) as saved:
        actual = {g: dict(rows) for g, rows in saved.xref.items()}
        actual.get(0, {}).pop(number, None)
        if (actual != reader.xref or saved.xref_objStm != reader.xref_objStm or
                trailer_bytes(saved) != trailer_bytes(reader)):
            raise ValueError('Xref bindings or document trailer changed.')
    return {'complete': True, 'method': 'identical object bytes, index bindings and trailer',
            'object_region_bytes': offset}



def optimize_bitmap(source, destination, expected_source_sha256=None, checkpoint=None,
                    time_budget_seconds=None, disk_budget_bytes=None, *, paper='letter', dpi=300):
    from pypdf import PdfWriter
    from pypdf.generic import (ArrayObject, DictionaryObject, NameObject, NumberObject, FloatObject,
                               DecodedStreamObject, EncodedStreamObject)
    from PIL import ImageChops
    import pypdfium2 as pdfium

    options = settings(paper, dpi)
    source = Path(source).expanduser().absolute()
    destination = Path(destination).expanduser().absolute()
    targets = [destination, destination.with_suffix('.json'), destination.with_suffix('.md')]
    if any(p.exists() or p.is_symlink() for p in targets):
        raise FileExistsError('Output/report already exists. Choose a new filename.')
    if not destination.parent.is_dir():
        raise ValueError('The output folder does not exist.')
    for value in (time_budget_seconds, disk_budget_bytes):
        if value is not None and (not math.isfinite(value) or value <= 0):
            raise ValueError('Budgets must be finite and positive.')
    checkpoint = Path(checkpoint).absolute() if checkpoint else destination.with_suffix('.checkpoint')
    if checkpoint.is_symlink() or checkpoint.parent.resolve() != destination.parent.resolve():
        raise ValueError('Checkpoint must stay beside the output and must not be a shortcut.')
    budget = RunBudget(time_budget_seconds, disk_budget_bytes, checkpoint)
    source_hash = filehash(source)
    if expected_source_sha256 is not None and (not re.fullmatch('[0-9a-fA-F]{64}', expected_source_sha256) or
                                               source_hash != expected_source_sha256.lower()):
        raise ValueError('Original does not match the reviewed source hash.')
    report = {'status': 'optimized', 'profile': 'bitmap', 'contract': CONTRACT, 'settings': options,
              'source': str(source), 'source_sha256': source_hash, 'source_bytes': source.stat().st_size,
              'losses': LOSSES, 'rendering_performance': 'not measured', 'browser_checks': 'not run',
              'semantic_review': 'not performed', 'pages': []}
    with temporary_directory(prefix='pdf-study-', dir=destination.parent) as temporary:
        working = Path(temporary)
        def check(stage):
            budget.check(stage, (working,))
        def fresh():
            if filehash(source) != source_hash:
                raise ValueError('Original changed during processing.')
        print('Stage: study input checks', flush=True)
        check('input checks')
        identity = qpdf_identity(budget)
        prepared, report['source_preparation'] = prepare_source(source, working, identity, budget)
        with strict_parsing(), strict_reader(prepared) as reader:
            report['inventory'] = inventory(reader)
            # Use a separate reader: normalizing the comparison must not change renderer/writer input.
            with strict_reader(prepared) as original_features:
                expected_features = feature_signature(original_features)
            report['acceptance_context'] = acceptance_context(options, identity)
            with closing(PdfWriter(clone_from=reader)) as writer:
                writer._root_object.pop('/OCProperties', None)
                page_count = len(writer.pages)
                encoded_bytes = decoded_bytes = 0
                print('Stage: study rasterization', flush=True)
                for index, target in enumerate(writer.pages):
                    check('page rasterization')
                    # Reopen per page to release accumulated renderer resources on dense notebooks.
                    with pdfium.PdfDocument(prepared) as document:
                        if len(document) != page_count: raise ValueError('Renderer page count disagrees with parser.')
                        page = document[index]
                        try:
                            with closing(page.get_textpage()) as text:
                                if text.count_chars():
                                    raise ValueError('Searchable text is unsupported in bitmap study copies.')
                            # PDFium reports canvas units and does not apply the PDF's physical unit size.
                            unit = float(reader.pages[index].get('/UserUnit', NumberObject(1)).get_object())
                            canvas_width, canvas_height = page.get_size()
                            sw, sh = canvas_width*unit, canvas_height*unit
                            pw, ph = (612, 792) if paper == 'letter' else (sw, sh)
                            if not all(math.isfinite(v) and v > 0 for v in (unit, sw, sh, pw, ph)):
                                raise ValueError('Invalid page geometry.')
                            scale = min(pw/sw, ph/sh)
                            width, height = sw*scale, sh*scale
                            render_scale = scale*unit*dpi/72
                            if math.ceil(canvas_width*render_scale)*math.ceil(canvas_height*render_scale) > 40_000_000:
                                raise ValueError('Study page exceeds the 40-million-pixel limit; choose Letter or lower DPI.')
                            bitmap = page.render(scale=render_scale, draw_annots=True)
                            try:
                                image = bitmap.to_pil().convert('RGB')
                            finally:
                                bitmap.close()
                        finally:
                            page.close()
                    try:
                        channels = image.split()
                        gray = all(ImageChops.difference(channels[0], channel).getbbox() is None for channel in channels[1:])
                        for channel in channels: channel.close()
                        if gray:
                            rgb = image; image = rgb.convert('L'); rgb.close()
                        raw = image.tobytes()
                        decoded_bytes += len(raw)
                        if decoded_bytes > MAX_DECODED_BYTES:
                            raise ValueError('Study document exceeds the total decoded image limit; choose lower DPI or split the input.')
                        stream = EncodedStreamObject(); stream._data = zlib.compress(raw, 6)
                        encoded_bytes += len(stream._data)
                        if encoded_bytes > MAX_ENCODED_IMAGE_BYTES:
                            raise ValueError('Study document exceeds the total encoded image limit; choose lower DPI or split the input.')
                        stream.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Image'),
                                       NameObject('/Width'): NumberObject(image.width), NameObject('/Height'): NumberObject(image.height),
                                       NameObject('/BitsPerComponent'): NumberObject(8), NameObject('/Filter'): NameObject('/FlateDecode'),
                                       NameObject('/ColorSpace'): NameObject('/DeviceGray' if gray else '/DeviceRGB')})
                        for key in ('/CropBox', '/TrimBox', '/BleedBox', '/ArtBox', '/Rotate', '/UserUnit', '/Group'):
                            target.pop(key, None)
                        paper_points = [FloatObject(pw), FloatObject(ph)]
                        target[NameObject('/MediaBox')] = ArrayObject([NumberObject(0), NumberObject(0), *paper_points])
                        target[NameObject('/CropBox')] = target['/MediaBox']
                        target[NameObject('/Rotate')] = NumberObject(0)
                        target[NameObject('/UserUnit')] = NumberObject(1)
                        target[NameObject('/Resources')] = DictionaryObject({NameObject('/XObject'): DictionaryObject({NameObject('/StudyImage'): writer._add_object(stream)})})
                        content = DecodedStreamObject()
                        placement = [FloatObject(value).myrepr() if value else '0'
                                     for value in (width, height, (pw-width)/2, (ph-height)/2)]
                        w, h, x, y = placement
                        content.set_data(f'q {w} 0 0 {h} {x} {y} cm /StudyImage Do Q\n'.encode())
                        target[NameObject('/Contents')] = writer._add_object(content)
                        report['pages'].append({'page': index+1, 'pixels': list(image.size),
                                                'paper_points': [float(value.myrepr()) for value in paper_points],
                                                'image_sha256': hashlib.sha256(raw).hexdigest(), 'color': image.mode,
                                                'content_sha256': hashlib.sha256(content.get_data()).hexdigest(),
                                                'placement_points': list(map(float, placement))})
                    finally:
                        image.close()
                    del raw
                    print(f'Study pages rasterized: {index+1}/{page_count}', flush=True)
                candidate = working/'candidate.pdf'
                from viewing_copy import _prune_unreachable
                _prune_unreachable(writer)
                writer.write(candidate)
                print('Stage: study packing', flush=True)
        check('study packing')
        packed = working/'packed.pdf'
        build_qpdf_candidate(candidate, packed, budget, identity, strict=True)
        candidate = packed
        check('saved verification')
        print('Stage: study saved verification', flush=True)
        check_qpdf(candidate, identity, budget)
        with strict_parsing(), strict_reader(candidate) as saved:
            if len(saved.pages) != len(report['pages']): raise ValueError('Page count changed.')
            decoded_work = [0]
            for page, record in zip(saved.pages, report['pages']):
                check('image verification')
                image = page['/Resources']['/XObject']['/StudyImage']
                if image.get('/Filter') != '/FlateDecode' or '/DecodeParms' in image:
                    raise ValueError('Unexpected transport for saved study image.')
                count, digest = _stream_digest(image._data, True, lambda: check('image verification'), decoded_work)
                width, height = record['pixels']
                channels = 1 if record['color'] == 'L' else 3
                if (count != width * height * channels or digest.hex() != record['image_sha256'] or
                        [image['/Width'], image['/Height']] != record['pixels'] or
                        hashlib.sha256(page.get_contents().get_data()).hexdigest() != record['content_sha256'] or
                        list(map(float, page.mediabox)) != [0, 0, *record['paper_points']] or
                        list(map(float, page.cropbox)) != [0, 0, *record['paper_points']] or
                        page.rotation != 0 or page.get('/UserUnit', 1) != 1 or
                        image['/BitsPerComponent'] != 8 or image['/ColorSpace'] !=
                        ('/DeviceGray' if record['color'] == 'L' else '/DeviceRGB')):
                    raise ValueError('Saved study image changed.')
                print(f"Study pages verified: {record['page']}/{page_count}", flush=True)
            if feature_signature(saved) != expected_features:
                raise ValueError('Metadata, bookmarks, labels, attachments or retained private data changed.')
        report.update(output=str(destination), output_sha256=filehash(candidate), output_bytes=candidate.stat().st_size,
                      verification={'complete': True, 'coverage': 'all saved image payloads and qpdf syntax; human meaning review pending'})
        created = []
        try:
            print('Stage: publication', flush=True)
            fresh(); check('publication')
            with destination.open('xb') as output:
                created.append(destination)
                with candidate.open('rb') as stream: shutil.copyfileobj(stream, output)
            if filehash(destination) != report['output_sha256']: raise ValueError('Published checksum mismatch.')
            for target, body in ((targets[1], json.dumps(report, indent=2)),
                                 (targets[2], '# Experimental bitmap study copy\n\n'+LOSSES+'\n\n'+
                                  f"Paper: {paper}; {dpi} DPI. Pages: {len(report['pages'])}.\n\n"+
                                  f"Source SHA256: {source_hash}\n\nOutput SHA256: {report['output_sha256']}\n\n"+
                                  'All saved images and placement programs checked. Retained document features compared. '+
                                  'Viewing performance unmeasured; human meaning review pending.\n')):
                fresh(); check('report publication')
                with target.open('x', encoding='utf-8') as stream:
                    created.append(target); stream.write(body)
            fresh(); check('completion')
        except BaseException:
            for target in created: target.unlink(missing_ok=True)
            raise
    print('Study copy created; human review required. Viewing performance not measured.', flush=True)
    return report['source_bytes'], report['output_bytes']

