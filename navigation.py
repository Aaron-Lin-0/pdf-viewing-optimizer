"""Compare PDF navigation without depending on rewritten object numbers."""
import hashlib
from decimal import Decimal
from pypdf import PdfReader
from pypdf.errors import EmptyFileError, PdfReadError
from pypdf.generic import IndirectObject, StreamObject, NullObject, NumberObject, FloatObject


def reader_for(path):
    try:
        reader=PdfReader(path)
        if reader.is_encrypted:return reader
        return _repair_layer_references(reader)
    except EmptyFileError:raise
    except PdfReadError as exc:
        raise ValueError(f'Not a readable PDF; the file may be damaged or incomplete ({exc}).') from exc


def _repair_layer_references(reader):
    valid={(n,g) for g,rows in reader.xref.items() for n,offset in rows.items()
           if n and offset>0 and not reader.xref_free_entry.get(g,{}).get(n,False)}
    valid.update((n,0) for n in reader.xref_objStm)
    missing=set()
    for page in reader.pages:
        for ref in (page['/Annots'] if '/Annots' in page else []):
            annot=ref.get_object();oc=annot.get('/OC')
            if isinstance(oc,IndirectObject) and (oc.idnum,oc.generation) not in valid:
                if not (oc.generation==0 and oc.idnum in {-1,0,1738407840} and
                        (annot.get('/Name')=='/#ONYX-STROKE' or '/onyxtag' in annot)):
                    raise ValueError(f'Unrecognized broken layer reference {(oc.idnum,oc.generation)}; transformation blocked.')
                missing.add((oc.idnum,oc.generation))
    for n,g in missing:
        reader.resolved_objects[g,n]=NullObject()
    reader.missing_optional_content=sorted(missing)
    return reader


def validate_input(reader):
    if reader.is_encrypted:
        raise ValueError('Encrypted PDFs are not supported; their protection would be lost.')
    if '/Perms' in reader.trailer['/Root'] or any(
            f.get('/FT') == '/Sig' for f in (reader.get_fields() or {}).values()):
        raise ValueError('PDFs with signatures or signature fields are not supported.')
    if not len(reader.pages):
        raise ValueError('Empty PDF: no pages to optimize.')
    # Signature widgets can be missing from the catalog's field tree.
    for page in reader.pages:
        for ref in (page['/Annots'] if '/Annots' in page else []):
            obj = ref.get_object()
            seen = set()
            while isinstance(obj, dict) and id(obj) not in seen:
                seen.add(id(obj))
                if obj.get('/FT') == '/Sig':
                    raise ValueError('Signature fields are not supported.')
                obj = obj['/Parent'] if '/Parent' in obj else None


