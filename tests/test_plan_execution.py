import argparse
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfWriter

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tools')]
from pdf_characterize import characterize
from plan_optimization import build_plan
from run_optimization_plan import execute


class PlanExecutionTests(unittest.TestCase):
    def test_failure_isolation_exclusive_output_cleanup_and_original_fallback(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root = Path(folder)
            source = root/'source.pdf'
            writer = PdfWriter(); writer.add_blank_page(width=20, height=20); writer.write(source)
            fake = root/'fake.py'
            fake.write_text(
                "import shutil,sys,time\n"
                "if '--version' in sys.argv: print('fixture-1')\n"
                "elif any(a.startswith('--help') for a in sys.argv): print('--recompress-flate --compression-level --object-streams --stream-data --linearize')\n"
                "elif '--linearize' in sys.argv: time.sleep(30)\n"
                "else: shutil.copyfile(sys.argv[1],sys.argv[-1])\n")
            config = {'tools': {'qpdf': {'command': [sys.executable, str(fake)],
                                         'version': 'fixture-1', 'version_args': ['--version'],
                                         'help_args': ['--help=all']}}, 'imports': []}
            plan = build_plan(characterize(source), config)
            linear = next(item for item in plan['candidates'] if item['id'] == 'qpdf/flate-linearized')
            linear.update(decision='run', reason='failure isolation fixture')
            plan['selected'].append(linear['id'])
            plan_path = root/'plan.json'; plan_path.write_text(json.dumps(plan))
            config_path = root/'config.json'; config_path.write_text(json.dumps(config))
            output = root/'run'
            args = argparse.Namespace(pdf=source, plan=plan_path, output=output,
                tools_config=config_path, timeout=5, verification_timeout=30,
                max_output_bytes=1_000_000)

            result = execute(args)

            self.assertEqual(result['status'], 'complete')
            self.assertEqual(result['fallback'], 'source.pdf')
            self.assertTrue(result['source_integrity'])
            self.assertEqual(result['candidates'][0]['status'], 'function-pass')
            self.assertEqual(result['candidates'][1]['status'], 'timeout')
            self.assertFalse(any(output.rglob('input.pdf')))
            self.assertFalse((output/'qpdf-flate-linearized'/'output.pdf').exists())
            self.assertTrue((output/'result.json').is_file())
            self.assertTrue((output/'report.md').is_file())
            with self.assertRaisesRegex(FileExistsError, 'must not exist'):
                execute(args)

            plan['selected'] = ['qpdf/flate']
            budget_plan = root/'budget-plan.json'; budget_plan.write_text(json.dumps(plan))
            budget_args = argparse.Namespace(pdf=source, plan=budget_plan, output=root/'budget-run',
                tools_config=config_path, timeout=30, verification_timeout=30, max_output_bytes=1)
            budget = execute(budget_args)
            self.assertEqual(budget['candidates'][0]['status'], 'budget-exhausted')
            self.assertIn('output-size budget', budget['candidates'][0]['reason'])
            self.assertFalse((root/'budget-run'/'qpdf-flate'/'output.pdf').exists())

            interrupt_args = argparse.Namespace(pdf=source, plan=budget_plan, output=root/'interrupt-run',
                tools_config=config_path, timeout=30, verification_timeout=30,
                max_output_bytes=1_000_000, resume=False)
            with patch('run_optimization_plan.run_process', side_effect=KeyboardInterrupt), \
                 self.assertRaises(KeyboardInterrupt):
                execute(interrupt_args)
            self.assertFalse((root/'interrupt-run'/'result.json').exists())
            self.assertFalse(any((root/'interrupt-run').rglob('input.pdf')))
            interrupt_args.resume = True
            resumed = execute(interrupt_args)
            self.assertEqual(resumed['candidates'][0]['status'], 'function-pass')
            entry_count = len(list((root/'interrupt-run'/'operations').rglob('*')))
            self.assertEqual(execute(interrupt_args)['status'], 'complete')
            self.assertEqual(len(list((root/'interrupt-run'/'operations').rglob('*'))), entry_count)
            interrupt_args.timeout = 31
            with self.assertRaisesRegex(ValueError, 'Resume context'):
                execute(interrupt_args)

            fake.write_text(fake.read_text() + '# changed\n')
            tamper_args = argparse.Namespace(pdf=source, plan=budget_plan, output=root/'tamper-run',
                tools_config=config_path, timeout=30, verification_timeout=30,
                max_output_bytes=1_000_000)
            tampered = execute(tamper_args)
            self.assertIn('tool hash changed', tampered['candidates'][0]['reason'])

            source.write_bytes(source.read_bytes() + b'changed')
            source_args = argparse.Namespace(pdf=source, plan=budget_plan, output=root/'source-run',
                tools_config=config_path, timeout=30, verification_timeout=30,
                max_output_bytes=1_000_000)
            with self.assertRaisesRegex(ValueError, 'does not match'):
                execute(source_args)


if __name__ == '__main__':
    unittest.main()
