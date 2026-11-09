"""Deterministic calibration controls and small-writing preservation verifier for P16-C."""
import argparse
import math
from pathlib import Path
import sys

import numpy as np
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject
import pypdfium2 as pdfium

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools')]
from comparison_metrics import image_metrics
from pdf_characterize import check_transform_support as check_transform_support


CALIBRATION_LADDER_MM = [0.0, 0.01, 0.025, 0.05]


def stream(writer, data):
    val = DecodedStreamObject()
    val.set_data(data)
    return writer._add_object(val)


def render_page(pdf_path, page_index=0, scale=4):
    """Render PDF page with pdfium at given scale (scale=4 -> 288 DPI)."""
    with pdfium.PdfDocument(str(pdf_path)) as doc:
        page = doc[page_index]
        try:
            bmp = page.render(scale=scale, draw_annots=True)
            try:
                return bmp.to_pil().convert('RGB')
            finally:
                bmp.close()
        finally:
            page.close()


def make_geometric_controls_stream():
    """Generate deterministic geometric paths with known dimensions and topology."""
    cmds = []
    # 1. Straight oversampled line (10 collinear points over 40pt)
    cmds.append(b'1 w 10 10 m 14 10 l 18 10 l 22 10 l 26 10 l 30 10 l 34 10 l 38 10 l 42 10 l 50 10 l S')
    # 2. Minimal line (2 points)
    cmds.append(b'1 w 60 10 m 100 10 l S')
    # 3. Smooth arc (quadrant sampled at 10 points)
    pts = [f'{70 + 20 * math.cos(t):.2f} {30 + 20 * math.sin(t):.2f}' for t in np.linspace(0, math.pi / 2, 8)]
    cmds.append(f'1 w {pts[0]} m ' + ' '.join(f'{p} l' for p in pts[1:]) + ' S')
    # 4. Sharp cusp (< 45 degrees)
    cmds.append(b'1 w 10 40 m 20 60 l 12 40 l S')
    # 5. Open loop (horseshoe)
    loop_open = [f'{35 + 10 * math.cos(t):.2f} {50 + 10 * math.sin(t):.2f}' for t in np.linspace(0.2, math.pi * 1.8, 12)]
    cmds.append(f'1 w {loop_open[0]} m ' + ' '.join(f'{p} l' for p in loop_open[1:]) + ' S')
    # 6. Closed loop
    cmds.append(b'1 w 60 40 m 75 40 l 75 55 l 60 55 l h S')
    # 7. Self-crossing figure-8
    cmds.append(b'1 w 90 40 m 110 60 l 90 60 l 110 40 l S')
    # 8. Narrow gap (two parallel strokes 0.8 pt apart)
    cmds.append(b'0.5 w 10 75 m 40 75 l S 0.5 w 10 77 m 40 77 l S')
    # 9. Touching strokes (share contact point at 60, 75)
    cmds.append(b'1 w 50 75 m 60 75 l S 1 w 60 75 m 70 85 l S')
    # 10. Short pen lift (gap of 0.6 pt)
    cmds.append(b'1 w 80 75 m 95 75 l S 1 w 96 75 m 110 75 l S')
    # 11. Zero-length dot
    cmds.append(b'q 1 J 2 w 10 95 m 10 95 l S Q')
    # 12. Repeated points
    cmds.append(b'1 w 25 95 m 35 95 l 35 95 l 45 95 l S')
    # 13. Width ladder spanning supported range: 0.5, 1.0, 2.0, 4.0 pt
    cmds.append(b'0.5 w 60 95 m 70 95 l S')
    cmds.append(b'1.0 w 75 95 m 85 95 l S')
    cmds.append(b'2.0 w 90 95 m 100 95 l S')
    cmds.append(b'4.0 w 105 95 m 115 95 l S')
    return b'\n'.join(c.encode() if isinstance(c, str) else c for c in cmds)


