"""Independent normalized comparisons of original and inlined BOOX drawing trees."""
import hashlib
import re
from pypdf.generic import StreamObject
from handwriting import FORM_CALL, GROUP, HEADER, PATH, SEGMENT, PEN_STATE, _inline_leaf, duplicate_layer_match
from navigation import Graph

PREFIX = re.compile(rb'q\n/InlinePen[0-9]* gs\n0 0 0 RG\n1 j 1 J\n')


def ink_body(body):
    segments = list(SEGMENT.finditer(body))
    if segments and b''.join(s[0] for s in segments) == body:
        values = ((s[1],s[2],s[3]) for s in segments)
    else:
        groups = list(GROUP.finditer(body))
        if not groups or b''.join(g[0] for g in groups) != body:
            return None
        values = ((g[1],p[1],p[2]) for g in groups for p in PATH.finditer(g[2]))
    digest = hashlib.sha256()
    for width,start,end in values:
        digest.update(width+b'|'+start+b'|'+end+b'\n')
    return digest.hexdigest()


def clip_for(form):
    x0,y0,x1,y1 = [str(n).encode() for n in form['/BBox']]
    return b' '.join([x0,y0,b'm',x1,y0,b'l',x1,y1,b'l',x0,y1,b'l h W n\n'])


def page_signature(reader, index, normalize_duplicate=False, excluded_names=()):
    graph = Graph(reader)
    cache, active = {}, set()
    def target(obj):
        obj = graph.resolve(obj)
        if not isinstance(obj,StreamObject): return graph.value(obj)
        if obj.get('/Subtype') != '/Form': return graph.value(obj)
        if id(obj) in cache: return cache[id(obj)]
        if id(obj) in active: raise ValueError('Cyclic drawing resource graph.')
        active.add(id(obj))
        data = obj.get_data()
        resources = obj.get('/Resources',{}).get_object() if hasattr(obj.get('/Resources',{}),'get_object') else obj.get('/Resources',{})
        calls = list(FORM_CALL.finditer(data))
        sequence = None;used_names=set();added_states=set()
        if calls and b''.join(c[0] for c in calls)==data:
            sequence = []
            for c in calls:
                child = resources['/XObject']['/'+c[2].decode()]
                used_names.add('/'+c[2].decode())
                raw = child.get_data()
                ink = ink_body(raw[len(HEADER):]) if raw.startswith(HEADER) else None
                if c[1]==b'1 0 0 1 0 0' and ink and _inline_leaf(child) is not None:
                    sequence.append(('ink',clip_for(child),ink))
                else: sequence.append(('call',c[1],c[2],target(child)))
        elif PREFIX.search(data):
            sequence=[];cursor=0
            while cursor<len(data):
                c=FORM_CALL.match(data,cursor)
                if c:
                    used_names.add('/'+c[2].decode())
                    sequence.append(('call',c[1],c[2],target(resources['/XObject']['/'+c[2].decode()])))
                    cursor=c.end();continue
                prefix=PREFIX.match(data,cursor)
                if not prefix: raise ValueError('Unrecognized saved handwriting wrapper.')
                gs=prefix[0].split()[1].decode()
                if dict(resources['/ExtGState'][gs])!=PEN_STATE: raise ValueError('Saved pen state changed.')
                added_states.add(gs)
                cursor=prefix.end()
                while data[cursor:cursor+2]==b'q\n':
                    start=cursor+2;end=data.index(b'\n',start)+1
                    clip=data[start:end];body_end=data.index(b'Q\n',end)
                    ink=ink_body(data[end:body_end])
                    if not ink or not clip.endswith(b'l h W n\n'): raise ValueError('Saved ink grammar changed.')
                    sequence.append(('ink',clip,ink));cursor=body_end+2
                if data[cursor:cursor+2]!=b'Q\n': raise ValueError('Saved scope changed.')
                cursor+=2
        if sequence is not None:
            extras={k:v for k,v in resources.items() if k not in {'/XObject','/ExtGState'}}
            states={k:v for k,v in graph.resolve(resources.get('/ExtGState',{})).items() if k not in added_states}
            if states:extras['/ExtGState']=states
            unused=tuple((str(k),target(v)) for k,v in sorted(graph.resolve(resources.get('/XObject',{})).items()) if k not in used_names)
            result=('container',tuple(sequence),graph.value(extras),unused,graph.value({k:v for k,v in obj.items() if k not in {'/Length','/Filter','/DecodeParms','/Resources'}}))
        else:
            ink=ink_body(data[len(HEADER):]) if data.startswith(HEADER) else None
            content=('segments',ink) if ink else ('bytes',hashlib.sha256(data).hexdigest())
            nested=tuple((str(k),target(v)) for k,v in sorted(graph.resolve(resources.get('/XObject',{})).items()))
            result=(content,nested,graph.value({k:v for k,v in resources.items() if k!='/XObject'}),graph.value({k:v for k,v in obj.items() if k not in {'/Length','/Filter','/DecodeParms','/Resources'}}))
        active.remove(id(obj));cache[id(obj)]=result
        return result
    page=reader.pages[index];content=page.get_contents();data=content.get_data() if content is not None else b''
    if normalize_duplicate:
        match=duplicate_layer_match(page,reader)
        if match:data=data[:match.start()]+match[1]+data[match.end():]
    resources=page.get('/Resources',{}).get_object() if hasattr(page.get('/Resources',{}),'get_object') else page.get('/Resources',{})
    drawings=tuple((str(k),target(v)) for k,v in sorted(graph.resolve(resources.get('/XObject',{})).items()) if k not in excluded_names)
    return data,drawings,graph.value({k:v for k,v in resources.items() if k!='/XObject'})
