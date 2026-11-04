import struct
import unittest
import gzip
import hashlib
import time
from types import SimpleNamespace

from mesh_asset_decoder import (DecodedMeshAsset, MeshAssetError, _MeshAssetRedirect,
                                decode_filemesh, mesh_asset_id,
                                MeshAssetQueue,
                                visual_meshes_from_world,
                                visual_mesh_world_geometry)


class FileMeshDecoderTests(unittest.TestCase):
    @staticmethod
    def fixture_mesh_bytes():
        vectors=[(0,0,0),(0,1,0),(0,0,0),(1,0,0),(0,1,0),(0,0,0),(0,0,1),(0,1,0),(0,0,0)]
        return b'version 1.01\n1\n'+b''.join(('[%s,%s,%s]'%v).encode() for v in vectors)

    def test_decodes_v1_ascii_visual_triangles(self):
        vectors=[(0,0,0),(0,1,0),(0,0,0),(1,0,0),(0,1,0),(0,0,0),(0,0,1),(0,1,0),(0,0,0)]
        raw=b'version 1.01\n1\n'+b''.join(
            ('[%s,%s,%s]'%v).encode() for v in vectors)
        mesh=decode_filemesh(raw)
        self.assertEqual(mesh.format_version,'version 1.01')
        self.assertEqual(mesh.vertices[1],(1.,0.,0.))
        self.assertEqual(mesh.triangles,((0,1,2),))
        self.assertEqual(len(mesh.sha256),64)
        world=visual_mesh_world_geometry(mesh,(10,2,3),(1,0,0,0,1,0,0,0,1),(2,1,1),source='part')
        self.assertEqual(world.vertices[1],(12.,2.,3.))
        self.assertIn('CollisionFidelity geometry is unknown',world.assumptions[0])

    def test_decodes_v2_and_applies_count_and_index_checks(self):
        header=struct.pack('<HBBII',12,36,12,3,1)
        vertices=b''.join(struct.pack('<9f',*xyz,0,1,0,0,0,0) for xyz in ((0,0,0),(1,0,0),(0,0,1)))
        raw=b'version 2.00\n'+header+vertices+struct.pack('<3I',0,1,2)
        mesh=decode_filemesh(raw)
        self.assertEqual(mesh.vertices[1],(1.,0.,0.))
        self.assertEqual(mesh.triangles,((0,1,2),))
        for malformed in (raw[:-1],raw+b'junk',b'version 3.00\n'+b'x'):
            with self.assertRaises(MeshAssetError): decode_filemesh(malformed)

    def test_rejects_invalid_legacy_data_and_oversized_input(self):
        with self.assertRaises(MeshAssetError): decode_filemesh(b'version 1.00\n1\n[NaN,0,0]')
        with self.assertRaises(MeshAssetError): decode_filemesh(b'x'*(16*1024*1024+1))

    def test_decodes_gzip_wrapped_mesh_and_hashes_original_asset_bytes(self):
        plain=b'version 1.01\n1\n'+b''.join(
            ('[%s,%s,%s]'%v).encode() for v in [(0,0,0),(0,1,0),(0,0,0),(1,0,0),(0,1,0),(0,0,0),(0,0,1),(0,1,0),(0,0,0)])
        compressed=gzip.compress(plain)
        mesh=decode_filemesh(compressed)
        self.assertEqual(mesh.sha256,hashlib.sha256(compressed).hexdigest())

    def test_reference_normalization_and_redirect_credential_stripping(self):
        self.assertEqual(mesh_asset_id(b'rbxassetid://00123'),'123')
        for value in ('rbxassetid://0','https://evil.example/asset?id=123','file:///tmp/x'):
            with self.assertRaises(MeshAssetError): mesh_asset_id(value)
        request=__import__('urllib.request').request.Request(
            'https://assetdelivery.roblox.com/v1/asset/?id=123',headers={'Cookie':'private'})
        redirect=_MeshAssetRedirect().redirect_request(request,None,302,'Found',{},'https://t1.rbxcdn.com/content')
        self.assertFalse(redirect.has_header('Cookie'))
        with self.assertRaises(MeshAssetError):
            _MeshAssetRedirect().redirect_request(request,None,302,'Found',{},'https://example.com/content')

    def test_received_mesh_content_builds_opt_in_world_visual_geometry(self):
        from physics_ground import extract_ground
        properties=[{'name':'CanCollide','type':9},{'name':'Anchored','type':9},
                    {'name':'size','type':22},{'name':'CFrame','type':27},
                    {'name':'shape','type':7,'enum':0},{'name':'ReplicationPV','type':57},
                    {'name':'MeshContent','type':61}]
        classes=[{'name':'Part','properties':list(range(6))},
                 {'name':'Terrain','properties':[]},
                 {'name':'MeshPart','properties':list(range(len(properties)))}]
        props={'CanCollide':True,'Anchored':True,'size':[2,2,2],
               'CFrame':{'position':[4,5,6],'rotation':[1,0,0,0,1,0,0,0,1]},
               'MeshContent':b'rbxassetid://123'}
        world=SimpleNamespace(schema={'classes':classes,'properties':properties,
            'enums':[{'name':'PartType'}]},instances={'part':{'class':2,'parent':None,'properties':props}},
            property_versions={})
        asset=DecodedMeshAsset(((0,0,0),(2,0,0),(0,2,0),(0,0,2)),
                               ((0,1,2),(0,3,1),(0,2,3)),'version 2.00','a'*64)
        visual=visual_meshes_from_world(world,{b'rbxassetid://123':asset})
        self.assertEqual(visual['part'].vertices[0],(3.,4.,5.))
        strict=extract_ground(world,visual_meshes=visual)
        self.assertFalse(strict.colliders)
        approx=extract_ground(world,visual_meshes=visual,allow_visual_mesh_approximation=True)
        self.assertEqual(len(approx.colliders),1)
        self.assertIn('CollisionFidelity',approx.colliders[0].assumptions[-1])

    def test_bounded_mesh_asset_queue_deduplicates_and_returns_only_completed_meshes(self):
        calls=[]; raw=self.fixture_mesh_bytes()
        queue=MeshAssetQueue(fetcher=lambda ref:calls.append(ref) or raw,max_assets=4,workers=1)
        self.addCleanup(queue.close)
        queue.submit([b'rbxassetid://123',b'rbxassetid://123'])
        ready={}
        deadline=time.monotonic()+2
        while not ready and time.monotonic()<deadline:
            ready=queue.drain(); time.sleep(.005)
        self.assertIn(b'rbxassetid://123',ready)
        self.assertEqual(len(ready[b'rbxassetid://123'].triangles),1)
        self.assertEqual(len(calls),1)

    def test_mesh_asset_queue_reports_cache_budget_failure_without_payload(self):
        queue=MeshAssetQueue(fetcher=lambda _:self.fixture_mesh_bytes(),max_assets=2,
                             workers=1,max_cache_bytes=0)
        self.addCleanup(queue.close)
        ref=b'rbxassetid://456'; queue.submit([ref]); deadline=time.monotonic()+2
        while not queue.errors() and time.monotonic()<deadline:
            queue.drain(); time.sleep(.005)
        self.assertEqual(queue.errors()[ref],'mesh_asset_cache_size_limit')


if __name__=='__main__': unittest.main()