def make_symbolic_controls_stream(scale_factor=1.0, y_offset=0.0):
    """Generate text-like and mathematical shapes at labeled physical sizes."""
    cmds = []
    # Helper to format coords with scale and offset
    def pt(x, y):
        return f'{x * scale_factor:.2f} {y * scale_factor + y_offset:.2f}'

    w = f'{0.8 * scale_factor:.2f} w'

    # Group 1: e vs c
    # e: loop on top + open bottom curve
    cmds.append(f'{w} {pt(10, 15)} m {pt(18, 15)} l {pt(18, 20)} l {pt(10, 20)} l {pt(10, 10)} l {pt(18, 10)} l S')
    # c: open curve without top loop
    cmds.append(f'{w} {pt(25, 20)} m {pt(20, 20)} l {pt(20, 10)} l {pt(25, 10)} l S')

    # Group 2: o vs c
    # o: closed box/oval
    cmds.append(f'{w} {pt(32, 10)} m {pt(38, 10)} l {pt(38, 20)} l {pt(32, 20)} l h S')

    # Group 3: 1 vs l
    # 1: stem with top serif
    cmds.append(f'{w} {pt(43, 17)} m {pt(46, 20)} l {pt(46, 10)} l S')
    # l: vertical stroke
    cmds.append(f'{w} {pt(52, 20)} m {pt(52, 10)} l S')

    # Group 4: rn vs m
    # rn: r arch + n arch with 0.8pt gap
    cmds.append(f'{w} {pt(58, 10)} m {pt(58, 18)} l {pt(62, 18)} l S {pt(64, 10)} m {pt(64, 18)} l {pt(68, 18)} l {pt(68, 10)} l S')
    # m: continuous three-legged arch
    cmds.append(f'{w} {pt(74, 10)} m {pt(74, 18)} l {pt(78, 18)} l {pt(78, 10)} l {pt(82, 18)} l {pt(82, 10)} l S')

    # Group 5: Decimal point in number "3.14"
    cmds.append(f'{w} {pt(90, 18)} m {pt(95, 18)} l {pt(95, 15)} l {pt(91, 15)} l {pt(95, 15)} l {pt(95, 10)} l {pt(90, 10)} l S')
    # Decimal point (dot)
    cmds.append(f'q 1 J {1.2 * scale_factor:.2f} w {pt(97, 10.5)} m {pt(97, 10.5)} l S Q')
    cmds.append(f'{w} {pt(100, 20)} m {pt(100, 10)} l S')

    # Group 6: Fraction bar in "1/2"
    cmds.append(f'{w} {pt(110, 22)} m {pt(110, 16)} l S')
    cmds.append(f'{w} {pt(106, 14)} m {pt(114, 14)} l S')  # horizontal fraction bar
    cmds.append(f'{w} {pt(108, 12)} m {pt(112, 12)} l {pt(108, 6)} l {pt(112, 6)} l S')

    # Group 7: Prime mark f'(x)
    cmds.append(f'{w} {pt(10, 35)} m {pt(10, 45)} l {pt(14, 45)} l S')
    cmds.append(f'{w} {pt(13, 47)} m {pt(12, 43)} l S')  # prime tick

    # Group 8: Accent (circumflex)
    cmds.append(f'{w} {pt(22, 35)} m {pt(26, 42)} l S {pt(26, 35)} m {pt(22, 42)} l S')
    cmds.append(f'{w} {pt(22, 44)} m {pt(24, 47)} l {pt(26, 44)} l S')  # hat

    # Group 9: Subscript vs Superscript
    cmds.append(f'{w} {pt(35, 35)} m {pt(40, 45)} l S {pt(40, 35)} m {pt(35, 45)} l S')
    cmds.append(f'{w} {pt(42, 35)} m {pt(45, 35)} l {pt(42, 32)} l {pt(45, 32)} l S')  # subscript 2
    cmds.append(f'{w} {pt(52, 35)} m {pt(57, 45)} l S {pt(57, 35)} m {pt(52, 45)} l S')
    cmds.append(f'{w} {pt(59, 48)} m {pt(62, 48)} l {pt(59, 45)} l {pt(62, 45)} l S')  # superscript 2

    # Group 10: Minus vs Equals
    cmds.append(f'{w} {pt(70, 40)} m {pt(76, 40)} l S')  # minus
    cmds.append(f'{w} {pt(82, 42)} m {pt(88, 42)} l S {pt(82, 38)} m {pt(88, 38)} l S')  # equals

    # Group 11: Arrow ->
    cmds.append(f'{w} {pt(95, 40)} m {pt(105, 40)} l S {pt(102, 43)} m {pt(105, 40)} l {pt(102, 37)} l S')

    # Group 12: Looped letter "a"
    cmds.append(f'{w} {pt(112, 35)} m {pt(118, 35)} l {pt(118, 42)} l {pt(112, 42)} l h S {pt(118, 43)} m {pt(118, 33)} l S')

    return '\n'.join(cmds).encode()


