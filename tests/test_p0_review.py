import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tools')]
from review_p0 import checkpoint, load_evidence, timing_summary
from viewing_copy import filehash


def firefox(output, repetition, first, zoom, revisit=10):
    return {'kind': 'firefox', 'output': output, 'repetition': repetition, 'error': None,
            'summary': {
                'first_visit/p1/None': {'median_ms': first, 'resolved': True},
                'zoom/p1/2': {'median_ms': zoom, 'resolved': True},
                'revisit_forward/p1/None': {'median_ms': revisit, 'resolved': revisit > 0},
            }}


class P0ReviewTests(unittest.TestCase):
    def test_requires_hash_matched_artifacts_and_complete_timing_repetitions(self):
        records = []
        for repetition in range(1, 4):
            records += [firefox('source/original', repetition, 100, 50),
                        firefox('repository/inline', repetition, 75, 40, 0)]
        summary = timing_summary(records, ('repository/inline',))
        self.assertEqual(summary['repository/inline']['first_visit']['change_percent'], 25)
        self.assertEqual(summary['repository/inline']['zoom/2']['change_percent'], 20)
        self.assertIsNone(summary['repository/inline']['revisit_forward']['median_total_ms'])

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root = Path(folder)
            source = root/'source.pdf'; source.write_bytes(b'source')
            candidate = root/'candidate.pdf'; candidate.write_bytes(b'candidate')
            (root/'launch.json').write_text(json.dumps({'expected_source_sha256': filehash(source)}))
            outputs = [
                {'kind': 'output', 'output': 'source/original', 'metadata': {
                    'status': 'usable', 'path': str(source), 'output_sha256': filehash(source)}},
                {'kind': 'output', 'output': 'repository/inline', 'metadata': {
                    'status': 'usable', 'path': str(candidate), 'output_sha256': filehash(candidate)}},
            ]
            for number, record in enumerate(outputs, 1):
                (root/f'record-{number:06}.json').write_text(json.dumps(record))
            _, verified = load_evidence(root, ('repository/inline',))
            self.assertEqual(verified['repository/inline']['sha256'], filehash(candidate))
            candidate.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'hash no longer matches'):
                load_evidence(root, ('repository/inline',))

    def test_checkpoint_is_immutable_and_reused(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder)/'operation.json'
            calls = []
            self.assertEqual(checkpoint(path, lambda: calls.append(1) or {'value': 1}), {'value': 1})
            self.assertEqual(checkpoint(path, lambda: calls.append(2) or {'value': 2}), {'value': 1})
            self.assertEqual(calls, [1])

            path.unlink()
            def competing_writer():
                path.write_text(json.dumps({'value': 3}))
                return {'value': 4}
            self.assertEqual(checkpoint(path, competing_writer), {'value': 3})


if __name__ == '__main__':
    unittest.main()
