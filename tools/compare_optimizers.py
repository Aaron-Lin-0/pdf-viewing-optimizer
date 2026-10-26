"""Run local PDF competitors and report independent preservation, appearance and Firefox evidence."""
import argparse
from collections import Counter
import contextlib
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(Path(__file__).resolve().parent)]
from navigation import Graph, navigation_fingerprint, reader_for, validate_input
from geometry import page_signature
from viewing_copy import filehash
from feature_loss import image_stamp_rectangles, summarize_feature_loss
from evidence_cache import EvidenceCache
import firefox_measure

PROFILES = {
    'repository': ['viewing-copy', 'default', 'compact', 'inline', 'inline+compact',
                   'fast-handwriting', 'fast-handwriting+compact'],
    'boox-pdf': ['vector-6', 'vector-2', 'vector-1', 'color-300', 'color-200', 'color-150', 'gray-150'],
    'qpdf': ['flate', 'flate-linearized'], 'mupdf': ['compress', 'compress-packed'],
    'pdfsizeopt': ['default'], 'ocrmypdf': ['level-1', 'level-2', 'level-3'],
    'mattdesl': ['default', 'quality-95', 'quality-85-200'],
    'ghostscript': ['default', 'prepress', 'ebook', 'screen'],
    'acrobat': ['conservative', '300-dpi', '150-dpi'], 'boox-note': ['default-export'],
}
REQUIRED = ('page_geometry', 'page_order', 'text', 'bookmarks', 'links', 'page_labels', 'attachments',
            'forms', 'comments', 'layers', 'navigation_signature')
DEPENDENCIES = ('selenium', 'psutil', 'numpy', 'scipy', 'scikit-image', 'matplotlib',
                'pypdf', 'pypdfium2', 'Pillow', 'PyMuPDF')


