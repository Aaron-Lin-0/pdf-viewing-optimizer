"""BOOX-specific drawing-call reduction; preserves segments, permits edge shading changes."""
import re

from PIL import ImageChops, ImageStat
import pypdfium2 as pdfium
from pypdf.generic import ContentStream, DecodedStreamObject, DictionaryObject, NameObject, StreamObject

NUMBER = rb"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
POINT = NUMBER + rb" " + NUMBER
SEGMENT = re.compile(rb"(" + NUMBER + rb") w (" + POINT + rb") m (" + POINT + rb") l S\n")
PATH = re.compile(rb"(" + POINT + rb") m (" + POINT + rb") l\n")
GROUP = re.compile(rb"(" + NUMBER + rb") w\n((?:" + POINT + rb" m " + POINT + rb" l\n)+)S\n")
HEADER = b"/GS gs\n0 0 0 RG\n1 j 1 J\n"
DUPLICATE = re.compile(
    rb"q 1 0 0 1 0 0 cm /FFT0 Do Q\n"
    rb"(q\n0 0 0 RG 0 0 0 rg 1 w 0 J 0 j\n/([A-Za-z0-9]+) gs q /FFT0 Do Q\n)"
)
FORM_CALL = re.compile(rb'q ((?:' + NUMBER + rb' ){5}' + NUMBER + rb') cm /([A-Za-z0-9]+) Do Q\n')
PEN_STATE = {'/AIS': False, '/BM': '/Normal', '/CA': 1, '/LC': 2,
             '/Type': '/ExtGState', '/ca': 1}


def _inline_leaf(form):
    if form.get('/Subtype') != '/Form' or list(form['/Matrix'] if '/Matrix' in form else [1, 0, 0, 1, 0, 0]) != [1, 0, 0, 1, 0, 0]:
        return None
    if set(form) - {'/BBox', '/Filter', '/FormType', '/Matrix', '/Resources', '/Subtype', '/Type'}:
        return None
    if form.get('/FormType', 1) != 1:
        return None
    resources = form.get('/Resources', DictionaryObject()).get_object()
    if set(resources) != {'/ExtGState'}:
        return None
    states = resources['/ExtGState']
    if set(states) != {'/GS'} or dict(states['/GS']) != PEN_STATE:
        return None
    data = form.get_data()
    if not data.startswith(HEADER):
        return None
    body = data[len(HEADER):]
    groups = list(GROUP.finditer(body))
    if not groups or b''.join(g[0] for g in groups) != body:
        segments = list(SEGMENT.finditer(body))
        if not segments or b''.join(s[0] for s in segments) != body:
            return None
    bbox = form['/BBox'] if '/BBox' in form else None
    if bbox is None or len(bbox) != 4 or bbox[0] > bbox[2] or bbox[1] > bbox[3]:
        return None
    # Endpoint literals avoid rounding a subtraction to obtain rectangle width/height.
    x0, y0, x1, y1 = [str(x).encode('ascii') for x in bbox]
    clip = b' '.join([x0, y0, b'm', x1, y0, b'l', x1, y1, b'l', x0, y1, b'l h W n\n'])
    return body, clip, states.raw_get('/GS')


