import copy
import os
from pathlib import Path
import struct
import unittest
from unittest import mock

from received_attributes import decode_attributes, attribute_snapshot, attribute_update
from game_wire import encode_unsigned
from replication_out import guid
from test_world_snapshot import fixture
from world_snapshot import make_world_snapshot, make_world_delta
import persistent_runtime


def string(value):return struct.pack('<I',len(value))+value
def blob(entries):return struct.pack('<I',len(entries))+b''.join(string(n.encode())+v for n,v in entries)
def update_packet(key,name,value):
    name=name.encode()
    return b'\x83\x15'+guid(key)+encode_unsigned(len(name))+name+encode_unsigned(len(value))+value+b'\0'


class ReceivedAttributeTests(unittest.TestCase):
    def test_empty_constructor_attributes_require_complete_fresh_creation(self):
        for missing in ('fresh','complete'):
            state={'fresh':True,'complete':True,'ordinary':True};state[missing]=False
            self.assertEqual(attribute_snapshot({},creation=state),{})
        world,key=fixture();node=next(n for n in make_world_snapshot(world,123)['nodes'] if n['id']=='1:4')
        self.assertEqual(node['attributes'],{});self.assertTrue(node['attributes_known'])
        self.assertEqual(node['attributes_origin'],'constructor_default')
        world.packet(update_packet(key,'ap',b'\xff'))
        node=next(n for n in make_world_snapshot(world,123)['nodes'] if n['id']=='1:4')
        self.assertFalse(node['attributes_known']);self.assertEqual(node['attributes'],{})

    def test_cached_creation_exposes_nil_then_received_value_without_fake_attributes(self):
        world,key=fixture()
        world.creation[key].update(ordinary=False,property_lists=1)
        snapshot=make_world_snapshot(world,123)
        node=next(n for n in snapshot['nodes'] if n['id']=='1:4')
        self.assertTrue(node['attributes_known']);self.assertEqual(node['attributes'],{})
        binary=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(persistent_runtime.RUST_HOST)))
        with mock.patch.object(persistent_runtime,'RUST_HOST',binary):host=persistent_runtime.PersistentLuauHost(snapshot)
        self.addCleanup(host.close)
        r=host.call({'op':'execute','timeout_ms':100,'source': '''
p=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
assert(p:GetAttribute("ap")==nil and next(p:GetAttributes())==nil)
'''})
        self.assertTrue(r['ok'],r)
        cursor=world.revision;world.packet(update_packet(key,'ap',b'\x06'+struct.pack('<d',19)))
        r=host.call({'op':'update','timeout_ms':100,'delta':make_world_delta(world,cursor)})
        self.assertTrue(r['ok'],r)
        r=host.call({'op':'execute','timeout_ms':100,'source':'assert(p:GetAttribute("ap")==19 and p:GetAttribute("unset")==nil)'})
        self.assertTrue(r['ok'],r)
        self.assertFalse(attribute_snapshot({'AttributesReplicate':b'\xff'},creation=world.creation[key])['attributes_known'])

    def test_fresh_constructor_exposes_nil_and_empty_attributes_to_luau(self):
        world,key=fixture()
        binary=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(persistent_runtime.RUST_HOST)))
        with mock.patch.object(persistent_runtime,'RUST_HOST',binary):host=persistent_runtime.PersistentLuauHost(make_world_snapshot(world,123))
        self.addCleanup(host.close)
        r=host.call({'op':'execute','timeout_ms':100,'source': '''
local p=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
assert(p:GetAttribute("unset")==nil and next(p:GetAttributes())==nil)
p:SetAttribute("Known",7)
assert(p:GetAttributes().Known==7 and p:GetAttribute("unset")==nil)
'''})
        self.assertTrue(r['ok'],r)

    def test_incremental_native_value_format_is_not_a_full_blob(self):
        self.assertEqual(attribute_update(b'ap',b'\x03\x01'),{'name':'ap','known':True,'value':True})
        self.assertEqual(attribute_update(b'text',b'\x02'+string(b'\xff')),{'name':'text','known':True,'value':{'$bytes_hex':'ff'}})
        self.assertEqual(attribute_update(b'ap',b'\x01'),{'name':'ap','known':True,'value':None})
        for data in (b'',b'\x03\x02',b'\x03\x01extra',b'\xff',b'\x01extra'):
            self.assertEqual(attribute_update(b'ap',data),{'name':'ap','known':False})

    def test_incremental_receipt_order_snapshot_merge_and_full_replacement(self):
        world,key=fixture();world.instances[key]['properties']['AttributesReplicate']=blob([('other',b'\x03\x01')])
        cursor=world.revision
        for value in (False,True,False):world.packet(update_packet(key,'ap',bytes((3,value))))
        actions=make_world_delta(world,cursor)['actions']
        self.assertEqual([a['attribute_update']['value'] for a in actions],[False,True,False])
        node=next(n for n in make_world_snapshot(world,123)['nodes'] if n['id']=='1:4')
        self.assertEqual(node['attributes'],{'other':True,'ap':False});self.assertTrue(node['attributes_known'])
        world.packet(update_packet(key,'ap',b'\x01'))
        node=next(n for n in make_world_snapshot(world,123)['nodes'] if n['id']=='1:4')
        self.assertEqual(node['attributes'],{'other':True});self.assertTrue(node['attributes_known'])
        world.instances[key]['properties']['AttributesReplicate']=b'';world.touch(key,['AttributesReplicate'])
        node=next(n for n in make_world_snapshot(world,123)['nodes'] if n['id']=='1:4')
        self.assertEqual(node['attributes'],{});self.assertTrue(node['attributes_known'])

    def test_incremental_updates_reach_luau_signals_and_invalidate_unknown_values(self):
        world,key=fixture();world.instances[key]['properties']['AttributesReplicate']=b''
        binary=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(persistent_runtime.RUST_HOST)))
        with mock.patch.object(persistent_runtime,'RUST_HOST',binary):host=persistent_runtime.PersistentLuauHost(make_world_snapshot(world,123))
        self.addCleanup(host.close)
        r=host.call({'op':'execute','timeout_ms':100,'source': '''
p=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
changes=0;p:GetAttributeChangedSignal("ap"):Connect(function()changes+=1 end)
assert(p:GetAttribute("ap")==nil)
'''})
        self.assertTrue(r['ok'],r)
        for data in (b'\x03\0',b'\x03\x01',b'\x03\x01'):
            cursor=world.revision;world.packet(update_packet(key,'ap',data))
            r=host.call({'op':'update','timeout_ms':100,'delta':make_world_delta(world,cursor)});self.assertTrue(r['ok'],r)
        r=host.call({'op':'execute','timeout_ms':100,'source':'assert(p:GetAttribute("ap")==true and changes==2)'})
        self.assertTrue(r['ok'],r)
        cursor=world.revision;world.packet(update_packet(key,'ap',b'\xff'))
        host.call({'op':'update','timeout_ms':100,'delta':make_world_delta(world,cursor)})
        r=host.call({'op':'execute','timeout_ms':100,'source':'assert(changes==3 and not pcall(function()return p:GetAttribute("ap")end))'})
        self.assertTrue(r['ok'],r)
        node=next(n for n in make_world_snapshot(world,123)['nodes'] if n['id']=='1:4')
        self.assertEqual(node['attributes'],{});self.assertFalse(node['attributes_known'])
        cursor=world.revision;world.packet(update_packet(key,'ap',b'\x01'))
        host.call({'op':'update','timeout_ms':100,'delta':make_world_delta(world,cursor)})
        r=host.call({'op':'execute','timeout_ms':100,'source':'assert(changes==4 and p:GetAttribute("ap")==nil)'})
        self.assertTrue(r['ok'],r)

    def test_recreated_explicit_unknown_attributes_do_not_preserve_stale_values(self):
        world,key=fixture();world.instances[key]['properties']['AttributesReplicate']=blob([('ap',b'\x03\x01')])
        snapshot=make_world_snapshot(world,123);node=next(n for n in snapshot['nodes'] if n['id']=='1:4')
        binary=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(persistent_runtime.RUST_HOST)))
        with mock.patch.object(persistent_runtime,'RUST_HOST',binary):host=persistent_runtime.PersistentLuauHost(snapshot)
        self.addCleanup(host.close);node=copy.deepcopy(node);node.update(attributes={},attributes_known=False)
        host.call({'op':'update','timeout_ms':100,'delta':{'version':1,'delta':True,'actions':[{'kind':'create','sequence':world.revision+1,'id':'1:4','node':node}]}})
        r=host.call({'op':'execute','timeout_ms':100,'source':'assert(not pcall(function()return game:GetService("Players").LocalPlayer.Character.HumanoidRootPart:GetAttribute("ap")end))'})
        self.assertTrue(r['ok'],r)

    def test_captured_property_layout_and_binary_strings(self):
        # Public engine property name from a received Lighting blob.
        data=bytes.fromhex('01000000260000005242585f4c69676874696e67546563686e6f6c6f6779556e69666965644d6967726174696f6e0301')
        self.assertEqual(decode_attributes(data),{'RBX_LightingTechnologyUnifiedMigration':True})
        data=blob([('bytes',b'\x02'+string(b'\0\xff')),('n',b'\x06'+struct.pack('<d',12.5)),
                   ('v',b'\x11'+struct.pack('<fff',1,2,3))])
        self.assertEqual(decode_attributes(data),{'bytes':{'$bytes_hex':'00ff'},'n':12.5,
                                                'v':{'$vector3':[1.,2.,3.]}})

    def test_empty_unknown_and_malformed_are_distinct(self):
        self.assertEqual(attribute_snapshot({}),{})
        self.assertEqual(attribute_snapshot({'AttributesReplicate':b''}),{'attributes':{},'attributes_known':True})
        for data in (b'\x01',blob([('bad',b'\x03\x02')]),blob([('bad',b'\x7f')]),
                     blob([('x',b'\x03\x01'),('x',b'\x03\0')]),b'\0'*5,
                     struct.pack('<I',1025)):
            with self.subTest(data=data):
                self.assertEqual(attribute_snapshot({'AttributesReplicate':data}),
                                 {'attributes':{},'attributes_known':False})

    def test_snapshot_and_receipt_order_preserve_attribute_values(self):
        world,key=fixture();world.instances[key]['properties']['AttributesReplicate']=blob([('ap',b'\x03\0')])
        snapshot=make_world_snapshot(world,123)
        node=next(n for n in snapshot['nodes'] if n['id']=='1:4')
        self.assertEqual(node['attributes'],{'ap':False})
        cursor=world.revision
        for value in (True,False,True):
            world.instances[key]['properties']['AttributesReplicate']=blob([('ap',bytes((3,value)))])
            world.touch(key,['AttributesReplicate'])
        actions=make_world_delta(world,cursor)['actions']
        self.assertEqual([a['attribute_snapshot']['attributes']['ap'] for a in actions],[True,False,True])
        self.assertEqual(node['attributes'],{'ap':False})

    def test_received_attributes_reach_luau_and_emit_real_changes(self):
        world,key=fixture();world.instances[key]['properties']['AttributesReplicate']=blob([('ap',b'\x03\0')])
        host_path=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(persistent_runtime.RUST_HOST)))
        with mock.patch.object(persistent_runtime,'RUST_HOST',host_path):
            host=persistent_runtime.PersistentLuauHost(make_world_snapshot(world,123))
        self.addCleanup(host.close)
        first=host.call({'op':'execute','source':'''getgenv().p=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
assert(p:GetAttribute("ap")==false and p:GetAttribute("missing")==nil)
getgenv().observed={};p:GetAttributeChangedSignal("ap"):Connect(function()table.insert(observed,p:GetAttribute("ap")==nil and "deleted" or p:GetAttribute("ap"))end)
return true''','timeout_ms':100})
        self.assertTrue(first['ok'],first)
        for data in (blob([('ap',b'\x03\x01')]),blob([('ap',b'\x03\x01')]),b''):
            cursor=world.revision;world.instances[key]['properties']['AttributesReplicate']=data
            world.touch(key,['AttributesReplicate'])
            result=host.call({'op':'update','delta':make_world_delta(world,cursor),'timeout_ms':100})
            self.assertTrue(result['ok'],result)
        result=host.call({'op':'execute','source':'assert(#observed==2 and observed[1]==true and observed[2]=="deleted");return p:GetAttribute("ap")==nil','timeout_ms':100})
        self.assertTrue(result['ok'],result);self.assertEqual(result['values'],[True])


if __name__=='__main__':unittest.main()
