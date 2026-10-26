"""Inventory PDF structure and drawing mechanisms without changing the source."""
import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import re
import sys

from pypdf.generic import ContentStream, IndirectObject

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from annotation_ink import eligible, has_interactive_data
from navigation import navigation_fingerprint, reader_for, validate_input


PATH_OPERATORS = {b'm': 'moves', b'l': 'lines', b'c': 'cubic_curves',
                  b'v': 'cubic_curves', b'y': 'cubic_curves', b'h': 'closes', b're': 'rectangles'}
STROKES = {b'S', b's', b'B', b'B*', b'b', b'b*'}
FILLS = {b'f', b'F', b'f*', b'B', b'B*', b'b', b'b*'}
PAINTS = STROKES | FILLS | {b'n'}
MAX_EXPANDED_OPERATIONS = 2_000_000


def filehash(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def identity(reference, value):
    if isinstance(reference, IndirectObject):
        return reference.idnum, reference.generation
    return 'direct', id(value)


def resolve(value):
    return value.get_object() if hasattr(value, 'get_object') else value


def number(value):
    return format(Decimal(str(value)).normalize(), 'f')


def matrix(values):
    return tuple(float(value) for value in values)


def multiply(left, right):
    a, b, c, d, e, f = left
    g, h, i, j, k, l = right
    return (a*g+c*h, b*g+d*h, a*i+c*j, b*i+d*j, a*k+c*l+e, b*k+d*l+f)


def simple(value, depth=0):
    value = resolve(value)
    if depth > 4:
        return 'nested'
    if isinstance(value, dict):
        return {str(key): simple(item, depth+1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [simple(item, depth+1) for item in value]
    return str(value) if value is not None else None


def unsafe_name(value):
    return bool(re.search(r'[^/A-Za-z0-9_.+\-]', str(value)))


def resource_scope(page):
    resources = page.get('/Resources', {})
    own = page.raw_get('/Resources') if '/Resources' in page else None
    parent = page.get('/Parent')
    seen = set()
    while parent is not None:
        parent = resolve(parent)
        if id(parent) in seen:
            break
        seen.add(id(parent))
        if '/Resources' in parent:
            inherited = parent.raw_get('/Resources')
            if own is None or resolve(own) is resolve(inherited):
                return resources, 'inherited'
        parent = parent.get('/Parent')
    return resources, 'page' if own is not None else 'absent'


def stream_filters(stream):
    filters = resolve(stream.get('/Filter', []))
    if not isinstance(filters, (list, tuple)):
        filters = [filters] if filters else []
    return [str(resolve(value)) for value in filters]


def image_definition(reference, image):
    try:
        data = image.get_data()
        decoded_bytes = len(data)
        digest = hashlib.sha256(data).hexdigest()
        error = None
    except Exception as exc:
        decoded_bytes = None
        digest = None
        error = f'{type(exc).__name__}: {exc}'
    encoded = getattr(image, '_data', None)
    result = {
        'object': list(identity(reference, image)),
        'width': int(image.get('/Width', 0)), 'height': int(image.get('/Height', 0)),
        'bits_per_component': int(image.get('/BitsPerComponent', 1)) if image.get('/BitsPerComponent') is not None else None,
        'color_space': simple(image.get('/ColorSpace')),
        'filters': stream_filters(image), 'encoded_bytes': len(encoded) if encoded is not None else None,
        'decoded_bytes': decoded_bytes, 'decoded_sha256': digest,
        'image_mask': bool(image.get('/ImageMask', False)),
        'soft_mask': '/SMask' in image, 'mask': '/Mask' in image,
    }
    if error:
        result['decode_error'] = error
    return result


def image_signature(image):
    fields = {key: image.get(key) for key in
              ('width', 'height', 'bits_per_component', 'color_space', 'filters',
               'decoded_bytes', 'decoded_sha256', 'image_mask', 'soft_mask', 'mask')}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def annotation_inventory(page):
    subtypes = Counter()
    eligible_count = image_stamps = protected = actions = 0
    links = []
    issues = []
    refs = []
    try:
        refs = list(resolve(page.get('/Annots', [])))
        for reference in refs:
            annot = resolve(reference)
            subtype = str(annot.get('/Subtype', '/Unknown'))
            subtypes[subtype] += 1
            eligible_count += eligible(annot) is not None
            image_stamps += subtype == '/Stamp' and annot.get('/Name') == '/#ONYX-IMAGE'
            protected += has_interactive_data(annot)
            actions += int('/A' in annot) + int('/AA' in annot)
            if subtype == '/Link':
                action = resolve(annot.get('/A', {}))
                links.append({'rect': [float(value) for value in annot.get('/Rect', [])],
                              'target': 'uri' if '/URI' in action else
                                        'action' if action else 'destination' if '/Dest' in annot else 'unknown'})
    except Exception as exc:
        issues.append(f'Annotations unavailable: {type(exc).__name__}: {exc}')
    leading = 0
    blocked = '/Group' in page
    if not blocked:
        for reference in refs:
            annot = resolve(reference)
            border = annot.get('/Border', [0, 0, 1])
            style = resolve(annot.get('/BS', {}))
            width = style.get('/W', border[2] if len(border) >= 3 else 1)
            if annot.get('/Subtype') == '/Link' and '/AP' not in annot and width == 0:
                continue
            if eligible(annot) is None:
                blocked = True
                break
            leading += 1
    return ({'count': sum(subtypes.values()), 'subtypes': dict(sorted(subtypes.items())),
             'eligible_individual': eligible_count, 'eligible_leading_prefix': leading,
             'image_stamps': image_stamps, 'protected_or_interactive': protected,
             'actions': actions, 'links': links,
             'conversion_barrier': blocked and bool(refs)}, issues)


def characterize_page(reader, page, page_number):
    counts = Counter()
    widths = Counter()
    issues = []
    forms = set()
    images = {}
    image_calls = Counter()
    escaped_names = set()
    active = set()
    cache = {}
    paint = hashlib.sha256()
    state = {'expanded': 0}

    def issue(message):
        if message not in issues:
            issues.append(message)

    def operations(stream, key):
        if key not in cache:
            cache[key] = ContentStream(stream, reader).operations
        return cache[key]

    def transparent(graphics):
        return (graphics.get('/CA', 1) != 1 or graphics.get('/ca', 1) != 1 or
                graphics.get('/BM', '/Normal') != '/Normal' or
                graphics.get('/SMask', '/None') != '/None')

    def walk(stream, resources, scope, ctm=(1, 0, 0, 1, 0, 0), depth=0):
        if depth > 64:
            issue('Form nesting exceeds 64 levels')
            return
        try:
            resources = resolve(resources or {})
            key = identity(getattr(stream, 'indirect_reference', None), stream)
            stream_operations = operations(stream, key)
        except Exception as exc:
            issue(f'{scope} content unavailable: {type(exc).__name__}: {exc}')
            return
        for resource_type in ('/XObject', '/ExtGState', '/Properties'):
            try:
                for name in resolve(resources.get(resource_type, {})):
                    if unsafe_name(name):
                        escaped_names.add(str(name))
            except Exception as exc:
                issue(f'{scope} {resource_type} unavailable: {type(exc).__name__}: {exc}')
        graphics_stack = []
        width = '1'
        width_declared = False
        simple_width = None
        simple_run = 0
        path = []
        current = None
        for operands, operator in stream_operations:
            state['expanded'] += 1
            if state['expanded'] > MAX_EXPANDED_OPERATIONS:
                issue(f'Expanded operation limit {MAX_EXPANDED_OPERATIONS} exceeded')
                return
            if operator == b'q':
                simple_width = None; simple_run = 0
                graphics_stack.append((ctm, width))
                counts['graphics_state_saves'] += 1
                continue
            if operator == b'Q':
                simple_width = None; simple_run = 0
                counts['graphics_state_restores'] += 1
                if not graphics_stack:
                    issue('Unbalanced graphics-state restore')
                else:
                    ctm, width = graphics_stack.pop()
                continue
            if operator == b'cm':
                simple_width = None; simple_run = 0
                counts['transforms'] += 1
                try:
                    ctm = multiply(matrix(operands), ctm)
                except Exception as exc:
                    issue(f'Invalid transform: {type(exc).__name__}: {exc}')
                continue
            if operator == b'w':
                new_width = number(operands[0])
                counts['width_changes'] += new_width != width
                width = new_width
                width_declared = True
                widths[width] += 1
                continue
            if operator == b'gs':
                simple_width = None; simple_run = 0
                try:
                    graphics = resolve(resolve(resources.get('/ExtGState', {}))[operands[0]])
                    counts['transparency_state_uses'] += transparent(graphics)
                except Exception as exc:
                    issue(f'Graphics state unresolved: {type(exc).__name__}: {exc}')
                continue
            if operator in {b'W', b'W*'}:
                simple_width = None; simple_run = 0
                counts['clips'] += 1
                continue
            if operator in {b'BMC', b'BDC'}:
                simple_width = None; simple_run = 0
                counts['marked_content'] += 1
                counts['optional_content_marks'] += bool(operands and str(operands[0]) == '/OC')
                continue
            if operator in {b'Tj', b'TJ', b"'", b'"'}:
                simple_width = None; simple_run = 0
                counts['text_show_operations'] += 1
                continue
            if operator == b'INLINE IMAGE':
                simple_width = None; simple_run = 0
                counts['inline_images'] += 1
                paint.update(b'image\n')
                continue
            if operator in PATH_OPERATORS:
                counts[PATH_OPERATORS[operator]] += 1
                counts[f'{scope}_vector_operators'] += 1
                values = tuple(number(value) for value in operands)
                if operator == b'm':
                    current = values[:2]
                elif operator == b'l':
                    counts['zero_length_segments'] += current == values[:2]
                    current = values[:2]
                elif operator in {b'c', b'v', b'y'}:
                    current = values[-2:]
                path.append((operator.decode(), values))
                continue
            if operator in PAINTS:
                is_stroke = operator in STROKES
                is_fill = operator in FILLS
                counts['stroke_paints'] += is_stroke
                counts['fill_paints'] += is_fill
                counts['filled_outlines'] += is_fill and any(op in {'c', 'v', 'y', 'h'} for op, _ in path)
                simple_line = (width_declared and is_stroke and len(path) == 2 and
                               path[0][0] == 'm' and path[1][0] == 'l')
                if simple_line:
                    counts['simple_stroke_segments'] += 1
                    if simple_width == width:
                        counts['same_width_adjacent_segments'] += 1
                        if simple_run == 1:
                            counts['same_width_runs'] += 1
                        simple_run += 1
                    else:
                        simple_width, simple_run = width, 1
                else:
                    simple_width, simple_run = None, 0
                category = b'stroke' if is_stroke and not is_fill else b'fill' if is_fill and not is_stroke else b'fill-stroke' if is_fill else b'clip-only'
                paint.update(category + b'\n')
                counts['paint_operations'] += operator != b'n'
                path = []
                current = None
                width_declared = False
                continue
            if operator != b'Do':
                continue
            simple_width = None; simple_run = 0
            try:
                name = operands[0]
                if unsafe_name(name):
                    escaped_names.add(str(name))
                reference = resolve(resources.get('/XObject', {})).get(name)
                if reference is None:
                    raise KeyError(str(name))
                child = resolve(reference)
                subtype = child.get('/Subtype')
            except Exception as exc:
                issue(f'XObject call unresolved: {type(exc).__name__}: {exc}')
                continue
            key = identity(reference, child)
            if subtype == '/Image':
                counts['image_calls'] += 1
                paint.update(b'image\n')
                if key not in images:
                    images[key] = image_definition(reference, child)
                definition = images[key]
                signature = image_signature(definition)
                image_calls[signature] += 1
                x_points, y_points = math.hypot(ctm[0], ctm[1]), math.hypot(ctm[2], ctm[3])
                if x_points > 0 and y_points > 0:
                    definition.setdefault('placements', []).append({
                        'x_dpi': definition['width'] * 72 / x_points,
                        'y_dpi': definition['height'] * 72 / y_points})
                else:
                    definition.setdefault('placement_issues', []).append('Zero-area image transform')
            elif subtype == '/Form':
                counts['form_calls'] += 1
                counts['nested_form_calls'] += depth > 0
                if key in forms:
                    counts['repeated_form_calls'] += 1
                forms.add(key)
                paint.update(b'form\n')
                if key in active:
                    issue('Cyclic Form reference')
                    continue
                active.add(key)
                counts['transparency_groups'] += '/Group' in child
                form_matrix = matrix(child.get('/Matrix', [1, 0, 0, 1, 0, 0]))
                walk(child, child.get('/Resources', {}), 'form', multiply(form_matrix, ctm), depth + 1)
                active.remove(key)
            else:
                issue(f'Unsupported XObject subtype: {subtype}')
        if graphics_stack:
            issue(f'Unbalanced graphics-state saves in {scope} content')

    annotations, annotation_issues = annotation_inventory(page)
    issues.extend(annotation_issues)
    content = page.get_contents()
    resources, scope = resource_scope(page)
    if '/Group' in page:
        counts['transparency_groups'] += 1
    if content is not None:
        walk(content, resources, 'page')
    counts['unique_forms'] = len(forms)
    counts['unique_images'] = len(images)
    counts['expanded_operations'] = state['expanded']
    definitions = []
    for definition in images.values():
        signature = image_signature(definition)
        definition['signature'] = signature
        definition['calls'] = image_calls[signature]
        placements = definition.get('placements', [])
        if placements:
            definition['effective_dpi'] = {
                'minimum': min(min(value['x_dpi'], value['y_dpi']) for value in placements),
                'maximum': max(max(value['x_dpi'], value['y_dpi']) for value in placements),
            }
        definitions.append(definition)
    categories = []
    if counts['page_vector_operators']:
        categories.append('page_vectors')
    if counts['form_calls']:
        categories.append('form_xobjects')
    if annotations['count']:
        categories.append('annotations')
    if counts['image_calls'] or counts['inline_images']:
        categories.append('images')
    if counts['text_show_operations']:
        categories.append('text')
    labels = categories + (['mixed'] if len(categories) > 1 else [])
    content_status = 'absent' if content is None else 'partial' if issues else 'complete'
    return {
        'page': page_number, 'labels': labels or ['blank_or_unclassified'],
        'coverage': 'complete' if not issues else 'partial',
        'confidence': 'high' if not issues else 'low', 'issues': issues,
        'coverage_by_category': {
            'content': content_status,
            'annotations': 'failed' if annotation_issues else 'complete' if annotations['count'] else 'absent',
            'images': 'partial' if any('decode_error' in value for value in definitions) else
                      'complete' if definitions or counts['inline_images'] else 'absent',
            'navigation': 'complete' if annotations['links'] else 'absent',
        },
        'resource_scope': scope, 'escaped_resource_names': sorted(escaped_names),
        'annotations': annotations,
        'content': dict(sorted(counts.items())),
        'line_widths': {'declared': dict(sorted(widths.items())),
                        'meaning': 'PDF line-width declarations; filled pressure strokes may not use them'},
        'paint_order_sha256': paint.hexdigest(),
        'images': {'definitions': definitions, 'calls': counts['image_calls'],
                   'unique': len(definitions), 'inline': counts['inline_images']},
    }


def outline_inventory(reader):
    count = max_depth = destinations = 0
    def walk(items, depth=1):
        nonlocal count, max_depth, destinations
        for item in items:
            if isinstance(item, list):
                walk(item, depth + 1)
            else:
                count += 1
                max_depth = max(max_depth, depth)
                try:
                    destinations += reader.get_destination_page_number(item) >= 0
                except Exception:
                    pass
    walk(reader.outline)
    return {'count': count, 'maximum_depth': max_depth, 'resolved_destinations': destinations}


def document_inventory(reader, pages):
    root = reader.trailer['/Root']
    issues = []
    text = {'pages_with_text': 0, 'characters': 0, 'non_ascii_characters': 0,
            'math_like_characters': 0, 'failed_pages': []}
    math_symbols = set('=+-×÷∫∑√∞≈≠≤≥αβγδεθλμπσφω')
    for page_number, page in enumerate(reader.pages, 1):
        try:
            value = page.extract_text() or ''
            text['pages_with_text'] += bool(value.strip())
            text['characters'] += len(value)
            text['non_ascii_characters'] += sum(ord(char) > 127 for char in value)
            text['math_like_characters'] += sum(char in math_symbols for char in value)
        except Exception as exc:
            text['failed_pages'].append({'page': page_number, 'error': f'{type(exc).__name__}: {exc}'})
    try:
        attachments = [{'name': name, 'files': [{'bytes': len(value),
                        'sha256': hashlib.sha256(value).hexdigest()} for value in values]}
                       for name, values in reader.attachments.items()]
    except Exception as exc:
        attachments = []
        issues.append(f'Attachments unavailable: {type(exc).__name__}: {exc}')
    try:
        fields = reader.get_fields() or {}
    except Exception as exc:
        fields = {}
        issues.append(f'Forms unavailable: {type(exc).__name__}: {exc}')
    try:
        layers = resolve(root.get('/OCProperties', {}))
        groups = resolve(layers.get('/OCGs', [])) if layers else []
    except Exception as exc:
        layers, groups = {}, []
        issues.append(f'Layers unavailable: {type(exc).__name__}: {exc}')
    try:
        labels = list(reader.page_labels)
    except Exception as exc:
        labels = []
        issues.append(f'Page labels unavailable: {type(exc).__name__}: {exc}')
    try:
        bookmarks = outline_inventory(reader)
    except Exception as exc:
        bookmarks = {'count': None, 'maximum_depth': None, 'resolved_destinations': None}
        issues.append(f'Bookmarks unavailable: {type(exc).__name__}: {exc}')
    try:
        navigation = hashlib.sha256(repr(navigation_fingerprint(reader)).encode()).hexdigest()
        navigation_status = 'complete'
    except Exception as exc:
        navigation = None
        navigation_status = f'failed: {type(exc).__name__}: {exc}'
    image_counts = Counter()
    image_calls = 0
    for page in pages:
        for definition in page['images']['definitions']:
            image_counts[definition['signature']] += 1
            image_calls += definition['calls']
    return {
        'coverage': 'partial' if issues or text['failed_pages'] else 'complete',
        'issues': issues, 'text': text, 'bookmarks': bookmarks, 'page_labels': labels,
        'attachments': attachments,
        'forms': {'present': '/AcroForm' in root, 'fields': len(fields),
                  'xfa': '/AcroForm' in root and '/XFA' in resolve(root['/AcroForm'])},
        'layers': {'present': bool(layers), 'groups': len(groups)},
        'structural_tags': {'present': '/StructTreeRoot' in root},
        'actions': {'catalog_open_action': '/OpenAction' in root,
                    'annotation_actions': sum(page['annotations']['actions'] for page in pages)},
        'links': {'count': sum(len(page['annotations']['links']) for page in pages)},
        'images': {'calls': image_calls, 'unique_decoded_definitions': len(image_counts),
                   'repeated_definitions': sum(count > 1 for count in image_counts.values()),
                   'repeated_calls': max(0, image_calls - len(image_counts))},
        'navigation_fingerprint': navigation, 'navigation_status': navigation_status,
    }


def characterize(path):
    path = Path(path)
    before = filehash(path)
    with reader_for(path) as reader:
        validate_input(reader)
        pages = [characterize_page(reader, page, page_number)
                 for page_number, page in enumerate(reader.pages, 1)]
        document = document_inventory(reader, pages)
    if filehash(path) != before:
        raise ValueError('Source changed during characterization.')
    complete = sum(page['coverage'] == 'complete' for page in pages)
    return {
        'schema': 2, 'source_sha256': before, 'bytes': path.stat().st_size,
        'page_count': len(pages),
        'coverage': {'pages_total': len(pages), 'pages_complete': complete,
                     'pages_partial': len(pages) - complete,
                     'text': 'partial' if document['text']['failed_pages'] else
                             'complete' if document['text']['pages_with_text'] else 'absent',
                     'navigation': document['navigation_status'],
                     'document_features': document['coverage']},
        'document': document, 'pages': pages,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf', type=Path)
    args = parser.parse_args()
    print(json.dumps(characterize(args.pdf), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
