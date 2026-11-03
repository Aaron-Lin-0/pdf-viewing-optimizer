"""Shared non-rendering runtime, process budgets and qpdf compression recipe."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


class TimeBudgetExceeded(RuntimeError):
    pass


class DiskBudgetExceeded(RuntimeError):
    pass


class RunBudget:
    def __init__(self, seconds=None, bytes_limit=None, checkpoint=None):
        self.started=time.monotonic();self.seconds=seconds;self.bytes_limit=bytes_limit
        self.checkpoint=Path(checkpoint) if checkpoint else None

    def check(self, stage, paths=()):
        if self.seconds is not None and time.monotonic()-self.started>=self.seconds:
            raise TimeBudgetExceeded(f'Time budget exhausted before {stage}.')
        if self.bytes_limit is not None:
            roots=[self.checkpoint,*map(Path,paths)] if self.checkpoint else list(map(Path,paths))
            total=sum(path.stat().st_size for root in roots if root and root.exists()
                      for path in ([root] if root.is_file() else root.rglob('*')) if path.is_file())
            if total>self.bytes_limit:raise DiskBudgetExceeded(f'Disk budget exhausted before {stage}.')

    def timeout(self, maximum):
        if self.seconds is None:return maximum
        return max(.001,min(maximum,self.seconds-(time.monotonic()-self.started)))


def _run(command,maximum,budget,stage,**kwargs):
    timeout=budget.timeout(maximum) if budget else maximum
    budget_limited=budget is not None and timeout<maximum
    try:return subprocess.run(command,timeout=timeout,**kwargs)
    except subprocess.TimeoutExpired as exc:
        if budget_limited:raise TimeBudgetExceeded(f'Time budget exhausted during {stage}.') from exc
        raise ValueError(f'{stage} timed out after {maximum:g} seconds.') from exc


def filehash(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block:=stream.read(1024*1024):digest.update(block)
    return digest.hexdigest()


def qpdf_identity(budget=None,root=None):
    requested=os.environ.get('PDF_QPDF')
    bundled=(Path(root) if root is not None else Path(__file__).resolve().parent)/'vendor'/'qpdf'/'bin'/'qpdf.exe'
    executable=shutil.which(requested) if requested else (
        str(bundled) if sys.platform=='win32' and bundled.is_file() else shutil.which('qpdf'))
    if not executable:raise ValueError('qpdf is unavailable; restore the bundled runtime or set PDF_QPDF.')
    version=_run([executable,'--version'],30,budget,'qpdf version check',capture_output=True,text=True,
                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if version.returncode:raise ValueError('qpdf version preflight failed: '+version.stderr[-500:])
    executable_hash=filehash(executable) if Path(executable).is_file() else hashlib.sha256(executable.encode()).hexdigest()
    return executable,version.stdout.strip().splitlines()[0],executable_hash


def build_qpdf_candidate(source,destination,budget=None,identity=None,strict=False):
    executable,version_text,_=identity or qpdf_identity(budget)
    command=[executable,str(source),'--recompress-flate','--compression-level=9',
             '--object-streams=generate','--stream-data=compress',str(destination)]
    if strict:
        # Preserve existing stream encodings. Recompressing already compressed
        # handwriting adds substantial CPU work for little storage benefit.
        command=[executable,str(source),'--compress-streams=y','--decode-level=none',
                 '--compression-level=9','--object-streams=generate','--preserve-unreferenced',
                 '--remove-unreferenced-resources=no',str(destination)]
    child=_run(command,1800,budget,'qpdf candidate generation',capture_output=True,text=True,
               creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if child.returncode not in ((0,) if strict else (0,3)) or (strict and child.stderr.strip()):
        raise ValueError('qpdf failed or reported warnings: '+child.stderr[-1500:])
    if not Path(destination).is_file():raise ValueError('qpdf produced no output.')
    return version_text


def check_qpdf(path,identity,budget=None):
    result=_run([identity[0],'--check',str(path)],1800,budget,'qpdf validity check',
                capture_output=True,text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode or result.stderr.strip():
        raise ValueError('qpdf validity check failed or reported warnings: '+result.stderr[-1500:])
