"""Plan and run verified viewing-copy jobs sequentially."""

from dataclasses import dataclass, replace
import ctypes
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

from navigation import reader_for, validate_input


ROOT = Path(__file__).resolve().parent
DEFAULT_TIME_BUDGET_SECONDS = 6 * 60 * 60
DEFAULT_DISK_BUDGET_BYTES = 10 * 1024 * 1024 * 1024


class JobTimeout(RuntimeError):
    pass


class JobBudgetExceeded(RuntimeError):
    pass


class JobAlreadyRunning(RuntimeError):
    pass


def system_resources():
    logical=os.cpu_count() or 1
    total=available=None
    if sys.platform == 'win32':
        class MemoryStatus(ctypes.Structure):
            _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong),
                      ('total_physical',ctypes.c_ulonglong),('available_physical',ctypes.c_ulonglong),
                      ('total_page_file',ctypes.c_ulonglong),('available_page_file',ctypes.c_ulonglong),
                      ('total_virtual',ctypes.c_ulonglong),('available_virtual',ctypes.c_ulonglong),
                      ('available_extended_virtual',ctypes.c_ulonglong)]
        try:
            status=MemoryStatus();status.length=ctypes.sizeof(status)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                raise OSError('GlobalMemoryStatusEx failed')
            total=status.total_physical;available=status.available_physical
        except (AttributeError,OSError):
            pass
    return {'logical_cpu_count':logical,'total_ram_bytes':total,'available_ram_bytes':available}


def resource_summary(workers, resources=None):
    from viewing_copy import estimated_verification_ram
    resources=resources or system_resources();estimate=estimated_verification_ram(workers)
    gib=lambda value:'unavailable' if value is None else f'{value/1024**3:.1f} GiB'
    lines=[f'Verification workers: {workers}',
           f"Logical CPU count: {resources['logical_cpu_count']}",
           f"Total RAM: {gib(resources['total_ram_bytes'])}",
           f"Available RAM: {gib(resources['available_ram_bytes'])}",
           f'Estimated verification RAM: {gib(estimate)} (conservative planning estimate, not a measured peak or limit).',
           'Workers may use multiple CPU threads internally; this selection does not reserve or cap CPU cores.']
    if resources['available_ram_bytes'] is not None and estimate>resources['available_ram_bytes']:
        lines.append('Warning: the estimate exceeds available RAM; fewer workers are recommended.')
    return '\n'.join(lines)


