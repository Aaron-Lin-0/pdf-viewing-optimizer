"""Run PDF.js navigation/layer checks in a disposable headless Zen profile."""
import json
import contextlib
import mimetypes
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit


@contextlib.contextmanager
def profile_directory(parent):
    temporary=tempfile.TemporaryDirectory(prefix='pdf-zen-',dir=parent)
    profile=Path(temporary.name).resolve()
    if profile.parent!=Path(parent).resolve() or not profile.name.startswith('pdf-zen-'):
        raise ValueError('Temporary profile escaped its output folder.')
    try:yield str(profile)
    finally:
        for attempt in range(20):
            try:temporary.cleanup();break
            except PermissionError:
                if attempt==19:raise
                time.sleep(.25)


def check_browser(source,candidate,pages=(1,7,29),timeout=1200):
    browser=os.environ.get('PDF_BROWSER') or r'C:\Program Files\Zen Browser\zen.exe'
    if not Path(browser).is_file():raise ValueError('Set PDF_BROWSER to Zen/Firefox for browser acceptance checks.')
    local=Path(__file__).resolve().parent/'node_modules'
    bundled=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'
    modules=Path(os.environ.get('PDF_NODE_MODULES',str(local if (local/'pdfjs-dist').is_dir() else bundled)))
    result={};guard=threading.Lock()
    files={'/pdf/source':Path(source),'/pdf/candidate':Path(candidate),'/':Path(__file__).with_name('browser_harness.html')}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            if self.path!='/result':self.send_error(404);return
            count=int(self.headers.get('Content-Length','0'))
            if count>2_000_000:self.send_error(413);return
            data=json.loads(self.rfile.read(count))
            with guard:result[data['which']]=data
            self.send_response(204);self.end_headers()
        def do_GET(self):
            route=urlsplit(self.path).path
            path=files.get(route)
            if route.startswith('/modules/'):
                path=(modules/route[len('/modules/'):]).resolve()
                if not path.is_relative_to(modules.resolve()):self.send_error(403);return
            if path is None or not path.is_file():self.send_error(404);return
            size=path.stat().st_size;start,end=0,size-1
            ranges=self.headers.get('Range')
            if ranges:
                try:
                    lo,hi=ranges.removeprefix('bytes=').split('-');start=int(lo);end=min(int(hi) if hi else size-1,size-1)
                    if start<0 or start>end:raise ValueError()
                except ValueError:self.send_error(416);return
            self.send_response(206 if ranges else 200)
            self.send_header('Content-Type','text/javascript' if path.suffix=='.mjs' else (mimetypes.guess_type(str(path))[0] or 'application/octet-stream'))
            self.send_header('Content-Length',str(end-start+1));self.send_header('Accept-Ranges','bytes')
            if ranges:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            self.end_headers()
            try:
                with path.open('rb') as stream:
                    stream.seek(start);left=end-start+1
                    while left:
                        block=stream.read(min(left,65536))
                        if not block:break
                        self.wfile.write(block);left-=len(block)
            except (BrokenPipeError,ConnectionResetError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        deadline=time.monotonic()+timeout
        for which in ['source','candidate']:
            with profile_directory(Path(candidate).parent) as profile:
                Path(profile,'user.js').write_text('user_pref("browser.shell.checkDefaultBrowser", false);\nuser_pref("browser.startup.page", 0);\nuser_pref("browser.aboutwelcome.enabled", false);\nuser_pref("toolkit.telemetry.enabled", false);\n')
                url=f'http://127.0.0.1:{server.server_port}/?which={which}&pages='+','.join(map(str,pages))
                log_path=Path(profile,'browser.log')
                log=log_path.open('wb')
                process=subprocess.Popen([browser,'--headless','--no-remote','--profile',profile,url],stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                try:
                    while which not in result:
                        if process.poll() is not None:
                            log.flush()
                            raise ValueError(f'Headless Zen exited ({process.returncode}): '+log_path.read_text(errors='replace')[-1500:])
                        if time.monotonic()>deadline:raise ValueError('Headless Zen browser verification timed out.')
                        time.sleep(.2)
                    if not result[which]['ok']:raise ValueError(result[which]['error'])
                finally:
                    if process.poll() is None:
                        if os.name=='nt':
                            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                        else:process.terminate()
                        try:process.wait(timeout=10)
                        except subprocess.TimeoutExpired:process.kill();process.wait()
                    log.close()
                    # Child file handles can close shortly after process-tree termination.
                    for attempt in range(20):
                        try:
                            probe=Path(profile,'browser.log');probe.unlink(missing_ok=True);break
                        except PermissionError:time.sleep(.1)
        before,after=result['source'],result['candidate']
        for key in ['pages','labels','destinationNames','links','layers','bookmarkTrace','interactive','resourceCleanups']:
            if before[key]!=after[key]:
                detail=''
                if isinstance(before[key],list) and isinstance(after[key],list):
                    detail=next((f' at item {i+1}: {a!r} != {b!r}' for i,(a,b) in enumerate(zip(before[key],after[key])) if a!=b),
                                f' (counts {len(before[key])} != {len(after[key])})')
                raise ValueError(f'Browser navigation/layer check failed: {key}'+detail[:800])
        return {'engine':'isolated headless Zen/Firefox; installed pdfjs-dist browser build','results':result}
    finally:server.shutdown();server.server_close()
