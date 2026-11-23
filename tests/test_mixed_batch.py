"""Mixed PDF/BOOX batch identity and publication regressions."""
import json
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace
from pypdf import PdfWriter
from batch_workflow import BatchController, NoteOptions, plan_batch, validate_result, filehash
from tests.test_note_to_pdf import create_test_note


class MixedBatchTests(unittest.TestCase):
    def test_mixed_conversion_and_report_validation(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as td:
            root=Path(td);pdf=root/'same.pdf';note=root/'same.NOTE'
            w=PdfWriter();w.add_blank_page(width=100,height=100);w.write(pdf);w.close()
            create_test_note(note,shapes=[{'pen_type':6,'text_plain':'Hello'}])
            jobs=plan_batch([note,pdf,note],profile='quick',note_options=NoteOptions(mode='fixed',optimize=False))
            self.assertEqual([j.input_kind for j in jobs],['note','pdf'])
            self.assertTrue(all(j.status=='ready' for j in jobs))
            self.assertNotEqual(jobs[0].output.parent,jobs[1].output.parent)
            before=filehash(note)
            result=BatchController([jobs[0]]).run()[0]
            self.assertEqual(result.status,'converted',result.error)
            self.assertEqual(filehash(note),before)
            self.assertIn('Converted only',result.error)
            data=json.loads(result.output.with_suffix('.json').read_text())
            data['requested_options']['keep_bookmarks']=False
            result.output.with_suffix('.json').write_text(json.dumps(data))
            with self.assertRaises(RuntimeError):validate_result(result)

    def test_missing_renderer_does_not_block_pdf(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as td:
            root=Path(td);pdf=root/'a.pdf';note=create_test_note(root/'a.note')
            w=PdfWriter();w.add_blank_page(width=100,height=100);w.write(pdf);w.close()
            jobs=plan_batch([note,pdf],profile='quick',renderer=root/'missing.exe')
            self.assertEqual([j.status for j in jobs],['rejected','ready'])

    def test_review_identity_changes_block_note_child(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as td:
            root=Path(td);note=create_test_note(root/'a.note')
            job=plan_batch([note],note_options=NoteOptions(optimize=False))[0]
            bad=replace(job,renderer_sha256='0'*64)
            self.assertEqual(BatchController([bad]).run()[0].status,'rejected')
            note.write_bytes(b'changed')
            self.assertEqual(BatchController([job]).run()[0].status,'rejected')
            self.assertFalse(job.output.exists())

    def test_note_option_validation(self):
        for options in [NoteOptions(mode='fixed',width='NaNmm'),NoteOptions(mode='scale',scale=0),NoteOptions(mode='bogus')]:
            with self.assertRaises(ValueError):options.validate()

    def test_child_rechecks_reviewed_hashes_after_controller_check(self):
        import shutil
        from note_to_pdf import find_renderer
        from batch_workflow import run_optimizer
        for target in ('source','renderer'):
            with self.subTest(target=target), tempfile.TemporaryDirectory(dir=Path(__file__).parent) as td:
                root=Path(td);note=create_test_note(root/'n.note');binary=root/'renderer.exe'
                shutil.copyfile(find_renderer(),binary)
                job=plan_batch([note],renderer=binary,note_options=NoteOptions(optimize=False))[0]
                self.assertEqual(job.status,'ready',job.error)
                def race(job,emit,started):
                    if target=='source':create_test_note(note,shapes=[{'pen_type':6,'text_plain':'Changed'}])
                    else:
                        with binary.open('ab') as stream:stream.write(b'changed')
                    return run_optimizer(job,emit,started)
                result=BatchController([job],runner=race).run()[0]
                self.assertEqual(result.status,'failed')
                self.assertIn('changed after review',result.error)
                self.assertFalse(job.output.exists())

    def test_note_failure_reason_and_budget_exit_codes(self):
        from contextlib import redirect_stderr
        from io import StringIO
        from unittest.mock import patch
        from note_to_pdf import main
        from pdf_runtime import TimeBudgetExceeded,DiskBudgetExceeded
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as td:
            note=create_test_note(Path(td)/'n.note',extra_files={'toc/pb/bad':b'\x0a\xff'})
            job=plan_batch([note],note_options=NoteOptions(optimize=False))[0]
            result=BatchController([job]).run()[0]
            self.assertEqual(result.status,'failed')
            self.assertIn('Truncated',result.error)
            self.assertFalse(job.output.exists())
        for exception,code in [(TimeBudgetExceeded('time'),2),(DiskBudgetExceeded('disk'),3)]:
            with patch('note_to_pdf.convert_note_to_pdf',side_effect=exception),redirect_stderr(StringIO()):
                self.assertEqual(main(['unused.note','out.pdf']),code)


class MixedGuiTests(unittest.TestCase):
    def test_native_mixed_selection_and_booX_settings(self):
        import time
        from tkinter import ttk
        from unittest.mock import patch
        from viewing_copy_gui import main, NOTE_WIDTH_LABELS
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as td:
            rootpath=Path(td);pdf=rootpath/'a.pdf';note=create_test_note(rootpath/'a.note')
            w=PdfWriter();w.add_blank_page(width=100,height=100);w.write(pdf);w.close()
            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)
            def smoke(root):
                root.withdraw()
                try:
                    widgets=list(descendants(root))
                    choose=next(w for w in widgets if isinstance(w,ttk.Button) and str(w['text']).startswith('Choose files'))
                    deadline=time.monotonic()+4
                    while choose.instate(['disabled']) and time.monotonic()<deadline:root.update();time.sleep(.01)
                    choose.invoke();root.update()
                    panel=next(w for w in widgets if isinstance(w,ttk.LabelFrame) and 'BOOX' in str(w['text']))
                    self.assertTrue(panel.winfo_manager())
                    modes=next(w for w in widgets if isinstance(w,ttk.Combobox) and NOTE_WIDTH_LABELS['fixed'] in w['values'])
                    modes.set(NOTE_WIDTH_LABELS['fixed']);root.update()
                    review=next(w for w in widgets if isinstance(w,ttk.Button) and str(w['text']).startswith('Review and run'))
                    review.invoke()
                    deadline=time.monotonic()+4
                    while not reviewed.called and time.monotonic()<deadline:root.update();time.sleep(.01)
                    self.assertTrue(reviewed.called)
                    self.assertIn('Pen widths: Fixed width 0.10mm',reviewed.call_args.args[1])
                    self.assertIn('experimental',reviewed.call_args.args[1])
                finally:root.destroy()
            with patch('tkinter.Tk.mainloop',smoke),patch('viewing_copy_gui.dependency_diagnostics',return_value=()),patch('tkinter.filedialog.askopenfilenames',return_value=(str(pdf),str(note))),patch('tkinter.messagebox.askyesno',return_value=False),patch('viewing_copy_gui.review_jobs',return_value=False) as reviewed:
                main()