def build_calibration_fixtures(directory):
    """Generate reference calibration PDF and labeled damaged test fixtures."""
    dir_path = Path(directory)
    dir_path.mkdir(parents=True, exist_ok=True)
    if any(dir_path.iterdir()):
        raise FileExistsError('Choose an empty output folder for calibration fixtures.')

    def save(name, *pages):
        path = dir_path / (name + '.pdf')
        with path.open('xb') as output, PdfWriter() as writer:
            for data in pages:
                page = writer.add_blank_page(width=150, height=150)
                page[NameObject('/Contents')] = stream(writer, data)
            writer.write(output)
        return path

    small = make_symbolic_controls_stream(scale_factor=0.7).decode()
    middle = make_symbolic_controls_stream(y_offset=35).decode()
    large = make_symbolic_controls_stream(scale_factor=1.3, y_offset=85).decode()

    def layout(symbols):
        return '\n'.join(f'q\n{group}\nQ' for group in (small, symbols, large)).encode()

    ref_path = save('calibration_reference', make_geometric_controls_stream(), layout(middle),
                    b'q 1.2 0 0 1.2 10 10 cm 1 w 10 10 m 50 50 l S Q')

    edits = {
        'deleted_decimal': ('q 1 J 1.20 w 97.00 45.50 m 97.00 45.50 l S Q', ''),
        'deleted_fraction_bar': ('0.80 w 106.00 49.00 m 114.00 49.00 l S', ''),
        'opened_loop': ('32.00 55.00 l h S', '32.00 55.00 l S'),
        'closed_loop': ('25.00 45.00 l S', '25.00 45.00 l h S'),
        'joined_letters': ('62.00 53.00 l S 64.00 45.00 m', '62.00 53.00 l 64.00 53.00 l'),
        'broken_stroke': ('52.00 55.00 m 52.00 45.00 l S',
                          '52.00 55.00 m 52.00 51.00 l S 52.00 49.00 m 52.00 45.00 l S'),
        'moved_superscript': ('59.00 83.00 m 62.00 83.00 l 59.00 80.00 l 62.00 80.00 l',
                              '59.00 82.50 m 62.00 82.50 l 59.00 79.50 l 62.00 79.50 l'),
        'thinned_ink': ('0.80 w 52.00 55.00 m', '0.35 w 52.00 55.00 m'),
        'thickened_ink': ('0.80 w 52.00 55.00 m', '1.50 w 52.00 55.00 m'),
        'faded_color': ('0.80 w 52.00 55.00 m 52.00 45.00 l S', 'q 0.50 G 0.80 w 52.00 55.00 m 52.00 45.00 l S Q'),
    }
    damaged = {}
    for label, (original, replacement) in edits.items():
        if middle.count(original) != 1:
            raise ValueError(f'Calibration edit must match one shape: {label}')
        changed = middle.replace(original, replacement, 1)
        damaged[label] = save('damaged_' + label, layout(changed))
    damaged['unsupported_shear'] = save('unsupported_shear', b'q 1 0.5 0 1 10 10 cm 1 w 10 10 m 50 50 l S Q')
    return ref_path, damaged


