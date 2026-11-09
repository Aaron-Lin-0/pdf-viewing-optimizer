"""Tests for P16-C deterministic calibration controls and small-writing verifier."""
import math
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools')]
from calibration_controls import (build_calibration_fixtures,
                                  check_transform_support,
                                  evaluate_calibration_preservation,
                                  render_page)


class CalibrationControlsTests(unittest.TestCase):
    def test_transform_support_detection(self):
        # Uniform scaling and translation: supported
        ok, _ = check_transform_support((1.5, 0, 0, 1.5, 10, 20))
        self.assertTrue(ok)

        # 45-degree rotation: supported
        s = 0.70710678
        ok, _ = check_transform_support((s, s, -s, s, 0, 0))
        self.assertTrue(ok)

        # Shear matrix: unsupported
        ok, msg = check_transform_support((1.0, 0.5, 0.0, 1.0, 0, 0))
        self.assertFalse(ok)
        self.assertIn('Shear', msg)
        ok, msg = check_transform_support((.01, .005, 0, math.hypot(.01, .005), 0, 0))
        self.assertFalse(ok)
        self.assertIn('Shear', msg)

        # Non-uniform scale: unsupported
        ok, msg = check_transform_support((2.0, 0, 0, 1.0, 0, 0))
        self.assertFalse(ok)
        self.assertIn('Non-uniform', msg)

    def test_fixtures_generation_and_exact_pass(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            ref_path, damaged = build_calibration_fixtures(folder)
            self.assertTrue(ref_path.is_file())
            self.assertGreaterEqual(len(damaged), 10)

            # Render reference page 1 (geometry) and verify self-comparison passes
            img_ref = render_page(ref_path, page_index=0, scale=4)
            page_mm = (150 * 25.4 / 72, 150 * 25.4 / 72)
            result = evaluate_calibration_preservation(img_ref, img_ref, page_mm)
            self.assertEqual(result['verdict'], 'pass')
            self.assertTrue(result['metrics']['exact'])

    def test_rejection_of_labeled_damaged_controls(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            ref_path, damaged = build_calibration_fixtures(folder)
            reference = render_page(ref_path, page_index=1, scale=4)
            expected = {
                'deleted_decimal': 'missing_component',
                'deleted_fraction_bar': 'missing_component',
                'opened_loop': 'loop_topology',
                'closed_loop': 'loop_topology',
                'joined_letters': 'break_or_merge',
                'broken_stroke': 'break_or_merge',
                'moved_superscript': 'displacement',
                'thinned_ink': 'thickness_change',
                'thickened_ink': 'thickness_change',
                'faded_color': 'color_fade',
            }
            for label, defect in expected.items():
                with self.subTest(label=label):
                    before = reference
                    after = render_page(damaged[label], scale=4)
                    size_pt = 150
                    if defect == 'thickness_change':
                        # Measure the edited stroke locally, clear of adjacent glyphs and caps.
                        crop = (48 * 4, (150 - 54) * 4, 56 * 4, (150 - 46) * 4)
                        before, after = before.crop(crop), after.crop(crop)
                        size_pt = 8
                    result = evaluate_calibration_preservation(before, after, (size_pt * 25.4 / 72,) * 2,
                                                               min_stroke_width_mm=0.28, min_gap_mm=0.28)
                    self.assertEqual(result['verdict'], 'rejected', result['reason'])
                    self.assertEqual(result['defect'], defect, result['reason'])

    def test_existing_fixture_is_never_replaced(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            existing = Path(folder) / 'calibration_reference.pdf'
            existing.write_bytes(b'existing fixture')
            with self.assertRaises(FileExistsError):
                build_calibration_fixtures(folder)
            self.assertEqual(existing.read_bytes(), b'existing fixture')
            self.assertEqual(list(Path(folder).iterdir()), [existing])

    def test_zero_tolerance_and_color_checks(self):
        before = Image.new('RGB', (50, 50), 'white')
        after = before.copy()
        ImageDraw.Draw(before).line((5, 10, 35, 10), fill=(255, 0, 0), width=2)
        ImageDraw.Draw(after).line((5, 10, 35, 10), fill=(0, 130, 0), width=2)
        for tolerance, defect in ((0, 'pixel_difference'), (0.01, 'color_fade')):
            with self.subTest(tolerance=tolerance):
                result = evaluate_calibration_preservation(before, after, (5, 5), tolerance_mm=tolerance)
                self.assertEqual(result['verdict'], 'rejected')
                self.assertEqual(result['defect'], defect)

    def test_tolerance_ladder_capping_rule(self):
        # Verify tolerance capping rule: 10% of local minimum stroke width and relevant gap
        page_mm = (50, 50)
        blank = Image.new('RGB', (100, 100), (255, 255, 255))
        res = evaluate_calibration_preservation(
            blank, blank, page_mm,
            tolerance_mm=0.05,
            min_stroke_width_mm=0.20,  # 10% is 0.02mm
            min_gap_mm=0.15,           # 10% is 0.015mm
        )
        self.assertEqual(res['verdict'], 'pass')
        self.assertTrue(res['metrics']['exact'])
        self.assertAlmostEqual(res['effective_tolerance_mm'], 0.015)


if __name__ == '__main__':
    unittest.main()