class Progress:
    """Console/file stages and a heartbeat while a blocking operation runs."""
    def __init__(self):
        self.started = self.stage_started = time.monotonic()
        self.stage = 'Starting comparison'
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.stream = None
        self.pending = []

    def _write(self, message):
        line = f"{datetime.now().astimezone().isoformat(timespec='seconds')} | elapsed {time.monotonic()-self.started:.1f}s | {message}\n"
        print(line, end='', flush=True)
        if self.stream:
            self.stream.write(line)
            self.stream.flush()
        else:
            self.pending.append(line)

    def __call__(self, message):
        with self.lock:
            self.stage, self.stage_started = message, time.monotonic()
            self._write(message)

    def attach(self, path):
        with self.lock:
            self.stream = Path(path).open('x', encoding='utf-8')
            self.stream.writelines(self.pending)
            self.stream.flush()
            self.pending.clear()

    def _heartbeat(self):
        while not self.stop.wait(15):
            with self.lock:
                self._write(f'Waiting: {self.stage} | stage {time.monotonic()-self.stage_started:.1f}s')

    def __enter__(self):
        self.thread = threading.Thread(target=self._heartbeat, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join()
        if self.stream:
            self.stream.close()


def save_json(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)


def command_prefix(value):
    if not isinstance(value, list) or not value or not all(isinstance(x, str) and x for x in value):
        raise ValueError('Command prefixes must be nonempty arrays of strings')
    forbidden = {'cmd', 'powershell', 'pwsh', 'sh', 'bash', 'zsh', 'csh', 'wscript', 'cscript'}
    for item in value:
        if Path(item).suffix.lower() in {'.cmd', '.bat', '.ps1', '.sh'}:
            raise ValueError('Shell/batch wrappers are unsupported; use python.exe script.py or node.exe cli.js')
    if Path(value[0]).stem.lower() in forbidden:
        raise ValueError('Shell interpreters are unsupported')
    resolved = shutil.which(value[0])
    if resolved and Path(resolved).suffix.lower() in {'.cmd', '.bat', '.ps1', '.sh'}:
        raise ValueError('Resolved executable is a shell wrapper; use a direct runtime')
    return value


def load_config(path):
    with Path(path).open(encoding='utf-8') as stream:
        config = json.load(stream)
    if not isinstance(config, dict) or set(config) - {'tools', 'imports'}:
        raise ValueError('Configuration must contain only tools and imports')
    if not isinstance(config.get('tools', {}), dict) or not isinstance(config.get('imports', []), list):
        raise ValueError('tools must be an object and imports an array')
    for tool, spec in config.get('tools', {}).items():
        if tool not in PROFILES or tool in {'repository', 'acrobat', 'boox-note'}:
            raise ValueError(f'Unsupported executable tool: {tool}; use imports for upstream/Acrobat outputs')
        required = {'command', 'version', 'help_args'}
        if (not isinstance(spec, dict) or not required <= set(spec)
                or set(spec) - required not in ({'version_args'}, {'version_command'})):
            raise ValueError(f'{tool} needs command, version, help_args and either version_args or version_command')
        command_prefix(spec['command'])
        if 'version_command' in spec:
            command_prefix(spec['version_command'])
        for key in ('help_args', 'version_args'):
            if key not in spec:
                continue
            if not isinstance(spec[key], list) or not all(isinstance(x, str) for x in spec[key]):
                raise ValueError(f'{tool}.{key} must be an argument array')
        if not isinstance(spec['version'], str) or not spec['version'].strip():
            raise ValueError('Pin a tool version or commit appearing in version output')
    keys = set()
    for entry in config.get('imports', []):
        if not isinstance(entry, dict) or not {'source_sha256', 'tool', 'profile', 'path', 'settings', 'version'} <= set(entry):
            raise ValueError('Imports need source_sha256, tool, profile, path, settings and version')
        if entry['tool'] not in PROFILES or entry['profile'] not in PROFILES[entry['tool']]:
            raise ValueError('Unknown imported profile')
        digest = entry['source_sha256']
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('source_sha256 must be a lowercase SHA-256')
        if not entry['settings'] or not entry['version'] or not isinstance(entry['path'], str):
            raise ValueError('Record import settings, version and path')
        if entry['tool'] == 'boox-note' and not {'note_path', 'note_sha256'} <= set(entry):
            raise ValueError('BOOX Note imports need matching note_path and note_sha256')
        key = (digest, entry['tool'], entry['profile'])
        if key in keys:
            raise ValueError('Duplicate imported profile for source hash')
        keys.add(key)
    return config


def run_process(command, directory, name, timeout=1800):
    """Owned trees only; logs and operation records survive timeouts and interruption."""
    import psutil
    command_prefix(command[:1])
    directory = Path(directory)
    record = {'command': list(map(str, command)), 'cwd': str(directory),
              'timeout_s': timeout, 'started_utc': datetime.now(timezone.utc).isoformat()}
    processes = {}
    started = time.monotonic()
    process = None
    try:
        with (directory / f'{name}.stdout.log').open('xb') as stdout, (directory / f'{name}.stderr.log').open('xb') as stderr:
            process = subprocess.Popen(record['command'], cwd=directory, shell=False, stdout=stdout,
                stderr=stderr, stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            parent = psutil.Process(process.pid)
            processes[parent.pid] = parent
            while process.poll() is None:
                with contextlib.suppress(psutil.NoSuchProcess):
                    for child in parent.children(recursive=True):
                        processes[child.pid] = child
                if time.monotonic() - started > timeout:
                    record['status'] = 'timeout'
                    break
                time.sleep(.05)
            else:
                record['status'] = 'completed'
            if record['status'] == 'timeout':
                firefox_measure.kill_owned(list(processes.values()))
            record['exit_code'] = process.wait(timeout=10)
    except KeyboardInterrupt:
        record['status'] = 'interrupted'
        raise
    except Exception as exc:
        record.update(status='failed', error=f'{type(exc).__name__}: {exc}')
    finally:
        firefox_measure.kill_owned(list(processes.values()))
        if process:
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)
        record['duration_s'] = time.monotonic() - started
        record['logs'] = [f'{name}.stdout.log', f'{name}.stderr.log']
        save_json(directory / f'{name}.json', record)
    return record


def commands(tool, profile, source, output):
    a, b = str(source), str(output)
    if tool == 'repository':
        flags = {'viewing-copy': ['--viewing-copy'], 'default': [], 'compact': ['--compact-structure'],
            'inline': ['--inline-handwriting'], 'inline+compact': ['--inline-handwriting', '--compact-structure'],
            'fast-handwriting': ['--fast-handwriting'],
            'fast-handwriting+compact': ['--fast-handwriting', '--compact-structure']}
        return [sys.executable, str(ROOT / 'optimize_pdf.py'), a, b] + flags[profile], []
    if tool == 'boox-pdf':
        kind, value = profile.split('-')
        flags = ['--precision', value] if kind == 'vector' else ['--mode', 'rasterize', '--dpi', value]
        if kind == 'gray':
            flags += ['--grayscale']
        return [a, '-o', b] + flags, ['--precision', '--output'] if kind == 'vector' else ['--mode', '--dpi', '--output'] + (['--grayscale'] if kind == 'gray' else [])
    if tool == 'qpdf':
        flags = ['--recompress-flate', '--compression-level=9', '--object-streams=generate', '--stream-data=compress']
        if profile == 'flate-linearized':
            flags += ['--linearize']
        return [a] + flags + [b], [x.split('=')[0] for x in flags]
    if tool == 'mupdf':
        flags = ['-gggg', '-z', '-f', '-i', '-m', '--structure=keep']
        if profile == 'compress-packed':
            flags += ['-Z']
        return ['clean'] + flags + [a, b], ['-g', '-z', '-f', '-i', '-m', '--structure'] + (['-Z'] if '-Z' in flags else [])
    if tool == 'pdfsizeopt':
        return [a, b], []
    if tool == 'ocrmypdf':
        return ['--output-type', 'pdf', '--skip-text', '--optimize', profile[-1], a, b], ['--output-type', '--skip-text', '--optimize']
    if tool == 'mattdesl':
        flags = [] if profile == 'default' else ['--quality', '95' if profile == 'quality-95' else '85',
                   '--dpi', '0' if profile == 'quality-95' else '200', '--no-blank-offpage']
        return [a, '-o', b] + flags, ['--output'] + (['--quality', '--dpi', '--no-blank-offpage'] if flags else [])
    if tool == 'ghostscript':
        flags = ['-sDEVICE=pdfwrite', '-dBATCH', '-dNOPAUSE', '-dSAFER']
        if profile != 'default':
            flags += ['-dPDFSETTINGS=/' + profile]
        return flags + ['-sOutputFile=' + b, a], ['pdfwrite']
    raise ValueError('This workflow requires a locally generated import')


def preflight(config, directory, firefox, progress=None):
    progress = progress or (lambda message: None)
    progress('Preflight: dependencies and configured tools')
    result = {'dependencies': {}, 'tools': {}, 'environment': {
        'python': sys.version, 'platform': platform.platform(), 'node': os.environ.get('PDF_NODE') or shutil.which('node'),
        'repository_commit': subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip(),
        'repository_dirty': bool(subprocess.run(['git', '-C', str(ROOT), 'status', '--porcelain'], capture_output=True, text=True).stdout.strip())}}
    for name in DEPENDENCIES:
        try:
            result['dependencies'][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result['dependencies'][name] = None
    result['imports'] = [{'tool': entry['tool'], 'profile': entry['profile'],
                          'source_sha256': entry['source_sha256'],
                          'exists': Path(entry['path']).is_file()}
                         for entry in config.get('imports', [])]
    if result['dependencies']['psutil'] is None:
        result.update(pdfjs={'status': 'not tested', 'reason': 'Install requirements-comparison.txt'},
                      firefox={'status': 'unavailable', 'error': 'Install requirements-comparison.txt'})
        return result
    for tool, spec in config.get('tools', {}).items():
        progress(f'Preflight: {tool} version and command interface')
        work = directory / tool
        work.mkdir()
        version_command = spec.get('version_command') or spec['command'] + spec['version_args']
        version = run_process(version_command, work, 'version', 30)
        help_record = run_process(spec['command'] + spec['help_args'], work, 'help', 30)
        version_text = '\n'.join((work / f'version.{kind}.log').read_text(errors='replace') for kind in ('stdout', 'stderr'))
        help_text = '\n'.join((work / f'help.{kind}.log').read_text(errors='replace') for kind in ('stdout', 'stderr'))
        result['tools'][tool] = {'version_probe': version, 'help_probe': help_record,
            'version_pinned': spec['version'] in version_text and version.get('status') == 'completed' and version.get('exit_code') == 0,
            'version_text': version_text, 'help_text': help_text, 'declared_version': spec['version'],
            'prefix_hashes': {str(Path(p).resolve()): filehash(p)
                              for p in [shutil.which(spec['command'][0]) or spec['command'][0]] + spec['command'][1:]
                              if Path(p).is_file()}}
    node = result['environment']['node']
    result['pdfjs'] = run_process([node or 'node', str(ROOT / 'pdfjs_benchmark.mjs'), '-', '1', 'preflight'], directory, 'pdfjs-preflight', 30)
    progress('Preflight: native Firefox fixture smoke test')
    result['firefox'] = firefox_measure.smoke(firefox, directory, progress=progress)
    progress(f"Preflight: Firefox {result['firefox']['status']}")
    return result


def annotation_signatures(reader, category='comments'):
    from annotation_ink import eligible, has_interactive_data
    graph = Graph(reader)
    def group(a):
        if eligible(a) is not None or (a.get('/Subtype') == '/Ink' and not has_interactive_data(a)):
            return 'ink'
        if a.get('/Subtype') == '/Stamp' and a.get('/Name') == '/#ONYX-IMAGE' and not has_interactive_data(a):
            return 'image_stamps'
        return 'comments'
    return [[graph.value(a.get_object()) for a in p.get('/Annots', [])
             if group(a.get_object()) == category and a.get_object().get('/Subtype') != '/Link']
            for p in reader.pages]


def independent_checks(source, output, progress=None):
    """Each check reopens both PDFs so an unsupported representation cannot hide other evidence."""
    def leaves(value):
        if isinstance(value, dict):return sum(leaves(item) for item in value.values())
        if isinstance(value, (list, tuple)):return sum(leaves(item) for item in value)
        return int(value not in (None, ''))
    def inventory(name, value):
        present = True if name in {'page_geometry', 'page_order', 'navigation_signature',
                                   'geometry_resource_signature'} else bool(leaves(value))
        return {'items': leaves(value), 'sha256': hashlib.sha256(repr(value).encode()).hexdigest(),
                'presence': 'present' if present else 'absent'}
    def geometries(r):
        return [([float(x) for x in p.mediabox], [float(x) for x in p.cropbox],
                 p.rotation, float(p.get('/UserUnit', 1))) for p in r.pages]
    def text(r):
        return [(p.extract_text() or '').replace('\r\n', '\n').replace('\r', '\n') for p in r.pages]
    def bookmarks(r):
        def walk(items):
            return [walk(item) if isinstance(item, list) else
                (str(item.title), r.get_destination_page_number(item),
                 {str(k): str(v) for k, v in item.items() if k != '/Page'}) for item in items]
        return walk(r.outline)
    def links(r):
        g = Graph(r)
        result = []
        for page in r.pages:
            items = []
            for ref in page.get('/Annots', []):
                annot = ref.get_object()
                if annot.get('/Subtype') != '/Link':
                    continue
                action = annot.get('/A', {}).get_object() if hasattr(annot.get('/A', {}), 'get_object') else annot.get('/A', {})
                items.append({'signature': g.value(annot), 'rect': [float(x) for x in annot.get('/Rect', [])],
                              'uri': str(action['/URI']) if '/URI' in action else None})
            result.append(items)
        return result
    def catalog(r, key):
        value = r.trailer['/Root'].get(key)
        return Graph(r).value(value) if value is not None else None
    functions = {
        'page_geometry': geometries, 'text': text, 'bookmarks': bookmarks, 'links': links,
        'page_order': lambda r: [hashlib.sha256(repr(page_signature(r, i)).encode()).hexdigest() for i in range(len(r.pages))],
        'page_labels': lambda r: r.page_labels,
        'attachments': lambda r: {k: [hashlib.sha256(v).hexdigest() for v in values] for k, values in r.attachments.items()},
        'forms': lambda r: catalog(r, '/AcroForm'), 'comments': annotation_signatures,
        'layers': lambda r: catalog(r, '/OCProperties'),
        'annotation_editability': lambda r: annotation_signatures(r, 'ink'),
        'image_stamp_annotations': lambda r: annotation_signatures(r, 'image_stamps'),
        'navigation_signature': navigation_fingerprint,
        'geometry_resource_signature': lambda r: [hashlib.sha256(repr(page_signature(r, i)).encode()).hexdigest() for i in range(len(r.pages))],
    }
    checks = {}
    for name, function in functions.items():
        if progress:
            progress(f'Preservation check: {name}')
        try:
            with reader_for(source) as a, reader_for(output) as b:
                before, after = function(a), function(b)
            if before == after:
                status = 'pass'
            elif name in {'page_geometry', 'page_labels', 'attachments'}:
                status = 'confirmed difference'
            elif name == 'page_order':
                status = 'confirmed difference' if Counter(before) == Counter(after) else 'unproven'
                if status == 'unproven':
                    with reader_for(source) as a, reader_for(output) as b:
                        old_text, new_text = text(a), text(b)
                    if old_text == new_text and all(s.strip() for s in old_text) and len(set(old_text)) == len(old_text):
                        status = 'pass'
            elif name == 'text':
                old, new = Counter(''.join(before)), Counter(''.join(after))
                # Different extraction order is evidence, never automatic semantic loss.
                status = 'unproven'
                if old != new:
                    import pymupdf
                    with pymupdf.open(source) as ma, pymupdf.open(output) as mb:
                        ma_text = Counter(''.join(p.get_text() for p in ma))
                        mb_text = Counter(''.join(p.get_text() for p in mb))
                    # Two independent extractors must agree on a changed character multiset.
                    if ((old-new) == (ma_text-mb_text) and (new-old) == (mb_text-ma_text)
                            or old and not new and ma_text and not mb_text):
                        status = 'confirmed difference'
            elif name in {'links', 'bookmarks', 'comments', 'annotation_editability'}:
                def leaves(value):
                    return sum(leaves(x) if isinstance(x, list) else 1 for x in value)
                status = 'confirmed difference' if leaves(after) < leaves(before) else 'unproven'
                if name == 'links' and len(before) == len(after):
                    for old_page, new_page in zip(before, after):
                        if len(old_page) == len(new_page) and any(
                            x['uri'] != y['uri'] or x['rect'] != y['rect'] for x, y in zip(old_page, new_page)):
                            status = 'confirmed difference'
            else:
                status = 'unproven'
            before_inventory, after_inventory = inventory(name, before), inventory(name, after)
            checks[name] = {'status': status, 'source_presence': before_inventory['presence'],
                            'before_inventory': before_inventory, 'after_inventory': after_inventory}
            if status != 'pass':
                checks[name].update(before=before, after=after,
                    note='Representation/extraction differences require inspection; exact signatures do not prove loss.')
        except Exception as exc:
            checks[name] = {'status': 'check failed', 'error': f'{type(exc).__name__}: {exc}'}
    # Annotation conversion may be allowed, but an external rewrite is not given a fabricated manifest.
    return checks


def render_worker(path, page, scale, destination, crop=None):
    from viewing_copy import render
    with render(path, page - 1, scale, crop) as image:
        image.save(destination)


def rendered(path, page, scale, directory, name, crop=None, cache=None, cache_context=None):
    from PIL import Image
    target = directory / (name + '.png')
    if cache is not None and cache.materialize(cache_context, target):
        with Image.open(target) as image:
            return image.convert('RGB')
    command = [sys.executable, str(Path(__file__).resolve()), '--render-worker', str(path), str(page), str(scale), str(target), json.dumps(crop)]
    result = run_process(command, directory, name, 300)
    if result.get('exit_code') != 0 or not target.is_file():
        raise RuntimeError(f'Render failed: {name}; see operation logs')
    if cache is not None:
        cache.store_file(cache_context, target)
    with Image.open(target) as image:
        return image.convert('RGB')


def firefox_cache_context(source_sha256, candidate_sha256, count, samples, repetition,
                          order, position, metadata):
    return {'operation': 'native-firefox', 'source_sha256': source_sha256,
            'candidate_sha256': candidate_sha256, 'page': sorted(samples), 'crop': None,
            'renderer': 'firefox-pdfjs',
            'renderer_version': {key: metadata.get(key) for key in
                ('firefox', 'geckodriver', 'pdfjs', 'userAgent')},
            'geometry': {'pages': count, 'sample_pages': sorted(samples)},
            'resolution': {'viewport': metadata.get('viewport'),
                           'devicePixelRatio': metadata.get('devicePixelRatio')},
            'background': 'viewer-default', 'thresholds': [],
            'metric_version': filehash(ROOT/'tools'/'firefox_measure.py'),
            'repetition': repetition, 'order': order, 'position': position,
            'rendering_preferences': metadata.get('rendering_preferences')}


def analyze(source, output, directory, node, checkpoint, result=None, progress=None, cache=None):
    progress = progress or (lambda message: None)
    from comparison_metrics import image_metrics, flagged_regions, evidence
    from feature_loss import image_stamp_loss
    import numpy as np
    pages = []
    regions = []
    result = result if result is not None else {}
    result.update(pages=pages, regions=regions,
                  analysis='foreground analysis; handwriting isolation uncertain',
                  topology_note='Topology flags are potential defects; antialiasing can change skeletons.')
    source_hash, output_hash = filehash(source), filehash(output)
    renderer_version = importlib.metadata.version('pypdfium2')
    render_version = filehash(ROOT/'viewing_copy.py')
    metric_version = filehash(ROOT/'tools'/'comparison_metrics.py')
    def context(operation, page, crop, geometry, resolution, renderer='pdfium'):
        return {'operation': operation, 'source_sha256': source_hash,
                'candidate_sha256': output_hash, 'page': page, 'crop': crop,
                'renderer': renderer, 'renderer_version': renderer_version,
                'geometry': repr(geometry), 'resolution': resolution,
                'background': 'white', 'thresholds': [200, 225, 250],
                'metric_version': metric_version if operation == 'image-metrics' else render_version}
    def metrics(context_value, before, after, size):
        cached = cache.load_json(context_value) if cache is not None else None
        if cached is not None:return cached
        value = image_metrics(before, after, size)
        if cache is not None:cache.store_json(context_value, value)
        return value
    with reader_for(source) as reader:
        count = len(reader.pages)
    with reader_for(output) as reader:
        out_count = len(reader.pages)
    for number in range(1, count + 1):
        progress(f'Image analysis: page {number}/{count}, geometry')
        page_result = {'page': number}
        pages.append(page_result)
        if number > out_count:
            page_result['status'] = 'confirmed difference: missing page'
            checkpoint({'kind': 'page-analysis', 'page': page_result})
            continue
        with reader_for(source) as a, reader_for(output) as b:
            pa, pb = a.pages[number-1], b.pages[number-1]
            ga = (tuple(pa.mediabox), tuple(pa.cropbox), pa.rotation, pa.get('/UserUnit', 1))
            gb = (tuple(pb.mediabox), tuple(pb.cropbox), pb.rotation, pb.get('/UserUnit', 1))
            rect = [float(x) for x in pa.cropbox]
            unit = float(pa.get('/UserUnit', 1))
            width, height = (rect[2]-rect[0])*unit, (rect[3]-rect[1])*unit
            if pa.rotation % 180:
                width, height = height, width
            from annotation_ink import eligible
            recognized = []
            for ref in pa.get('/Annots', []):
                annot = ref.get_object()
                if eligible(annot) is not None or annot.get('/Subtype') == '/Ink':
                    recognized.append({'rect_pdf_points': [float(v) for v in annot.get('/Rect', [])],
                                       'subtype': str(annot.get('/Subtype'))})
            page_result['source_handwriting_regions'] = recognized
            source_images = image_stamp_rectangles(pa)
            page_result['region_identification'] = ('original handwriting annotation appearance bounds; foreground overlap remains uncertain'
                                                     if recognized else 'foreground fallback; handwriting structures not isolated')
            from pdf_characterize import characterize_page
            source_inventory = characterize_page(a, pa, number)
            source_content = source_inventory['content']
            page_result['source_vector_features'] = {
                'coverage': source_inventory['coverage'], 'issues': source_inventory['issues'],
                'filled_outlines': source_content.get('filled_outlines', 0),
                'zero_length_segments': source_content.get('zero_length_segments', 0),
                'lines': source_content.get('lines', 0),
                'cubic_curves': source_content.get('cubic_curves', 0),
                'same_width_runs': source_content.get('same_width_runs', 0),
                'declared_line_widths': source_inventory['line_widths']['declared']}
        if ga != gb:
            page_result['status'] = 'confirmed difference: geometry changed; pixel comparison unproven'
            checkpoint({'kind': 'page-analysis', 'page': page_result})
            continue
        work = directory / f'page-{number:04}'
        work.mkdir()
        try:
            progress(f'Image analysis: page {number}/{count}, PDFium 144 DPI and local regions')
            source_144 = context('render', number, None, ga, {'dpi': 144, 'scale': 2}) | {'candidate_sha256': source_hash}
            output_144 = context('render', number, None, gb, {'dpi': 144, 'scale': 2}) | {'source_sha256': output_hash}
            metric_144 = context('image-metrics', number, None, [ga, gb], {'dpi': 144, 'scale': 2})
            with rendered(source, number, 2, work, 'source-144', cache=cache, cache_context=source_144) as a, rendered(output, number, 2, work, 'output-144', cache=cache, cache_context=output_144) as b:
                page_result['pdfium_144'] = metrics(metric_144, a, b, (width*25.4/72, height*25.4/72))
                page_result['visual_feature_loss'] = image_stamp_loss(a, b, source_images, rect, ga[2])
                boxes = flagged_regions(a, b)
                # Preserve all flagged tiles; rankings never discard a tiny dot.
                for index, box in enumerate(boxes):
                    with a.crop(box) as ca, b.crop(box) as cb:
                        region_context = context('image-metrics', number, {'bbox_144_pixels': box}, [ga, gb], {'dpi': 144, 'scale': 2})
                        region_metrics = metrics(region_context, ca, cb, ((box[2]-box[0])/a.width*width*25.4/72,
                                                                        (box[3]-box[1])/a.height*height*25.4/72))
                    region = {'page': number, 'bbox_144_pixels': box,
                        'bbox_display_points': [box[0]/a.width*width, box[1]/a.height*height,
                                                box[2]/a.width*width, box[3]/a.height*height],
                        'metrics_144': region_metrics, 'id': f'p{number}-{index}', 'resolution_dpi': 144}
                    regions.append(region)
            # Every page gets an independent PDF.js comparison at the existing width.
            js_images = []
            progress(f'Image analysis: page {number}/{count}, standalone PDF.js')
            for which, path in [('source', source), ('output', output)]:
                target = work / f'{which}-pdfjs.png'
                operation = run_process([node or 'node', str(ROOT/'pdfjs_benchmark.mjs'), str(path), str(number), 'render', '1404', str(target)], work, f'{which}-pdfjs', 300)
                if operation.get('exit_code') != 0:
                    raise RuntimeError('Standalone PDF.js render unavailable; see logs')
                js_images.append(target)
            from PIL import Image
            with Image.open(js_images[0]) as a, Image.open(js_images[1]) as b:
                page_result['pdfjs'] = {'status': 'pass', 'exact': a.size == b.size and a.tobytes() == b.tobytes()}
            # Recheck changed pages and one representative detail on every page at 288 DPI.
            progress(f'Image analysis: page {number}/{count}, PDFium 288 DPI')
            source_288 = context('render', number, None, ga, {'dpi': 288, 'scale': 4}) | {'candidate_sha256': source_hash}
            output_288 = context('render', number, None, gb, {'dpi': 288, 'scale': 4}) | {'source_sha256': output_hash}
            metric_288 = context('image-metrics', number, None, [ga, gb], {'dpi': 288, 'scale': 4})
            with rendered(source, number, 4, work, 'source-288', cache=cache, cache_context=source_288) as a, rendered(output, number, 4, work, 'output-288', cache=cache, cache_context=output_288) as b:
                page_result['pdfium_288'] = metrics(metric_288, a, b, (width*25.4/72, height*25.4/72))
            checkpoint({'kind': 'page-analysis', 'page': page_result})
        except Exception as exc:
            page_result.update(status='check failed', error=f'{type(exc).__name__}: {exc}')
            checkpoint({'kind': 'page-analysis', 'page': page_result})
    def priority(region):
        m = region['metrics_144']['thresholds']['225']
        return (bool(m['potential_missing_components'] or m['potential_topology_change']),
                m['stroke_displacement_mm']['max'] or 0,
                m['thickness_absolute_change_mm']['max'] or 0,
                region['metrics_144']['contrast_difference']['max'] or 0,
                region['metrics_144']['rgb_foreground_absolute_difference']['max'] or 0)
    regions.sort(key=priority, reverse=True)
    for index, region in enumerate(regions):
        progress(f"Detail evidence: region {index+1}/{len(regions)}, page {region['page']}, 576 DPI")
        # Render true 576-DPI detail crops, never upscale the low-resolution evidence.
        number = region['page']
        x0, y0, x1, y1 = region['bbox_display_points']
        with reader_for(source) as reader:
            p = reader.pages[number-1]
            unit = float(p.get('/UserUnit', 1))
            w, h = float(p.cropbox.width)*unit, float(p.cropbox.height)*unit
            if p.rotation % 180:
                w, h = h, w
        crop = [x0, h-y1, w-x1, y0]
        work = directory / f"detail-{region['id']}"
        work.mkdir()
        try:
            source_576 = context('render', number, crop, [w, h], {'dpi': 576, 'scale': 8}) | {'candidate_sha256': source_hash}
            output_576 = context('render', number, crop, [w, h], {'dpi': 576, 'scale': 8}) | {'source_sha256': output_hash}
            metric_576 = context('image-metrics', number, crop, [w, h], {'dpi': 576, 'scale': 8})
            with rendered(source, number, 8, work, 'source-576', crop, cache, source_576) as a, rendered(output, number, 8, work, 'output-576', crop, cache, output_576) as b:
                region['metrics_576'] = metrics(metric_576, a, b, ((x1-x0)*25.4/72, (y1-y0)*25.4/72))
                region['artifacts'] = {key: str((work/value).relative_to(directory)) for key, value in evidence(a, b, work, 'detail').items()}
        except Exception as exc:
            region['evidence_error'] = str(exc)
        checkpoint({'kind': 'detail-analysis', 'region': region})
    if not regions and count:
        progress('Detail evidence: representative crop, 576 DPI')
        # Exact output still supplies a representative high-resolution detail.
        with reader_for(source) as reader:
            p = reader.pages[0]
            unit = float(p.get('/UserUnit', 1))
            w, h = float(p.cropbox.width)*unit, float(p.cropbox.height)*unit
            if p.rotation % 180:
                w, h = h, w
        work = directory / 'representative'
        work.mkdir()
        from PIL import Image
        with Image.open(directory/'page-0001'/'source-144.png') as image:
            foreground = np.dot(np.asarray(image.convert('RGB')), [.299, .587, .114]) < 250
            ys, xs = np.nonzero(foreground)
            x0 = min(max(0, (float(xs[0])/image.width*w - 12) if len(xs) else 0), max(0, w-96))
            y0 = min(max(0, (float(ys[0])/image.height*h - 12) if len(ys) else 0), max(0, h-96))
        crop = [x0, max(0, h-y0-96), max(0, w-x0-96), y0]
        try:
            source_576 = context('render', 1, crop, [w, h], {'dpi': 576, 'scale': 8}) | {'candidate_sha256': source_hash}
            output_576 = context('render', 1, crop, [w, h], {'dpi': 576, 'scale': 8}) | {'source_sha256': output_hash}
            with rendered(source, 1, 8, work, 'source-576', crop, cache, source_576) as a, rendered(output, 1, 8, work, 'output-576', crop, cache, output_576) as b:
                pages[0]['representative_576_exact'] = a.size == b.size and a.tobytes() == b.tobytes()
        except Exception as exc:
            pages[0]['representative_576_error'] = str(exc)
        checkpoint({'kind': 'representative-detail', 'page': pages[0]})
    result['gallery'] = [r['id'] for r in regions[:10]]
    if cache is not None:result['cache'] = dict(cache.stats)
    result['document_ink_summary'] = {}
    for threshold in ('200', '225', '250'):
        valid = [p['pdfium_144']['thresholds'][threshold] for p in pages
                 if 'thresholds' in p.get('pdfium_144', {})]
        original_area = sum(m['original_foreground_pixels'] for m in valid)
        summary = {'original_foreground_pixels': original_area, 'analyzed_pages': len(valid), 'tolerances_mm': {}}
        for tolerance in ('0', '0.025', '0.05', '0.1'):
            missing = sum(m['tolerances_mm'][tolerance]['missing_pixels'] for m in valid)
            added = sum(m['tolerances_mm'][tolerance]['added_pixels'] for m in valid)
            summary['tolerances_mm'][tolerance] = {'missing_pixels': missing, 'added_pixels': added,
                'missing_fraction': missing/original_area if original_area else None,
                'added_fraction': added/original_area if original_area else None}
        source_total = sum(m['matched_skeleton_coverage']['source']['total'] for m in valid)
        source_matched = sum(m['matched_skeleton_coverage']['source']['matched'] for m in valid)
        output_total = sum(m['matched_skeleton_coverage']['output']['total'] for m in valid)
        output_matched = sum(m['matched_skeleton_coverage']['output']['matched'] for m in valid)
        summary['matching_radius_mm'] = valid[0]['matching_radius_mm'] if valid else .1
        summary['matched_skeleton_coverage'] = {
            'source': {'matched': source_matched, 'total': source_total,
                       'fraction': source_matched/source_total if source_total else None},
            'output': {'matched': output_matched, 'total': output_total,
                       'fraction': output_matched/output_total if output_total else None}}
        summary['source_feature_counts'] = {
            'components': sum(m['source_features']['component_count'] for m in valid),
            'dot_candidates': sum(m['source_features']['dot_candidates'] for m in valid),
            'fraction_bar_candidates': sum(m['source_features']['fraction_bar_candidates'] for m in valid),
            'crossing_or_join_components': sum(m['source_features']['crossing_or_join_components'] for m in valid),
            'loop_components': sum(m['source_features']['loop_components'] for m in valid)}
        result['document_ink_summary'][threshold] = summary
    return result


def classification(row):
    if row.get('status') not in {'usable', 'original selected'}:
        return 'Unavailable/failed'
    checks = row.get('checks', {})
    if any(checks.get(k, {}).get('status') == 'confirmed difference' for k in REQUIRED):
        return 'Feature-loss diagnostic'
    if any(p.get('visual_feature_loss', {}).get('region_counts', {}).get('blanked', 0)
           for p in row.get('appearance', {}).get('pages', [])):
        return 'Feature-loss diagnostic'
    if not all(checks.get(k, {}).get('status') == 'pass' for k in REQUIRED):
        return 'Unproven'
    appearance = row.get('appearance', {})
    pages = appearance.get('pages', [])
    if not pages or any(p.get('status') for p in pages):
        return 'Unproven'
    if any(r.get('evidence_error') for r in appearance.get('regions', [])) or any('representative_576_error' in p for p in pages):
        return 'Unproven'
    if all(p.get('pdfium_144', {}).get('exact') and p.get('pdfium_288', {}).get('exact')
           and p.get('pdfjs', {}).get('exact') for p in pages):
        return 'Strict preservation candidate' if pages[0].get('representative_576_exact') else 'Unproven'
    return 'Potential visual tradeoff'


def quality_summary(row):
    measurements = [p['pdfium_144']['thresholds']['225'] for p in row.get('appearance', {}).get('pages', [])
                    if 'thresholds' in p.get('pdfium_144', {})]
    if not measurements:
        return {}
    def maximum(values):
        values = [v for v in values if v is not None]
        return max(values) if values else None
    def minimum(values):
        values = [v for v in values if v is not None]
        return min(values) if values else None
    pages = [p.get('pdfium_144', {}) for p in row.get('appearance', {}).get('pages', [])]
    return {'worst_page_missing_fraction_005mm': maximum(m['tolerances_mm']['0.05']['missing_fraction'] for m in measurements),
            'worst_page_added_fraction_005mm': maximum(m['tolerances_mm']['0.05']['added_fraction'] for m in measurements),
            'worst_page_displacement_p95_mm': maximum(m['stroke_displacement_mm']['p95'] for m in measurements),
            'worst_page_thickness_change_p95_mm': maximum(m['thickness_absolute_change_mm']['p95'] for m in measurements),
            'worst_page_thinning_p05_mm': minimum(m['thickness_change_mm']['p05'] for m in measurements),
            'worst_page_thickening_p95_mm': maximum(m['thickness_change_mm']['p95'] for m in measurements),
            'worst_page_contrast_difference_p95': maximum(
                p.get('contrast_difference', {}).get('p95') for p in pages),
            'worst_page_rgb_difference_p95': maximum(
                p.get('rgb_foreground_absolute_difference', {}).get('p95') for p in pages),
            'minimum_source_matched_skeleton_fraction': min(
                (m['matched_skeleton_coverage']['source']['fraction'] for m in measurements
                 if m['matched_skeleton_coverage']['source']['fraction'] is not None), default=None),
            'minimum_output_matched_skeleton_fraction': min(
                (m['matched_skeleton_coverage']['output']['fraction'] for m in measurements
                 if m['matched_skeleton_coverage']['output']['fraction'] is not None), default=None),
            'pages_with_threshold_sensitive_findings': sum(
                p.get('threshold_sensitive', False) for p in pages),
            'source_components_analyzed': sum(m['source_features']['component_count'] for m in measurements),
            'pages_with_potential_shape_changes': sum(m['potential_topology_change'] for m in measurements)}


def report(directory, state):
    try:
        plots(directory, state)
    except Exception as exc:
        state['plot_error'] = f'{type(exc).__name__}: {exc}'
    lines = ['# PDF optimizer comparison', '',
        'Measurements are descriptive. Altered handwriting requires human review. No aggregate quality score is used.', '',
        'Fresh sessions do not imply cold OS caches; sampled memory is not guaranteed peak memory.', '',
        'Foreground analysis; handwriting isolation uncertain. Thresholds: 200/225/250. Ink tolerances: 0/0.025/0.05/0.1 mm.', '']
    pre = state['preflight']
    lines += ['## Preflight', '', f"Native Firefox: **{pre['firefox']['status']}**.", '',
              pre['firefox'].get('error', 'Generated native-viewer smoke fixture passed.'), '',
              '| Tool | Preflight |', '|---|---|']
    for tool in PROFILES:
        probe = pre.get('tools', {}).get(tool)
        status = 'repository commit recorded' if tool == 'repository' else (
            'version pin matched; per-profile help checked at execution' if probe and probe['version_pinned'] else
            'unverified version/interface' if probe else 'not configured; import or executable required')
        lines += [f'| {tool} | {status} |']
    lines += ['', 'Dependency versions: `' + json.dumps(pre['dependencies']) + '`.', '']
    flat = []
    for doc in state['documents']:
        lines += [f"## {doc['id']}", '', f"Source SHA-256: `{doc.get('source_sha256', 'unavailable')}`", '']
        if doc.get('error'):
            lines += [doc['error'], '']
        lines += ['| Tool/profile | Status | Recommendation class | Bytes |', '|---|---|---|---:|']
        for row in doc.get('outputs', []):
            row['recommendation'] = classification(row)
            row['feature_loss'] = summarize_feature_loss(row.get('checks', {}), row.get('appearance'))
            lines.append(f"| {row['tool']}/{row['profile']} | {row['status']} | {row['recommendation']} | {row.get('bytes', '')} |")
            flat.append({'document': doc['id'], 'tool': row['tool'], 'profile': row['profile'],
                'status': row['status'], 'recommendation': row['recommendation'], 'bytes': row.get('bytes'),
                'optimization_s': row.get('execution', {}).get('duration_s'), 'source_sha256': doc.get('source_sha256'),
                'output_sha256': row.get('output_sha256'), 'reason': row.get('reason', ''),
                'functional_feature_changes': len(row['feature_loss']['functional_confirmed_changes']),
                'functional_features_absent': len(row['feature_loss']['functional_absent_in_source']),
                'functional_features_unresolved': len(row['feature_loss']['functional_unresolved']),
                'source_image_regions_blanked_fraction': row['feature_loss'].get('visual_source_features', {}).get('blanked_region_fraction'),
                'source_image_regions_changed_fraction': row['feature_loss'].get('visual_source_features', {}).get('changed_region_fraction'),
                'firefox_status': 'unavailable/partial' if row.get('firefox_error') else 'measured' if row.get('firefox_summary') else 'not tested',
                'firefox_timings_json': json.dumps(row.get('firefox_summary', {})),
                **{k: quality_summary(row).get(k) for k in (
                    'worst_page_missing_fraction_005mm', 'worst_page_added_fraction_005mm',
                    'worst_page_displacement_p95_mm', 'worst_page_thickness_change_p95_mm',
                    'worst_page_thinning_p05_mm', 'worst_page_thickening_p95_mm',
                    'worst_page_contrast_difference_p95', 'worst_page_rgb_difference_p95',
                    'minimum_source_matched_skeleton_fraction', 'minimum_output_matched_skeleton_fraction',
                    'pages_with_threshold_sensitive_findings', 'source_components_analyzed',
                    'pages_with_potential_shape_changes')}})
        lines += ['', '### Evidence and limitations', '']
        for row in doc.get('outputs', []):
            label = row['tool'] + '/' + row['profile']
            lines += [f'#### {label}', '', row.get('reason', ''), '']
            features = row['feature_loss']
            lines += [f"Feature checks: {len(features['functional_preserved'])} preserved; {len(features['functional_absent_in_source'])} absent in source; {len(features['functional_confirmed_changes'])} confirmed changes; {len(features['functional_unresolved'])} unresolved.", '']
            if features.get('visual_source_features'):
                visual = features['visual_source_features']
                lines += [f"Source image regions: {visual['region_counts']}; {visual['visible_source_regions']} visible regions assessed. Blanked means the entire region became white; changed regions are not automatically lost content.", '']
            summary = quality_summary(row)
            if summary:
                lines += ['Foreground summary at threshold 225 (all thresholds retained in JSON):', '']
                lines += [f'- {key}: {value}' for key, value in summary.items()]
                lines += ['']
                ink = row.get('appearance', {}).get('document_ink_summary', {}).get('225')
                if ink:
                    lines += ['Document foreground totals at threshold 225: `' + json.dumps(ink) + '`.', '']
            if row.get('checks'):
                lines += [f"- {key}: **{value['status']}**" for key, value in row['checks'].items()]
                lines += ['', 'Ink annotation editability may be lost; this is permitted only for viewing copies.', '']
            if row.get('firefox_error'):
                lines += ['Native Firefox unavailable: ' + row['firefox_error'], '']
            if row.get('firefox_summary'):
                lines += ['| Operation/page/zoom | Median ms | Range ms |', '|---|---:|---|']
                for key, value in row['firefox_summary'].items():
                    lines.append(f"| {key} | {value['median_ms']:.3f} | {value['range_ms']} |")
                lines += ['']
            for region in row.get('appearance', {}).get('regions', [])[:10]:
                lines += [f"Page {region['page']}, display points {region['bbox_display_points']}; [metrics](results.json).", '']
                for name, path in region.get('artifacts', {}).items():
                    link = Path(row['work']).relative_to(directory).as_posix() + '/' + path.replace('\\', '/')
                    lines += [f'![{label} {name}]({link})', '']
            if row.get('appearance'):
                lines += [f"All {len(row['appearance']['regions'])} flagged regions and all threshold/resolution measurements are indexed in results.json.", '']
        if doc.get('nondominated'):
            lines += ['### Nondominated choices by operation and metric', '',
                      'Axes: 0 = missing ink, 1 = displacement, 2 = thickness, 3 = shape flags. Feature-loss and unproven labels still apply.', '']
            for operation, axes in doc['nondominated'].items():
                lines += [f'- {operation}: {json.dumps(axes)}']
        for artifact in doc.get('plot_artifacts', []):
            lines += ['', f'![Speed and separate quality measurements]({artifact})', '']
    with (directory / 'report.md').open('x', encoding='utf-8') as stream:
        stream.write('\n'.join(lines))
    with (directory / 'summary.csv').open('x', newline='', encoding='utf-8') as stream:
        fields = list(flat[0]) if flat else ['document', 'tool', 'profile', 'status']
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(flat)
    save_json(directory / 'results.json', state)


def plots(directory, state):
    if not any(r.get('firefox_summary') for d in state['documents'] for r in d.get('outputs', [])):
        return
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    for doc in state['documents']:
        rows = doc.get('outputs', [])
        baseline = next((r for r in rows if r['tool'] == 'source'), {})
        baseline_summary = baseline.get('firefox_summary', {})
        plot_rows = [r for r in rows if r.get('appearance') and r.get('firefox_summary')]
        for operation, base in baseline_summary.items():
            if not base['resolved']:
                continue
            fig, axes = plt.subplots(1, 4, figsize=(18, 5))
            found = False
            for row in plot_rows:
                timing = row['firefox_summary'].get(operation)
                if not timing or not timing['resolved']:
                    continue
                pages = row['appearance']['pages']
                ms = [p['pdfium_144']['thresholds']['225'] for p in pages if 'thresholds' in p.get('pdfium_144', {})]
                if not ms:
                    continue
                def worst(values):
                    return max((v for v in values if v is not None), default=None)
                values = [worst(m['tolerances_mm']['0.05']['missing_fraction'] for m in ms),
                          worst(m['stroke_displacement_mm']['p95'] for m in ms),
                          worst(m['thickness_absolute_change_mm']['p95'] for m in ms),
                          sum(m['potential_topology_change'] for m in ms)]
                speed = base['median_ms'] / timing['median_ms']
                row.setdefault('tradeoffs', {})[operation] = {'speed_ratio': speed, 'quality_axes': values}
                label = f"{row['tool']}/{row['profile']} ({classification(row)})"
                for ax, value in zip(axes, values):
                    if value is None:
                        continue
                    ax.scatter(speed, value)
                    ax.annotate(label, (speed, value), fontsize=5)
                found = True
            for ax, title in zip(axes, ['Missing ink fraction at .05 mm', 'Displacement p95 mm', 'Thickness change p95 mm', 'Pages with shape flags']):
                ax.set(xlabel='Speed / original (higher is faster)', ylabel=title)
            if found:
                name = hashlib.sha256(operation.encode()).hexdigest()[:12]
                fig.suptitle(f"{doc['id']}: {operation}; foreground analysis")
                fig.tight_layout()
                fig.savefig(directory / f"{doc['id']}-{name}.png", dpi=160)
                doc.setdefault('plot_artifacts', []).append(f"{doc['id']}-{name}.png")
            plt.close(fig)
        # Nondomination is reported independently for each operation and quality axis.
        for operation in baseline_summary:
            candidates = [r for r in rows if operation in r.get('tradeoffs', {})]
            for axis in range(4):
                comparable = [r for r in candidates if r['tradeoffs'][operation]['quality_axes'][axis] is not None]
                choices = []
                for r in comparable:
                    point = r['tradeoffs'][operation]
                    if not any(o['tradeoffs'][operation]['speed_ratio'] >= point['speed_ratio']
                        and o['tradeoffs'][operation]['quality_axes'][axis] <= point['quality_axes'][axis]
                        and (o['tradeoffs'][operation]['speed_ratio'] > point['speed_ratio']
                             or o['tradeoffs'][operation]['quality_axes'][axis] < point['quality_axes'][axis])
                        for o in comparable):
                        choices.append(r['tool'] + '/' + r['profile'])
                doc.setdefault('nondominated', {}).setdefault(operation, {})[str(axis)] = choices


def execute(args):
    with Progress() as progress:
        return _execute(args, progress)


def _execute(args, progress):
    progress('Checking configuration and exclusive output destination')
    config = load_config(args.tools_config)
    directory = args.output.resolve()
    if directory.exists() or directory.is_symlink():
        raise FileExistsError('Output must not exist')
    # Preflight processes generated data only, even if --input was supplied.
    with tempfile.TemporaryDirectory(prefix='comparison-preflight-', dir=ROOT/'audit-output' if (ROOT/'audit-output').is_dir() else ROOT) as tmp:
        pre = preflight(config, Path(tmp), firefox_measure.discover(args.firefox), progress=progress)
        directory.mkdir(parents=True, exist_ok=False)
        shutil.copytree(tmp, directory/'preflight')
    progress.attach(directory/'progress.log')
    progress(f'Run directory: {directory}')
    cache_root = Path(args.cache).resolve() if getattr(args, 'cache', None) else directory/'cache'
    cache = EvidenceCache(cache_root)
    state = {'schema': 1, 'started_utc': datetime.now(timezone.utc).isoformat(),
             'configuration': config, 'preflight': pre, 'documents': [], 'status': 'running',
             'cache': {'root': str(cache_root)}}
    sequence = 0
    document_id = output_id = None
    def checkpoint(event):
        nonlocal sequence
        sequence += 1
        # Save each operation once; repeated full snapshots grow quadratically.
        save_json(directory / f'record-{sequence:06}.json', {
            'schema': 2, 'sequence': sequence, 'status': state['status'],
            'document': document_id, 'output': output_id, **event})
    checkpoint({'kind': 'run-start', 'configuration': config, 'preflight': pre})
    if args.preflight_only:
        progress('Writing preflight report')
        state['status'] = 'preflight only'
        report(directory, state)
        return state
    if any(v is None for v in pre['dependencies'].values()):
        state['status'] = 'missing comparison dependencies'
        report(directory, state)
        return state
    try:
        for doc_index, input_path in enumerate(args.input):
            progress(f'Document {doc_index+1}/{len(args.input)}: snapshot and input validation')
            doc = {'id': f'document-{doc_index+1:03}', 'outputs': []}
            document_id, output_id = doc['id'], None
            state['documents'].append(doc)
            work = directory / doc['id']
            work.mkdir()
            source = work/'source.pdf'
            try:
                input_path = input_path.resolve(strict=True)
                digest = filehash(input_path)
                with input_path.open('rb') as a, source.open('xb') as b:
                    shutil.copyfileobj(a, b)
                if filehash(source) != digest or filehash(input_path) != digest:
                    raise RuntimeError('Source changed during snapshot')
                with reader_for(source) as reader:
                    validate_input(reader)
                    count = len(reader.pages)
                    if not count:
                        raise ValueError('Input has no pages')
                doc.update(source_sha256=digest, source_snapshot=str(source.relative_to(directory)), pages=count)
            except Exception as exc:
                doc['error'] = f'{type(exc).__name__}: {exc}'
                progress(f"{doc['id']}: invalid input: {doc['error']}")
                checkpoint({'kind': 'invalid-input', 'error': doc['error']})
                continue
            firefox = firefox_measure.discover(args.firefox)
            native_ok = pre['firefox']['status'] == 'available'
            samples = {p for p in (1, 7, 29) if p <= count}
            if native_ok:
                try:
                    progress(f"{doc['id']}: original-only Firefox exploratory pass")
                    doc['exploratory_firefox'] = firefox_measure.measure(source, firefox, work, exploratory=True, progress=progress)
                    timings = [r for r in doc['exploratory_firefox']['observations'] if r['operation'] == 'first_visit']
                    samples.update(r['page'] for r in sorted(timings, key=lambda x: x['ms'], reverse=True)[:3])
                except Exception as exc:
                    native_ok = False
                    doc['firefox_error'] = str(exc)
            doc['frozen_sample_pages'] = sorted(samples)
            checkpoint({'kind': 'document', 'metadata': {k: v for k, v in doc.items() if k != 'outputs'}})
            baseline_work = work/'original'
            baseline_work.mkdir()
            doc['outputs'].append({'tool': 'source', 'profile': 'original', 'status': 'usable',
                'path': str(source), 'work': str(baseline_work), 'output_sha256': digest, 'bytes': source.stat().st_size})
            output_id = 'source/original'
            checkpoint({'kind': 'output', 'metadata': doc['outputs'][-1]})
            for tool, profiles in PROFILES.items():
                for profile in profiles:
                    output_id = f'{tool}/{profile}'
                    progress(f"{doc['id']}: optimizer {tool}/{profile}")
                    tool_work = work / f'{tool}-{profile}'
                    tool_work.mkdir()
                    row = {'tool': tool, 'profile': profile, 'work': str(tool_work), 'status': 'not tested'}
                    doc['outputs'].append(row)
                    output = tool_work/'output.pdf'
                    try:
                        imports = [i for i in config.get('imports', []) if i['source_sha256'] == digest and i['tool'] == tool and i['profile'] == profile]
                        if imports:
                            entry = imports[0]
                            imported = Path(entry['path']).resolve(strict=True)
                            imported_hash = filehash(imported)
                            if tool == 'boox-note':
                                if filehash(entry['note_path']) != entry['note_sha256']:
                                    raise ValueError('Matching .note hash differs')
                            with imported.open('rb') as a, output.open('xb') as b:
                                shutil.copyfileobj(a, b)
                            if filehash(output) != imported_hash or filehash(imported) != imported_hash:
                                raise RuntimeError('Imported output changed during copy')
                            row['import'] = entry
                        elif tool in {'acrobat', 'boox-note'}:
                            mismatch = any(i['tool'] == tool and i['profile'] == profile for i in config.get('imports', []))
                            row['reason'] = 'Imported source SHA-256 does not match; refused comparison' if mismatch else (
                                'Licensed local Acrobat output required' if tool == 'acrobat' else 'Matching .note source and locally exported PDF required; separate upstream workflow')
                            continue
                        else:
                            tail, tokens = commands(tool, profile, source, output)
                            if tool == 'repository':
                                command = tail
                            else:
                                probe = pre['tools'].get(tool)
                                if not probe:
                                    row['reason'] = 'Tool not configured; not tested'
                                    continue
                                if not probe['version_pinned'] or probe['help_probe']['status'] != 'completed' or any(token not in probe['help_text'] for token in tokens):
                                    row['reason'] = 'Unsupported/unverified pinned interface; not tested'
                                    continue
                                command = config['tools'][tool]['command'] + tail
                            # Competitors see only their own disposable source copy.
                            private_input = tool_work/'input.pdf'
                            with source.open('rb') as a, private_input.open('xb') as b:
                                shutil.copyfileobj(a, b)
                            command = [str(private_input) if part == str(source) else part for part in command]
                            progress(f"Optimizer {tool}/{profile}; logs: {tool_work/'optimizer.stdout.log'} and optimizer.stderr.log")
                            row['execution'] = run_process(command, tool_work, 'optimizer')
                            checkpoint({'kind': 'optimizer-execution', 'execution': row['execution']})
                            if filehash(source) != digest or filehash(private_input) != digest:
                                raise RuntimeError('Source snapshot or tool input changed')
                            success_codes = (0, 3) if tool == 'qpdf' else (0,)
                            if row['execution'].get('exit_code') not in success_codes or row['execution']['status'] != 'completed':
                                raise RuntimeError('Optimizer failed; see logs')
                            if tool == 'repository' and profile == 'viewing-copy' and not output.exists():
                                # Verify the production report explicitly selected the original.
                                reports = list(tool_work.glob('*.json'))
                                selected = [json.loads(p.read_text(encoding='utf-8')) for p in reports if p.name != 'optimizer.json']
                                if not any(r.get('status') == 'no improvement' or r.get('decision') == 'no improvement' for r in selected):
                                    raise RuntimeError('No viewing output; inspect production report')
                                output = source
                                row.update(status='original selected', reason='Production viewing-copy: no improvement; original retained')
                        with reader_for(output) as reader:
                            validate_input(reader)
                        row.update(path=str(output), bytes=output.stat().st_size, output_sha256=filehash(output))
                        if row['status'] != 'original selected':
                            row['status'] = 'usable'
                    except Exception as exc:
                        row.update(status='failed', reason=f'{type(exc).__name__}: {exc}')
                    finally:
                        progress(f"{tool}/{profile}: {row['status']} {row.get('reason', '')}")
                        checkpoint({'kind': 'output', 'metadata': row})
            # No optimizers or image analysis run concurrently with timed passes.
            usable = [r for r in doc['outputs'] if r['status'] in {'usable', 'original selected'}]
            for row in usable:
                row['firefox_runs'] = []
                if not native_ok:
                    row['firefox_error'] = doc.get('firefox_error') or pre['firefox'].get('error', 'Native instrumentation unavailable')
            if native_ok:
                repetitions = 3
                repeat = 0
                while repeat < repetitions:
                    order = usable[repeat % len(usable):] + usable[:repeat % len(usable)]
                    if repeat % 2:
                        order = list(reversed(order))
                    order_ids = [f"{item['tool']}/{item['profile']}" for item in order]
                    for position, row in enumerate(order):
                        output_id = f"{row['tool']}/{row['profile']}"
                        try:
                            label = f"{doc['id']} {row['tool']}/{row['profile']} repetition {repeat+1}/{repetitions}"
                            progress(f'Firefox: {label}')
                            measurement_dir = Path(row['work']) / f'firefox-{repeat+1}'
                            measurement_dir.mkdir()
                            smoke_metadata = pre.get('firefox', {}).get('metadata', {})
                            timing_context = firefox_cache_context(
                                digest, row['output_sha256'], count, samples, repeat+1,
                                order_ids, position, smoke_metadata)
                            measurement = cache.load_json(timing_context)
                            if measurement is None:
                                measurement = firefox_measure.measure(row['path'], firefox, measurement_dir, sorted(samples), progress=lambda message: progress(f'{label}: {message}'))
                                cache.store_json(timing_context, measurement)
                            save_json(measurement_dir/'measurement.json', measurement)
                            row['firefox_runs'].append(measurement)
                            row['firefox_summary'] = firefox_measure.summarize(row['firefox_runs'])
                        except Exception as exc:
                            row['firefox_error'] = str(exc)
                            progress(f'Firefox failed: {exc}')
                        checkpoint({'kind': 'firefox', 'repetition': repeat+1,
                                    'summary': row.get('firefox_summary'), 'error': row.get('firefox_error'),
                                    'measurement_directory': str(measurement_dir.relative_to(directory))})
                    repeat += 1
                    if repeat == 3 and firefox_measure.needs_more([r.get('firefox_summary', {}) for r in usable]):
                        repetitions = 5
                        progress('Firefox: extending to five repetitions for noisy or close timings')
            for row in usable:
                output_id = f"{row['tool']}/{row['profile']}"
                label = f"{doc['id']} {row['tool']}/{row['profile']}"
                progress(f'Preservation: {label}')
                row['checks'] = independent_checks(source, row['path'], progress=lambda message: progress(f'{label}: {message}'))
                checkpoint({'kind': 'preservation', 'checks': row['checks']})
                try:
                    analysis_dir = Path(row['work'])/'analysis'
                    analysis_dir.mkdir()
                    row['appearance'] = {}
                    analyze(source, row['path'], analysis_dir, pre['environment']['node'], checkpoint,
                            row['appearance'], progress=lambda message: progress(f'{label}: {message}'), cache=cache)
                    # Evidence links are relative to this row's work directory.
                    for region in row['appearance']['regions']:
                        region['artifacts'] = {k: 'analysis/' + v for k, v in region.get('artifacts', {}).items()}
                except Exception as exc:
                    row['appearance_error'] = str(exc)
                    progress(f'{label}: image analysis failed: {exc}')
                checkpoint({'kind': 'appearance-summary',
                            'summary': {k: v for k, v in row.get('appearance', {}).items() if k not in {'pages', 'regions'}},
                            'error': row.get('appearance_error')})
            doc['source_integrity'] = filehash(input_path) == digest and filehash(source) == digest
            output_id = None
            checkpoint({'kind': 'source-integrity', 'passed': doc['source_integrity']})
        state['status'] = 'complete'
        state['cache']['stats'] = dict(cache.stats)
        checkpoint({'kind': 'run-complete'})
    except KeyboardInterrupt:
        progress('Interrupted; saving partial results')
        state['status'] = 'interrupted'
        checkpoint({'kind': 'interrupted'})
    finally:
        state['cache']['stats'] = dict(cache.stats)
        progress('Writing report, plots, gallery and machine-readable summaries')
        report(directory, state)
        progress(f"{state['status']}: {directory/'report.md'}")
    return state


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--render-worker':
        render_worker(sys.argv[2], int(sys.argv[3]), float(sys.argv[4]), sys.argv[5], json.loads(sys.argv[6]))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--tools-config', type=Path, required=True)
    parser.add_argument('--firefox')
    parser.add_argument('--cache', type=Path)
    parser.add_argument('--preflight-only', action='store_true')
    args = parser.parse_args()
    if not args.input and not args.preflight_only:
        parser.error('--input is required unless --preflight-only')
    try:
        state = execute(args)
        print(f"{state['status']}: {args.output.resolve() / 'report.md'}")
    except (ValueError, OSError) as exc:
        parser.exit(2, f'{exc}\n')


if __name__ == '__main__':
    main()
