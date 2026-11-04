"""Font assets, scoped loading, installed files, and native render projection."""
import base64
import copy
import json
import os
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import Mock,patch

from render_assets import RenderAssets,fetch_visual_asset
from render_fonts import (MAX_FAMILY,decode_family,font_descriptor,font_mime,font_reference,select_face)
from persistent_runtime import PersistentLuauHost

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tests/helpers'))
from font_showcase import install_font_showcase

def sfnt(signature=b'\x00\x01\x00\x00'):
    return signature+struct.pack('>4H',1,16,0,0)+struct.pack('>4sIII',b'head',0,28,4)+b'font'

def family(*faces):
    return json.dumps({'name':'Fixture','faces':[{'weight':w,'style':s,'assetId':a} for a,w,s in faces]}).encode()

def descriptor(name='Example',weight=400,style=0):
    return {'family':f'rbxasset://fonts/families/{name}.json','weight':weight,'style':style}


class RenderFontTests(unittest.TestCase):
    def test_asset_references_only_allow_font_locations_or_asset_ids(self):
        self.assertEqual(font_reference('rbxassetid://007',family=True),'rbxassetid://7')
        self.assertEqual(font_reference('rbxasset://fonts/families/BuilderSans.json',family=True),descriptor('BuilderSans')['family'])
        self.assertEqual(font_reference('rbxasset://fonts/BuilderSans-Regular.otf'),'rbxasset://fonts/BuilderSans-Regular.otf')
        for value in ['file:///etc/passwd','https://example.com/font.ttf','rbxasset://fonts/../secrets/a.ttf','rbxasset://fonts/families/A/B.json','rbxassetid://0','rbxassetid://18446744073709551616',None]:
            for is_family in (True,False):
                with self.subTest(value=value,family=is_family):
                    with self.assertRaises(ValueError):font_reference(value,family=is_family)

    def test_descriptor_types_and_weights_are_strict(self):
        self.assertEqual(font_descriptor(descriptor()),(descriptor()['family'],400,0))
        for value in [None,{},dict(descriptor(),weight=True),dict(descriptor(),weight=450),dict(descriptor(),style=True),dict(descriptor(),style=2),dict(descriptor(),extra=1)]:
            with self.assertRaises(ValueError):font_descriptor(value)

    def test_family_decoding_rejects_invalid_faces_and_external_references(self):
        good=family(('rbxasset://fonts/Regular.ttf',400,'normal'))
        self.assertEqual(decode_family(good),[('rbxasset://fonts/Regular.ttf',400,0)])
        invalid=[b'',b'bad',b'[]',b'{}',b'{"faces":[]}',b'x'*(MAX_FAMILY+1),
                 family(('https://example.com/a.ttf',400,'normal')),
                 family(('rbxassetid://1',450,'normal')),family(('rbxassetid://1',400,'oblique')),
                 json.dumps({'faces':[None]}).encode(),family(*[('rbxassetid://1',400,'normal')]*65)]
        for value in invalid:
            with self.subTest(value=value[:50]):
                with self.assertRaises(ValueError):decode_family(value)

    def test_css_weight_fallback_and_style_preference(self):
        faces=[('regular',400,0),('medium',500,0),('bold',700,0),('light-italic',300,1)]
        for weight,style,expected in [(400,0,'regular'),(500,0,'medium'),(600,0,'bold'),(800,0,'bold'),(300,0,'regular'),(700,1,'light-italic')]:
            self.assertEqual(select_face(faces,weight,style)[0],expected)
        self.assertEqual(select_face([('light',300,0),('medium',500,0)],400,0)[0],'medium')
        self.assertEqual(select_face([('regular',400,0),('bold',700,0)],500,0)[0],'regular')
        self.assertEqual(select_face([('regular',400,0)],400,1)[0],'regular')

    def test_sfnt_headers_and_table_bounds(self):
        self.assertEqual(font_mime(sfnt()),'font/ttf')
        self.assertEqual(font_mime(sfnt(b'OTTO')),'font/otf')
        bad_offset=bytearray(sfnt());struct.pack_into('>I',bad_offset,20,9999)
        bad_count=bytearray(sfnt());struct.pack_into('>H',bad_count,4,129)
        for raw in [b'',b'woff'+b'\0'*100,sfnt()[:-1],bytes(bad_offset),bytes(bad_count)]:
            with self.assertRaises(ValueError):font_mime(raw)

    def test_current_text_scope_required_before_cached_or_uncached_fetch(self):
        fetcher=Mock(side_effect=[family(('rbxasset://fonts/Test.ttf',400,'normal')),sfnt()])
        store=RenderAssets(fetcher);d=descriptor()
        with self.assertRaises(ValueError):store.get('font',d['family'],weight=400,style=0)
        fetcher.assert_not_called();store.observe({'gui':[{'fontFace':d}]})
        result=store.get('font',d['family'],weight=400,style=0)
        self.assertEqual(base64.b64decode(result['data']),sfnt())
        self.assertIs(store.get('font',d['family'],weight=400,style=0),result)
        self.assertEqual(fetcher.call_count,2)
        with self.assertRaises(ValueError):store.get('font',d['family'],weight=700,style=0)
        store.observe({})
        with self.assertRaises(ValueError):store.get('font',d['family'],weight=400,style=0)

    def test_style_variants_reuse_family_and_file_cache(self):
        fetcher=Mock(side_effect=[family(('rbxasset://fonts/Test.ttf',400,'normal')),sfnt()])
        store=RenderAssets(fetcher);a,b=descriptor(),descriptor(style=1)
        store.observe({'gui':[{'fontFace':a},{'fontFace':b}]})
        regular=store.get('font',a['family'],weight=400,style=0)
        fallback=store.get('font',a['family'],weight=400,style=1)
        self.assertEqual(regular['data'],fallback['data']);self.assertEqual(fallback['style'],0)
        self.assertEqual(fetcher.call_count,2)

    def test_scope_revoked_during_fetch_does_not_return_or_cache_font(self):
        store=None
        def fetch(reference,**kwargs):
            if reference.endswith('.json'):return family(('rbxasset://fonts/Test.ttf',400,'normal'))
            store.observe({});return sfnt()
        store=RenderAssets(fetch);store.observe({'gui':[{'fontFace':descriptor()}]})
        with self.assertRaisesRegex(ValueError,'no longer'):store.get('font',descriptor()['family'],weight=400,style=0)
        self.assertFalse(store.fonts.cache)

    def test_failed_family_or_font_does_not_poison_response_cache(self):
        fetcher=Mock(side_effect=[b'bad',family(('rbxasset://fonts/Test.ttf',400,'normal')),sfnt()])
        store=RenderAssets(fetcher);store.observe({'gui':[{'fontFace':descriptor()}]})
        with self.assertRaises(ValueError):store.get('font',descriptor()['family'],weight=400,style=0)
        self.assertFalse(store.fonts.cache)
        self.assertFalse(store.fonts.resources)
        result=store.get('font',descriptor()['family'],weight=400,style=0)
        self.assertTrue(result['ok']);self.assertEqual(fetcher.call_count,3)

    def test_cache_eviction_and_busy_workers_stay_bounded(self):
        data=family(('rbxassetid://100',400,'normal'))
        fetcher=lambda ref,**kwargs:sfnt() if ref=='rbxassetid://100' else data
        store=RenderAssets(fetcher);fonts=[descriptor('Family'+str(i)) for i in range(70)]
        store.observe({'gui':[{'fontFace':d} for d in fonts]})
        for d in fonts:store.get('font',d['family'],weight=400,style=0)
        self.assertEqual(len(store.fonts.cache),64)
        self.assertLessEqual(store.fonts.bytes,store.fonts.max_bytes)
        self.assertLessEqual(store.fonts.resource_bytes,store.fonts.max_bytes)
        self.assertTrue(store.fonts.workers.acquire(False));self.assertTrue(store.fonts.workers.acquire(False))
        try:
            with self.assertRaisesRegex(ValueError,'busy'):store.get('font',fonts[0]['family'],weight=400,style=0)
        finally:store.fonts.workers.release();store.fonts.workers.release()

    @unittest.skipUnless(Path('/Applications/Roblox.app/Contents/Resources/content/fonts/families/BuilderSans.json').is_file(),'installed Roblox font fixture needed')
    def test_installed_roblox_font_bytes_load_without_network(self):
        d=descriptor('BuilderSans',700)
        with patch.dict(os.environ,{'ROBLOX_CONTENT_DIR':'/Applications/Roblox.app/Contents/Resources/content'}):
            store=RenderAssets();store.observe({'gui':[{'fontFace':d}]})
            result=store.get('font',d['family'],weight=700,style=0)
        self.assertEqual(result['mime'],'font/otf');self.assertEqual(result['weight'],700)
        self.assertGreater(len(base64.b64decode(result['data'])),1000)


