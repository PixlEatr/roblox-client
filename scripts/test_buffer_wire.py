import json
from pathlib import Path
import unittest

from game_wire import encode_unsigned
from persistent_runtime import PersistentLuauHost
from wire_reader import Reader, WireError
from world_replication import World
from world_snapshot import _encode


class BufferWireTests(unittest.TestCase):
    def read(self,data):
        return World({'classes':[]}).value(Reader(data),{'type':56},set())

    def test_empty_and_binary_buffers_preserve_the_next_field(self):
        for data in (b'',b'\0\xff\x80ABC'):
            reader=Reader(b'\0'+encode_unsigned(len(data))+data+b'\x7f')
            value=World({'classes':[]}).value(reader,{'type':56},set())
            self.assertEqual(value,{'$buffer_hex':data.hex()})
            self.assertEqual(_encode([value],29),[value])
            self.assertEqual(reader.u8(),127);reader.finish()

    def test_bad_lengths_and_invalid_compression_fail_explicitly(self):
        for data in (b'',b'\0',b'\0\x03AB',b'\0'+encode_unsigned(1024*1024+1)):
            with self.assertRaises(WireError):self.read(data)
        with self.assertRaisesRegex(WireError,'Invalid compressed replicated buffer'):
            self.read(b'\x02\x10AB')

    def test_compression_size_truncation_and_extra_frames(self):
        import zstandard
        data=b'\0\xffBuffer fixture'*30
        encoded=zstandard.ZstdCompressor().compress(data)
        prefix=encode_unsigned(len(encoded))+encode_unsigned(len(data))
        self.assertEqual(self.read(prefix+encoded),{'$buffer_hex':data.hex()})
        for length in range(len(encoded)):
            with self.assertRaises(WireError):self.read(prefix+encoded[:length])
        for content,size in [(encoded,len(data)-1),(encoded,len(data)+1),(encoded+encoded,len(data))]:
            with self.assertRaises(WireError):
                self.read(encode_unsigned(len(content))+encode_unsigned(size)+content)

    def test_nested_remote_argument_is_a_mutable_luau_buffer(self):
        snapshot=json.loads((Path(__file__).resolve().parents[1]/'runtime/tests/world_snapshot.json').read_text())
        snapshot['nodes'].append({'id':'buffer:remote','class_name':'RemoteEvent','name':'BufferEvent',
                                 'parent':'scope:workspace','properties':{}})
        with PersistentLuauHost(snapshot) as host:
            result=host.call({'op':'execute','source':'''getgenv().received={}
workspace.BufferEvent.OnClientEvent:Connect(function(args)table.insert(received,args[1])end)
''','timeout_ms':100})
            self.assertTrue(result['ok'],result.get('error'))
            value=self.read(b'\0\x04\0\xffAB')
            actions=[{'sequence':100+i,'kind':'event','id':'buffer:remote','name':'OnClientEvent',
                      'arguments':[_encode([value],29)]} for i in range(2)]
            result=host.call({'op':'update','delta':{'version':1,'delta':True,'sequence':101,
                                                   'actions':actions},'timeout_ms':100})
            self.assertTrue(result['ok'],result.get('error'))
            result=host.call({'op':'execute','source':'''buffer.writeu8(received[1],0,7)
return typeof(received[1]),buffer.len(received[1]),buffer.readu8(received[1],1),
    buffer.readu8(received[1],0),buffer.readu8(received[2],0)
''','timeout_ms':100})
            self.assertTrue(result['ok'],result.get('error'))
            self.assertEqual(result['values'],['buffer',4,255,7,0])

    def test_cpp_worker_constructs_buffer_from_received_property(self):
        from local_runtime import run_source
        snapshot=json.loads((Path(__file__).resolve().parents[1]/'runtime/tests/world_snapshot.json').read_text())
        node=next(n for n in snapshot['nodes'] if n['id']=='scope:root-1')
        node['properties']['Payload']=self.read(b'\0\x03\0\xffA')
        result=run_source('''local p=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
return typeof(p.Payload),buffer.len(p.Payload),buffer.readu8(p.Payload,1)
''',world_snapshot=snapshot,timeout_ms=1000)
        self.assertTrue(result['ok'],result.get('error'))
        self.assertEqual(result['values'],['buffer',3,255])


if __name__ == '__main__':unittest.main()
