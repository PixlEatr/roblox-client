"""Bounded font-family and SFNT loading for text in the displayed world."""
import base64
from collections import OrderedDict
import json
import re
import struct
import threading

MAX_FONT=8*1024*1024
MAX_FAMILY=256*1024


def font_reference(value, *, family=False):
    if not isinstance(value,str) or len(value)>512:
        raise ValueError('Invalid font asset reference')
    pattern=r'rbxasset://fonts/families/[A-Za-z0-9_-]+\.json' if family else r'rbxasset://fonts/[A-Za-z0-9_-]+\.(?:ttf|otf)'
    if re.fullmatch(pattern,value):return value
    match=re.fullmatch(r'rbxassetid://([0-9]{1,20})',value,re.I)
    if match and 0<int(match[1])<2**64:return 'rbxassetid://'+str(int(match[1]))
    raise ValueError('Unsupported font asset reference')


def font_descriptor(value):
    if not isinstance(value,dict) or set(value)!={'family','weight','style'}:
        raise ValueError('Invalid displayed font descriptor')
    family=font_reference(value['family'],family=True)
    weight,style=value['weight'],value['style']
    if type(weight) is not int or weight not in range(100,901,100) or type(style) is not int or style not in (0,1):
        raise ValueError('Invalid displayed font weight or style')
    return family,weight,style


def decode_family(raw):
    if not isinstance(raw,bytes) or not 0<len(raw)<=MAX_FAMILY:
        raise ValueError('Font family exceeds size limit')
    try:value=json.loads(raw)
    except (ValueError,UnicodeDecodeError,RecursionError):raise ValueError('Invalid font family JSON') from None
    faces=value.get('faces') if isinstance(value,dict) else None
    if not isinstance(faces,list) or not 0<len(faces)<=64:
        raise ValueError('Invalid font family face count')
    result=[]
    for face in faces:
        if not isinstance(face,dict):raise ValueError('Invalid font family face')
        weight,style=face.get('weight'),face.get('style')
        if type(weight) is not int or weight not in range(100,901,100) or style not in ('normal','italic'):
            raise ValueError('Invalid font family weight or style')
        result.append((font_reference(face.get('assetId')),weight,int(style=='italic')))
    return result


def select_face(faces,weight,style):
    """Style first, then CSS Fonts' weight fallback search order."""
    candidates=[f for f in faces if f[2]==style] or faces
    def rank(face):
        w=face[1]
        if 400<=weight<=500:
            return (0,w) if weight<=w<=500 else (1,-w) if w<weight else (2,w)
        if weight<400:return (0,-w) if w<=weight else (1,w)
        return (0,w) if w>=weight else (1,-w)
    return min(candidates,key=rank)


def font_mime(raw):
    if not isinstance(raw,bytes) or not 12<=len(raw)<=MAX_FONT:
        raise ValueError('Invalid or oversized font file')
    signature=raw[:4]
    if signature not in (b'\x00\x01\x00\x00',b'OTTO',b'true'):
        raise ValueError('Unsupported font file format')
    count=struct.unpack_from('>H',raw,4)[0]
    if not 0<count<=128 or 12+16*count>len(raw):raise ValueError('Invalid font table directory')
    tags=set()
    for i in range(count):
        tag,_,offset,size=struct.unpack_from('>4sIII',raw,12+16*i)
        if tag in tags or offset<12+16*count or offset+size>len(raw):
            raise ValueError('Invalid font table bounds')
        tags.add(tag)
    return 'font/otf' if signature==b'OTTO' else 'font/ttf'


class RenderFonts:
    def __init__(self,fetcher,max_bytes=16*1024*1024):
        self.fetcher=fetcher;self.max_bytes=max_bytes
        self.allowed=set();self.cache=OrderedDict();self.resources=OrderedDict()
        self.bytes=0;self.resource_bytes=0
        self.lock=threading.Lock();self.workers=threading.BoundedSemaphore(2)

    def observe(self,frame):
        allowed=set()
        for gui in frame.get('gui',[])[:600]:
            if not isinstance(gui,dict):continue
            runs = gui.get('richRuns',[])
            nodes = [gui] + runs[:128] if isinstance(runs,list) else [gui]
            for node in nodes:
                if not isinstance(node,dict):continue
                try:allowed.add(font_descriptor(node.get('fontFace')))
                except ValueError:pass
        with self.lock:self.allowed=allowed

    def resource(self,reference,limit,validate):
        with self.lock:
            if reference in self.resources:
                raw=self.resources[reference];self.resources.move_to_end(reference)
            else:raw=None
        if raw is not None:
            validate(raw);return raw
        raw=self.fetcher(reference,max_bytes=limit)
        if not isinstance(raw,bytes) or not 0<len(raw)<=limit:raise ValueError('Font asset exceeds size limit')
        validate(raw)
        with self.lock:
            if reference not in self.resources and len(raw)<=self.max_bytes:
                while self.resources and (self.resource_bytes+len(raw)>self.max_bytes or len(self.resources)>=128):
                    _,old=self.resources.popitem(last=False);self.resource_bytes-=len(old)
                self.resources[reference]=raw;self.resource_bytes+=len(raw)
        return raw

    def get(self,reference,weight,style):
        key=font_descriptor({'family':reference,'weight':weight,'style':style})
        with self.lock:
            if key not in self.allowed:raise ValueError('Font is not referenced by displayed text')
            if key in self.cache:
                self.cache.move_to_end(key);return self.cache[key]
        if not self.workers.acquire(blocking=False):raise ValueError('Font asset queue is busy')
        try:
            faces=decode_family(self.resource(key[0],MAX_FAMILY,decode_family))
            reference,actual_weight,actual_style=select_face(faces,key[1],key[2])
            raw=self.resource(reference,MAX_FONT,font_mime);mime=font_mime(raw)
            result={'ok':True,'mime':mime,'data':base64.b64encode(raw).decode('ascii'),
                    'weight':actual_weight,'style':actual_style}
            size=len(json.dumps(result))
            if size>self.max_bytes:raise ValueError('Decoded font exceeds cache budget')
            with self.lock:
                if key not in self.allowed:raise ValueError('Font is no longer referenced by displayed text')
                if key not in self.cache:
                    while self.cache and (self.bytes+size>self.max_bytes or len(self.cache)>=64):
                        _,old=self.cache.popitem(last=False);self.bytes-=len(json.dumps(old))
                    self.cache[key]=result;self.bytes+=size
            return result
        finally:self.workers.release()
