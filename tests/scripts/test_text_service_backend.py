"""Font-file measurement and asynchronous worker behavior, without a game."""
import json
import math
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import Mock

from PIL import ImageFont
import regex
from text_service import TextService,TextServiceQueue,wrap_lines

CONTENT=Path('/Applications/Roblox.app/Contents/Resources/content')
FONT=CONTENT/'fonts/BuilderSans-Regular.otf'

def request(text='Measured text',width=0,size=20):
    return {'request_id':1,'kind':'bounds','font':{'family':'rbxasset://fonts/families/BuilderSans.json','weight':400,'style':0},
            'text':text,'size':size,'width':width}


@unittest.skipUnless(FONT.is_file(),'installed Roblox font fixture needed')
class FontMeasurementTests(unittest.TestCase):
    def setUp(self):self.service=TextService()

    def test_family_names_and_face_metadata_match_the_installed_manifest(self):
        expected=json.loads((CONTENT/'fonts/families/BuilderSans.json').read_text())
        result=self.service.family('rbxasset://fonts/families/BuilderSans.json')
        self.assertEqual(result['Name'],expected['name'])
        self.assertEqual(result['Faces'],[{'Name':f['name'],'Weight':f['weight'],'Style':int(f['style']=='italic')} for f in expected['faces']])

    def test_unwrapped_widths_match_freetype_raqm_not_character_count(self):
        for size in [12,20,20.5,48]:
            font=ImageFont.truetype(str(FONT),size=size,layout_engine=ImageFont.Layout.RAQM)
            for text in ['iiiiiiii','WWWWWWWW','AVATAR','hello world','e\u0301','שלום','مرحبا','👩\u200d💻']:
                with self.subTest(text=text,size=size):
                    self.assertEqual(self.service.bounds(request(text,size=size)),[math.ceil(font.getlength(text)),math.ceil(size)])

    def test_newlines_tabs_empty_text_and_unlimited_width(self):
        self.assertEqual(self.service.bounds(request('')),[0,0])
        first=self.service.bounds(request('hello'))
        self.assertEqual(self.service.bounds(request('hello\nhello')),[first[0],40])
        self.assertEqual(self.service.bounds(request('hello\r\nhello')),[first[0],40])
        self.assertEqual(self.service.bounds(request('a\tb')),self.service.bounds(request('a b')))
        self.assertEqual(self.service.bounds(request('hello\n')),[first[0],40])

    def test_wrapping_increases_height_and_never_splits_extended_graphemes(self):
        text='e\u0301'*30;shaper=ImageFont.truetype(str(FONT),size=20)
        width=shaper.getlength('e\u0301')+.1
        lines=wrap_lines(text,width,shaper.getlength,lambda value:regex.findall(r'\X',value))
        self.assertEqual(lines,['e\u0301']*30)
        self.assertEqual(self.service.bounds(request(text,width=width)),[math.ceil(width-.1),600])
        for cluster in ['👩\u200d💻','🇺🇸','क्\u200dष']:
            parts=wrap_lines(cluster*4,.001,shaper.getlength,lambda value:regex.findall(r'\X',value))
            self.assertEqual(parts,[cluster]*4)

    def test_wrapping_prefers_spaces_and_breaks_long_words(self):
        lines=wrap_lines('word word longword',6,len,list)
        self.assertEqual(lines,['word','word','longwo','rd'])
        self.assertEqual(wrap_lines('word word tail',9,len,list),['word word','tail'])
        self.assertGreater(self.service.bounds(request('A long string with several words',width=60))[1],20)

    def test_invalid_inputs_and_rich_text_are_explicit_errors(self):
        for fields in [{'size':True},{'size':0},{'size':1001},{'width':-1},{'width':math.inf},
                       {'text':'x'*4097},{'text':'A\0B'},{'rich_text':True}]:
            with self.assertRaises(ValueError):self.service.bounds({**request(),**fields})

    def test_font_object_cache_and_source_bytes_stay_bounded(self):
        for size in range(12,48):self.service.bounds(request('sample',size=size))
        self.assertLessEqual(len(self.service.fonts),32)
        self.assertLessEqual(self.service.font_bytes,32*1024*1024)


class TextServiceQueueTests(unittest.TestCase):
    def drain_until(self,worker,count):
        result=[];deadline=time.monotonic()+2
        while time.monotonic()<deadline and len(result)<count:
            result+=worker.drain();time.sleep(.005)
        self.assertEqual(len(result),count);return result

    def test_queue_does_not_block_caller_and_close_discards_inflight_result(self):
        entered=threading.Event();release=threading.Event()
        class Service:
            def handle(self,request):entered.set();release.wait(2);return {'Name':'A','Faces':[]}
        worker=TextServiceQueue(Service());self.addCleanup(worker.close);self.addCleanup(release.set)
        start=time.monotonic();worker.submit([{'request_id':1,'kind':'family'}],[1])
        self.assertLess(time.monotonic()-start,.1);self.assertTrue(entered.wait(1))
        worker.close();release.set();time.sleep(.02);self.assertEqual(worker.drain(),[])

    def test_cancelled_requests_do_not_deliver_and_failures_are_catchable(self):
        entered=threading.Event();release=threading.Event()
        class Service:
            def handle(self,request):
                if request['request_id']==1:entered.set();release.wait(2);return [1,2]
                raise ValueError('/private/path/that/must/not/escape')
        worker=TextServiceQueue(Service());self.addCleanup(worker.close);self.addCleanup(release.set)
        worker.submit([{'request_id':1}],[1]);self.assertTrue(entered.wait(1))
        worker.submit([],[]);release.set();time.sleep(.02);self.assertEqual(worker.drain(),[])
        worker.submit([{'request_id':2}],[2]);result=self.drain_until(worker,1)[0]
        self.assertEqual(result['request_id'],2);self.assertIn('ValueError',result['error'])
        self.assertNotIn('/private',result['error'])

    def test_dependency_failures_return_an_actionable_error(self):
        service=Mock();service.handle.side_effect=ImportError('missing dependency')
        worker=TextServiceQueue(service);self.addCleanup(worker.close)
        worker.submit([{'request_id':1}],[1]);result=self.drain_until(worker,1)[0]
        self.assertIn('requirements-session.txt',result['error'])


if __name__=='__main__':unittest.main()