def inline_handwriting(writer):
    forms = parents = 0
    cache = {}
    for parent in list(writer._objects):
        if not isinstance(parent, StreamObject) or parent.get('/Subtype') != '/Form' or '/Group' in parent:
            continue
        data = parent.get_data()
        calls = list(FORM_CALL.finditer(data))
        # shortcut: only BOOX containers consisting entirely of isolated Form calls are supported.
        if not calls or b''.join(c[0] for c in calls) != data:
            continue
        resources = parent.get('/Resources', DictionaryObject()).get_object()
        xobjects = resources.get('/XObject', DictionaryObject()).get_object()
        leaves = []
        for call in calls:
            form = xobjects.get('/' + call[2].decode())
            if call[1] != b'1 0 0 1 0 0' or form is None:
                leaves.append(None)
                continue
            form = form.get_object()
            if id(form) not in cache:
                cache[id(form)] = _inline_leaf(form)
            leaves.append(cache[id(form)])
        if not any(leaf is not None for leaf in leaves):
            continue
        new_resources = DictionaryObject(resources)
        states = DictionaryObject(resources.get('/ExtGState', DictionaryObject()).get_object())
        pen_name = '/InlinePen'
        suffix = 0
        while pen_name in states:
            suffix += 1
            pen_name = '/InlinePen' + str(suffix)
        states[NameObject(pen_name)] = next(leaf[2] for leaf in leaves if leaf is not None)
        new_resources[NameObject('/ExtGState')] = states
        prefix = b'q\n' + HEADER.replace(b'/GS', pen_name.encode(), 1)
        parts = []
        active = False
        removed = set()
        retained = set()
        for call, leaf in zip(calls, leaves):
            name = '/' + call[2].decode()
            if leaf is None:
                if active:
                    parts.append(b'Q\n')
                    active = False
                parts.append(call[0])
                retained.add(name)
                continue
            if not active:
                parts.append(prefix)
                active = True
            body, clip, state = leaf
            parts.append(b'q\n' + clip + body + b'Q\n')
            if parts[-1][len(b'q\n' + clip):-len(b'Q\n')] != body:
                raise ValueError('Handwriting bytes changed; candidate rejected.')
            removed.add(name)
            forms += 1
        if active:
            parts.append(b'Q\n')
        kept = DictionaryObject({name: ref for name, ref in xobjects.items()
                                 if name not in removed or name in retained})
        new_resources[NameObject('/XObject')] = kept
        parent[NameObject('/Resources')] = new_resources
        parent.set_data(b''.join(parts))
        parents += 1
    if not forms:
        raise ValueError('No supported BOOX handwriting Forms found; use the default lossless mode.')
    print(f'Inlined {forms:,} handwriting Form calls in {parents:,} containers; retained every clipping boundary.', flush=True)
    return forms, parents


def batch_segments(data):
    if not data.startswith(HEADER):
        return data, 0, 0
    body = data[len(HEADER):]
    matches = list(SEGMENT.finditer(body))
    if not matches or b"".join(m[0] for m in matches) != body:
        return data, 0, 0
    original = [(m[1], m[2], m[3]) for m in matches]
    parts = []
    width = None
    groups = 0
    for new_width, start, end in original:
        if new_width != width:
            if width is not None:
                parts.append(b"S\n")
            parts.append(new_width + b" w\n")
            width = new_width
            groups += 1
        # Separate subpaths retain every segment's round caps, including zero-length dots.
        parts.append(start + b" m " + end + b" l\n")
    parts.append(b"S\n")
    result = b"".join(parts)
    parsed = list(GROUP.finditer(result))
    reconstructed = [(g[1], p[1], p[2]) for g in parsed for p in PATH.finditer(g[2])]
    if b"".join(g[0] for g in parsed) != result or reconstructed != original:
        raise ValueError("Segment geometry changed; candidate rejected.")
    return HEADER + result, len(original), groups


def duplicate_layer_match(page, writer):
    content = page.get_contents()
    if content is None:
        return False
    data = content.get_data()
    matches = list(DUPLICATE.finditer(data))
    if len(matches) != 1:
        return False
    match = matches[0]
    resources = page.get('/Resources', {})
    state = resources.get('/ExtGState', {}).get('/' + match[2].decode('ascii'))
    if state is None:
        return False
    state = state.get_object()
    if dict(state) != {'/BM': '/Normal', '/CA': 1, '/ca': 1}:
        return False
    layer = resources.get('/XObject', {}).get('/FFT0')
    if layer is None or layer.get_object().get('/Subtype') != '/Form':
        return False
    if not opaque_drawing(layer.get_object(), writer):
        return False
    # Both invocations must start in the same default graphics state.
    prefix = StreamObject()
    prefix._data = data[:match.start()]
    depth = 0
    for operands, operator in ContentStream(prefix, writer).operations:
        if operator == b'q':
            depth += 1
        elif operator == b'Q':
            depth -= 1
            if depth < 0:
                return False
        elif depth == 0:
            return False
    if depth:
        return False
    return match


def remove_duplicate_layer(page, writer):
    match = duplicate_layer_match(page, writer)
    if not match:
        return False
    data = page.get_contents().get_data()
    updated = DecodedStreamObject()
    updated.set_data(data[:match.start()] + match[1] + data[match.end():])
    # replace_contents nullifies old array members, which other pages may still share.
    page[NameObject('/Contents')] = writer._add_object(updated.flate_encode())
    return True


