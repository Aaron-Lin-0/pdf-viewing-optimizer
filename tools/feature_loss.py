"""Separate document-function changes from visible source-feature changes."""
import math

FUNCTIONAL_FEATURES = ('page_geometry', 'page_order', 'text', 'bookmarks', 'links',
                       'page_labels', 'attachments', 'forms', 'comments', 'layers')


def image_stamp_rectangles(page):
    from annotation_ink import has_interactive_data
    return [[float(v) for v in a.get_object()['/Rect']] for a in page.get('/Annots', [])
            if a.get_object().get('/Subtype') == '/Stamp'
            and a.get_object().get('/Name') == '/#ONYX-IMAGE'
            and not has_interactive_data(a.get_object())]


def image_stamp_loss(before, after, rectangles, cropbox, rotation=0):
    """Count source image-stamp regions, not PDF object identities or semantic symbols."""
    import numpy as np
    if before.size != after.size:
        return {'status': 'unproven', 'reason': 'Rendered dimensions differ'}
    left, bottom, right, top = map(float, cropbox)
    if not all(math.isfinite(v) for v in (left, bottom, right, top)) or right <= left or top <= bottom or rotation % 90:
        return {'status': 'unproven', 'reason': 'Unsupported page geometry'}
    a, b = np.asarray(before.convert('RGB')), np.asarray(after.convert('RGB'))
    height, width = a.shape[:2]
    changed = np.any(a != b, axis=2)
    covered = np.zeros((height, width), dtype=bool)
    regions = []
    for rect in rectangles:
        if len(rect) != 4 or not all(math.isfinite(v) for v in rect) or rect[2] <= rect[0] or rect[3] <= rect[1]:
            return {'status': 'check failed', 'reason': 'Invalid source feature rectangle'}
        points = []
        for x, y in ((rect[0], rect[1]), (rect[0], rect[3]), (rect[2], rect[1]), (rect[2], rect[3])):
            u, v = (x-left)/(right-left), (top-y)/(top-bottom)
            u, v = {0: (u, v), 90: (1-v, u), 180: (1-u, 1-v), 270: (v, 1-u)}[rotation % 360]
            points.append((u*width, v*height))
        x0 = max(0, min(width, math.floor(min(p[0] for p in points))))
        y0 = max(0, min(height, math.floor(min(p[1] for p in points))))
        x1 = max(0, min(width, math.ceil(max(p[0] for p in points))))
        y1 = max(0, min(height, math.ceil(max(p[1] for p in points))))
        covered[y0:y1, x0:x1] = True
        old, new = a[y0:y1, x0:x1], b[y0:y1, x0:x1]
        if not old.size or not np.any(old != 255):
            status = 'not visible'
        elif np.array_equal(old, new):
            status = 'unchanged'
        elif np.all(new == 255):
            status = 'blanked'
        else:
            status = 'changed'
        regions.append({'source_rect_pdf_points': rect, 'bbox_pixels': [x0, y0, x1, y1], 'status': status})
    counts = {name: sum(r['status'] == name for r in regions)
              for name in ('unchanged', 'blanked', 'changed', 'not visible')}
    visible = len(regions)-counts['not visible']
    return {'status': 'pass', 'source_image_stamp_regions': len(regions),
            'visible_source_regions': visible, 'region_counts': counts,
            'blanked_region_fraction': counts['blanked']/visible if visible else None,
            'changed_region_fraction': (counts['blanked']+counts['changed'])/visible if visible else None,
            'changed_pixels_outside_source_image_regions': int(np.count_nonzero(changed & ~covered)),
            'regions': regions,
            'coverage': 'Recognized source image-stamp rectangles only; overlapping handwriting is not isolated. Blanked means all rendered RGB pixels became white.'}


