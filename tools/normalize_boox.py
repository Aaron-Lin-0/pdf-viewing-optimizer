"""Research-only, verified current-revision snapshots of NeoPdf exports."""
from contextlib import contextmanager
import logging

from pypdf import PdfReader

from lossless_verify import (_StrictReader, _check_file, MAX_OBJECTS, UnsupportedPDF,
                             strict_parsing, validate_quick_input, verify_lossless)
from navigation import validate_input
from pdf_runtime import build_qpdf_candidate, check_qpdf, filehash, qpdf_identity


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
            raise UnsupportedPDF('Current-revision preprocessing is restricted to NeoPdf exports.')
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
            if record.getMessage() == 'Xref table not zero-indexed. ID numbers for objects will be corrected.':
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


def normalize(source, destination):
    if destination.exists():
        raise FileExistsError('Normalized research copy already exists.')
    original_hash = filehash(source)
    warnings = []
    identity = qpdf_identity()
    # qpdf must independently accept the original without repair warnings.
    check_qpdf(source, identity)
    with strict_parsing(), known_index_warning(warnings), current_reader(source) as reader:
        pages = len(reader.pages)
    build_qpdf_candidate(source, destination, identity=identity, strict=True)
    check_qpdf(destination, identity)
    validate_quick_input(destination)
    with known_index_warning(warnings):
        proof = verify_lossless(source, destination, _source_reader=current_reader)
    if filehash(source) != original_hash:
        raise ValueError('Original changed during preprocessing.')
    return {'policy': 'neopdf-current-revision-1', 'source_sha256': original_hash,
            'output_sha256': filehash(destination), 'path': str(destination), 'pages': pages,
            'index_warnings': warnings, 'preservation': proof,
            'history': 'Current logical objects/streams preserved; historical revision layout is not retained. Original untouched.'}
