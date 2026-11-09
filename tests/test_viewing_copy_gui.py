import json
import os
import subprocess
from datetime import date
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from dataclasses import replace

from pypdf import PdfWriter
from viewing_copy_gui import collect_pdf_paths,progress_text

from batch_workflow import (BatchController, JobAlreadyRunning, JobBudgetExceeded, JobTimeout,
                            OPTIMIZER_LABELS,
                            _claim_job, _process_alive, _release_claim, _save_job, confirmation_text,
                            dependency_diagnostics, dependency_report, filehash, plan_batch,
                            resource_summary, run_optimizer)


def make_pdf(path, encrypted=False):
    writer = PdfWriter()
    writer.add_blank_page(width=60, height=60)
    if encrypted:
        writer.encrypt("secret")
    writer.write(path)


class WindowsPathTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows path limit')
    def test_long_source_name_has_collision_safe_resumable_output(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root = Path(folder)
            source = root / ('x' * (235 - len(str(root)) - len('.pdf')) + '.pdf')
            self.assertEqual(len(str(source)), 236)
            make_pdf(source)
            first = plan_batch([source], profile='quick')[0]
            self.assertEqual(first.status, 'ready', first.error)
            self.assertEqual(first.output.parent, root)
            first.output.touch()
            second = plan_batch([source], profile='quick')[0]
            self.assertEqual(second.status, 'ready', second.error)
            self.assertNotEqual(first.output, second.output)
            _save_job(replace(second, status='timeout'))
            self.assertEqual(plan_batch([source], profile='quick')[0].output, second.output)

    @unittest.skipUnless(os.name == 'nt', 'Windows path limit')
    def test_deep_folder_is_rejected_in_review(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root = Path(folder) / ('x' * 150)
            root.mkdir()
            source = root / 'notes.pdf'
            make_pdf(source)
            job = plan_batch([source], profile='quick')[0]
            self.assertEqual(job.status, 'rejected')
            self.assertIn('shorter folder path', job.error)


def complete(job, status="no improvement", **changes):
    report = {"status": status, "source": str(job.source),
              "source_sha256": job.source_sha256}
    if status == "optimized":
        job.output.write_bytes(b"verified copy")
        report.update(output=str(job.output), output_sha256=filehash(job.output))
    report.update(changes)
    job.output.with_suffix(".json").write_text(json.dumps(report), encoding="utf-8")
    job.output.with_suffix(".md").write_text("report", encoding="utf-8")


class ViewingCopyGuiTests(unittest.TestCase):
    def test_release_can_inspect_pdf_without_checkout_imports(self):
        from tools.build_release import build

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root = Path(folder).resolve()
            release, extracted, source = (root / name for name in ('release', 'extracted', 'input.pdf'))
            make_pdf(source)
            build(release)
            with zipfile.ZipFile(release / 'PDF Optimizer.zip') as archive:
                self.assertNotIn('docs/antigravity-local-handoff.md', archive.namelist())
                archive.extractall(extracted)
            result = subprocess.run(
                [sys.executable, '-I', '-c',
                 'import sys; sys.path.insert(0, "."); import viewing_copy_gui; '
                 'from experiment_manifest import analyze_drawing_opportunity; '
                 'print(analyze_drawing_opportunity(sys.argv[1])["denominator_tracking"]["total_pages"])',
                 str(source)],
                cwd=extracted, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), '1')

    def test_native_mode_controls_follow_profile_and_preflight(self):
        import tkinter as tk
        from tkinter import ttk
        from unittest.mock import patch
        from viewing_copy_gui import main
        from batch_workflow import PROFILE_LABELS
        def smoke(root):
            root.withdraw()
            try:
                def wait_for(predicate):
                    deadline=time.monotonic()+2
                    while not predicate() and time.monotonic()<deadline:
                        root.update();time.sleep(.01)
                    self.assertTrue(predicate())
                root.update()
                def descendants(widget):
                    for child in widget.winfo_children():
                        yield child;yield from descendants(child)
                combos=[child for child in descendants(root) if isinstance(child,ttk.Combobox)]
                profile=next(child for child in combos if child.get()==PROFILE_LABELS['quick'])
                optimizer=next(child for child in combos if child is not profile)
                worker=next(child for child in descendants(root) if isinstance(child,ttk.Spinbox))
                self.assertTrue(optimizer.instate(['disabled']));self.assertTrue(worker.instate(['disabled']))
                wait_for(lambda:profile.instate(['!disabled']))
                profile.set(PROFILE_LABELS['thorough'])
                wait_for(lambda:optimizer.instate(['!disabled']) and worker.instate(['!disabled']))
                profile.set(PROFILE_LABELS['quick'])
                wait_for(lambda:optimizer.instate(['disabled']) and worker.instate(['disabled']))
            finally:root.tk.call(root.protocol('WM_DELETE_WINDOW'))
        with patch.object(tk.Tk,'mainloop',smoke),\
             patch('viewing_copy_gui.dependency_diagnostics',return_value=()) as setup:
            main()
        self.assertEqual([call.args[0] for call in setup.call_args_list],['quick','thorough','quick'])

    def test_settings_remain_editable_after_selection_and_back_from_review(self):
        import tkinter as tk
        from tkinter import ttk
        from unittest.mock import patch
        from viewing_copy_gui import PROFILE_LABELS, main
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source=Path(folder)/'source.pdf';make_pdf(source);gate=threading.Event();ticks=[]
            def blocked(*args,**kwargs):
                gate.wait(2);return plan_batch(*args,**kwargs)
            def smoke(root):
                root.withdraw()
                def descendants(widget):
                    for child in widget.winfo_children():
                        yield child;yield from descendants(child)
                def wait_for(predicate):
                    deadline=time.monotonic()+2
                    while not predicate() and time.monotonic()<deadline:
                        root.update();time.sleep(.01)
                    self.assertTrue(predicate())
                widgets=list(descendants(root))
                buttons={str(child['text']).rstrip('…'):child for child in widgets if isinstance(child,ttk.Button)}
                choose, review_button, setup_button = (buttons[label] for label in ('Choose PDFs','Review batch','Check setup'))
                profile,optimizer=[child for child in widgets if isinstance(child,ttk.Combobox)]
                worker=next(child for child in widgets if isinstance(child,ttk.Spinbox))
                parallel=next(child for child in widgets if isinstance(child,ttk.Checkbutton))
                try:
                    wait_for(lambda:choose.instate(['!disabled']))
                    choose.invoke()
                    planner.assert_not_called()
                    self.assertTrue(review_button.instate(['!disabled']))
                    profile.set(PROFILE_LABELS['thorough'])
                    wait_for(lambda:review_button.instate(['!disabled']))
                    root.setvar(worker['textvariable'], 3)
                    parallel.invoke()
                    review_button.invoke()
                    root.after(10,lambda:ticks.append(True))
                    wait_for(lambda:bool(ticks))
                    self.assertTrue(choose.instate(['disabled']))
                    self.assertTrue(setup_button.instate(['disabled']))
                    self.assertTrue(optimizer.instate(['disabled']))
                    root.event_generate('<Alt-s>');root.update()
                    self.assertEqual(setup.call_count,2)
                    gate.set()
                    wait_for(lambda:review.call_count==1)
                    self.assertIn('Verification workers: 3',review.call_args.args[1])
                    self.assertIn('Candidate generation: parallel',review.call_args.args[1])
                    self.assertTrue(review_button.instate(['!disabled']))
                    root.setvar(worker['textvariable'], 1)
                    optimizer.set(OPTIMIZER_LABELS['qpdf'])
                    review_button.invoke()
                    wait_for(lambda:review.call_count==2)
                    self.assertIn('Verification workers: 1',review.call_args.args[1])
                    self.assertIn('Candidate generation: sequential',review.call_args.args[1])
                    self.assertIn(OPTIMIZER_LABELS['qpdf'],review.call_args.args[1])
                    self.assertEqual([call.args[0] for call in planner.call_args_list],[(str(source),)]*2)
                    picker.assert_called_once()
                    runner.assert_not_called()
                finally:
                    gate.set()
                    root.tk.call(root.protocol('WM_DELETE_WINDOW'))
            with patch.object(tk.Tk,'mainloop',smoke),\
                 patch('viewing_copy_gui.dependency_diagnostics',return_value=()) as setup,\
                 patch('viewing_copy_gui.system_resources',return_value={'logical_cpu_count':4,'total_ram_bytes':None,'available_ram_bytes':None}),\
                 patch('viewing_copy_gui.plan_batch',side_effect=blocked) as planner,\
                 patch('viewing_copy_gui.review_jobs',return_value=False) as review,\
                 patch('batch_workflow.BatchController.run') as runner,\
                 patch('tkinter.filedialog.askopenfilenames',return_value=(str(source),)) as picker,\
                 patch('tkinter.messagebox.askyesno',return_value=False):
                main()

    def test_review_opens_offset_from_current_main_window_position(self):
        import tkinter as tk
        from viewing_copy_gui import review_jobs

        root=tk.Tk()
        try:
            for x,y in ((80,90),(180,120)):
                root.geometry(f'680x460+{x}+{y}')
                root.update()
                positions=[]
                def close_review():
                    dialog=root.winfo_children()[0]
                    positions.append((dialog.winfo_x(),dialog.winfo_y()))
                    dialog.tk.call(dialog.protocol('WM_DELETE_WINDOW'))
                root.after(50,close_review)
                self.assertFalse(review_jobs(root,'Review sample.pdf'))
                self.assertEqual(positions,[(root.winfo_x()+28,root.winfo_y()+28)])
        finally:
            root.destroy()

    @unittest.skipUnless(os.name=='nt','Windows File Explorer')
    def test_batch_summary_shows_saved_paths_and_opens_their_folders(self):
        import tkinter as tk
        from tkinter import ttk
        from unittest.mock import call, patch
        from viewing_copy_gui import batch_dialog

        root=tk.Tk()
        outputs=(Path(r'C:\Example\First folder\copy.pdf'),Path(r'C:\Example\Second folder\copy.pdf'))
        shown=[]
        def inspect_summary():
            dialog=root.winfo_children()[0]
            try:
                text=next(child for frame in dialog.winfo_children() for child in frame.winfo_children()
                          if isinstance(child,tk.Text))
                shown.append(text.get('1.0','end'))
                for button in text.winfo_children():
                    if isinstance(button,ttk.Button) and button['text']=='Open folder':
                        button.invoke()
            finally:
                dialog.destroy()
        try:
            with patch('viewing_copy_gui.os.startfile') as open_folder:
                root.after(100,inspect_summary)
                batch_dialog(root,'Copies saved: 2',saved_paths=outputs)
                self.assertEqual(open_folder.call_args_list,[call(str(output.parent)) for output in outputs])
            self.assertIn('Copies saved: 2',shown[0])
            self.assertIn('Saved locations',shown[0])
            for output in outputs:self.assertIn(str(output),shown[0])
        finally:
            root.destroy()

    def test_activity_log_only_follows_when_viewing_latest_lines(self):
        import tkinter as tk
        from viewing_copy_gui import append_activity

        root=tk.Tk()
        try:
            log=tk.Text(root,height=5,state='disabled')
            log.pack()
            root.update()
            append_activity(log,'\n'.join(f'Activity {number}' for number in range(40)))
            root.update()
            self.assertEqual(log.yview()[1],1.0)
            log.yview('10.0')
            log.tag_add('sel','10.0','10.end')
            root.update()
            top=log.index('@0,0')
            append_activity(log,'New activity')
            root.update()
            self.assertEqual(log.index('@0,0'),top)
            self.assertEqual(log.get('sel.first','sel.last'),'Activity 9')
            log.tag_remove('sel','1.0','end')
            log.see('end')
            append_activity(log,'Latest activity')
            root.update()
            self.assertEqual(log.yview()[1],1.0)
            self.assertIn('Latest activity',log.get('end-2l','end'))
        finally:
            root.destroy()

    def test_slow_setup_does_not_block_tk_events(self):
        import tkinter as tk
        from unittest.mock import patch
        from viewing_copy_gui import main
        gate=threading.Event();ticks=[]
        def blocked(_profile):gate.wait(2);return ()
        def smoke(root):
            root.withdraw();root.after(10,lambda:ticks.append(True));deadline=time.monotonic()+.3
            while time.monotonic()<deadline and not ticks:root.update();time.sleep(.01)
            self.assertTrue(ticks);root.tk.call(root.protocol('WM_DELETE_WINDOW'));gate.set()
        with patch.object(tk.Tk,'mainloop',smoke),patch('viewing_copy_gui.dependency_diagnostics',side_effect=blocked):
            main()

    def test_quick_profile_is_reviewed_and_isolated_from_thorough_recovery(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source=Path(folder)/'source.pdf';make_pdf(source)
            quick=plan_batch([source],profile='quick')[0]
            self.assertEqual(quick.profile,'quick');self.assertEqual(quick.optimizer,'adaptive')
            text=confirmation_text((quick,))
            self.assertIn('Direct optimization',text)
            self.assertIn('Direct checks preservation without measuring viewing speed.',text)
            self.assertNotIn('PDFium verification RAM',text)
            _save_job(replace(quick,status='running'))
            self.assertEqual(json.loads(quick.output.with_suffix('.checkpoint').joinpath('job.json').read_text())['schema'],2)
            self.assertEqual(plan_batch([source],profile='quick')[0].output,quick.output)
            self.assertNotEqual(plan_batch([source],'qpdf')[0].output,quick.output)

    def test_direct_setup_checks_renderers_but_does_not_require_browser(self):
        from unittest.mock import patch
        versions={'pypdf':'6.10.0','pypdfium2':'5.14.0','Pillow':'12.3.0'}
        completed=__import__('subprocess').CompletedProcess([],0,'{"version":"6.4.299","dependencies":"ready"}\n','')
        with patch('batch_workflow.importlib.metadata.version',side_effect=lambda name:versions[name]) as version,\
             patch('viewing_copy.qpdf_identity',return_value=('qpdf.exe','qpdf version 12.4.2','f'*64)),\
             patch('viewing_copy.runtime_node',return_value=__import__('sys').executable),\
             patch('batch_workflow.subprocess.run',return_value=completed):
            rows=dependency_diagnostics('quick')
        self.assertEqual([row['name'] for row in rows],['pypdf','pypdfium2','Pillow','Node/PDF.js','qpdf'])
        self.assertTrue(all(row['ok'] for row in rows));self.assertEqual(version.call_count,3)

    def test_quick_report_requires_complete_verification_and_matching_profile(self):
        from batch_workflow import validate_result
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source=Path(folder)/'source.pdf';make_pdf(source)
            quick=plan_batch([source],profile='quick')[0];_save_job(quick)
            complete(quick,'optimized',profile='thorough')
            with self.assertRaisesRegex(RuntimeError,'profile'):validate_result(quick)
            complete(quick,'optimized',profile='quick')
            with self.assertRaisesRegex(RuntimeError,'verification'):validate_result(quick)

    def test_direct_verification_failure_is_distinct_from_no_size_benefit(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            sources = [Path(folder) / name for name in ('renderer.pdf', 'small.pdf')]
            for source in sources:
                make_pdf(source)
            jobs = plan_batch(sources, profile='quick')

            def runner(job, emit, started):
                started()
                failed = job.source == sources[0]
                complete(job, 'no improvement', profile='quick',
                         classification='verification failed' if failed else 'no size benefit',
                         reason='Renderer stopped' if failed else 'Candidate was not smaller')

            results = BatchController(jobs, runner=runner).run()
            self.assertEqual([job.status for job in results], ['failed', 'no improvement'])
            self.assertEqual(results[0].error, 'Verification failed: Renderer stopped')
            self.assertIsNone(results[1].error)
            self.assertEqual(json.loads(jobs[0].targets[1].read_text())['status'], 'no improvement')

    def test_incremental_pdf_review_shows_thorough_option_and_report_policy(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source, revised = (Path(folder) / name for name in ('source.pdf', 'revised.pdf'))
            make_pdf(source)
            writer = PdfWriter(source, incremental=True)
            writer.add_metadata({'/Title': 'Revision'})
            writer.write(revised)
            job = plan_batch([revised], profile='quick')[0]
            self.assertEqual(job.status, 'rejected')
            review = confirmation_text((job,))
            self.assertIn('incremental revisions', review)
            self.assertIn('Select Thorough optimization', review)
            self.assertIn('do not receive output reports', review)
            self.assertEqual(plan_batch([revised], profile='thorough')[0].status, 'ready')
            self.assertCountEqual(Path(folder).iterdir(), (source, revised))

    def test_picker_accumulates_pdfs_across_folders(self):
        batches=iter([('first/a.pdf','first/b.pdf'),('second/c.pdf',)])
        prompts=[]
        selected=collect_pdf_paths(lambda:next(batches),lambda count:prompts.append(count) or count<3)
        self.assertEqual(selected,('first/a.pdf','first/b.pdf','second/c.pdf'))
        self.assertEqual(prompts,[2,3])
        batches=iter([('first/a.pdf',),()])
        self.assertEqual(collect_pdf_paths(lambda:next(batches),lambda _count:True),('first/a.pdf',))

    def test_resource_summary_warns_without_blocking_selection(self):
        resources={'logical_cpu_count':8,'total_ram_bytes':16*1024**3,
                   'available_ram_bytes':4*1024**3}
        text=resource_summary(2,resources)
        self.assertIn('Verification workers: 2',text)
        self.assertIn('5.0 GiB',text)
        self.assertIn('exceeds available RAM',text)
        self.assertIn('does not reserve or cap CPU cores',text)

    def test_batch_child_receives_reviewed_worker_count(self):
        from unittest.mock import patch
        class Child:
            stdout=()
            def __enter__(self):return self
            def __exit__(self,*_args):pass
            def wait(self):return 0
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source=Path(folder)/'source.pdf';make_pdf(source);job=plan_batch([source])[0]
            with patch('batch_workflow.subprocess.Popen',return_value=Child()) as popen:
                run_optimizer(job,lambda *_:None,lambda:None,3,True)
            command=popen.call_args.args[0]
            self.assertEqual(command[command.index('--verification-workers')+1],'3')
            self.assertIn('--parallel-candidates',command)
            self.assertEqual(command[command.index('--viewing-optimizer')+1],'adaptive')

    def test_optimizer_selection_is_reviewed_and_recovery_specific(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source=Path(folder)/'source.pdf';make_pdf(source)
            qpdf=plan_batch([source],'qpdf')[0]
            self.assertEqual(qpdf.optimizer,'qpdf')
            self.assertIn(OPTIMIZER_LABELS['qpdf'],confirmation_text((qpdf,)))
            _save_job(replace(qpdf,status='running'))
            self.assertEqual(plan_batch([source],'qpdf')[0].output,qpdf.output)
            adaptive=plan_batch([source])[0]
            self.assertEqual(adaptive.optimizer,'adaptive')
            self.assertNotEqual(adaptive.output,qpdf.output)
            with self.assertRaisesRegex(ValueError,'Unknown'):plan_batch([source],'other')

    def test_memory_detection_failure_is_reported_as_unavailable(self):
        from unittest.mock import patch
        from batch_workflow import system_resources
        kernel=type('Kernel',(),{'GlobalMemoryStatusEx':lambda *_args:0})()
        with patch('batch_workflow.sys.platform','win32'),patch('batch_workflow.ctypes.windll',create=True) as windll:
            windll.kernel32=kernel;resources=system_resources()
        self.assertIsNone(resources['total_ram_bytes'])
        self.assertIn('Available RAM: unavailable',resource_summary(1,resources))

    def test_progress_copy_is_exact_batch_completion(self):
        self.assertEqual(progress_text(0,0),'No batch running')
        self.assertEqual(progress_text(0,2),'0 of 2 files complete')
        self.assertEqual(progress_text(1,2),'1 of 2 files complete')
        self.assertEqual(progress_text(2,2),'2 of 2 files complete')

    def test_progress_bar_advances_on_stages_and_verified_pages(self):
        import tkinter as tk
        from tkinter import ttk
        from unittest.mock import patch
        from viewing_copy_gui import main

        page_ready, publication_ready, result_ready, second_ready, finish = (
            threading.Event() for _ in range(5))
        saved=[]
        def run(controller):
            job,second=controller.jobs
            controller.emit('current',job)
            controller.emit('stage',('verify preserved content on every page',time.monotonic()))
            page_ready.wait(4)
            controller.emit('output','Verified vector content/navigation/appearance: 1/2')
            publication_ready.wait(4)
            controller.emit('stage',('publication',time.monotonic()))
            result_ready.wait(4)
            result=replace(job,status='optimized')
            saved.append(result.output)
            controller.emit('result',result)
            second_ready.wait(4)
            controller.emit('current',second)
            controller.emit('stage',('quick input checks',time.monotonic()))
            finish.wait(4)
            second_result=replace(second,status='no improvement')
            controller.emit('result',second_result)
            return (result,second_result)

        def smoke(root):
            root.withdraw()
            def descendants(widget):
                for child in widget.winfo_children():
                    yield child;yield from descendants(child)
            def wait_for(predicate):
                deadline=time.monotonic()+3
                while not predicate() and time.monotonic()<deadline:
                    root.update();time.sleep(.01)
                self.assertTrue(predicate())
            widgets=list(descendants(root))
            buttons={str(child['text']).rstrip('…'):child for child in widgets if isinstance(child,ttk.Button)}
            progress=next(child for child in widgets if isinstance(child,ttk.Progressbar))
            try:
                wait_for(lambda:buttons['Choose PDFs'].instate(['!disabled']))
                buttons['Choose PDFs'].invoke()
                buttons['Review batch'].invoke()
                wait_for(lambda:0<progress['value']<1)
                self.assertEqual(progress['maximum'],2)
                stage=progress['value']
                page_ready.set()
                wait_for(lambda:stage<progress['value']<1)
                page=progress['value']
                publication_ready.set()
                wait_for(lambda:page<progress['value']<1)
                result_ready.set()
                wait_for(lambda:progress['value']==1)
                second_ready.set()
                wait_for(lambda:1<progress['value']<2)
                finish.set()
                wait_for(lambda:progress['value']==2)
                wait_for(lambda:summary.called)
                self.assertIn('Copies saved: 1',summary.call_args.args[1])
                self.assertEqual(summary.call_args.kwargs['saved_paths'],tuple(saved))
            finally:
                for gate in (page_ready,publication_ready,result_ready,second_ready,finish):gate.set()
                root.tk.call(root.protocol('WM_DELETE_WINDOW'))

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            sources=(Path(folder)/'first.pdf',Path(folder)/'second.pdf')
            for source in sources:make_pdf(source)
            with patch.object(tk.Tk,'mainloop',smoke),\
                 patch('viewing_copy_gui.dependency_diagnostics',return_value=()),\
                 patch('viewing_copy_gui.review_jobs',return_value=True),\
                 patch.object(BatchController,'run',run),\
                 patch('tkinter.filedialog.askopenfilenames',return_value=tuple(map(str,sources))),\
                 patch('tkinter.messagebox.askyesno',return_value=False),\
                 patch('viewing_copy_gui.batch_dialog') as summary:
                main()

    def test_repeated_thorough_stages_leave_room_for_timing_and_publication(self):
        from viewing_copy_gui import stage_progress

        progress=0
        for index in range(12):
            for stage in (f'candidate generation: {index}',f'preservation verification: {index}'):
                start,end=stage_progress(stage,progress)
                self.assertTrue(progress<start<end<58)
                progress=end
        timing,_=stage_progress('balanced performance timing',progress)
        start,end=stage_progress('final acceptance: first',timing)
        retry,_=stage_progress('final acceptance: retry',end)
        publication,_=stage_progress('publication',retry)
        self.assertTrue(progress<timing<start<end<retry<publication<100)

    def test_dependency_preflight_requires_bundled_qpdf(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            browser=Path(folder)/'zen.exe';browser.write_bytes(b'fixture')
            completed=__import__('subprocess').CompletedProcess([],0,'{"version":"6.4.299","dependencies":"ready"}\n','')
            with patch.dict('os.environ',{'PDF_BROWSER':str(browser)},clear=False),\
                 patch('batch_workflow.importlib.metadata.version',side_effect=lambda name:{'pypdf':'6.10.0','pypdfium2':'5.14.0','Pillow':'12.3.0','PyMuPDF':'1.28.2'}[name]),\
                 patch('viewing_copy.runtime_node',return_value=__import__('sys').executable),\
                 patch('viewing_copy.qpdf_identity',return_value=('qpdf.exe','qpdf version 12.4.2','f'*64)) as qpdf,\
                 patch('batch_workflow.subprocess.run',return_value=completed):
                rows=dependency_diagnostics()
                qpdf.side_effect=ValueError('qpdf unavailable')
                missing=dependency_diagnostics()
            self.assertFalse([row for row in rows if not row['ok'] and not row['optional']])
            self.assertEqual(rows[-1]['name'],'qpdf');self.assertFalse(rows[-1]['optional'])
            self.assertIn('Ready: qpdf',dependency_report(rows))
            self.assertFalse(missing[-1]['ok']);self.assertFalse(missing[-1]['optional'])
            self.assertIn('Restore the bundled qpdf files',missing[-1]['action'])

    def test_second_process_claim_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source=Path(folder)/'source.pdf';make_pdf(source);job=plan_batch([source])[0]
            claim=_claim_job(job)
            try:
                with self.assertRaisesRegex(JobAlreadyRunning,'already being optimized'):_claim_job(job)
            finally:_release_claim(claim)

    @unittest.skipUnless(sys.platform == 'win32','Windows process probe')
    def test_process_probe_does_not_signal_the_process(self):
        from unittest.mock import patch
        with patch('batch_workflow.os.kill',side_effect=AssertionError('must not signal')):
            self.assertTrue(_process_alive(os.getpid()))

    def test_checkpoint_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root=Path(folder);source=root/'source.pdf';make_pdf(source);job=plan_batch([source])[0]
            job.output.parent.mkdir()
            target=root/'elsewhere';target.mkdir()
            try:job.output.with_suffix('.checkpoint').symlink_to(target,target_is_directory=True)
            except OSError:self.skipTest('directory symlinks are unavailable')
            with self.assertRaisesRegex(ValueError,'shortcut'):_save_job(job)

    def test_plan_deduplicates_in_order_and_reserves_complete_output_sets(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            first, second = Path(folder) / "first.pdf", Path(folder) / "second.pdf"
            make_pdf(first); make_pdf(second)
            today=date.today().isoformat()
            first.with_name(f"first.optimized-{today}").mkdir()
            jobs = plan_batch([first, second, first])
            self.assertEqual([job.source for job in jobs], [first.absolute(), second.absolute()])
            self.assertEqual(jobs[0].output.parent.name, f"first.optimized-{today}-2")
            self.assertEqual(jobs[0].output.name, "first.optimized.pdf")
            self.assertEqual(jobs[1].output.parent.name, f"second.optimized-{today}")
            self.assertTrue(all(target.parent==job.output.parent for job in jobs for target in job.targets))
            self.assertTrue(all(job.status == "ready" for job in jobs))
            text = confirmation_text(jobs,parallel_candidates=True)
            self.assertLess(text.index('Candidate generation: parallel'),text.index('Files and outputs'))
            self.assertLess(text.index(str(first)), text.index(str(second)))
            self.assertIn(f'Source: {first}',text)
            self.assertIn(f'Save copy to: {jobs[0].output}',text)
            self.assertIn('Candidate generation: parallel',text)
            self.assertEqual([job.source for job in plan_batch([second, first])],
                             [second.absolute(), first.absolute()])

    def test_preflight_rejects_missing_and_encrypted_inputs(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            encrypted, missing = Path(folder) / "locked.pdf", Path(folder) / "missing.pdf"
            make_pdf(encrypted, encrypted=True)
            jobs = plan_batch([encrypted, missing])
            self.assertEqual([job.status for job in jobs], ["rejected", "rejected"])
            self.assertIn("Encrypted", jobs[0].error)

    def test_confirmation_cancel_skips_ready_jobs_without_launching(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source = Path(folder) / "source.pdf"; make_pdf(source)
            controller = BatchController(plan_batch([source]), runner=lambda *_: self.fail("launched"))
            results = controller.cancel()
            self.assertEqual(results[0].status, "skipped")
            self.assertEqual(controller.launches, 0)
            self.assertFalse(results[0].output.with_suffix('.checkpoint').exists())

    def test_launch_recheck_rejects_source_mutation_and_output_race(self):
        for race in ("source", "folder", "output"):
            with self.subTest(race=race), tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
                source = Path(folder) / "source.pdf"; make_pdf(source)
                jobs = plan_batch([source])
                if race == "source":
                    source.write_bytes(b"changed")
                elif race == "folder":
                    jobs[0].output.parent.mkdir()
                else:
                    jobs[0].output.parent.mkdir()
                    jobs[0].output.with_suffix(".md").write_text("appeared", encoding="utf-8")
                controller = BatchController(jobs, runner=lambda *_: self.fail("launched"))
                result = controller.run()[0]
                self.assertEqual(result.status, "rejected")
                self.assertEqual(controller.launches, 0)

    def test_child_initialization_rejects_mutation_after_controller_recheck(self):
        from viewing_copy import optimize_viewing
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source = Path(folder) / "source.pdf"; make_pdf(source)

            def runner(job, emit, started):
                started()
                source.write_bytes(b"changed after launch")
                optimize_viewing(job.source, job.output, job.source_sha256)

            result = BatchController(plan_batch([source]), runner=runner).run()[0]
            self.assertEqual(result.status, "failed")
            self.assertIn("reviewed source hash", result.error)
            self.assertFalse(any(path.exists() for path in result.targets))

    def test_output_race_after_launch_fails_without_replacing_file(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source = Path(folder) / "source.pdf"; make_pdf(source)

            def runner(job, emit, started):
                started()
                job.output.write_bytes(b"other process")
                raise FileExistsError("output appeared during processing")

            result = BatchController(plan_batch([source]), runner=runner).run()[0]
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.output.read_bytes(), b"other process")

    def test_process_start_failure_is_failed_and_later_job_runs(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            sources = [Path(folder) / name for name in ("bad.pdf", "good.pdf")]
            for source in sources: make_pdf(source)

            def runner(job, emit, started):
                if job.source.name == "bad.pdf":
                    raise OSError("could not start")
                started(); complete(job)

            results = BatchController(plan_batch(sources), runner=runner).run()
            self.assertEqual([item.status for item in results], ["failed", "no improvement"])

    def test_time_and_disk_budgets_are_typed_terminal_results(self):
        for error,status in ((JobTimeout('late'),'timeout'),(JobBudgetExceeded('large'),'budget exhausted')):
            with self.subTest(status=status), tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
                source=Path(folder)/'source.pdf';make_pdf(source)
                def runner(job,emit,started):started();raise error
                result=BatchController(plan_batch([source]),runner=runner).run()[0]
                self.assertEqual(result.status,status)
                recovered=plan_batch([source])[0]
                self.assertEqual(recovered.status,'ready')
                self.assertEqual(recovered.output,result.output)

    def test_stop_and_close_preserve_retry_checkpoint(self):
        for saved_status in ('timeout','budget exhausted'):
            for action in ('stop','close'):
                with self.subTest(saved_status=saved_status,action=action),\
                     tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
                    source=Path(folder)/'source.pdf';make_pdf(source);job=plan_batch([source])[0]
                    _save_job(replace(job,status=saved_status,error='saved progress'))
                    retry=plan_batch([source])[0]
                    self.assertEqual(retry.output,job.output)
                    controller=BatchController((retry,),runner=lambda *_:self.fail('launched'))
                    if action=='close':self.assertTrue(controller.request_close())
                    else:controller.request_stop();controller.run()
                    self.assertEqual(controller.jobs[0].status,'skipped');self.assertEqual(controller.launches,0)
                    record=json.loads(job.output.with_suffix('.checkpoint').joinpath('job.json').read_text())
                    self.assertEqual(record['status'],saved_status)
                    recovered=plan_batch([source])[0]
                    self.assertEqual(recovered.status,'ready');self.assertEqual(recovered.output,job.output)
                    self.assertEqual(recovered.output.with_suffix('.checkpoint'),job.output.with_suffix('.checkpoint'))

    def test_restart_recovers_valid_terminal_report_and_resumes_incomplete_job(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source=Path(folder)/'source.pdf';make_pdf(source)
            job=plan_batch([source])[0]
            _save_job(replace(job,status='running'))
            self.assertEqual(plan_batch([source])[0].status,'ready')
            complete(job)
            recovered=plan_batch([source])[0]
            self.assertEqual(recovered.status,'no improvement')
            self.assertEqual(recovered.output,job.output)

    def test_sequential_failure_isolation_and_reports_only_result(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            sources = [Path(folder) / name for name in ("first.pdf", "bad.pdf", "last.pdf")]
            for source in sources: make_pdf(source)
            active = 0

            def runner(job, emit, started):
                nonlocal active
                self.assertEqual(active, 0); active += 1; started()
                try:
                    if job.source.name == "bad.pdf": raise RuntimeError("broken")
                    complete(job, "optimized" if job.source.name == "first.pdf" else "no improvement")
                finally: active -= 1

            results = BatchController(plan_batch(sources), runner=runner).run()
            self.assertEqual([item.status for item in results], ["optimized", "failed", "no improvement"])
            self.assertFalse(results[2].output.exists())

    def test_malformed_missing_and_mismatched_reports_fail_closed(self):
        cases = ("missing", "malformed", "source hash", "output hash", "source path")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
                source = Path(folder) / "source.pdf"; make_pdf(source)

                def runner(job, emit, started):
                    started()
                    if case == "missing": return
                    if case == "malformed":
                        job.output.with_suffix(".json").write_text("{", encoding="utf-8")
                        job.output.with_suffix(".md").write_text("report", encoding="utf-8")
                    elif case == "source hash": complete(job, source_sha256="0" * 64)
                    elif case == "source path": complete(job, source=str(Path(folder) / "other.pdf"))
                    else: complete(job, "optimized", output_sha256="0" * 64)

                self.assertEqual(BatchController(plan_batch([source]), runner=runner).run()[0].status, "failed")

    def test_stop_after_current_and_close_boundaries_skip_queue(self):
        for action in ("stop", "close"):
            with self.subTest(action=action), tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
                sources = [Path(folder) / name for name in ("first.pdf", "second.pdf")]
                for source in sources: make_pdf(source)
                controller = None

                def emit(kind, value):
                    if action == "close" and kind == "result" and value.status != "skipped":
                        self.assertFalse(controller.request_close())
                    if action == "close" and kind == "boundary":
                        self.assertTrue(controller.request_close())

                def runner(job, emit_event, started):
                    self.assertFalse(controller.request_close())
                    started()
                    self.assertFalse(controller.request_close())
                    complete(job)
                    if action == "stop": controller.request_stop()

                controller = BatchController(plan_batch(sources), emit=emit, runner=runner)
                results = controller.run()
                self.assertEqual([item.status for item in results], ["no improvement", "skipped"])
                self.assertEqual(controller.launches, 1)

    def test_inspect_button_inspects_drawing_opportunities(self):
        import tkinter as tk
        from tkinter import ttk
        from unittest.mock import patch
        from viewing_copy_gui import main
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.pdf'
            make_pdf(source)

            def smoke(root):
                root.withdraw()
                try:
                    def descendants(widget):
                        for child in widget.winfo_children():
                            yield child
                            yield from descendants(child)

                    deadline = time.monotonic() + 3
                    inspect_btn = None
                    while time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)
                        inspect_btn = next((child for child in descendants(root)
                                           if isinstance(child, ttk.Button) and 'Inspect PDF' in str(child['text'])), None)
                        if inspect_btn and inspect_btn.instate(['!disabled']):
                            break
                    self.assertIsNotNone(inspect_btn)
                    self.assertTrue(inspect_btn.instate(['!disabled']))

                    log = next(child for child in descendants(root) if isinstance(child, tk.Text))
                    inspect_btn.invoke()

                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)
                        if 'Drawing Opportunity Inspection' in log.get('1.0', 'end'):
                            break
                    log_content = log.get('1.0', 'end')
                    self.assertIn('Drawing Opportunity Inspection', log_content)
                    self.assertIn('Status: NO SUPPORTED OPPORTUNITY', log_content)
                    self.assertIn('Explanation: Document contains no vector drawing operations.', log_content)
                    self.assertIn('Pages: 1 total', log_content)
                finally:
                    root.tk.call(root.protocol('WM_DELETE_WINDOW'))

            with patch.object(tk.Tk, 'mainloop', smoke), \
                 patch('viewing_copy_gui.dependency_diagnostics', return_value=()), \
                 patch('tkinter.filedialog.askopenfilename', return_value=str(source)):
                main()

    def test_inspect_formats_numeric_line_widths_without_crashing_event_loop(self):
        import tkinter as tk
        from tkinter import ttk
        from unittest.mock import patch
        from viewing_copy_gui import main
        mock_report = {
            'inventory': {
                'multi_vertex_stroked_paths': 5, 'two_point_segments': 2, 'curves': 0,
                'variable_width_runs': 0, 'filled_outlines': 0, 'image_stamps': 0, 'annotations': 0,
                'raster_content': {'image_calls': 0, 'unique_images': 0},
                'local_widths': [0.5, 1.0, 2.5],
                'point_counts': 12, 'paint_calls': 3,
            },
            'first_supported_subset': {
                'status': 'supported drawing opportunity',
                'explanation': 'Opaque constant-width vector strokes found.',
                'skip_reasons': {'device_dependent_hairlines': 0},
            },
            'denominator_tracking': {
                'total_pages': 1, 'pages_with_supported_opportunity': 1,
                'pages_raster_only': 0, 'pages_already_efficient': 0, 'pages_unsupported_or_no_op': 0,
            },
        }
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'drawing.pdf'
            make_pdf(source)
            def smoke(root):
                root.withdraw()
                try:
                    def descendants(widget):
                        for child in widget.winfo_children():
                            yield child
                            yield from descendants(child)

                    deadline = time.monotonic() + 3
                    inspect_btn = None
                    while time.monotonic() < deadline:
                        root.update(); time.sleep(0.01)
                        inspect_btn = next((child for child in descendants(root)
                                           if isinstance(child, ttk.Button) and 'Inspect PDF' in str(child['text'])), None)
                        if inspect_btn and inspect_btn.instate(['!disabled']):
                            break
                    log = next(child for child in descendants(root) if isinstance(child, tk.Text))
                    inspect_btn.invoke()
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        root.update(); time.sleep(0.01)
                        if 'Declared line widths: 0.5, 1.0, 2.5' in log.get('1.0', 'end'):
                            break
                    log_content = log.get('1.0', 'end')
                    self.assertIn('Declared line widths: 0.5, 1.0, 2.5', log_content)
                    self.assertIn('Status: SUPPORTED DRAWING OPPORTUNITY', log_content)
                finally:
                    root.tk.call(root.protocol('WM_DELETE_WINDOW'))

            with patch.object(tk.Tk, 'mainloop', smoke), \
                 patch('viewing_copy_gui.dependency_diagnostics', return_value=()), \
                 patch('tkinter.filedialog.askopenfilename', return_value=str(source)), \
                 patch('experiment_manifest.analyze_drawing_opportunity', return_value=mock_report):
                main()


if __name__ == "__main__":
    unittest.main()
