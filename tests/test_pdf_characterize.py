from pathlib import Path
import sys
import tempfile
import unittest

from pypdf import PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject,
                           FloatObject, NameObject, NumberObject, TextStringObject)

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tools')]
from pdf_characterize import characterize


def stream(writer, data):
    value = DecodedStreamObject()
    value.set_data(data)
    return writer._add_object(value)


class CharacterizeTests(unittest.TestCase):
    def test_classifies_every_page_and_repeated_nested_resources(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source = Path(folder)/'fixture.pdf'
            writer = PdfWriter()
            vector = writer.add_blank_page(width=100, height=100)
            vector[NameObject('/Contents')] = stream(writer, b'1 w 10 10 m 90 90 l S')

            mixed = writer.add_blank_page(width=100, height=100)
            image = DecodedStreamObject()
            image.set_data(b'\0\0\0')
            image.update({NameObject('/Type'): NameObject('/XObject'),
                          NameObject('/Subtype'): NameObject('/Image'),
                          NameObject('/Width'): NumberObject(1), NameObject('/Height'): NumberObject(1),
                          NameObject('/ColorSpace'): NameObject('/DeviceRGB'),
                          NameObject('/BitsPerComponent'): NumberObject(8)})
            image_ref = writer._add_object(image)
            form = DecodedStreamObject()
            form.set_data(b'0 0 m 10 10 l S /Im Do')
            form.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Form'),
                         NameObject('/BBox'): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(10), NumberObject(10)]),
                         NameObject('/Resources'): DictionaryObject({NameObject('/XObject'): DictionaryObject({NameObject('/Im'): image_ref})})})
            form_ref = writer._add_object(form)
            mixed[NameObject('/Resources')] = DictionaryObject({NameObject('/XObject'): DictionaryObject({NameObject('/F'): form_ref})})
            mixed[NameObject('/Contents')] = stream(writer, b'q 1 0 0 1 0 0 cm /F Do /F Do Q')
            mixed[NameObject('/Annots')] = ArrayObject([writer._add_object(DictionaryObject({
                NameObject('/Subtype'): NameObject('/Text'),
                NameObject('/Rect'): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(10), NumberObject(10)])}))])

            cyclic = writer.add_blank_page(width=100, height=100)
            cyclic_form = DecodedStreamObject()
            cyclic_form.set_data(b'/Cycle Do')
            cyclic_form.update({NameObject('/Type'): NameObject('/XObject'),
                                NameObject('/Subtype'): NameObject('/Form'),
                                NameObject('/BBox'): ArrayObject([NumberObject(0)] * 4)})
            cyclic_ref = writer._add_object(cyclic_form)
            cyclic_resources = DictionaryObject({NameObject('/XObject'): DictionaryObject({NameObject('/Cycle'): cyclic_ref})})
            cyclic_form[NameObject('/Resources')] = cyclic_resources
            cyclic[NameObject('/Resources')] = cyclic_resources
            cyclic[NameObject('/Contents')] = stream(writer, b'/Cycle Do')
            writer.write(source)

            result = characterize(source)

            self.assertEqual(result['schema'], 2)
            self.assertEqual({key: result['coverage'][key] for key in
                              ('pages_total', 'pages_complete', 'pages_partial')},
                             {'pages_total': 3, 'pages_complete': 2, 'pages_partial': 1})
            self.assertEqual(result['pages'][0]['labels'], ['page_vectors'])
            page = result['pages'][1]
            self.assertEqual(page['labels'], ['form_xobjects', 'annotations', 'images', 'mixed'])
            self.assertEqual(page['annotations']['count'], 1)
            self.assertEqual(page['annotations']['subtypes'], {'/Text': 1})
            self.assertEqual(page['content']['form_calls'], 2)
            self.assertEqual(page['content']['repeated_form_calls'], 1)
            self.assertEqual(page['content']['unique_forms'], 1)
            self.assertEqual(page['content']['image_calls'], 2)
            self.assertEqual(page['content']['unique_images'], 1)
            self.assertEqual(page['content']['form_vector_operators'], 4)
            self.assertEqual(result['pages'][2]['coverage'], 'partial')
            self.assertEqual(result['pages'][2]['issues'], ['Cyclic Form reference'])

    def test_inventories_width_transparency_images_annotations_and_navigation(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source = Path(folder)/'signals.pdf'
            writer = PdfWriter()
            page = writer.add_blank_page(width=144, height=144)

            image = DecodedStreamObject()
            image.set_data(bytes(144 * 72 * 3))
            image.update({NameObject('/Type'): NameObject('/XObject'),
                          NameObject('/Subtype'): NameObject('/Image'),
                          NameObject('/Width'): NumberObject(144), NameObject('/Height'): NumberObject(72),
                          NameObject('/ColorSpace'): NameObject('/DeviceRGB'),
                          NameObject('/BitsPerComponent'): NumberObject(8),
                          NameObject('/Mask'): ArrayObject([NumberObject(0)] * 6)})
            image_ref = writer._add_object(image)
            form = DecodedStreamObject()
            form.set_data(b'/Im#20space Do')
            form.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Form'),
                         NameObject('/BBox'): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(1), NumberObject(1)]),
                         NameObject('/Resources'): DictionaryObject({NameObject('/XObject'): DictionaryObject({NameObject('/Im space'): image_ref})})})
            form_ref = writer._add_object(form)
            fade = writer._add_object(DictionaryObject({NameObject('/ca'): FloatObject(.5)}))
            font = writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                                                        NameObject('/Subtype'): NameObject('/Type1'),
                                                        NameObject('/BaseFont'): NameObject('/Helvetica')}))
            resources = writer._add_object(DictionaryObject({
                NameObject('/XObject'): DictionaryObject({NameObject('/F space'): form_ref}),
                NameObject('/ExtGState'): DictionaryObject({NameObject('/Fade'): fade}),
                NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})}))
            writer._pages.get_object()[NameObject('/Resources')] = resources
            page.pop('/Resources', None)
            page[NameObject('/Contents')] = stream(writer,
                b'q /Fade gs 0 0 100 100 re W n 1 w 10 10 m 20 20 l S 1 w 30 30 m 40 40 l S '
                b'2 w 50 50 m 50 50 l S 0 0 m 10 0 10 10 0 10 c h f '
                b'72 0 0 36 0 0 cm /F#20space Do /F#20space Do Q '
                b'BT /F1 12 Tf 5 5 Td (math = x) Tj ET')

            appearance = DecodedStreamObject()
            appearance.set_data(b'0 0 m 10 10 l S')
            appearance.update({NameObject('/Type'): NameObject('/XObject'),
                               NameObject('/Subtype'): NameObject('/Form'),
                               NameObject('/BBox'): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(10), NumberObject(10)]),
                               NameObject('/Resources'): DictionaryObject()})
            ink = DictionaryObject({NameObject('/Subtype'): NameObject('/Stamp'),
                                    NameObject('/Name'): NameObject('/#ONYX-STROKE'),
                                    NameObject('/F'): NumberObject(4),
                                    NameObject('/Rect'): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(10), NumberObject(10)]),
                                    NameObject('/AP'): DictionaryObject({NameObject('/N'): writer._add_object(appearance)})})
            link = DictionaryObject({NameObject('/Subtype'): NameObject('/Link'),
                                     NameObject('/Rect'): ArrayObject([NumberObject(0)] * 4),
                                     NameObject('/Border'): ArrayObject([NumberObject(0)] * 3),
                                     NameObject('/Dest'): ArrayObject([page.indirect_reference, NameObject('/Fit')])})
            comment = DictionaryObject({NameObject('/Subtype'): NameObject('/Text'),
                                        NameObject('/Rect'): ArrayObject([NumberObject(0)] * 4),
                                        NameObject('/Contents'): TextStringObject('protected')})
            image_stamp = DictionaryObject({NameObject('/Subtype'): NameObject('/Stamp'),
                                            NameObject('/Name'): NameObject('/#ONYX-IMAGE'),
                                            NameObject('/Rect'): ArrayObject([NumberObject(0)] * 4)})
            page[NameObject('/Annots')] = ArrayObject([writer._add_object(link), writer._add_object(ink),
                                                       writer._add_object(comment), writer._add_object(image_stamp)])

            layer = writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/OCG'),
                                                         NameObject('/Name'): TextStringObject('Layer')}))
            writer.root_object[NameObject('/OCProperties')] = DictionaryObject({NameObject('/OCGs'): ArrayObject([layer])})
            writer.root_object[NameObject('/AcroForm')] = DictionaryObject({NameObject('/Fields'): ArrayObject()})
            writer.root_object[NameObject('/StructTreeRoot')] = DictionaryObject()
            writer.root_object[NameObject('/OpenAction')] = ArrayObject([page.indirect_reference, NameObject('/Fit')])
            writer.add_outline_item('Signals', 0)
            writer.add_attachment('fixture.txt', b'kept')
            writer.set_page_label(0, 0, prefix='Signal ')
            writer.write(source)

            result = characterize(source)
            page_result = result['pages'][0]

            self.assertEqual(page_result['resource_scope'], 'inherited')
            self.assertEqual(page_result['content']['simple_stroke_segments'], 3)
            self.assertEqual(page_result['content']['same_width_runs'], 1)
            self.assertEqual(page_result['content']['zero_length_segments'], 1)
            self.assertEqual(page_result['content']['filled_outlines'], 1)
            self.assertEqual(page_result['content']['clips'], 1)
            self.assertEqual(page_result['content']['transparency_state_uses'], 1)
            self.assertEqual(page_result['content']['form_calls'], 2)
            self.assertEqual(page_result['content']['image_calls'], 2)
            self.assertEqual(page_result['images']['unique'], 1)
            self.assertAlmostEqual(page_result['images']['definitions'][0]['effective_dpi']['minimum'], 144)
            self.assertTrue(page_result['images']['definitions'][0]['mask'])
            self.assertEqual(sorted(page_result['line_widths']['declared']), ['1', '2'])
            self.assertIn('/F space', page_result['escaped_resource_names'])
            self.assertIn('/Im space', page_result['escaped_resource_names'])
            self.assertEqual(page_result['annotations']['eligible_individual'], 1)
            self.assertEqual(page_result['annotations']['eligible_leading_prefix'], 1)
            self.assertTrue(page_result['annotations']['conversion_barrier'])
            self.assertEqual(page_result['annotations']['protected_or_interactive'], 1)
            self.assertEqual(page_result['annotations']['image_stamps'], 1)
            self.assertEqual(result['document']['bookmarks']['resolved_destinations'], 1)
            self.assertEqual(result['document']['attachments'][0]['files'][0]['bytes'], 4)
            self.assertEqual(result['document']['layers']['groups'], 1)
            self.assertTrue(result['document']['structural_tags']['present'])
            self.assertEqual(result['document']['links']['count'], 1)
            self.assertTrue(result['document']['forms']['present'])
            self.assertTrue(result['document']['actions']['catalog_open_action'])
            self.assertEqual(result['document']['text']['pages_with_text'], 1)
            self.assertGreaterEqual(result['document']['text']['math_like_characters'], 1)


if __name__ == '__main__':
    unittest.main()
