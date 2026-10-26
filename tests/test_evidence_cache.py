import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tools')]
from evidence_cache import EvidenceCache


def context(**changes):
    value = {'operation': 'render', 'source_sha256': 'a'*64, 'candidate_sha256': 'b'*64,
             'page': 1, 'crop': None, 'renderer': 'pdfium', 'renderer_version': '1',
             'geometry': [0, 0, 10, 10], 'resolution': {'dpi': 144},
             'background': 'white', 'thresholds': [200, 225, 250], 'metric_version': '1'}
    value.update(changes)
    return value


class EvidenceCacheTests(unittest.TestCase):
    def test_completion_invalidation_corruption_and_bounded_growth(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            cache = EvidenceCache(folder)
            key = context()
            self.assertEqual(len(cache.key(key)), 32)
            self.assertIsNone(cache.load_json(key))
            cache.store_json(key, {'value': 1})
            self.assertEqual(cache.load_json(key), {'value': 1})
            count = len(list(Path(folder).rglob('*')))
            cache.store_json(key, {'value': 2})
            self.assertEqual(cache.load_json(key), {'value': 1})
            self.assertEqual(len(list(Path(folder).rglob('*'))), count)

            for field, changed in [('source_sha256', 'c'*64), ('candidate_sha256', 'd'*64),
                                   ('page', 2), ('crop', [0, 0, 1, 1]),
                                   ('renderer_version', '2'), ('geometry', [0, 0, 20, 20]),
                                   ('resolution', {'dpi': 288}), ('background', 'transparent'),
                                   ('thresholds', [225]), ('metric_version', '2')]:
                self.assertIsNone(cache.load_json(context(**{field: changed})), field)

            incomplete = Path(folder)/cache.key(context(operation='incomplete'))
            incomplete.mkdir(); (incomplete/'payload.json').write_text('{}')
            self.assertIsNone(cache.load_json(context(operation='incomplete')))

            artifact = Path(folder)/'render.png'; artifact.write_bytes(b'rendered-page')
            render_context = context(operation='render-file')
            payload = cache.store_file(render_context, artifact)
            materialized = Path(folder)/'materialized.png'
            self.assertTrue(cache.materialize(render_context, materialized))
            self.assertEqual(materialized.read_bytes(), b'rendered-page')
            payload.unlink()
            self.assertFalse(cache.materialize(render_context, Path(folder)/'evicted.png'))

            complete = Path(folder)/cache.key(key)/'complete.json'
            marker = json.loads(complete.read_text())
            (complete.parent/marker['payload']).write_text('corrupt')
            self.assertIsNone(cache.load_json(key))
            self.assertGreaterEqual(cache.stats['hits'], 2)
            self.assertGreaterEqual(cache.stats['invalid'], 1)


if __name__ == '__main__':
    unittest.main()
