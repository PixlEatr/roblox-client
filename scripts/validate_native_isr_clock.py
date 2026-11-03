#!/usr/bin/env python3
"""Offline parity check for the explicitly selected native integer ISR clock branch."""
import argparse
import hashlib
import json
import random
import struct
from pathlib import Path

from unicorn import (Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_MEM_FETCH_UNMAPPED,
                     UC_HOOK_MEM_READ_UNMAPPED, UC_HOOK_MEM_WRITE_UNMAPPED)
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X30, UC_ARM64_REG_SP, UC_ARM64_REG_PC
from physics_macho_pointers import Image
from physics_rotation_callscan import EXPECTED_SHA256
from isr_clock import IsrClockEstimator


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();data=args.image.read_bytes()
    if hashlib.sha256(data).hexdigest()!=EXPECTED_SHA256:raise ValueError('Pinned image mismatch')
    image=Image.parse(data);uc=Uc(UC_ARCH_ARM64,UC_MODE_ARM);mapped=set()
    state,stack,end=0x20001000,0x2000e000,0x30000000
    uc.mem_map(0x20000000,0x10000)
    def page(address):
        address&=~4095
        if address in mapped:return
        segment=next(s for s in image.segments if s.vmaddr<=address and address+4096<=s.vmaddr+s.filesize)
        offset=segment.fileoff+address-segment.vmaddr
        uc.mem_map(address,4096);uc.mem_write(address,data[offset:offset+4096]);mapped.add(address)
    def missing(_uc,access,address,size,value,userdata):page(address);return True
    for kind in (UC_HOOK_MEM_FETCH_UNMAPPED,UC_HOOK_MEM_READ_UNMAPPED,UC_HOOK_MEM_WRITE_UNMAPPED):uc.hook_add(kind,missing)
    # Select the documented base branch, not an assertion about live flags.
    page(0x106AB1DB0)
    uc.mem_write(0x106AB1DB0,struct.pack('<i',10))
    uc.mem_write(0x106AB1DD8,b'\0')
    uc.mem_write(0x106AB1E20,struct.pack('<I',20))
    uc.mem_write(0x106AB1DC8,bytes(8))  # logging disabled
    def call(address,step=0):
        for reg,val in ((UC_ARM64_REG_X0,state),(UC_ARM64_REG_X1,step),
                        (UC_ARM64_REG_X30,end),(UC_ARM64_REG_SP,stack)):
            uc.reg_write(reg,val)
        uc.emu_start(address,end,count=12000)
        if uc.reg_read(UC_ARM64_REG_PC)!=end:raise RuntimeError('Native clock did not return')
    rng=random.Random(0x49b4ec);comparisons=0
    for scenario in range(8):
        uc.mem_write(state,bytes(64))
        uc.mem_write(state+8,struct.pack('<Ifiiii',0x7fffffff,float(2**31),-2**31,-2**31,-2**31,-2**31))
        clock=IsrClockEstimator();local=1000;remote=1000+scenario*100
        for tick in range(256):
            local+=rng.randint(1,30);remote+=rng.randint(1,30)
            if rng.random()<.85:
                clock.observe_remote(remote);call(0x10449B474,remote)
            if rng.random()<.6:
                ack=local-rng.randint(1,20)
                clock.observe_delivery('acknowledged',ack);call(0x10449B498,ack)
            else:clock.observe_delivery('lost',local)
            uc.mem_write(state+20,struct.pack('<I',local))
            expected=clock.update(local);call(0x10449B4EC)
            actual=struct.unpack('<i',uc.mem_read(state+8,4))[0]
            actual=None if actual==0x7fffffff else actual
            stabilized=bool(uc.mem_read(state+3,1)[0])
            if actual!=expected or stabilized!=clock.stabilized:
                raise AssertionError(f'Clock mismatch scenario={scenario} tick={tick}: native={actual}, model={expected}')
            comparisons+=1
    report={'offline_only':True,'image_sha256':EXPECTED_SHA256,
        'native_update':'0x10449B4EC','comparisons':comparisons,
        'branch':{'float_smoothing':False,'smoothing_percent':10,'stabilization_update_threshold':20},
        'native_clock_values_and_stabilization_match':True,
        'live_feature_configuration_parity_verified':False,
        'server_pose_application_verified':False}
    args.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))


if __name__=='__main__':main()
