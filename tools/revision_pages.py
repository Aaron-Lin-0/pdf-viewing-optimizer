"""Reuse exact PDFium comparisons for self-contained pages across Direct runs."""
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import stat

from pypdf.generic import (ArrayObject, BooleanObject, ByteStringObject, DictionaryObject,
                           FloatObject, IndirectObject, NameObject, NullObject, NumberObject,
                           StreamObject, TextStringObject)

from lossless_verify import _StrictReader, strict_parsing
from pdf_runtime import filehash


SCHEMA = 1
CATALOG_KEYS = {'/Type', '/Pages', '/Version', '/Outlines', '/Names', '/PageLabels',
                '/Metadata', '/Lang', '/PageLayout', '/PageMode', '/ViewerPreferences'}
TREE_KEYS = {'/Type', '/Parent', '/Kids', '/Count', '/MediaBox', '/CropBox', '/Rotate', '/Resources'}
PAGE_KEYS = TREE_KEYS - {'/Kids', '/Count'} | {'/Contents', '/Annots', '/UserUnit',
                                                '/BleedBox', '/TrimBox', '/ArtBox'}
EXTERNAL_KEYS = {'/Font', '/OC', '/Ref', '/OPI', '/Alternates', '/F', '/FFilter', '/FDecodeParms'}


def page_keys(path, check):
    """Hash raw page inputs, retaining reference topology and exact decimal values."""
    with strict_parsing(), Path(path).open('rb') as stream, _StrictReader(stream, strict=True) as reader:
        root = reader.trailer['/Root']
        if set(root) - CATALOG_KEYS or '/JavaScript' in root.get('/Names', DictionaryObject()).get_object():
            return []
        context = [reader.pdf_header, str(root.get('/Version', ''))]
        streams = {}
        def key(page):
            fields = {k: v for k, v in page.items() if k != '/Parent'}
            if set(page) - PAGE_KEYS or page.get('/Annots', ArrayObject()).get_object():
                return None
            ancestors, inherited = set(), []
            parent = page.get('/Parent')
            while isinstance(parent, IndirectObject):
                tree = parent.get_object()
                if id(tree) in ancestors or set(tree) - TREE_KEYS:
                    return None
                ancestors.add(id(tree))
                inherited.append({k: v for k, v in tree.items()
                                  if k not in {'/Type', '/Kids', '/Count', '/Parent'}})
                parent = tree.get('/Parent')
            if parent is not None:
                return None
            refs, active = {}, set()
            count = 0

            def require_no_text(stream):
                try:
                    data = stream.get_data()
                    if b'BT' in data or b'Tf' in data:
                        raise ValueError('Page content may depend on font rendering.')
                finally:
                    if hasattr(stream, 'decoded_self'):
                        stream.decoded_self = None

            def token(value, depth=0):
                nonlocal count
                count += 1
                if count > 100_000 or depth > 64:
                    raise ValueError('Revision page identity exceeded its work bound.')
                if isinstance(value, IndirectObject):
                    ref = value.idnum, value.generation
                    if ref in active:
                        raise ValueError('Cyclic page resource dependency.')
                    if ref in refs:
                        return ['ref', refs[ref]]
                    refs[ref] = len(refs)
                    active.add(ref)
                    result = token(value.get_object(), depth+1)
                    active.remove(ref)
                    return ['object', result]
                if isinstance(value, dict):
                    if set(value) & EXTERNAL_KEYS:
                        raise ValueError('Page has dependencies outside the static render subset.')
                    fields = [[str(k), token(v, depth+1)] for k,v in sorted(value.items())]
                    if isinstance(value, StreamObject):
                        if id(value) not in streams:
                            if value.get('/Subtype') == '/Form' or value.get('/PatternType') == 1:
                                require_no_text(value)
                            streams[id(value)] = hashlib.sha256(value._data).hexdigest()
                        return ['stream', streams[id(value)], fields]
                    return ['dict', fields]
                if isinstance(value, ArrayObject):
                    return ['array', [token(v, depth+1) for v in value]]
                if isinstance(value, FloatObject):
                    return ['real', str(value.exact_decimal)]
                if isinstance(value, (ByteStringObject, TextStringObject)):
                    return ['string', hashlib.sha256(value.original_bytes).hexdigest()]
                if isinstance(value, (BooleanObject, NameObject, NullObject, NumberObject)):
                    out = BytesIO()
                    value.write_to_stream(out)
                    return ['scalar', out.getvalue().hex()]
                raise ValueError('Unsupported PDF value in revision page identity.')

            try:
                content = page.get_contents()
                if content is not None:
                    require_no_text(content)
                identity = [context, token(fields), [token(values) for values in inherited]]
                return hashlib.sha256(json.dumps(identity, separators=(',', ':')).encode()).hexdigest()
            except (AttributeError, ValueError, TypeError):
                return None

        keys = []
        for page in reader.pages:
            check()
            keys.append(key(page))
        return keys


