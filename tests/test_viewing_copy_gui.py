import json
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace

from pypdf import PdfWriter
from viewing_copy_gui import progress_text

from batch_workflow import (BatchController, JobAlreadyRunning, JobBudgetExceeded, JobTimeout,
                            _claim_job, _release_claim, _save_job, confirmation_text,
                            dependency_diagnostics, dependency_report, filehash, plan_batch,
                            resource_summary, run_optimizer)


def make_pdf(path, encrypted=False):
    writer = PdfWriter()
    writer.add_blank_page(width=60, height=60)
    if encrypted:
        writer.encrypt("secret")
    writer.write(path)


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
    def test_resource_summary_warns_without_blocking_selection(self):
        resources={'logical_cpu_count':8,'total_ram_bytes':16*1024**3,
                   'available_ram_bytes':4*1024**3}
        text=resource_summary(2,resources)
        self.assertIn('Verification workers: 2',text)
        self.assertIn('8.0 GiB',text)
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
                run_optimizer(job,lambda *_:None,lambda:None,3)
            command=popen.call_args.args[0]
            self.assertEqual(command[command.index('--verification-workers')+1],'3')

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

    def test_dependency_preflight_distinguishes_required_and_optional_tools(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            browser=Path(folder)/'zen.exe';browser.write_bytes(b'fixture')
            completed=__import__('subprocess').CompletedProcess([],0,'{"version":"6.4.299","dependencies":"ready"}\n','')
            with patch.dict('os.environ',{'PDF_BROWSER':str(browser)},clear=False),\
                 patch('batch_workflow.importlib.metadata.version',side_effect=lambda name:{'pypdf':'6.10.0','pypdfium2':'5.14.0','Pillow':'12.3.0','PyMuPDF':'1.28.2'}[name]),\
                 patch('viewing_copy.runtime_node',return_value=__import__('sys').executable),\
                 patch('batch_workflow.subprocess.run',return_value=completed),\
                 patch('batch_workflow.shutil.which',return_value=None):
                rows=dependency_diagnostics()
            self.assertFalse([row for row in rows if not row['ok'] and not row['optional']])
            self.assertEqual(rows[-1]['name'],'qpdf');self.assertTrue(rows[-1]['optional'])
            self.assertIn('Optional: qpdf',dependency_report(rows))

    def test_second_process_claim_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source=Path(folder)/'source.pdf';make_pdf(source);job=plan_batch([source])[0]
            claim=_claim_job(job)
            try:
                with self.assertRaisesRegex(JobAlreadyRunning,'already being optimized'):_claim_job(job)
            finally:_release_claim(claim)

    def test_checkpoint_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root=Path(folder);source=root/'source.pdf';make_pdf(source);job=plan_batch([source])[0]
            target=root/'elsewhere';target.mkdir()
            try:job.output.with_suffix('.checkpoint').symlink_to(target,target_is_directory=True)
            except OSError:self.skipTest('directory symlinks are unavailable')
            with self.assertRaisesRegex(ValueError,'shortcut'):_save_job(job)

    def test_plan_deduplicates_in_order_and_reserves_complete_output_sets(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            first, second = Path(folder) / "first.pdf", Path(folder) / "second.pdf"
            make_pdf(first); make_pdf(second)
            first.with_name("first.optimized.pdf").write_bytes(b"old")
            jobs = plan_batch([first, second, first])
            self.assertEqual([job.source for job in jobs], [first.absolute(), second.absolute()])
            self.assertEqual(jobs[0].output.name, "first.optimized-2.pdf")
            self.assertTrue(all(job.status == "ready" for job in jobs))
            text = confirmation_text(jobs)
            self.assertLess(text.index(str(first)), text.index(str(second)))
            self.assertIn(str(jobs[0].output), text)
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
        for race in ("source", "output"):
            with self.subTest(race=race), tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
                source = Path(folder) / "source.pdf"; make_pdf(source)
                jobs = plan_batch([source])
                if race == "source":
                    source.write_bytes(b"changed")
                else:
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


if __name__ == "__main__":
    unittest.main()
