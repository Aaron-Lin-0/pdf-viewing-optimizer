from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1])]
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

import verification_worker as worker
from viewing_copy import detail_crop, visual_delta


def make_pdf(path, color='1 0 0', size=200):
    writer = PdfWriter()
    page = writer.add_blank_page(width=size, height=size)
    stream = DecodedStreamObject()
    stream.set_data(f'{color} rg 20 30 80 100 re f 0 0 1 rg 120 140 40 20 re f'.encode())
    page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(path)


def strip_delta(a, b, rows, use_halo=True):
    delta = worker.DeltaAccumulator(True)
    for top, bottom in worker.strip_rows(a.width, a.height, a.width * rows):
        low, high = worker.halo(top, bottom, a.height) if use_halo else (top, bottom)
        with a.crop((0, low, a.width, high)) as x, b.crop((0, low, b.width, high)) as y:
            delta.add(x, y, (top - low, bottom - low))
    return delta.result()


class StripVerificationTests(unittest.TestCase):
    def test_edge_band_sees_across_strip_boundaries(self):
        a = Image.new('RGB', (60, 60), 'white')
        for x in range(10, 50):
            for y in (29, 30, 31):
                a.putpixel((x, y), (0, 0, 0))
        edge = a.copy()
        edge.putpixel((20, 28), (230, 230, 230))
        self.assertFalse(visual_delta(a, edge, True)['exact'])
        self.assertEqual(strip_delta(a, edge, 29), visual_delta(a, edge, True))
        with self.assertRaisesRegex(ValueError, 'edge band'):
            strip_delta(a, edge, 29, use_halo=False)
        outside = a.copy()
        outside.putpixel((20, 26), (230, 230, 230))
        for rows in (1, 27, 29, 60):
            with self.assertRaisesRegex(ValueError, 'edge band'):
                strip_delta(a, outside, rows)

    def test_forced_strips_keep_page_verdicts(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source, same, changed = (Path(folder) / name for name in ('a.pdf', 'b.pdf', 'c.pdf'))
            make_pdf(source)
            make_pdf(same)
            make_pdf(changed, color='0 1 0')
            whole_crop = detail_crop(source, [1])
            with patch.object(worker, 'STRIP_PIXELS', 1000):
                self.assertEqual(worker.page_delta(source, same, 0)['exact'], True)
                self.assertEqual(worker.page_delta(source, same, 0, scale=4)['exact'], True)
                with self.assertRaisesRegex(ValueError, 'guard'):
                    worker.page_delta(source, changed, 0, True)
                self.assertEqual(detail_crop(source, [1]), whole_crop)

    def test_cached_source_strips_round_trip(self):
        try:
            with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
                source, same, changed = (Path(folder) / name for name in ('a.pdf', 'b.pdf', 'c.pdf'))
                make_pdf(source)
                make_pdf(same)
                make_pdf(changed, color='0 1 0')
                whole, strips = Path(folder) / 'whole.png', Path(folder) / 'strips.png'
                worker.render_source((str(source), 0, str(whole), 2, None, False))
                self.assertTrue(whole.is_file())
                with patch.object(worker, 'STRIP_PIXELS', 4000):
                    worker.render_source((str(source), 0, str(strips), 2, None, False))
                    self.assertTrue(strips.is_dir())
                    self.assertEqual(len(list(strips.glob('*.png'))), 40)
                    self.assertFalse(list(Path(folder).glob('*.tmp')))
                    for cached in (whole, strips):
                        result = worker.compare_page((str(cached), str(same), 0, False, 2, None, False))
                        self.assertEqual(result, {'page': 1, 'exact': True, 'peak': 0, 'mean': 0})
                        with self.assertRaisesRegex(ValueError, 'guard'):
                            worker.compare_page((str(cached), str(changed), 0, False, 2, None, False))
                for index in (0, 5):
                    sorted(strips.glob('*.png'))[index].unlink()
                    with self.assertRaisesRegex(ValueError, 'incomplete'):
                        worker.compare_page((str(strips), str(same), 0, False, 2, None, False))
                for document in worker._DOCUMENTS.values():document.close()
                worker._DOCUMENTS.clear();worker._PERSISTENT.clear()
        finally:
            for document in worker._DOCUMENTS.values():document.close()
            worker._DOCUMENTS.clear();worker._PERSISTENT.clear()

    def test_page_size_change_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source, larger = Path(folder) / 'a.pdf', Path(folder) / 'b.pdf'
            make_pdf(source)
            make_pdf(larger, size=201)
            with self.assertRaisesRegex(ValueError, 'dimensions changed'):
                worker.page_delta(source, larger, 0)


if __name__ == '__main__':
    unittest.main()
