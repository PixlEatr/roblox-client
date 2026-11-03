#!/usr/bin/env python3
"""Offline ABI checks for pinned 0.745 string/reference writers, synthetic inputs.

Runs only bounded ARM64 helpers in Unicorn. Raw stream writes are recorded by a
hook; no Roblox startup, network, authentication, or process telemetry runs.
This does not emit or validate a live RequestCharacter packet.
"""
import hashlib
import json
from pathlib import Path
import struct
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import (UC_ARM64_REG_X0, UC_ARM64_REG_X1,
    UC_ARM64_REG_X2, UC_ARM64_REG_X3, UC_ARM64_REG_X30,
    UC_ARM64_REG_SP, UC_ARM64_REG_PC)

ROOT=Path(__file__).resolve().parents[1]
EXPECTED='b6b2184109237a8fea5de33d3265ac026cda3db05dbe3d66f749c91b4297b870'
RAW_WRITE=0x1015F9230
STOP=0x30000000
MEMORY=0x20000000


def varuint(value):
    result=bytearray()
    while value>=128:
        result.append((value&127)|128);value>>=7
    result.append(value)
    return bytes(result)


class NativeHelpers:
    def __init__(self):
        binary=(ROOT/'inputs/RobloxPlayer-0.745-arm64').read_bytes()
        if hashlib.sha256(binary).hexdigest()!=EXPECTED:
            raise ValueError('Selected native binary changed')
        layout=json.loads((ROOT/'outputs/macho-layout.json').read_text())
        self.machine=Uc(UC_ARCH_ARM64,UC_MODE_ARM)
        pages=set()
        # String wrapper/body, scope/index writer, scope selector, and varuint.
        for address in [0x1015F9324,0x1015F9344,0x1015F943C,0x1015F9530,
                        0x104CD1E48,0x104F23D80,RAW_WRITE]:
            page=address&~4095
            if page in pages:continue
            pages.add(page)
            segment=next(s for s in layout['segments'] if
                s['address']<=page and page+4096<=s['address']+s['file_size'])
            offset=segment['offset']+page-segment['address']
            self.machine.mem_map(page,4096)
            self.machine.mem_write(page,binary[offset:offset+4096])
        self.machine.mem_map(MEMORY,0x20000)
        self.machine.mem_map(STOP,4096)
        self.result=bytearray()
        self.machine.hook_add(UC_HOOK_CODE,self.record,begin=RAW_WRITE,end=RAW_WRITE)

    def record(self,machine,address,size,user):
        source=machine.reg_read(UC_ARM64_REG_X1)
        count=machine.reg_read(UC_ARM64_REG_X2)
        if count>4096:raise ValueError('Native test write exceeded bound')
        self.result.extend(machine.mem_read(source,count))
        machine.reg_write(UC_ARM64_REG_PC,machine.reg_read(UC_ARM64_REG_X30))

    def call(self,address,*args):
        self.result.clear()
        for register,value in zip([UC_ARM64_REG_X0,UC_ARM64_REG_X1,
                                   UC_ARM64_REG_X2,UC_ARM64_REG_X3],args):
            self.machine.reg_write(register,value)
        self.machine.reg_write(UC_ARM64_REG_SP,MEMORY+0x18000)
        self.machine.reg_write(UC_ARM64_REG_X30,STOP)
        self.machine.emu_start(address,STOP,timeout=100000,count=20000)
        if self.machine.reg_read(UC_ARM64_REG_PC)!=STOP:
            raise AssertionError('Native helper did not return within bounds')
        return bytes(self.result)

    def string(self,value):
        descriptor=MEMORY+0x1000;data=MEMORY+0x2000
        self.machine.mem_write(data,value or b'\0')
        # libc++ long-string representation: ptr, len, capacity/sign tag.
        self.machine.mem_write(descriptor,struct.pack('<QQQ',data,len(value),1<<63))
        return self.call(0x1015F9324,MEMORY,descriptor,4096)

    def reference(self,scope,index,local_scope=42):
        replicator=MEMORY+0x3000;identity=MEMORY+0x4000
        self.machine.mem_write(replicator+168,struct.pack('<I',local_scope))
        self.machine.mem_write(identity,struct.pack('<II',scope&0xffffffff,index))
        return self.call(0x104CD1E48,replicator,MEMORY,identity)


def main():
    helpers=NativeHelpers();strings=[];references=[]
    for value in [b'',b'a',b'SpawnA','spawn-\u00e9'.encode(),b'x'*127,b'x'*128,b'x'*256]:
        actual=helpers.string(value);expected=struct.pack('<I',len(value))+value
        assert actual==expected,(len(value),actual.hex())
        strings.append({'length':len(value),'prefix_hex':actual[:4].hex(),'passed':True})
    for scope,index in [(1,0),(42,0x01020304),(127,0xffffffff),(128,123),
                        (16384,0),(0xfffffffe,0x12345678),(0,33),(-1,99)]:
        actual=helpers.reference(scope,index)
        expected=b'\0' if scope==-1 else varuint(scope or 42)+struct.pack('<I',index)
        assert actual==expected,(scope,index,actual.hex(),expected.hex())
        references.append({'synthetic_scope':scope,'synthetic_index':index,
                           'local_scope':42,'wire_hex':actual.hex(),'passed':True})
    report={'binary_sha256':EXPECTED,'native_build':'macOS ARM64 0.745.0.7451137',
        'offline_only':True,'native_helper_cases_passed':len(strings)+len(references),
        'raw_write_hooked':True,'live_spawn_validated':False,
        'string_writer':'0x1015F9324 -> 0x1015F9344',
        'reference_writer':'0x104CD1E48 -> 0x104F23D80 -> 0x1015F943C -> 0x1015F9530',
        'strings':strings,'references':references}
    out=ROOT/'outputs/request-character/native-helper-validation.json'
    out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'native_helper_cases_passed':len(strings)+len(references),'report':str(out)}))


if __name__=='__main__':main()