def evaluate_calibration_preservation(source_rendered, candidate_rendered, page_mm,
                                     tolerance_mm=0.01, min_stroke_width_mm=None, min_gap_mm=None):
    """Evaluate calibrated preservation on rendered images per P16-C steps 4-6."""
    bounds = [0.10 * value for value in (min_stroke_width_mm, min_gap_mm) if value is not None]
    effective_tolerance = min([tolerance_mm, *bounds])
    metrics = image_metrics(source_rendered, candidate_rendered, page_mm)
    if metrics.get('exact', False):
        return {'verdict': 'pass', 'reason': 'Exact pixel match; zero deviation', 'defect': None,
                'effective_tolerance_mm': effective_tolerance, 'metrics': metrics}
    if effective_tolerance == 0:
        return {'verdict': 'rejected', 'reason': 'Zero tolerance requires exact pixels',
                'defect': 'pixel_difference', 'effective_tolerance_mm': effective_tolerance, 'metrics': metrics}

    th_225 = metrics.get('thresholds', {}).get('225', {})
    if not th_225:
        return {'verdict': 'rejected', 'reason': 'Image analysis threshold 225 unavailable', 'defect': 'metric_error'}

    missing_components = th_225.get('potential_missing_components', [])
    added_components = th_225.get('potential_added_components', [])
    merges = th_225.get('potential_breaks_merges', [])
    topo_before = th_225.get('topology_before', {})
    topo_after = th_225.get('topology_after', {})
    disp = th_225.get('stroke_displacement_mm', {})
    thick_change = th_225.get('thickness_absolute_change_mm', {})
    rgb_diff = metrics['rgb_foreground_absolute_difference']

    # Check 1: Missing components (deleted decimal, fraction bar, dot)
    if missing_components:
        return {
            'verdict': 'rejected',
            'reason': f'Missing component detected ({len(missing_components)} component(s) lost)',
            'defect': 'missing_component',
            'effective_tolerance_mm': effective_tolerance,
            'metrics': metrics,
        }

    # Check 2: Added components
    if added_components:
        return {
            'verdict': 'rejected',
            'reason': f'Added component detected ({len(added_components)} component(s) added)',
            'defect': 'added_component',
            'effective_tolerance_mm': effective_tolerance,
            'metrics': metrics,
        }

    # Check 3: Breaks and merges (joined letters or broken strokes)
    if merges:
        kinds = set(m.get('kind', '') for m in merges)
        return {
            'verdict': 'rejected',
            'reason': f'Break or merge detected ({", ".join(sorted(kinds))})',
            'defect': 'break_or_merge',
            'effective_tolerance_mm': effective_tolerance,
            'metrics': metrics,
        }

    # Check 4: Topology holes / loops (opened loop or closed loop)
    if topo_before.get('holes', 0) != topo_after.get('holes', 0):
        return {
            'verdict': 'rejected',
            'reason': f'Loop topology altered (holes: {topo_before.get("holes")} vs {topo_after.get("holes")})',
            'defect': 'loop_topology',
            'effective_tolerance_mm': effective_tolerance,
            'metrics': metrics,
        }

    max_disp = disp.get('max')
    max_thick = thick_change.get('max')
    displaced = max_disp is not None and max_disp > effective_tolerance
    # A width change across the region can also shift raster skeleton endpoints.
    typical_width_change = thick_change.get('median') or 0
    if max_thick is not None and max_thick > effective_tolerance and (
            not displaced or typical_width_change > effective_tolerance):
        return {
            'verdict': 'rejected',
            'reason': f'Thickness change {max_thick:.4f} mm exceeds tolerance {effective_tolerance:.4f} mm',
            'defect': 'thickness_change',
            'effective_tolerance_mm': effective_tolerance,
            'metrics': metrics,
        }
    if displaced:
        return {
            'verdict': 'rejected',
            'reason': f'Stroke displacement {max_disp:.4f} mm exceeds tolerance {effective_tolerance:.4f} mm',
            'defect': 'displacement',
            'effective_tolerance_mm': effective_tolerance,
            'metrics': metrics,
        }

    # Check 7: Color or contrast fading
    max_rgb_delta = rgb_diff['max']
    if max_rgb_delta is not None and max_rgb_delta > 30:
        return {
            'verdict': 'rejected',
            'reason': f'RGB difference {max_rgb_delta:.1f} exceeds threshold 30 (faded or color-altered ink)',
            'defect': 'color_fade',
            'effective_tolerance_mm': effective_tolerance,
            'metrics': metrics,
        }

    return {
        'verdict': 'pass',
        'reason': 'All calibrated preservation checks passed within effective tolerance',
        'defect': None,
        'effective_tolerance_mm': effective_tolerance,
        'metrics': metrics,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generate-fixtures', type=Path, default=None, help='Directory to generate fixtures')
    args = parser.parse_args()

    if args.generate_fixtures:
        ref, damaged = build_calibration_fixtures(args.generate_fixtures)
        print(f'Generated reference fixture: {ref}')
        print(f'Generated {len(damaged)} damaged control fixtures in {args.generate_fixtures}')


if __name__ == '__main__':
    main()
