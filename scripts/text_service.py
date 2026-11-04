"""Local font metadata and FreeType/RAQM text measurements for Luau jobs."""
from collections import OrderedDict,deque
from bisect import bisect_right
import io
import json
import math
import queue
import threading

from render_fonts import RenderFonts,MAX_FAMILY,MAX_FONT,decode_family,font_descriptor,font_mime,font_reference,select_face
from render_assets import fetch_visual_asset


def wrap_ranges(text,width,advance,graphemes):
    """Wrap at whitespace, then at extended grapheme boundaries in long words."""
    offset = 0
    for paragraph in text.split('\n'):
        paragraph_start = offset
        offset += len(paragraph) + 1
        if not width or advance(paragraph_start, offset - 1) <= width:
            yield paragraph_start, offset - 1
            continue
        clusters=graphemes(paragraph);start=0
        positions = [paragraph_start]
        for cluster in clusters:
            positions.append(positions[-1] + len(cluster))
        while start<len(clusters):
            # Exponential search avoids re-shaping the entire remaining word
            # for each line when a very narrow container fits one cluster.
            low=0;high=1;remaining=len(clusters)-start
            while high<=remaining and advance(positions[start], positions[start+high])<=width:
                low=high;high*=2
            high=min(high,remaining)
            while low<high:
                middle=(low+high+1)//2
                if advance(positions[start], positions[start+middle])<=width:low=middle
                else:high=middle-1
            count=max(1,low);end=start+count
            if end<len(clusters) and not clusters[end].isspace():
                whitespace=next((i for i in range(end-1,start-1,-1) if clusters[i].isspace()),None)
                if whitespace is not None and whitespace>start:end=whitespace
            line = text[positions[start]:positions[end]].rstrip()
            yield positions[start], positions[start] + len(line)
            start=end
            while start<len(clusters) and clusters[start].isspace():start+=1


def wrap_lines(text,width,advance,graphemes):
    return [text[start:end] for start,end in wrap_ranges(
        text,width,lambda start,end:advance(text[start:end]),graphemes)]


class TextService:
    def __init__(self,fetcher=fetch_visual_asset):
        self.assets=RenderFonts(fetcher);self.fonts=OrderedDict();self.font_bytes=0;self.lock=threading.Lock()

    def family(self,reference):
        reference=font_reference(reference,family=True)
        raw=self.assets.resource(reference,MAX_FAMILY,decode_family)
        data=json.loads(raw)
        if not isinstance(data.get('name'),str) or len(data['name'].encode())>256:raise ValueError('Font family name is unavailable')
        faces=[]
        for face in data['faces']:
            if not isinstance(face.get('name'),str) or len(face['name'].encode())>128:raise ValueError('Font face name is unavailable')
            faces.append({'Name':face['name'],'Weight':face['weight'],'Style':int(face['style']=='italic')})
        return {'Name':data['name'],'Faces':faces}

    def load_font(self,family,weight,style,size):
        from PIL import ImageFont
        faces=decode_family(self.assets.resource(family,MAX_FAMILY,decode_family))
        asset,_,_=select_face(faces,weight,style)
        key=(asset,size)
        with self.lock:
            if key in self.fonts:
                self.fonts.move_to_end(key);font=self.fonts[key][0]
            else:
                raw=self.assets.resource(asset,MAX_FONT,font_mime)
                font=ImageFont.truetype(io.BytesIO(raw),size=size,layout_engine=ImageFont.Layout.RAQM)
                while self.fonts and (len(self.fonts)>=32 or self.font_bytes+len(raw)>32*1024*1024):
                    _,(_,count)=self.fonts.popitem(last=False);self.font_bytes-=count
                self.fonts[key]=(font,len(raw));self.font_bytes+=len(raw)
        return font

    def bounds(self,request):
        family,weight,style=font_descriptor(request.get('font'))
        text,size,width=request.get('text'),request.get('size'),request.get('width')
        if (not isinstance(text,str) or len(text.encode('utf-8'))>4096 or '\0' in text
                or type(size) not in (int,float) or not math.isfinite(size) or not 1<=size<=1000
                or type(width) not in (int,float) or not math.isfinite(width) or not 0<=width<=32768):
            raise ValueError('Invalid text measurement parameters')
        if request.get('rich_text'):
            raise ValueError('Rich text must be parsed by the native worker')
        from PIL import features
        import regex
        if not features.check_feature('raqm'):
            raise ValueError('Text measurements require Pillow with RAQM')
        runs=request.get('runs',[{'text':text,'style':{}}])
        if not isinstance(runs,list) or len(runs)>128:
            raise ValueError('Invalid text runs')
        parts, starts, styles = [], [], []
        source_parts = []
        position = 0
        ends_with_cr = False
        for run in runs:
            if not isinstance(run,dict) or not isinstance(run.get('text'),str) or not isinstance(run.get('style'),dict):
                raise ValueError('Invalid text run')
            raw, overrides = run['text'], run['style']
            source_parts.append(raw)
            content = raw[1:] if ends_with_cr and raw.startswith('\n') else raw
            if raw:
                ends_with_cr = raw.endswith('\r')
            content = content.replace('\r\n','\n').replace('\r','\n').replace('\t',' ')
            if not content:
                continue
            descriptor = {
                'family':overrides.get('family',family),
                'weight':overrides.get('weight',weight),
                'style':overrides.get('style',style),
            }
            run_size = overrides.get('size',size)
            if type(run_size) not in (int,float) or not 1<=run_size<=1000:
                raise ValueError('Invalid text run size')
            size_scale = overrides.get('sizeScale',1)
            if type(size_scale) not in (int,float) or not .5<=size_scale<=1:
                raise ValueError('Invalid text run size scale')
            run_size *= size_scale
            font_spec = (*font_descriptor(descriptor), run_size)
            if not styles or styles[-1] != font_spec:
                starts.append(position)
                styles.append(font_spec)
            parts.append(content)
            position += len(content)
        if ''.join(source_parts)!=text:
            raise ValueError('Text runs do not match their source')
        if not parts:
            return [0,0]
        if len(set(styles))>16:
            raise ValueError('Text measurement font variant limit exceeded')
        text = ''.join(parts)
        fonts = {spec:self.load_font(*spec) for spec in set(styles)}
        measured = {}
        work = 0

        def metrics(start,end):
            nonlocal work
            if start==end:
                return 0,size
            extent = height = 0
            index = bisect_right(starts,start)-1
            while start<end:
                stop = min(end,starts[index+1]) if index+1<len(starts) else end
                spec = styles[index]
                value = text[start:stop]
                key = (spec,value)
                if key not in measured:
                    work += len(value)
                    if work>2_000_000:
                        raise ValueError('Text shaping work limit exceeded')
                    with self.lock:
                        measured[key]=float(fonts[spec].getlength(value))
                extent += measured[key]
                height = max(height,spec[-1])
                start = stop
                index += 1
            return extent,height

        widest = total_height = 0
        for start,end in wrap_ranges(text,width,lambda a,b:metrics(a,b)[0],lambda value:regex.findall(r'\X',value)):
            extent,height = metrics(start,end)
            widest = max(widest,extent)
            total_height += height
        return [math.ceil(widest),math.ceil(total_height)]

    def handle(self,request):
        if not isinstance(request,dict) or type(request.get('request_id')) is not int or request['request_id']<1:
            raise ValueError('Invalid text service request')
        if request.get('kind')=='family':return self.family(request.get('family'))
        if request.get('kind')=='bounds':return self.bounds(request)
        raise ValueError('Unsupported text service request')