class Graph:
    def __init__(self, reader):
        self.reader = reader
        self.pages = {}
        for i, page in enumerate(reader.pages):
            ref = page.indirect_reference
            key = (ref.idnum, ref.generation)
            if key in self.pages:
                raise ValueError('Distinct pages were merged; candidate rejected.')
            self.pages[key] = i
        if hasattr(reader, 'xref'):
            self.valid = {(n, g) for g, rows in reader.xref.items() for n,offset in rows.items()
                          if n and offset>0 and not reader.xref_free_entry.get(g,{}).get(n,False)}
            self.valid.update((n, 0) for n in reader.xref_objStm)
        else:
            self.valid = {(n+1, 0) for n,obj in enumerate(reader._objects) if obj is not None}
        self.memo = {}
        self.active = {}
        self.missing_oc = set()

    def resolve(self, obj, optional=False):
        if not isinstance(obj, IndirectObject):
            return obj
        key = (obj.idnum, obj.generation)
        if key not in self.valid:
            if optional and key in getattr(self.reader,'missing_optional_content',[]):
                self.missing_oc.add(key)
                return None
            raise ValueError(f'Unresolved PDF reference {key}; transformation blocked.')
        value = obj.get_object()
        if value is None and not optional:
            raise ValueError(f'Unresolved PDF reference {key}; transformation blocked.')
        return value

    def value(self, obj, optional=False):
        if isinstance(obj, IndirectObject):
            key = (obj.idnum, obj.generation)
            if key in self.pages:
                return ('page', self.pages[key])
        obj = self.resolve(obj, optional)
        if obj is None or isinstance(obj, NullObject):
            return ('null',)
        if not isinstance(obj, (dict, list, tuple)):
            if isinstance(obj,(NumberObject,FloatObject)):
                return ('number',format(Decimal(str(obj)).normalize(),'f'))
            if isinstance(obj, bytes):
                return ('bytes', hashlib.sha256(obj).hexdigest())
            return (type(obj).__name__, str(obj))
        identity = id(obj)
        if identity in self.active:
            return ('cycle', len(self.active) - self.active[identity])
        if identity in self.memo:
            return self.memo[identity][1]
        self.active[identity] = len(self.active)
        if isinstance(obj, dict):
            skip = {'/Length', '/Filter', '/DecodeParms'} if isinstance(obj, StreamObject) else set()
            filters=self.resolve(obj.get('/Filter',[])) if isinstance(obj,StreamObject) else []
            filters=filters if isinstance(filters,list) else [filters]
            if any(self.resolve(f) in {'/DCTDecode','/JPXDecode','/JBIG2Decode','/CCITTFaxDecode'} for f in filters):
                skip.discard('/DecodeParms')
            fields = tuple((str(k), self.value(v, k == '/OC'))
                           for k, v in sorted(obj.items()) if k not in skip)
            if isinstance(obj, StreamObject):
                result = ('stream', hashlib.sha256(obj.get_data()).hexdigest(), fields)
                if hasattr(obj, 'decoded_self'):
                    obj.decoded_self = None
            else:
                result = ('dict', fields)
        else:
            result = ('array', tuple(self.value(v) for v in obj))
        del self.active[identity]
        digest = hashlib.sha256(repr(result).encode()).hexdigest()
        self.memo[identity] = (obj, digest)
        return digest


def navigation_fingerprint(reader, excluded_ink=None):
    graph = Graph(reader)
    root = reader.trailer['/Root']
    if '/OCProperties' in root:
        groups=graph.resolve(root['/OCProperties'].get('/OCGs',[]))
        refs=[(g.idnum,g.generation) for g in groups if isinstance(g,IndirectObject)]
        if len(refs)!=len(set(refs)):
            raise ValueError('Distinct optional-content groups were merged; candidate rejected.')
    seen = set()

    def outline(ref):
        items = []
        while ref:
            node = graph.resolve(ref)
            if id(node) in seen:
                raise ValueError('Cyclic or merged bookmark nodes; candidate rejected.')
            seen.add(id(node))
            fields = {k: v for k, v in node.items()
                      if k not in {'/First', '/Last', '/Next', '/Prev', '/Parent'}}
            items.append((graph.value(fields), outline(node.get('/First'))))
            ref = node.get('/Next')
        return tuple(items)

    outlines = root.get('/Outlines')
    toc = outline(graph.resolve(outlines).get('/First')) if outlines else ()
    outline_root=graph.value({k:v for k,v in graph.resolve(outlines).items() if k not in {'/First','/Last'}}) if outlines else None
    catalog = graph.value({k: v for k, v in root.items() if k not in {'/Pages', '/Outlines'}})
    retained = []
    seen_annotations = set()
    for index, page in enumerate(reader.pages):
        refs = graph.resolve(page.get('/Annots', []))
        skip = set((excluded_ink or {}).get(index, []))
        items = []
        for position, ref in enumerate(refs):
            if position in skip:
                continue
            annot = graph.resolve(ref)
            # Merging two independently interactive objects changes editing/action identity.
            if isinstance(ref, IndirectObject):
                key = (ref.idnum, ref.generation)
                if key in seen_annotations:
                    raise ValueError('Distinct retained annotations were merged; candidate rejected.')
                seen_annotations.add(key)
            items.append(graph.value(annot))
        retained.append(tuple(items))
        graph.memo.clear()
    labels = tuple(graph.value({k:v for k,v in page.items() if k not in {'/Parent','/Contents','/Resources','/Annots'}})
                   for page in reader.pages)
    return toc, outline_root, catalog, tuple(retained), labels, graph.value(dict(reader.metadata or {}))