def prior_pairs(source, checkpoint, context, library_hash, check):
    """Look through prior adjacent runs for completed, hash-bound evidence."""
    prefix = source.stem + '.optimized-'
    folders = list(source.parent.iterdir())
    roots = [folder / (source.stem + '.optimized.checkpoint') for folder in folders
             if folder.name.startswith(prefix) and folder.is_dir() and not redirected(folder)]
    roots.extend(folder for folder in folders if folder.suffix == '.checkpoint')
    pairs = set()
    recent = sorted((root for root in roots if root != checkpoint and root.is_dir() and not redirected(root)),
                    key=lambda root: root.stat().st_mtime_ns, reverse=True)
    # shortcut: inspect eight recent runs, add an index if longer revision histories need reuse.
    for root in recent[:8]:
        check()
        state = root / 'quick' / 'state.json'
        report = root.with_suffix('.json')
        try:
            if any(redirected(path) for path in (root, state.parent, state, report)):
                continue
            marker = json.loads(state.read_bytes())
            if (marker.get('profile') != 'quick' or marker.get('stage') != 'complete' or
                    marker.get('status') != 'optimized'):
                continue
            data = report.read_bytes()
            if marker.get('report_sha256') != hashlib.sha256(data).hexdigest():
                continue
            old = json.loads(data)
            if (Path(old['source']).absolute() != source or old['source_sha256'] != marker['source_sha256']
                    or old['acceptance_context'] != context or old['status'] != 'optimized'):
                continue
            evidence = old['revision_evidence']
            if evidence['schema'] == SCHEMA and evidence['pdfium_sha256'] == library_hash:
                pairs.update(tuple(pair) for pair in evidence['exact_page_pairs']
                             if isinstance(pair, list) and len(pair) == 2 and
                             all(isinstance(digest, str) and re.fullmatch('[a-f0-9]{64}', digest) for digest in pair))
        except (OSError, KeyError, ValueError, TypeError, AttributeError):
            continue
    return pairs


def redirected(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def revision_evidence(source, candidate, checkpoint, context, check):
    from pypdfium2_raw.bindings import _libs

    library = Path(_libs['pdfium']._name)
    if not library.is_absolute() or not library.is_file():
        return {}, set()
    library_hash = filehash(library)
    source_keys = page_keys(source, check)
    if not any(source_keys):
        return {}, set()
    candidate_keys = page_keys(candidate, check)
    current = {index: (before, after) for index,(before,after) in enumerate(zip(source_keys,candidate_keys))
               if before and after}
    previous = prior_pairs(source, checkpoint, context, library_hash, check) if current else set()
    reused = {index for index, pair in current.items() if pair in previous}
    evidence = {'schema': SCHEMA, 'pdfium_sha256': library_hash, 'exact_page_pairs': sorted(set(current.values())),
                'pdfium_pages_reused': sorted(index+1 for index in reused)}
    return evidence, reused
