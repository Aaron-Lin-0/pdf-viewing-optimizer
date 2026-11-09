"""Versioned experiment manifest and drawing opportunity reporting for P16-B."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools')]
from pdf_characterize import characterize


SCHEMA = 'p16-b-v1'

FROZEN_TREATMENTS = [
    {
        'id': 'treatment-0-original',
        'name': 'Original',
        'recipe': 'source',
        'purpose': 'Source reference and absolute behavior',
        'packing_settings': 'unchanged',
        'status': 'active',
    },
    {
        'id': 'treatment-1-exact-baseline',
        'name': 'Best accepted exact copy',
        'recipe': 'exact_viewing_copy',
        'purpose': 'Primary incremental-benefit comparator',
        'packing_settings': 'standard_qpdf_object_streams',
        'status': 'active',
    },
    {
        'id': 'treatment-2-qualified-batching',
        'name': 'Existing opacity-qualified batching',
        'recipe': 'fast_handwriting_qualified',
        'purpose': 'Distinguish paint-call savings from point reduction',
        'packing_settings': 'standard_qpdf_object_streams',
        'status': 'active',
    },
    {
        'id': 'treatment-3-zero-tolerance-control',
        'name': 'Zero-tolerance experimental control',
        'recipe': 'point_reduction_tolerance_0',
        'purpose': 'Prove the new path is a no-op when no reduction is requested',
        'packing_settings': 'standard_qpdf_object_streams',
        'status': 'active',
    },
    {
        'id': 'treatment-4-point-reduction-alone',
        'name': 'Point reduction alone',
        'recipe': 'point_reduction_no_batching',
        'purpose': 'Isolate fewer vertices from other transformations',
        'packing_settings': 'standard_qpdf_object_streams',
        'status': 'active',
    },
    {
        'id': 'treatment-5-reduction-plus-batching',
        'name': 'Point reduction plus qualified batching',
        'recipe': 'point_reduction_with_batching',
        'purpose': 'Measure the combination, with the same packing settings',
        'packing_settings': 'standard_qpdf_object_streams',
        'status': 'active',
    },
    {
        'id': 'treatment-6-curve-fitting',
        'name': 'Curve fitting',
        'recipe': 'curve_fitting_bezier',
        'purpose': 'Determine whether fewer curve commands actually beat line reduction',
        'packing_settings': 'standard_qpdf_object_streams',
        'status': 'deferred_until_p16_f_opens',
    },
]


def filehash(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def analyze_drawing_opportunity(source_or_characterization):
    """Inventory drawing mechanisms and isolate first supported subset per P16-B."""
    if isinstance(source_or_characterization, (str, Path)):
        char = characterize(Path(source_or_characterization))
    else:
        char = source_or_characterization

    pages = char['pages']
    page_reports = []

    total_multi_vertex = 0
    total_two_point = 0
    total_curves = 0
    total_variable_width = 0
    total_filled_outlines = 0
    total_image_stamps = 0
    total_annotations = 0
    total_image_calls = 0
    total_inline_images = 0
    total_decoded_path_bytes = 0
    total_point_counts = 0
    total_paint_calls = 0
    total_transforms = 0

    all_declared_widths = set()
    supported_runs_total = 0
    supported_points_total = 0
    supported_paint_total = 0

    skip_reasons = {
        'device_dependent_hairlines': 0,
        'variable_widths': 0,
        'filled_outlines': 0,
        'transparency_or_smask': 0,
        'unresolved_structures': 0,
        'unsupported_transforms': 0,
        'live_annotations': 0,
        'raster_only': 0,
        'no_drawing_ops': 0,
    }

    supported_pages_count = 0
    raster_only_pages_count = 0
    already_efficient_pages_count = 0
    unsupported_or_no_op_pages_count = 0

    for page in pages:
        p_num = page['page']
        content = page['content']
        annots = page['annotations']
        img_info = page['images']
        widths = page['line_widths'].get('declared', {})
        for w in widths:
            all_declared_widths.add(w)

        mv = content.get('multi_vertex_stroked_paths', 0)
        tp = content.get('simple_stroke_segments', 0)
        curves = content.get('cubic_curves', 0)
        width_changes = content.get('width_changes', 0)
        filled = content.get('filled_outlines', 0)
        stamps = annots.get('image_stamps', 0)
        annot_count = annots.get('count', 0)
        img_calls = img_info.get('calls', 0)
        inline_imgs = img_info.get('inline', 0)
        path_bytes = content.get('decoded_path_bytes', 0)
        points = content.get('path_vertices', 0)
        paints = content.get('paint_operations', 0)
        transforms = content.get('transforms', 0)
        transp_uses = content.get('transparency_state_uses', 0) + content.get('transparency_groups', 0)
        hairlines = content.get('hairline_strokes', 0)
        unsupported_transforms = content.get('unsupported_drawing_transforms', 0)
        issues = page.get('issues', [])

        total_multi_vertex += mv
        total_two_point += tp
        total_curves += curves
        total_variable_width += width_changes
        total_filled_outlines += filled
        total_image_stamps += stamps
        total_annotations += annot_count
        total_image_calls += img_calls
        total_inline_images += inline_imgs
        total_decoded_path_bytes += path_bytes
        total_point_counts += points
        total_paint_calls += paints
        total_transforms += transforms

        page_skips = []
        if hairlines > 0:
            skip_reasons['device_dependent_hairlines'] += hairlines
            page_skips.append(f'{hairlines} hairline stroke(s)')
        if width_changes > 0:
            skip_reasons['variable_widths'] += width_changes
            page_skips.append(f'{width_changes} variable-width transition(s)')
        if filled > 0:
            skip_reasons['filled_outlines'] += filled
            page_skips.append(f'{filled} filled outline(s)')
        if transp_uses > 0:
            skip_reasons['transparency_or_smask'] += transp_uses
            page_skips.append(f'{transp_uses} transparency/smask use(s)')
        if issues:
            skip_reasons['unresolved_structures'] += len(issues)
            page_skips.append(f'{len(issues)} unresolved structure issue(s)')
        if unsupported_transforms:
            skip_reasons['unsupported_transforms'] += unsupported_transforms
            page_skips.append(f'{unsupported_transforms} stroke(s) with unsupported transforms')
        if annot_count > 0 and annots.get('conversion_barrier', False):
            skip_reasons['live_annotations'] += annot_count
            page_skips.append(f'{annot_count} live annotation(s) with conversion barrier')

        has_vectors = (mv > 0 or tp > 0 or points > 0)
        has_raster = (img_calls > 0 or inline_imgs > 0)

        page_supported = False
        eligible_runs = 0
        eligible_points = 0
        eligible_paints = 0

        if not issues:
            eligible_runs = content.get('eligible_drawing_runs', 0)
            eligible_points = content.get('eligible_drawing_points', 0)
            eligible_paints = content.get('eligible_drawing_paints', 0)
        if eligible_runs:
            page_supported = True
            supported_pages_count += 1
            supported_runs_total += eligible_runs
            supported_points_total += eligible_points
            supported_paint_total += eligible_paints
        elif not has_vectors and has_raster:
            skip_reasons['raster_only'] += 1
            raster_only_pages_count += 1
            page_skips.append('raster-only page')
        elif (not issues and content.get('eligible_minimal_segments', 0) and
              not (hairlines or filled or transp_uses or unsupported_transforms)):
            already_efficient_pages_count += 1
        else:
            if not has_vectors:
                skip_reasons['no_drawing_ops'] += 1
                page_skips.append('no drawing operations on page')
            unsupported_or_no_op_pages_count += 1

        page_reports.append({
            'page': p_num,
            'supported_opportunity': page_supported,
            'multi_vertex_stroked_paths': mv,
            'two_point_segments': tp,
            'curves': curves,
            'variable_width_runs': width_changes,
            'filled_outlines': filled,
            'image_stamps': stamps,
            'annotations': annot_count,
            'raster_calls': img_calls + inline_imgs,
            'decoded_path_bytes': path_bytes,
            'point_counts': points,
            'paint_calls': paints,
            'declared_widths': sorted(widths.keys()),
            'eligible_runs': eligible_runs,
            'eligible_points': eligible_points,
            'skip_reasons': page_skips,
        })

    supported = supported_runs_total > 0
    if supported:
        status = 'supported opportunity'
        explanation = (
            f'Found {supported_runs_total} eligible opaque positive constant-width run(s) '
            f'with {supported_points_total} points across {supported_pages_count} page(s).'
        )
    else:
        status = 'no supported opportunity'
        if total_point_counts == 0 and (total_image_calls > 0 or total_inline_images > 0):
            explanation = 'Document is raster-only; contains no vector drawing paths.'
        elif total_point_counts == 0:
            explanation = 'Document contains no vector drawing operations.'
        elif skip_reasons['transparency_or_smask'] > 0 and supported_runs_total == 0:
            explanation = 'Transparency, soft masks, or groups were found without a supported drawing run.'
        elif skip_reasons['device_dependent_hairlines'] > 0 and supported_runs_total == 0:
            explanation = 'Device-dependent hairlines were found without a supported drawing run.'
        else:
            explanation = 'Document drawing geometry is already minimal or outside supported subset.'

    return {
        'inventory': {
            'multi_vertex_stroked_paths': total_multi_vertex,
            'two_point_segments': total_two_point,
            'curves': total_curves,
            'variable_width_runs': total_variable_width,
            'filled_outlines': total_filled_outlines,
            'image_stamps': total_image_stamps,
            'annotations': total_annotations,
            'raster_content': {
                'image_calls': total_image_calls,
                'inline_images': total_inline_images,
                'unique_images': char['document']['images']['unique_decoded_definitions'],
            },
            'decoded_path_bytes': total_decoded_path_bytes,
            'point_counts': total_point_counts,
            'paint_calls': total_paint_calls,
            'local_widths': sorted(all_declared_widths),
            'transforms': total_transforms,
        },
        'first_supported_subset': {
            'supported': supported,
            'status': status,
            'explanation': explanation,
            'eligible_runs_count': supported_runs_total,
            'eligible_points_count': supported_points_total,
            'eligible_paint_calls': supported_paint_total,
            'skip_reasons': skip_reasons,
        },
        'pages': page_reports,
        'denominator_tracking': {
            'total_pages': len(pages),
            'pages_with_supported_opportunity': supported_pages_count,
            'pages_unsupported_or_no_op': unsupported_or_no_op_pages_count,
            'pages_raster_only': raster_only_pages_count,
            'pages_already_efficient': already_efficient_pages_count,
            'policy': (
                'No-op, already-efficient, raster-only and unsupported inputs '
                'are represented, not silently removed from denominator.'
            ),
        },
    }


def select_sample_pages(char, opportunity_report):
    """Identify sample pages from immutable source only per P16-B step 4."""
    pages = char['pages']
    total = len(pages)
    if total <= 1:
        return {
            'all_pages': [1],
            'selected_samples': [1],
            'page_1': 1,
            'slow_dense_pages': [1],
            'representative_small_writing': [1],
            'selection_criteria': 'Single-page document; page 1 selected.',
        }

    # Find slow/dense pages by expanded operations and point counts
    by_density = sorted(
        pages,
        key=lambda p: (
            p['content'].get('expanded_operations', 0),
            p['content'].get('path_vertices', 0),
            p['content'].get('paint_operations', 0),
        ),
        reverse=True,
    )
    slow_dense = [p['page'] for p in by_density[:2]]

    # Find representative small writing pages: smallest positive declared widths
    def small_writing_rank(p):
        widths = [float(w) for w in p['line_widths'].get('declared', {}) if w != '0']
        min_width = min(widths) if widths else 999.0
        return (min_width, -p['content'].get('path_vertices', 0))

    by_small = sorted(pages, key=small_writing_rank)
    small_writing = [by_small[0]['page']]

    selected = sorted(set([1] + slow_dense + small_writing))

    return {
        'all_pages': [p['page'] for p in pages],
        'selected_samples': selected,
        'page_1': 1,
        'slow_dense_pages': slow_dense,
        'representative_small_writing': small_writing,
        'selection_criteria': (
            'Characterized from immutable source only; timing samples include '
            'page 1, source-only slow/dense pages and representative small writing. '
            'Do not choose pages from candidate results.'
        ),
    }


def exact_pages(rows, count, pdfjs=False):
    return (count > 0 and len(rows) == count and
            all(row.get('page') == page and row.get('exact') is True and
                (not pdfjs or row.get('ok') is True)
                for page, row in enumerate(rows, 1)))


def exact_report(report):
    count = report['pages']
    if report.get('profile') == 'quick':
        if report['optimizer'] != 'adaptive':
            return False
        verification = report['verification']
        detail = report['high_resolution_and_print']['detail']
        if verification['complete'] is not True or verification['policy'] != 'direct-viewing-2':
            return False
    else:
        selected = next(row for row in report['candidates'] if row['kind'] == report['selected'])
        if selected['converted_annotations'] != 0 or selected['exact'] is not True:
            return False
        verification = selected['verification']
        detail = report['detail_crop']
        if not all(report['browser']['results'][role]['ok'] is True for role in ('source', 'candidate')):
            return False
    checks = verification['checks']
    return (verification['full'] is True and verification['exact'] is True and
            all(checks[key] is True for key in ('navigation', 'ordered_geometry_and_resources', 'pdfium_144_dpi')) and
            exact_pages(verification['pages'], count) and exact_pages(report['pdfjs_all_pages'], count, pdfjs=True) and
            detail['exact'] is True)


def freeze_exact_baseline(source_path, exact_baseline_path=None):
    """Use a published exact viewing copy only with matching production evidence."""
    source_path = Path(source_path)
    source_hash = filehash(source_path)

    selection_rules = [
        'Require a passing production exact-viewing report and matching source/output hashes.',
        'Choose original source if no exact candidate qualifies or is provided.',
        'Choosing a weak comparator cannot manufacture an improvement.',
    ]

    if exact_baseline_path is not None:
        cand_path = Path(exact_baseline_path)
        try:
            evidence = cand_path.with_suffix('.json').read_bytes()
            report = json.loads(evidence)
            cand_hash = filehash(cand_path)
            if (report['status'] == 'optimized' and report['source_sha256'] == source_hash and
                    report['output_sha256'] == cand_hash and exact_report(report)):
                return {
                    'baseline_type': 'best_accepted_exact_copy',
                    'path': str(cand_path),
                    'sha256': cand_hash,
                    'source_sha256': source_hash,
                    'verification_report_sha256': hashlib.sha256(evidence).hexdigest(),
                    'selection_rules': selection_rules,
                    'verified': True,
                }
        except (OSError, ValueError, TypeError, KeyError, AttributeError, StopIteration):
            pass

    return {
        'baseline_type': 'original',
        'path': str(source_path),
        'sha256': source_hash,
        'source_sha256': source_hash,
        'selection_rules': selection_rules,
        'verified': True,
    }


def build_experiment_manifest(source_path, exact_baseline_path=None, config=None, budget=None):
    """Construct versioned experiment manifest and opportunity report per P16-B."""
    source_path = Path(source_path)
    source_hash = filehash(source_path)
    source_bytes = source_path.stat().st_size

    char = characterize(source_path)
    opp = analyze_drawing_opportunity(char)
    comparator = freeze_exact_baseline(source_path, exact_baseline_path)
    if comparator['source_sha256'] != source_hash:
        raise ValueError('Source changed while building the experiment manifest.')
    sample_pages = select_sample_pages(char, opp)

    manifest = {
        'schema': SCHEMA,
        'source': {
            'path': str(source_path),
            'bytes': source_bytes,
            'sha256': source_hash,
            'page_count': char['page_count'],
        },
        'comparator': comparator,
        'treatment_table': FROZEN_TREATMENTS,
        'sample_pages': sample_pages,
        'quality_policy': {
            'tolerance_ladder_mm': [0.0, 0.01, 0.025, 0.05],
            'tolerance_cap_rule': 'Capped at 10% of local minimum stroke width and relevant gap',
            'local_foreground_thresholds': [200, 225, 250],
            'foreground_matching_radius_mm': 0.1,
            'preservation_checks': [
                'page_geometry', 'page_order', 'text', 'bookmarks', 'links',
                'page_labels', 'attachments', 'forms', 'comments', 'layers',
                'annotation_editability', 'navigation_signature', 'geometry_resource_signature',
            ],
            'unsupported_structures_policy': 'Skip explicitly; preserve run without alteration',
        },
        'target_viewing_operations': {
            'operations': [
                'first_visible_render',
                'cached_page_revisit',
                'page_width_redraw',
                'zoom_100_redraw',
                'zoom_200_redraw',
            ],
            'renderers': {
                'pdfium': {
                    'resolutions_dpi': [144, 288],
                    'small_writing_crop_dpi': 576,
                },
                'pdfjs': {
                    'viewport_width': 1404,
                },
                'firefox_native': {
                    'viewports': ['page-width', '100%', '200%'],
                    'protocol': 'native-firefox-measure',
                },
            },
        },
        'run_budget': {
            'repetitions': (budget or {}).get('repetitions', 3),
            'noisy_repetition_extension': (budget or {}).get('noisy_repetition_extension', 5),
            'process_isolation': 'fresh sequential processes for every repetition',
            'treatment_order': 'alternating/rotating treatment order',
        },
        'opportunity_report': opp,
        'denominator_tracking': opp['denominator_tracking'],
    }

    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf', type=Path, help='Path to source PDF')
    parser.add_argument('--exact-baseline', type=Path, default=None,
                        help='Path to a published exact copy with its adjacent JSON verification report')
    parser.add_argument('--opportunity-only', action='store_true', help='Print opportunity report only')
    args = parser.parse_args()

    if args.opportunity_only:
        report = analyze_drawing_opportunity(args.pdf)
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        manifest = build_experiment_manifest(args.pdf, args.exact_baseline)
        print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