def dependency_diagnostics():
    rows=[]
    required={'pypdf':'6.10.0','pypdfium2':'5.14.0','Pillow':'12.3.0','PyMuPDF':'1.28.2'}
    for name,expected in required.items():
        try:actual=importlib.metadata.version(name);ok=actual==expected
        except importlib.metadata.PackageNotFoundError:actual='missing';ok=False
        rows.append({'name':name,'ok':ok,'optional':False,'detail':actual,
                     'action':None if ok else 'Run Setup Dependencies.cmd.'})
    try:
        from viewing_copy import runtime_node
        node=runtime_node()
        child=subprocess.run([node,str(ROOT/'pdfjs_benchmark.mjs'),'unused.pdf','1','preflight','1'],
                             capture_output=True,text=True,timeout=30,
                             creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        payload=json.loads(child.stdout.strip().splitlines()[-1]) if child.returncode==0 else {}
        ok=payload.get('version')=='6.4.299' and payload.get('dependencies')=='ready'
        rows.append({'name':'Node/PDF.js','ok':ok,'optional':False,
                     'detail':f"PDF.js {payload.get('version','unavailable')}",
                     'action':None if ok else 'Run Setup Dependencies.cmd after installing Node.js 22.13 or newer.'})
    except Exception as exc:
        rows.append({'name':'Node/PDF.js','ok':False,'optional':False,'detail':str(exc),
                     'action':'Run Setup Dependencies.cmd after installing Node.js 22.13 or newer.'})
    browser=Path(os.environ.get('PDF_BROWSER') or r'C:\Program Files\Zen Browser\zen.exe')
    rows.append({'name':'Zen/Firefox','ok':browser.is_file(),'optional':False,
                 'detail':str(browser),'action':None if browser.is_file() else
                 'Install Zen/Firefox, then set PDF_BROWSER to its executable.'})
    try:
        from viewing_copy import qpdf_identity
        qpdf,version,_=qpdf_identity()
        rows.append({'name':'qpdf','ok':True,'optional':False,'detail':f'{version} — {qpdf}','action':None})
    except Exception as exc:
        rows.append({'name':'qpdf','ok':False,'optional':False,'detail':str(exc),
                     'action':'Restore the bundled qpdf files or set PDF_QPDF to a working executable.'})
    return tuple(rows)


def dependency_report(rows):
    lines=[]
    for row in rows:
        state='Ready' if row['ok'] else ('Optional' if row['optional'] else 'Needs attention')
        lines.append(f"{state}: {row['name']} — {row['detail']}")
        if row.get('action'):lines.append('  '+row['action'])
    return '\n'.join(lines)


def filehash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _path_key(path):
    return os.path.normcase(str(Path(path).absolute()))


def _targets(output):
    output = Path(output)
    return output, output.with_suffix(".json"), output.with_suffix(".md")


def _targets_available(output, reserved=()):
    reserved = set(reserved)
    return all(_path_key(path) not in reserved and not path.exists() and not path.is_symlink()
               for path in _targets(output))


def adjacent_output(source, reserved=()):
    source = Path(source).absolute()
    number = 1
    while True:
        label = source.stem + ".optimized" + (f"-{number}" if number > 1 else "")
        output = source.with_name(label + ".pdf")
        if _targets_available(output, reserved):
            return output
        number += 1


def _job_record(job):
    return job.output.with_suffix('.checkpoint')/'job.json'


def _checkpoint_root(job):
    root=job.output.with_suffix('.checkpoint')
    if root.is_symlink() or root.parent.resolve()!=job.output.parent.resolve():
        raise ValueError('Saved-progress folder is a shortcut or points outside this PDF folder. Rename or remove it, then try again.')
    return root


def _save_job(job):
    if not job.output:return
    root=_checkpoint_root(job);root.mkdir(parents=True,exist_ok=True);record=root/'job.json'
    temporary=record.with_name(f'.job.{os.getpid()}.tmp')
    temporary.write_text(json.dumps({'schema':1,'source':str(job.source),'output':str(job.output),
                                     'source_sha256':job.source_sha256,'status':job.status,
                                     'error':job.error}),encoding='utf-8')
    os.replace(temporary,record)


def _process_alive(pid):
    if sys.platform == 'win32':
        kernel32=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel32.OpenProcess.argtypes=(ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong)
        kernel32.OpenProcess.restype=ctypes.c_void_p
        kernel32.CloseHandle.argtypes=(ctypes.c_void_p,)
        handle=kernel32.OpenProcess(0x100000,False,pid)  # SYNCHRONIZE
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return ctypes.get_last_error()!=87  # ERROR_INVALID_PARAMETER means no such PID.
    try:os.kill(pid,0);return True
    except PermissionError:return True
    except OSError:return False


def _claim_job(job):
    root=_checkpoint_root(job);root.mkdir(parents=True,exist_ok=True);claim=root/'running.json'
    while True:
        try:
            with claim.open('x',encoding='utf-8') as stream:json.dump({'schema':1,'pid':os.getpid()},stream)
            return claim
        except FileExistsError:
            try:pid=int(json.loads(claim.read_text(encoding='utf-8'))['pid'])
            except (KeyError,OSError,ValueError):raise JobAlreadyRunning('This PDF has an unreadable active-run marker. Close other optimizer windows or remove running.json, then try again.')
            if _process_alive(pid):raise JobAlreadyRunning('This PDF is already being optimized in another window. Wait for it to finish, then try again.')
            claim.unlink(missing_ok=True)


def _release_claim(claim):
    if not claim:return
    try:
        if int(json.loads(claim.read_text(encoding='utf-8'))['pid'])==os.getpid():claim.unlink(missing_ok=True)
    except (KeyError,OSError,ValueError):pass


def _last_stage(job):
    root=_checkpoint_root(job)
    try:
        states=sorted((path for path in root.glob('*/state.json') if not path.is_symlink()),
                      key=lambda path:path.stat().st_mtime,reverse=True)
        for path in states:
            data=json.loads(path.read_text(encoding='utf-8'))
            if data.get('source_sha256')==job.source_sha256:return data.get('stage','the current stage')
    except (OSError,ValueError):pass
    return 'the current stage'


def _recover_job(source,source_hash):
    prefix=source.stem+'.optimized'
    folders=[path for path in source.parent.iterdir()
             if path.is_dir() and not path.is_symlink() and path.parent.resolve()==source.parent.resolve()
             and path.name.startswith(prefix) and path.name.endswith('.checkpoint')]
    for folder in sorted(folders,key=lambda path:path.stat().st_mtime,reverse=True):
        try:
            data=json.loads((folder/'job.json').read_text(encoding='utf-8'))
            output=Path(data['output']).absolute()
            job=Job(source,output,source_hash,data['status'],data.get('error'))
            if (data.get('schema')!=1 or Path(data['source']).absolute()!=source or
                    data.get('source_sha256')!=source_hash or output.parent!=source.parent or
                    output.with_suffix('.checkpoint')!=folder.absolute() or job.status not in {'ready','running'}):
                continue
            try:return replace(job,status=validate_result(job))
            except Exception:
                if _targets_available(output):return replace(job,status='ready',error=None)
        except (KeyError,OSError,ValueError):
            continue
    return None


@dataclass(frozen=True)
class Job:
    source: Path
    output: Path | None
    source_sha256: str | None
    status: str
    error: str | None = None

    @property
    def targets(self):
        return _targets(self.output) if self.output else ()


def plan_batch(sources):
    jobs, seen, reserved = [], set(), set()
    for selected in sources:
        source = Path(selected).expanduser().absolute()
        key = _path_key(source)
        if key in seen:
            continue
        seen.add(key)
        output = adjacent_output(source, reserved)
        reserved.update(map(_path_key, _targets(output)))
        job = Job(source, output, None, "planned")
        try:
            if not source.is_file():
                raise ValueError("Input must be a PDF file.")
            source_hash = filehash(source)
            validate_input(reader_for(source))
            jobs.append(_recover_job(source,source_hash) or replace(job, source_sha256=source_hash, status="ready"))
        except Exception as exc:
            jobs.append(replace(job, status="rejected", error=str(exc)))
    return tuple(jobs)


def confirmation_text(jobs, verification_workers=None, resources=None, parallel_candidates=False):
    from viewing_copy import default_verification_workers
    verification_workers=default_verification_workers() if verification_workers is None else verification_workers
    ready=sum(job.status == 'ready' for job in jobs);recovered=sum(job.status in {'optimized','no improvement'} for job in jobs)
    lines = [f"Optimize {ready} PDF(s) and recover {recovered} completed result(s)?", ""]
    for job in jobs:
        if job.status == "ready":
            lines.extend((str(job.source), f"  → {job.output}", ""))
        elif job.status in {'optimized','no improvement'}:
            lines.extend((str(job.source),f"  Recovered: {job.status}",""))
        else:
            lines.extend((str(job.source), f"  Rejected: {job.error}", ""))
    lines.extend((resource_summary(verification_workers,resources),
                  'Candidate generation: '+('parallel (higher RAM and disk use)' if parallel_candidates else 'sequential'),"",
                  "Originals are never changed. Existing outputs are never overwritten."))
    return "\n".join(lines)


def run_optimizer(job, emit, started, verification_workers=None, parallel_candidates=False):
    command = [sys.executable, str(ROOT / "optimize_pdf.py"), str(job.source), str(job.output),
               "--viewing-copy", "--expected-source-sha256", job.source_sha256,
               "--checkpoint", str(job.output.with_suffix('.checkpoint')),
               "--time-budget-seconds", str(DEFAULT_TIME_BUDGET_SECONDS),
               "--disk-budget-bytes", str(DEFAULT_DISK_BUDGET_BYTES)]
    if verification_workers is not None:command.extend(("--verification-workers",str(verification_workers)))
    if parallel_candidates:command.append('--parallel-candidates')
    last_error=None
    with subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, errors="replace",
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as child:
        started()
        for line in child.stdout:
            line=line.rstrip()
            if line.startswith('Error: '):last_error=line[7:]
            if line.startswith('Stage: '):emit('stage',(line[7:],time.monotonic()))
            emit("output", line)
        returncode = child.wait()
    if returncode==2:raise JobTimeout(f'This PDF exceeded the six-hour limit during {_last_stage(job)}. The original is unchanged and completed checks were saved. Select it again to resume.')
    if returncode==3:raise JobBudgetExceeded(f'This PDF exceeded the 10-GiB working-data limit during {_last_stage(job)}. Nothing unchecked was published. Use a drive with more free space or raise the CLI limit.')
    if returncode:
        raise RuntimeError(last_error or f"optimizer exited with code {returncode}")


def validate_result(job):
    output, json_report, readable_report = job.targets
    if not json_report.is_file() or not readable_report.is_file():
        raise RuntimeError("optimizer completed without both reports")
    try:
        report = json.loads(json_report.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"could not read optimizer report: {exc}") from exc
    status = report.get("status")
    if status not in {"optimized", "no improvement"}:
        raise RuntimeError(f"unexpected optimizer status: {status}")
    if Path(report.get("source", "")).absolute() != job.source:
        raise RuntimeError("optimizer report source does not match the reviewed source")
    if report.get("source_sha256") != job.source_sha256 or filehash(job.source) != job.source_sha256:
        raise RuntimeError("source changed after review")
    if status == "optimized":
        if Path(report.get("output", "")).absolute() != output or not output.is_file():
            raise RuntimeError("optimizer report output does not match the reviewed output")
        if report.get("output_sha256") != filehash(output):
            raise RuntimeError("published copy hash does not match the optimizer report")
    elif output.exists() or output.is_symlink():
        raise RuntimeError("no-improvement report unexpectedly published an output")
    return status


class BatchController:
    def __init__(self, jobs, emit=lambda *_: None, runner=run_optimizer, verification_workers=None,
                 parallel_candidates=False):
        self.jobs = list(jobs)
        self.emit = emit
        self.runner = ((lambda job,emit,started:run_optimizer(job,emit,started,verification_workers,parallel_candidates))
                       if runner is run_optimizer else runner)
        self.verification_workers=verification_workers
        self.parallel_candidates=parallel_candidates
        self._lock = threading.RLock()
        self._busy = False
        self._stop_requested = False
        self._close_requested = False
        self.launches = 0

    def _record(self, index, status, error=None):
        self.jobs[index] = replace(self.jobs[index], status=status, error=error)
        if status in {'optimized','no improvement','failed','timeout','budget exhausted'} or _job_record(self.jobs[index]).is_file():
            try:_save_job(self.jobs[index])
            except (OSError,ValueError):pass
        self.emit("result", self.jobs[index])

    def cancel(self):
        with self._lock:
            for index, job in enumerate(self.jobs):
                if job.status == "ready":
                    self._record(index, "skipped", "confirmation cancelled")
        return tuple(self.jobs)

    def request_stop(self):
        with self._lock:
            self._stop_requested = True

    def request_close(self):
        with self._lock:
            if self._busy:
                return False
            self._close_requested = True
            self._skip_ready("window closed")
            return True

    def _skip_ready(self, reason):
        for index, job in enumerate(self.jobs):
            if job.status == "ready":
                self._record(index, "skipped", reason)

    def _launch_check(self, job):
        if not job.source.is_file() or filehash(job.source) != job.source_sha256:
            raise ValueError("source changed after review")
        if not _targets_available(job.output):
            raise FileExistsError("output or report appeared after review")

    def run(self):
        for index in range(len(self.jobs)):
            claim=None
            with self._lock:
                if self._stop_requested or self._close_requested:
                    self._skip_ready("stopped before launch")
                    break
                job = self.jobs[index]
                if job.status != "ready":
                    continue
                self._busy = True
                try:
                    self._launch_check(job)
                    claim=_claim_job(job)
                except Exception as exc:
                    self._record(index, "rejected", str(exc))
                    self._busy = False
                    continue
                try:
                    _save_job(job)
                except OSError as exc:
                    _release_claim(claim)
                    self._record(index, "failed", f"could not save restart checkpoint: {exc}")
                    self._busy = False
                    continue
            self.emit("current", job)
            started = False

            def mark_started():
                nonlocal started
                started = True
                self.launches += 1
                self.jobs[index] = replace(self.jobs[index], status="running")
                self.emit("state", self.jobs[index])

            try:
                self.runner(job, self.emit, mark_started)
                if not started:
                    raise RuntimeError("optimizer runner returned without starting a process")
                status = validate_result(job)
                with self._lock:
                    self._record(index, status)
            except Exception as exc:
                with self._lock:
                    status="timeout" if isinstance(exc,JobTimeout) else ("budget exhausted" if isinstance(exc,JobBudgetExceeded) else "failed")
                    self._record(index, status, str(exc))
            finally:
                _release_claim(claim)
                with self._lock:
                    self._busy = False
            self.emit("boundary", job)
        with self._lock:
            if self._stop_requested or self._close_requested:
                self._skip_ready("stopped before launch")
        return tuple(self.jobs)
