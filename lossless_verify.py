"""Strict logical-graph equivalence for compression-only PDF rewrites.

No drawing interpretation, rounding, annotation conversion or renderer is used.
Only storage layout, update IDs and lossless Flate encoding may differ.
"""
from contextlib import contextmanager
from decimal import Decimal
import base64
import hashlib
import logging
from pathlib import Path
import re
import threading
import zlib

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from pypdf.generic import (ArrayObject, BooleanObject, ByteStringObject,
    DictionaryObject, FloatObject, IndirectObject, NameObject, NullObject,
    NumberObject, StreamObject, TextStringObject)
from pypdf.generic import _base
from navigation import is_signature_object, validate_input


POLICY_VERSION = 'quick-lossless-1'
MAX_FILE_BYTES = 512 * 1024**2
MAX_OBJECTS = 500_000
MAX_DEPTH = 256
MAX_STREAM_BYTES = 256 * 1024**2
MAX_DECODED_BYTES = 1024**3
MAX_TRANSPORT_BYTES = 64 * 1024**2
_parser_lock = threading.RLock()


class UnsupportedPDF(ValueError):
    """Preservation could not be proved within the supported subset."""


class PreservationMismatch(ValueError):
    pass


class _ExactFloat(FloatObject):
    def __new__(cls, value='0.0', context=None):
        obj = super().__new__(cls, value, context)
        obj.exact_decimal = Decimal(value.decode('ascii') if isinstance(value, bytes) else str(value))
        if not obj.exact_decimal.is_finite():
            raise UnsupportedPDF('Non-finite PDF number.')
        return obj


class _StrictReader(PdfReader):
    def _read_pdf15_xref_stream(self,stream):
        result=super()._read_pdf15_xref_stream(stream)
        ref=result.indirect_reference
        self.quick_xref_ids=getattr(self,'quick_xref_ids',set()) | {(ref.idnum,ref.generation)}
        return result

    def _process_xref_stream(self, xrefstream):
        # pypdf omits /Prev from the public trailer of an xref-stream PDF.
        if '/Prev' in xrefstream:
            raise UnsupportedPDF('Incremental PDF revisions require thorough processing.')
        # The public trailer retains only pypdf's known keys. Do not lose
        # application-defined trailer entries when reading a stream-based xref.
        self.quick_trailer={key:value for key,value in xrefstream.items()
                            if key not in {'/Type','/W','/Index','/Length','/Filter','/DecodeParms'}}
        return super()._process_xref_stream(xrefstream)


