"""Feature loss must distinguish missing appearance, flattening and uncertainty."""
import importlib.util
from contextlib import nullcontext
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from feature_loss import FUNCTIONAL_FEATURES, image_stamp_loss, summarize_feature_loss
import compare_optimizers as comparison
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject


class FunctionalFeatureTests(unittest.TestCase):
    def test_unknown_checks_never_inflate_preservation(self):
        summary = summarize_feature_loss({'text': {'status': 'pass'},
                                          'attachments': {'status': 'confirmed difference'},
                                          'forms': {'status': 'pass', 'source_presence': 'absent',
                                                    'before_inventory': {'items': 0},
                                                    'after_inventory': {'items': 0}}})
        self.assertEqual(summary['functional_preserved'], ['text'])
        self.assertEqual(summary['functional_absent_in_source'], ['forms'])
        self.assertEqual(summary['functional_confirmed_changes'], ['attachments'])
        self.assertEqual(len(summary['functional_unresolved']), len(FUNCTIONAL_FEATURES)-3)
        self.assertEqual(summary['functional_inventories']['forms']['before_inventory']['items'], 0)

    def test_flattened_image_is_not_a_lost_comment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for filename, stamp in [('before.pdf', True), ('after.pdf', False)]:
                writer = PdfWriter()
                writer.add_blank_page(width=64, height=64)
                if stamp:
                    writer.add_annotation(0, DictionaryObject({
                        NameObject('/Type'): NameObject('/Annot'), NameObject('/Subtype'): NameObject('/Stamp'),
                        NameObject('/Name'): NameObject('/#ONYX-IMAGE'),
                        NameObject('/Rect'): ArrayObject([NumberObject(v) for v in [10, 10, 30, 15]])}))
                with (root/filename).open('xb') as stream:
                    writer.write(stream)
            checks = comparison.independent_checks(root/'before.pdf', root/'after.pdf')
            self.assertEqual(checks['comments']['status'], 'pass')
            self.assertEqual(checks['image_stamp_annotations']['status'], 'unproven')
            self.assertEqual(checks['forms']['source_presence'], 'absent')
            self.assertEqual(checks['forms']['before_inventory']['items'], 0)

    def test_newline_and_allowed_ink_editability_separate(self):
        summary = summarize_feature_loss({'text': {'status': 'unproven', 'before': ['x²'], 'after': ['x²\n']},
            'annotation_editability': {'status': 'confirmed difference', 'before': [['ink1', 'ink2']], 'after': [[]]}})
        self.assertTrue(summary['text_only_added_trailing_newlines'])
        self.assertEqual(summary['ink_annotation_objects_removed'], 2)
        self.assertEqual(summary['functional_confirmed_changes'], [])
        self.assertIn('text', summary['functional_unresolved'])

    def test_both_extractors_see_text_then_empty_output(self):
        from test_comparison import fixture
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture(root/'before.pdf', text='abc')
            fixture(root/'after.pdf', text='')
            source_page = MagicMock()
            source_page.get_text.return_value = 'abc\n\n'
            output_page = MagicMock()
            output_page.get_text.return_value = ''
            with patch('pymupdf.open', side_effect=[nullcontext([source_page]), nullcontext([output_page])]):
                checks = comparison.independent_checks(root/'before.pdf', root/'after.pdf')
            self.assertEqual(checks['text']['status'], 'confirmed difference')


@unittest.skipUnless(importlib.util.find_spec('numpy'), 'Install comparison dependencies')
class VisibleFeatureTests(unittest.TestCase):
    def images(self):
        from PIL import Image, ImageDraw
        before = Image.new('RGB', (64, 64), 'white')
        ImageDraw.Draw(before).rectangle((10, 49, 29, 53), fill='black')
        return before, Image.new('RGB', (64, 64), 'white')

    def test_lost_feature_all_rotations(self):
        from PIL import Image
        before, after = self.images()
        for rotation, transpose in [(0, None), (90, Image.Transpose.ROTATE_270),
                                    (180, Image.Transpose.ROTATE_180), (270, Image.Transpose.ROTATE_90)]:
            a = before if transpose is None else before.transpose(transpose)
            result = image_stamp_loss(a, after, [[10, 10, 30, 15]], [0, 0, 64, 64], rotation)
            self.assertEqual(result['region_counts']['blanked'], 1, result)
            self.assertEqual(result['blanked_region_fraction'], 1)
            self.assertEqual(result['changed_pixels_outside_source_image_regions'], 0)
            if a is not before:
                a.close()
        before.close(); after.close()

    def test_flattened_retained_pixels_are_not_loss(self):
        before, after = self.images()
        result = image_stamp_loss(before, before, [[10, 10, 30, 15]], [0, 0, 64, 64])
        self.assertEqual(result['region_counts']['unchanged'], 1)
        self.assertEqual(result['blanked_region_fraction'], 0)
        self.assertEqual(result['changed_region_fraction'], 0)
        before.close(); after.close()

    def test_fading_is_changed_not_blanked(self):
        from PIL import ImageDraw
        before, after = self.images()
        ImageDraw.Draw(after).rectangle((10, 49, 29, 53), fill=(252, 252, 252))
        result = image_stamp_loss(before, after, [[10, 10, 30, 15]], [0, 0, 64, 64])
        self.assertEqual(result['region_counts']['blanked'], 0)
        self.assertEqual(result['region_counts']['changed'], 1)
        before.close(); after.close()

    def test_no_features_and_geometry_failure_are_not_perfect_scores(self):
        before, after = self.images()
        result = image_stamp_loss(before, after, [], [0, 0, 64, 64])
        self.assertIsNone(result['blanked_region_fraction'])
        self.assertGreater(result['changed_pixels_outside_source_image_regions'], 0)
        self.assertEqual(image_stamp_loss(before, after, [], [0, 0, 0, 0])['status'], 'unproven')
        self.assertEqual(image_stamp_loss(before, after, [[30, 15, 10, 10]], [0, 0, 64, 64])['status'], 'check failed')
        before.close(); after.close()

    def test_general_source_features_and_worst_regions_are_separate_from_stamps(self):
        from comparison_metrics import image_metrics
        before, after = self.images()
        metrics = image_metrics(before, before, (6.4, 6.4))
        appearance = {'pages': [{'pdfium_144': metrics,
                                 'source_vector_features': {'filled_outlines': 2, 'zero_length_segments': 1}}],
                      'regions': [{'id': 'p1-0', 'metrics_144': metrics}]}
        summary = summarize_feature_loss({}, appearance)
        self.assertGreater(summary['general_source_features']['components'], 0)
        self.assertEqual(summary['worst_region_ids'], ['p1-0'])
        self.assertEqual(summary['threshold_sensitive_regions'], 0)
        self.assertEqual(summary['general_source_features']['filled_outlines'], 2)
        self.assertEqual(summary['general_source_features']['zero_length_segments'], 1)
        self.assertNotIn('visual_source_features', summary)
        before.close(); after.close()

    def test_cropped_coordinates_and_empty_original(self):
        before, after = self.images()
        result = image_stamp_loss(before, after, [[15, 17, 35, 22]], [5, 7, 69, 71])
        self.assertEqual(result['region_counts']['blanked'], 1)
        blank = image_stamp_loss(after, after, [[10, 10, 30, 15]], [0, 0, 64, 64])
        self.assertEqual(blank['region_counts']['not visible'], 1)
        self.assertIsNone(blank['blanked_region_fraction'])
        before.close(); after.close()
