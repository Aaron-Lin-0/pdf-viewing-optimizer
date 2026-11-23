"""Verified current-revision snapshots of NeoPdf exports."""
from contextlib import contextmanager
import logging
from pathlib import Path
import shutil
from time import monotonic

from pypdf import PdfReader

from lossless_verify import (_StrictReader, _check_file, INCREMENTAL_WARNING, MAX_OBJECTS, UnsupportedPDF,
                             strict_parsing, strict_reader, validate_quick_input, verify_lossless)
from navigation import validate_input
from pdf_runtime import build_qpdf_candidate, check_qpdf, filehash, qpdf_identity, temporary_directory


INDEX_WARNING = 'Xref table not zero-indexed. ID numbers for objects will be corrected.'
CURRENT_REVISION_POLICY = 'neopdf-current-revision-2'


class UnsupportedExporter(UnsupportedPDF):
    """The PDF does not declare the exporter qualified for this preparation."""


class CurrentRevisionReader(_StrictReader):
    def _process_xref_stream(self, stream):
        trailer = {key: value for key, value in stream.items()
                   if key not in {'/Type','/W','/Index','/Length','/Filter','/DecodeParms'}}
        self.quick_trailer = getattr(self, 'quick_trailer', {})
        for key, value in trailer.items():
            self.quick_trailer.setdefault(key, value)
        return PdfReader._process_xref_stream(self, stream)


@contextmanager
def current_reader(path):
    _check_file(path)
    with CurrentRevisionReader(path, strict=True) as reader:
        for key, value in getattr(reader, 'quick_trailer', {}).items():
            reader.trailer.setdefault(key, value)
        validate_input(reader)
        if not reader.metadata or reader.metadata.producer != 'NeoPdf':
            raise UnsupportedExporter('Current-revision preprocessing is restricted to NeoPdf exports.')
        root = reader.trailer['/Root']
        if '/AcroForm' in root and '/XFA' in root['/AcroForm']:
            raise UnsupportedPDF('XFA is unsupported.')
        if len(reader.xref_objStm) + sum(map(len, reader.xref.values())) > MAX_OBJECTS:
            raise UnsupportedPDF('Current-revision object limit exceeded.')
        yield reader


@contextmanager
def known_index_warning(warnings):
    class IndexWarning(logging.Filter):
        def filter(self, record):
            if record.getMessage() == INDEX_WARNING:
                warnings.append(record.getMessage())
                return False
            return True
    logger = logging.getLogger('pypdf._reader')
    policy = IndexWarning()
    logger.addFilter(policy)
    try:
        yield
    finally:
        logger.removeFilter(policy)


@contextmanager
def normalization_source_reader(path):
    """Read an original under the explicitly approved current-revision policy."""
    with strict_parsing(), known_index_warning([]), current_reader(path) as reader:
        yield reader


@contextmanager
def review_source_reader(source):
    """Open the source for review and indicate whether it needs a verified snapshot."""
    with strict_parsing():
        try:
            reader = strict_reader(source)
        except UnsupportedPDF as error:
            rejection = str(error)
            if rejection not in (INCREMENTAL_WARNING, f'PDF parser warning: {INDEX_WARNING}'):
                raise
        else:
            with reader:
                yield reader, False
            return
    try:
        with normalization_source_reader(source) as reader:
            yield reader, True
    except UnsupportedExporter:
        raise UnsupportedPDF(rejection) from None


def validate_direct_input(source):
    """Return the page count and whether Direct needs a verified snapshot."""
    with review_source_reader(source) as (reader, snapshot):
        return len(reader.pages), snapshot


def normalize(source, destination, *, identity=None, budget=None, expected_source_sha256=None):
    result = _normalize(source, destination, current_reader, False, identity=identity, budget=budget,
                        expected_source_sha256=expected_source_sha256)
    result.update(policy=CURRENT_REVISION_POLICY, path=str(destination),
                  history='Current logical objects/streams preserved; historical revision layout is not retained. Original untouched.')
    return result


def normalize_index_warning(source, destination):
    """Qualify only the known index warning; incremental history remains rejected."""
    return _normalize(source, destination, strict_reader, True)


def _normalize(source, destination, source_reader, require_warning, *, identity=None, budget=None,
               expected_source_sha256=None):
    source, destination = Path(source), Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('Snapshot already exists.')
    original_hash = filehash(source)
    if expected_source_sha256 is not None and original_hash != expected_source_sha256:
        raise ValueError('Original does not match the reviewed source hash.')
    warnings = []
    identity = identity or qpdf_identity(budget)
    check_qpdf(source, identity, budget)
    with strict_parsing(), known_index_warning(warnings), source_reader(source) as reader:
        pages = len(reader.pages)
        if not reader.metadata or reader.metadata.producer != 'NeoPdf':
            raise UnsupportedPDF('Index normalization is restricted to NeoPdf exports.')
    if require_warning and not warnings:
        raise UnsupportedPDF('The recognized index warning is required for normalization.')
    with temporary_directory(prefix='pdf-index-', dir=destination.parent) as temporary:
        candidate = Path(temporary) / 'snapshot.pdf'
        build_qpdf_candidate(source, candidate, budget=budget, identity=identity, strict=True)
        check_qpdf(candidate, identity, budget)
        validate_quick_input(candidate)
        next_budget_check = 0.0

        def check():
            nonlocal next_budget_check
            if budget is not None and (now := monotonic()) >= next_budget_check:
                budget.check('current-revision proof', (temporary,))
                next_budget_check = now + 1

        with strict_parsing(), known_index_warning(warnings):
            proof = verify_lossless(source, candidate, check, _source_reader=source_reader)
        if filehash(source) != original_hash:
            raise ValueError('Original changed during normalization.')
        snapshot_hash = filehash(candidate)
        with destination.open('xb') as output:
            try:
                with candidate.open('rb') as incoming:
                    shutil.copyfileobj(incoming, output)
                output.flush()
                if budget is not None:
                    budget.check('current-revision snapshot publication', (temporary, destination))
                if filehash(source) != original_hash or filehash(destination) != snapshot_hash:
                    raise ValueError('Source or snapshot changed during publication.')
            except BaseException:
                output.close()
                destination.unlink()
                raise
    return {'policy': 'neopdf-index-only-1', 'source_sha256': original_hash,
            'output_sha256': snapshot_hash, 'pages': pages,
            'index_warnings': warnings, 'preservation': proof}