@contextmanager
def strict_parsing():
    # pypdf stores reals as binary floats. Keep their original decimal value so
    # a subtle coordinate/width mutation cannot round to the same Python float.
    class RejectWarnings(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING:
                raise UnsupportedPDF('PDF parser warning: ' + record.getMessage())
    with _parser_lock:
        logger = logging.getLogger('pypdf'); handler = RejectWarnings()
        previous = _base.FloatObject
        logger.addHandler(handler); _base.FloatObject = _ExactFloat
        try:
            yield
        finally:
            _base.FloatObject = previous; logger.removeHandler(handler)


def _check_file(path):
    path = Path(path)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise UnsupportedPDF('Quick verification supports PDFs up to 512 MiB.')
    with path.open('rb') as stream:
        if stream.read(5) != b'%PDF-':
            raise UnsupportedPDF('Quick mode requires a PDF header at the start of the file.')
        stream.seek(max(0, path.stat().st_size - 1024))
        if not stream.read().rstrip().endswith(b'%%EOF'):
            raise UnsupportedPDF('Unaccounted data after the final PDF marker.')


def strict_reader(path):
    _check_file(path)
    try:reader = _StrictReader(path, strict=True)
    except PdfReadError as exc:raise UnsupportedPDF('Strict PDF parsing failed: '+str(exc)) from exc
    for key,value in getattr(reader,'quick_trailer',{}).items():reader.trailer.setdefault(key,value)
    validate_input(reader)
    if '/Prev' in reader.trailer:
        raise UnsupportedPDF('Incremental PDF revisions require thorough processing.')
    root = reader.trailer['/Root']
    if '/AcroForm' in root and '/XFA' in root['/AcroForm']:
        raise UnsupportedPDF('XFA is unsupported in quick mode.')
    if len(reader.xref_objStm) + sum(map(len, reader.xref.values())) > MAX_OBJECTS:
        raise UnsupportedPDF('Quick verification object limit exceeded.')
    return reader


def validate_quick_input(path):
    with strict_parsing():
        with strict_reader(path) as reader:
            return len(reader.pages)


def _stream_filter(stream):
    value = stream.get('/Filter')
    if isinstance(value, IndirectObject):
        value = value.get_object()
    if isinstance(value, ArrayObject) and len(value) == 1:
        value = value[0].get_object()
    return value


def _stream_digest(data, flate, check, counter):
    if len(data) > MAX_STREAM_BYTES:
        raise UnsupportedPDF('Quick verification stream limit exceeded.')
    digest = hashlib.sha256(); size = 0
    decoder = zlib.decompressobj() if flate else None
    def consume(block):
        nonlocal size
        size += len(block); counter[0] += len(block)
        if size > MAX_STREAM_BYTES or counter[0] > MAX_DECODED_BYTES:
            raise UnsupportedPDF('Quick verification decompression limit exceeded.')
        digest.update(block); check()
    for offset in range(0, len(data), 65536):
        block = data[offset:offset + 65536]
        if decoder:
            while block:
                consume(decoder.decompress(block, 65536))
                block = decoder.unconsumed_tail
        else:
            consume(block)
    if decoder and (not decoder.eof or decoder.unused_data or decoder.unconsumed_tail):
        raise UnsupportedPDF('Incomplete or trailing Flate stream data.')
    return size, digest.digest()


def _transport_digest(stream, check, counter):
    """Decode only bounded, lossless transport filters; never image codecs."""
    filters=stream.get('/Filter');params=stream.get('/DecodeParms')
    if isinstance(filters,IndirectObject):filters=filters.get_object()
    if isinstance(params,IndirectObject):params=params.get_object()
    filters=[] if filters is None else list(filters) if isinstance(filters,ArrayObject) else [filters]
    if params is None or isinstance(params,NullObject):params=[None]*len(filters)
    elif isinstance(params,ArrayObject):params=list(params)
    else:params=[params]
    if len(params)!=len(filters):raise UnsupportedPDF('Unsupported stream filter parameters.')
    data=stream._data
    if len(data)>MAX_TRANSPORT_BYTES:raise UnsupportedPDF('Changed transport streams exceed the 64-MiB quick limit.')
    def account(block):
        counter[0]+=len(block);check()
        if len(block)>MAX_TRANSPORT_BYTES or counter[0]>MAX_DECODED_BYTES:
            raise UnsupportedPDF('Quick verification decompression limit exceeded.')
        return block
    for index,(kind,options) in enumerate(zip(filters,params)):
        kind=kind.get_object();options=options.get_object() if isinstance(options,IndirectObject) else options
        if isinstance(options,NullObject):options=None
        if kind=='/ASCII85Decode':
            if options is not None:raise UnsupportedPDF('Unexpected ASCII85 parameters.')
            data=account(base64.a85decode(data,adobe=True,ignorechars=b'\x00\t\n\x0c\r '))
        elif kind=='/ASCIIHexDecode':
            if options is not None:raise UnsupportedPDF('Unexpected ASCIIHex parameters.')
            clean=b''.join(data.split()).replace(b'\0',b'')
            if not clean.endswith(b'>'):raise UnsupportedPDF('Missing ASCIIHex end marker.')
            clean=clean[:-1]
            if len(clean)%2:clean+=b'0'
            data=account(bytes.fromhex(clean.decode('ascii')))
        elif kind=='/FlateDecode':
            decoded=bytearray();decoder=zlib.decompressobj()
            for offset in range(0,len(data),65536):
                block=data[offset:offset+65536]
                while block:
                    decoded.extend(account(decoder.decompress(block,65536)))
                    if len(decoded)>MAX_TRANSPORT_BYTES:raise UnsupportedPDF('Quick verification stream limit exceeded.')
                    block=decoder.unconsumed_tail
            if not decoder.eof or decoder.unused_data:raise UnsupportedPDF('Incomplete or trailing Flate stream data.')
            data=decoded
            if options is not None:
                if not isinstance(options,DictionaryObject) or set(options)-{'/Predictor','/Colors','/BitsPerComponent','/Columns'}:
                    raise UnsupportedPDF('Unknown Flate decoding parameters.')
                values={key:options.get(key,NumberObject(default)).get_object() for key,default in
                        [('/Predictor',1),('/Colors',1),('/BitsPerComponent',8),('/Columns',1)]}
                if any(not isinstance(value,NumberObject) for value in values.values()):
                    raise UnsupportedPDF('Non-integer predictor parameters.')
                predictor,colors,bits,columns=[int(values[key]) for key in
                    ('/Predictor','/Colors','/BitsPerComponent','/Columns')]
                if colors<1 or columns<1 or bits not in (1,2,4,8,16):raise UnsupportedPDF('Unsupported predictor geometry.')
                if predictor!=1:
                    if index!=len(filters)-1 or not 10<=predictor<=15:
                        raise UnsupportedPDF('Unsupported predictor/filter combination.')
                    # PNG prediction is lossless integer arithmetic on bytes.
                    width=(colors*columns*bits+7)//8;pixel=(colors*bits+7)//8
                    if width>MAX_TRANSPORT_BYTES or len(data)%(width+1):raise UnsupportedPDF('Invalid PNG predictor rows.')
                    previous=bytearray(width);result=bytearray()
                    for offset in range(0,len(data),width+1):
                        check();mode=data[offset];row=bytearray(data[offset+1:offset+1+width])
                        if mode>4:raise UnsupportedPDF('Invalid PNG row filter.')
                        for col in range(width):
                            left=row[col-pixel] if col>=pixel else 0;up=previous[col]
                            corner=previous[col-pixel] if col>=pixel else 0
                            if mode==1:row[col]=(row[col]+left)&255
                            elif mode==2:row[col]=(row[col]+up)&255
                            elif mode==3:row[col]=(row[col]+((left+up)//2))&255
                            elif mode==4:
                                estimate=left+up-corner
                                distances=(abs(estimate-left),abs(estimate-up),abs(estimate-corner))
                                value=(left,up,corner)[distances.index(min(distances))]
                                row[col]=(row[col]+value)&255
                        result.extend(row);previous=row
                    data=account(result)
        else:raise UnsupportedPDF('Changed stream uses an unsupported transport filter: '+str(kind))
    return len(data),hashlib.sha256(data).digest()


def _raw_items(value):
    return {str(k): v for k, v in value.items()}


def verify_lossless(source, candidate, check=lambda: None):
    """Compare every reachable logical object, preserving reference identity.

    Unreachable objects must be preserved with a complete identity mapping too.
    Ambiguous mappings that cannot be verified and incremental history are declined.
    Stream decoding is bounded and restricted to the Flate transport layer.
    """
    with strict_parsing(), strict_reader(source) as before, strict_reader(candidate) as after:
        headers=[]
        for reader in (before,after):
            match=re.fullmatch(r'%PDF-(\d)\.(\d)',reader.pdf_header)
            if not match:raise UnsupportedPDF('Unsupported PDF version header.')
            headers.append(tuple(map(int,match.groups())))
        if headers[1]!=headers[0] and not (headers[0]<(1,5) and headers[1]==(1,5)):
            raise UnsupportedPDF('Unexpected PDF version change; only object-stream promotion to PDF 1.5 is permitted.')
        forward = {}; reverse = {}; storage = [set(), set()];journal=[]
        stack = []; nodes = 0; streams = 0; decoded = [0];digests={}
        def payload(obj,mode):
            key=(id(obj),mode)
            if key not in digests:
                if mode=='raw':digests[key]=(len(obj._data),hashlib.sha256(obj._data).digest())
                elif mode=='simple':digests[key]=_stream_digest(obj._data,_stream_filter(obj)=='/FlateDecode',check,decoded)
                else:digests[key]=_transport_digest(obj,check,decoded)
            return digests[key]
        def mismatch(path, reason):
            raise PreservationMismatch(f'Lossless verification failed at {path}: {reason}.')
        def push(a, b, path, depth):
            if depth > MAX_DEPTH:
                raise UnsupportedPDF('Quick verification depth limit exceeded.')
            if len(stack)>=MAX_OBJECTS:
                raise UnsupportedPDF('Quick verification pending-value limit exceeded.')
            stack.append((a, b, path, depth))
        def compare_dict(a, b, path, depth, excluded=()):
            aa = {k: v for k, v in _raw_items(a).items() if k not in excluded}
            bb = {k: v for k, v in _raw_items(b).items() if k not in excluded}
            if aa.keys() != bb.keys():
                mismatch(path, 'dictionary entries changed (' + ', '.join(sorted(aa.keys() ^ bb.keys())) + ')')
            for key in sorted(aa, reverse=True):
                push(aa[key], bb[key], path + key, depth + 1)
        # /Size, /Prev and /XRefStm describe file layout. Incremental sources are
        # rejected above. qpdf may create an ID or change its update identifier.
        for reader in (before, after):
            ids = reader.trailer.get('/ID')
            if ids is not None and (not isinstance(ids, ArrayObject) or len(ids) != 2 or
                                    any(not isinstance(item,(ByteStringObject,TextStringObject)) for item in ids)):
                raise UnsupportedPDF('Unsupported document ID representation.')
        if '/ID' in before.trailer:
            if '/ID' not in after.trailer:
                mismatch('/ID', 'document identity removed')
            push(before.trailer['/ID'][0], after.trailer['/ID'][0], '/ID[0]', 0)
        compare_dict(before.trailer, after.trailer, 'trailer', 0,
                     {'/Size', '/Prev', '/XRefStm', '/ID'})
        def walk():
            nonlocal nodes,streams
            while stack:
                a, b, path, depth = stack.pop(); nodes += 1
                if nodes > MAX_OBJECTS * 20:
                    raise UnsupportedPDF('Quick verification value limit exceeded.')
                if nodes % 1024 == 0: check()
                if isinstance(a, IndirectObject) or isinstance(b, IndirectObject):
                    if not isinstance(a, IndirectObject) or not isinstance(b, IndirectObject):
                        mismatch(path, 'direct/indirect identity changed')
                    ak = (a.idnum, a.generation); bk = (b.idnum, b.generation)
                    if ak in forward:
                        if forward[ak] != bk: mismatch(path, 'shared object split')
                        continue
                    if bk in reverse: mismatch(path, 'distinct objects merged')
                    forward[ak] = bk; reverse[bk] = ak
                    journal.append(ak)
                    if len(forward) > MAX_OBJECTS: raise UnsupportedPDF('Quick verification object limit exceeded.')
                    a = a.get_object(); b = b.get_object()
                    if a is None or b is None: raise UnsupportedPDF('Unresolved object reference.')
                for obj in (a,b):
                    if is_signature_object(obj):
                        raise UnsupportedPDF('Signature objects are unsupported, including unreferenced signatures.')
                if isinstance(a, StreamObject) or isinstance(b, StreamObject):
                    if not isinstance(a, StreamObject) or not isinstance(b, StreamObject): mismatch(path, 'stream type changed')
                    streams += 1
                    for side, obj in enumerate((a, b)):
                        length = obj.get('/Length')
                        if isinstance(length, IndirectObject): storage[side].add((length.idnum, length.generation))
                    af, bf = _stream_filter(a), _stream_filter(b)
                    simple_a = af is None or af == '/FlateDecode'
                    simple_b = bf is None or bf == '/FlateDecode'
                    same_params=a.get('/DecodeParms')==b.get('/DecodeParms')
                    if simple_a and simple_b and same_params:
                        # Predictor/codec parameters remain exact. Only the outer
                        # Flate transport may change; its bytes before prediction
                        # must be identical. No image decoder is trusted here.
                        compare_dict(a, b, path, depth, {'/Length', '/Filter'})
                        if a._data != b._data or af != bf:
                            da = payload(a,'simple')
                            db = payload(b,'simple')
                            if da != db: mismatch(path, 'stream bytes changed')
                    elif a._data==b._data and a.get('/Filter')==b.get('/Filter') and same_params:
                        compare_dict(a, b, path, depth, {'/Length'})
                    else:
                        # Decode only the enumerated lossless transports and compare
                        # their exact payload. Specialized image encodings stay raw.
                        da=payload(a,'transport');db=payload(b,'transport')
                        compare_dict(a,b,path,depth,{'/Length','/Filter','/DecodeParms'})
                        if da!=db:mismatch(path,'decoded stream bytes changed')
                elif isinstance(a, DictionaryObject) and isinstance(b, DictionaryObject):
                    compare_dict(a, b, path, depth)
                elif isinstance(a, ArrayObject) and isinstance(b, ArrayObject):
                    if len(a) != len(b): mismatch(path, 'array length changed')
                    for index in range(len(a) - 1, -1, -1):
                        push(list.__getitem__(a, index), list.__getitem__(b, index), f'{path}[{index}]', depth + 1)
                elif isinstance(a, (NumberObject, FloatObject)) and isinstance(b, (NumberObject, FloatObject)):
                    if (isinstance(a,FloatObject) and not isinstance(a,_ExactFloat) or
                        isinstance(b,FloatObject) and not isinstance(b,_ExactFloat)):
                        raise UnsupportedPDF('A PDF real number lost its original decimal representation.')
                    aa = a.exact_decimal if isinstance(a, _ExactFloat) else Decimal(int(a))
                    bb = b.exact_decimal if isinstance(b, _ExactFloat) else Decimal(int(b))
                    if aa != bb: mismatch(path, 'numeric value changed')
                elif isinstance(a, (ByteStringObject, TextStringObject)) and isinstance(b, (ByteStringObject, TextStringObject)):
                    aa = a.original_bytes if isinstance(a, TextStringObject) else bytes(a)
                    bb = b.original_bytes if isinstance(b, TextStringObject) else bytes(b)
                    if aa != bb: mismatch(path, 'string bytes changed')
                elif isinstance(a, NullObject) and isinstance(b, NullObject):
                    pass
                elif isinstance(a, BooleanObject) and isinstance(b, BooleanObject):
                    if a.value != b.value: mismatch(path, 'boolean changed')
                elif isinstance(a, NameObject) and isinstance(b, NameObject):
                    if a != b: mismatch(path, 'name changed')
                else:
                    mismatch(path, 'unsupported or changed value type')
        walk()
        # Include orphan data as additional graph roots. qpdf must retain it.
        # A complete verified bijection is required; a heuristic hash match only
        # schedules comparison and can never by itself approve an object.
        unused=[]
        for side,reader in enumerate((before,after)):
            mapped=forward if side==0 else reverse
            refs={(n,g) for g,rows in reader.xref.items() for n,offset in rows.items()
                  if n and offset>0 and not reader.xref_free_entry.get(g,{}).get(n,False)}
            refs.update((n,0) for n in reader.xref_objStm)
            transport=set(getattr(reader,'quick_xref_ids',set()))
            transport.update((value[0],0) for value in reader.xref_objStm.values())
            unused.append(refs-mapped.keys()-storage[side]-transport)
        original_orphans=len(unused[0])
        if len(unused[0])!=len(unused[1]):
            raise UnsupportedPDF('Unreferenced object inventory changed; quick preservation is unresolved.')
        known=(set(forward),dict(reverse))
        def local(value,side,depth=0,decode=False):
            if depth>MAX_DEPTH:raise UnsupportedPDF('Quick orphan comparison depth exceeded.')
            if isinstance(value,IndirectObject):
                key=(value.idnum,value.generation)
                label=key if side==0 and key in known[0] else known[1].get(key) if side==1 else None
                return ('reference',label)
            if isinstance(value,StreamObject):
                filters=value.get('/Filter');filters=filters.get_object() if isinstance(filters,IndirectObject) else filters
                kinds=[] if filters is None else list(filters) if isinstance(filters,ArrayObject) else [filters]
                if decode and all(item.get_object() in {'/FlateDecode','/ASCII85Decode','/ASCIIHexDecode'} for item in kinds):
                    content=payload(value,'transport');excluded={'/Length','/Filter','/DecodeParms'}
                else:
                    content=payload(value,'raw');excluded={'/Length'}
                return ('stream',content,tuple((str(k),local(v,side,depth+1,decode)) for k,v in sorted(value.items()) if k not in excluded))
            if isinstance(value,DictionaryObject):
                return ('dict',tuple((str(k),local(v,side,depth+1,decode)) for k,v in sorted(value.items())))
            if isinstance(value,ArrayObject):return ('array',tuple(local(v,side,depth+1,decode) for v in value))
            if isinstance(value,_ExactFloat):return ('number',value.exact_decimal.as_integer_ratio())
            if isinstance(value,NumberObject):return ('number',(int(value),1))
            if isinstance(value,(TextStringObject,ByteStringObject)):
                raw=value.original_bytes if isinstance(value,TextStringObject) else bytes(value)
                return ('string',len(raw),hashlib.sha256(raw).digest())
            if isinstance(value,BooleanObject):return ('bool',value.value)
            if isinstance(value,NullObject):return ('null',)
            if isinstance(value,NameObject):return ('name',str(value))
            raise UnsupportedPDF('Unsupported orphan value.')
        buckets={};normalized=None
        for key in sorted(unused[1]):
            check();obj=after.get_object(IndirectObject(*key,after))
            fingerprint=hashlib.sha256(repr(local(obj,1)).encode()).digest()
            buckets.setdefault(fingerprint,[]).append(key)
        for key in sorted(unused[0]):
            if key in forward:continue
            check();obj=before.get_object(IndirectObject(*key,before))
            fingerprint=hashlib.sha256(repr(local(obj,0)).encode()).digest()
            options=[item for item in buckets.get(fingerprint,[]) if item not in reverse]
            if not options:
                if normalized is None:
                    normalized={}
                    for extra in sorted(unused[1]-reverse.keys()):
                        check();value=after.get_object(IndirectObject(*extra,after))
                        token=hashlib.sha256(repr(local(value,1,decode=True)).encode()).digest()
                        normalized.setdefault(token,[]).append(extra)
                token=hashlib.sha256(repr(local(obj,0,decode=True)).encode()).digest()
                options=[item for item in normalized.get(token,[]) if item not in reverse]
            matched=False
            for match in options[:64]:
                mark=len(journal)
                try:
                    push(IndirectObject(*key,before),IndirectObject(*match,after),'unreferenced',0)
                    walk();matched=True;break
                except PreservationMismatch:
                    stack.clear()
                    while len(journal)>mark:
                        added=journal.pop();reverse.pop(forward.pop(added))
            if not matched:raise UnsupportedPDF('Unreferenced object matching is unresolved; use thorough processing.')
        if not unused[1].issubset(reverse):
            raise UnsupportedPDF('Some unreferenced candidate objects were not verified.')
        check()
        return {'policy': POLICY_VERSION, 'complete': True, 'logical_objects': len(forward),
                'streams': streams, 'pages': len(before.pages), 'decoded_bytes_compared': decoded[0],
                'identity_preserved': True, 'unreferenced_objects_preserved': original_orphans, 'visual_checks': 'not run',
                'browser_checks': 'not run', 'rendering_performance': 'not measured'}