class TextServiceQueue:
    def __init__(self,service=None):
        self.service=service or TextService();self.jobs=queue.Queue(maxsize=32)
        self.results=deque();self.pending=set();self.active=set();self.lock=threading.Lock();self.closed=False
        self.workers=[threading.Thread(target=self._work,daemon=True,name='luau-text-service') for _ in range(2)]
        for worker in self.workers:worker.start()

    def submit(self,requests,active):
        if not isinstance(requests,list) or len(requests)>32 or not isinstance(active,list) or len(active)>32:
            raise ValueError('Invalid TextService host request batch')
        with self.lock:
            if self.closed:return
            self.active={n for n in active if type(n) is int and n>0}
            for request in requests:
                token=request.get('request_id') if isinstance(request,dict) else None
                if type(token) is not int or token not in self.active or token in self.pending:continue
                self.pending.add(token)
                try:self.jobs.put_nowait(request)
                except queue.Full:
                    self.pending.discard(token);self.results.append({'request_id':token,'error':'TextService queue is busy'})

    def _work(self):
        while True:
            try:request=self.jobs.get(timeout=.25)
            except queue.Empty:
                with self.lock:
                    if self.closed:return
                continue
            token=request['request_id']
            with self.lock:enabled=not self.closed and token in self.active
            if enabled:
                try:response={'request_id':token,'result':self.service.handle(request)}
                except (ImportError,ModuleNotFoundError):response={'request_id':token,'error':'Text measurement dependencies are unavailable; install requirements-session.txt'}
                except Exception as error:
                    # Asset failures expose stable reasons rather than local
                    # file paths, private URLs, or credentials in tracebacks.
                    response={'request_id':token,'error':'TextService font asset or measurement failed: '+type(error).__name__}
                with self.lock:
                    if not self.closed and token in self.active:self.results.append(response)
            with self.lock:self.pending.discard(token)
            self.jobs.task_done()

    def drain(self):
        with self.lock:
            results = []
            # A family can contain 64 named faces. Keep even a batch of
            # maximum-sized family responses below the native payload limit.
            while self.results and len(results) < 8:
                result = self.results.popleft()
                if result['request_id'] in self.active:
                    results.append(result)
            return results

    def close(self):
        with self.lock:self.closed=True;self.active.clear();self.results.clear()