def opaque_drawing(form, writer, active=None):
    active = set() if active is None else active
    if id(form) in active or '/Group' in form or '/OC' in form:
        return False
    active.add(id(form))
    resources = form.get('/Resources', {}).get_object() if hasattr(form.get('/Resources', {}), 'get_object') else form.get('/Resources', {})
    states = resources.get('/ExtGState', {})
    if hasattr(states,'get_object'):states=states.get_object()
    for state in states.values():
        state = state.get_object()
        if (state.get('/CA',1)!=1 or state.get('/ca',1)!=1 or state.get('/BM','/Normal')!='/Normal'
                or state.get('/SMask','/None')!='/None' or state.get('/OP',False) or state.get('/op',False)):
            active.remove(id(form)); return False
    data = form.get_data()
    if data.startswith(HEADER) and '/GS' not in states:
        active.remove(id(form)); return False
    if data.startswith(HEADER) and (SEGMENT.fullmatch(data[len(HEADER):]) or
            b''.join(m[0] for m in SEGMENT.finditer(data[len(HEADER):])) == data[len(HEADER):] or
            b''.join(m[0] for m in GROUP.finditer(data[len(HEADER):])) == data[len(HEADER):]):
        active.remove(id(form)); return True
    allowed = {b'q',b'Q',b'cm',b'Do',b'gs',b'rg',b'RG',b'g',b'G',b'w',b'J',b'j',b'M',b'd',b'm',b'l',b'c',b'v',b'y',b'h',b're',b'S',b's',b'f',b'f*',b'W',b'W*',b'n'}
    for args, op in ContentStream(form,writer).operations:
        if op not in allowed:
            active.remove(id(form)); return False
        if op == b'gs' and (len(args)!=1 or args[0] not in states):
            active.remove(id(form)); return False
        if op == b'Do':
            target = resources.get('/XObject',{}).get(args[0])
            if target is None or target.get_object().get('/Subtype')!='/Form' or not opaque_drawing(target.get_object(),writer,active):
                active.remove(id(form)); return False
    active.remove(id(form)); return True


def rewrite_handwriting(writer):
    forms = segments = draws = 0
    for obj in list(writer._objects):
        if not isinstance(obj, StreamObject) or obj.get('/Subtype') != '/Form' or '/Group' in obj:
            continue
        state = obj.get('/Resources', {}).get('/ExtGState', {}).get('/GS')
        if state is None:
            continue
        state = state.get_object()
        if dict(state) != {'/AIS': False, '/BM': '/Normal', '/CA': 1, '/LC': 2,
                           '/Type': '/ExtGState', '/ca': 1}:
            continue
        data = obj.get_data()
        updated, count, groups = batch_segments(data)
        if count and updated != data:
            obj.set_data(updated)
            forms += 1
            segments += count
            draws += groups
    duplicates = sum(remove_duplicate_layer(page, writer) for page in writer.pages)
    if not forms and not duplicates:
        raise ValueError('No supported BOOX handwriting patterns found; nothing changed.')
    print(f'Preserved {segments:,} segments in {forms:,} forms; '
          f'batched into {draws:,} stroke calls; removed {duplicates} duplicate layer draws.', flush=True)
    return forms, segments, draws, duplicates


def _render(path, index):
    with pdfium.PdfDocument(path) as document:
        document.init_forms()
        page = document[index]
        try:
            bitmap = page.render(scale=2, draw_annots=True)
            try:
                return bitmap.to_pil().convert('RGB')
            finally:
                bitmap.close()
        finally:
            page.close()


def verify_handwriting(original, candidate):
    with pdfium.PdfDocument(original) as a, pdfium.PdfDocument(candidate) as b:
        if len(a) != len(b):
            raise ValueError('Page count changed; candidate rejected.')
        count = len(a)
    for index in range(count):
        with _render(original, index) as a, _render(candidate, index) as b:
            if a.size != b.size:
                raise ValueError('Page dimensions changed; candidate rejected.')
            with ImageChops.difference(a, b) as difference:
                peak = max(high for low, high in difference.getextrema())
                mean = sum(ImageStat.Stat(difference).mean) / 3
                # shortcut: edge-shading guard at 144 DPI, not a universal visual-equivalence proof.
                if peak > 96 or mean > 1.5:
                    raise ValueError(f'Page {index+1}: shading difference exceeds guard '
                                     f'(peak={peak}, mean={mean:.4f}); candidate rejected.')
        print(f'Checked page {index+1}/{count}: peak shading delta {peak}/255, '
              f'mean {mean:.4f}/255', flush=True)
    return count