class FontProjectionTests(unittest.TestCase):
    def test_native_luau_font_choices_reach_rendered_text_and_update(self):
        host=PersistentLuauHost(json.loads((ROOT/'tests/fixtures/world_snapshot.json').read_text()));self.addCleanup(host.close)
        install_font_showcase(host)
        response=host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[],
            'display':{'size':[1000,600],'device_insets':[0,0,0,0],'core_insets':[0,0,0,0]}}})
        self.assertTrue(response['ok'],response)
        frame=host.call({'op':'render'})['frame'];labels={g['name']:g for g in frame['gui'] if g['name'].startswith('FontSample')}
        self.assertEqual(len(labels),5)
        self.assertEqual(labels['FontSample2']['fontFace'],descriptor('BuilderSans',700))
        self.assertEqual(labels['FontSample3']['fontFace'],descriptor('SourceSansPro',400,1))
        self.assertEqual(labels['FontSample4']['fontFace'],descriptor('Inconsolata'))
        result=host.call({'op':'execute','source':'game:GetService("CoreGui").FontShowcase.Frame.FontSample1.Font=Enum.Font.SourceSansBold','timeout_ms':1000})
        self.assertTrue(result['ok'],result)
        updated=host.call({'op':'render'})['frame'];label=next(g for g in updated['gui'] if g['name']=='FontSample1')
        self.assertEqual(label['fontFace'],descriptor('SourceSansPro',700))


if __name__=='__main__':unittest.main()