def summarize_feature_loss(checks, appearance=None):
    functions = {name: checks.get(name, {'status': 'unproven'})['status'] for name in FUNCTIONAL_FEATURES}
    absent = [name for name in FUNCTIONAL_FEATURES
              if checks.get(name, {}).get('source_presence') == 'absent']
    summary = {'functional_preserved': [k for k, v in functions.items() if v == 'pass' and k not in absent],
               'functional_absent_in_source': absent,
               'functional_confirmed_changes': [k for k, v in functions.items() if v == 'confirmed difference'],
               'functional_unresolved': [k for k, v in functions.items() if v not in {'pass', 'confirmed difference'}],
               'functional_statuses': functions,
               'functional_inventories': {name: {key: checks.get(name, {}).get(key)
                   for key in ('source_presence', 'before_inventory', 'after_inventory')}
                   for name in FUNCTIONAL_FEATURES},
               'note': 'Category counts are not a weighted quality score. Unresolved checks never count as preserved. Annotation disappearance alone does not prove lost visible content.'}
    edit = checks.get('annotation_editability', {})
    if 'before' in edit and 'after' in edit:
        summary['ink_annotation_objects_removed'] = max(0, sum(map(len, edit['before']))-sum(map(len, edit['after'])))
    text = checks.get('text', {})
    if 'before' in text and 'after' in text:
        summary['text_only_added_trailing_newlines'] = all(a == b or a == b+'\n' for b, a in zip(text['before'], text['after'])) and len(text['before']) == len(text['after'])
    pages = (appearance or {}).get('pages', [])
    measured = [p['visual_feature_loss'] for p in pages if p.get('visual_feature_loss', {}).get('status') == 'pass']
    if measured:
        counts = {k: sum(p['region_counts'][k] for p in measured) for k in ('unchanged', 'blanked', 'changed', 'not visible')}
        visible = sum(p['visible_source_regions'] for p in measured)
        summary['visual_source_features'] = {'analyzed_pages': len(measured), 'total_pages': len(pages),
            'region_counts': counts, 'visible_source_regions': visible,
            'blanked_region_fraction': counts['blanked']/visible if visible else None,
            'changed_region_fraction': (counts['blanked']+counts['changed'])/visible if visible else None,
            'changed_pixels_outside_source_image_regions': sum(p['changed_pixels_outside_source_image_regions'] for p in measured),
            'coverage': measured[0]['coverage']}
    general = [p['pdfium_144']['thresholds']['225']['source_features'] for p in pages
               if 'source_features' in p.get('pdfium_144', {}).get('thresholds', {}).get('225', {})]
    if general:
        summary['general_source_features'] = {
            'analyzed_pages': len(general),
            'components': sum(item['component_count'] for item in general),
            'dot_candidates': sum(item['dot_candidates'] for item in general),
            'fraction_bar_candidates': sum(item['fraction_bar_candidates'] for item in general),
            'crossing_or_join_components': sum(item['crossing_or_join_components'] for item in general),
            'loop_components': sum(item['loop_components'] for item in general),
            'coverage': 'All rendered source foreground components at luminance threshold 225; labels are geometric candidates, not semantic certification.'}
        vector = [p['source_vector_features'] for p in pages if 'source_vector_features' in p]
        if vector:
            summary['general_source_features'].update(
                filled_outlines=sum(item['filled_outlines'] for item in vector),
                zero_length_segments=sum(item['zero_length_segments'] for item in vector),
                vector_pages=len(vector),
                vector_coverage='PDF operator inventory; filled outlines and zero-length segments are representation labels, not semantic handwriting labels.')
    regions = (appearance or {}).get('regions', [])
    if regions:
        summary['worst_region_ids'] = [region['id'] for region in regions[:10]]
        summary['threshold_sensitive_regions'] = sum(
            region.get('metrics_576', region.get('metrics_144', {})).get('threshold_sensitive', False)
            for region in regions)
    summary['foreground_loss_by_threshold'] = (appearance or {}).get('document_ink_summary', {})
    return summary
