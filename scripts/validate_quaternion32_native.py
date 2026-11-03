"""Compare the original four-byte quaternion writer using synthetic math data.

The matrix-to-quaternion dependency is supplied a synthetic quaternion; the
normalization, quantization, bit packing and byte write execute from the pinned
ARM64 image. This is not an end-to-end physics or game-server test.
"""
import hashlib
import json
import random
import struct
from pathlib import Path

from unicorn import (Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE,
                     UC_HOOK_MEM_READ_UNMAPPED, UC_HOOK_MEM_FETCH_UNMAPPED)
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X30, UC_ARM64_REG_PC, UC_ARM64_REG_SP
from quaternion32 import encode_quaternion32


def main():
    root=Path(__file__).resolve().parents[1]
    data=(root/'inputs/RobloxPlayer-0.745-arm64').read_bytes()
    digest=hashlib.sha256(data).hexdigest()
    if digest!='b6b2184109237a8fea5de33d3265ac026cda3db05dbe3d66f749c91b4297b870':
        raise RuntimeError('Pinned math image hash mismatch')
    segments=json.loads((root/'outputs/macho-layout.json').read_text())['segments']
    uc=Uc(UC_ARCH_ARM64,UC_MODE_ARM); mapped=set()
    def page(address):
        p=address&~4095
        if p in mapped:return
        uc.mem_map(p,4096);mapped.add(p)
        for seg in segments:
            if seg['address']<=p and p+4096<=seg['address']+seg['file_size']:
                off=seg['offset']+p-seg['address'];uc.mem_write(p,data[off:off+4096]);break
    for address in (0x104CBEF68,0x104CBF000,0x102E86744,0x1015F9230):page(address)
    uc.mem_map(0x20000000,0x10000);mapped.update(range(0x20000000,0x20010000,4096))
    source=(0.,0.,0.,1.); output=bytearray()
    def code(machine,address,size,unused):
        if address==0x102E86744:
            machine.mem_write(machine.reg_read(UC_ARM64_REG_X0),struct.pack('<4f',*source))
            machine.reg_write(UC_ARM64_REG_PC,machine.reg_read(UC_ARM64_REG_X30))
        elif address==0x1015F9230:
            output.extend(machine.mem_read(machine.reg_read(UC_ARM64_REG_X1),machine.reg_read(UC_ARM64_REG_X2)))
            machine.emu_stop()
    def missing(machine,access,address,size,value,unused):page(address);return True
    uc.hook_add(UC_HOOK_CODE,code)
    uc.hook_add(UC_HOOK_MEM_READ_UNMAPPED|UC_HOOK_MEM_FETCH_UNMAPPED,missing)
    rng=random.Random(74532)
    cases=[(0.,0.,0.,1.),(1.,0.,0.,0.),(0.,1.,0.,0.),(0.,0.,1.,0.),(.5,.5,.5,.5)]
    cases.extend(tuple(rng.uniform(-1,1) for _ in range(4)) for _ in range(1000))
    for index,q in enumerate(cases):
        source=struct.unpack('<4f',struct.pack('<4f',*q));output.clear()
        for reg,value in ((UC_ARM64_REG_X0,0x20001000),(UC_ARM64_REG_X1,0x20002000),
                          (UC_ARM64_REG_X30,0x30000000),(UC_ARM64_REG_SP,0x2000F000)):
            uc.reg_write(reg,value)
        uc.emu_start(0x104CBEF68,0x30000000,count=2000)
        if bytes(output)!=encode_quaternion32(source):
            raise AssertionError('Native quaternion packing differs at synthetic case '+str(index))
    report={'image_sha256':digest,'native_function':'0x104CBEF68',
            'synthetic_cases_passed':len(cases),'matrix_conversion_dependency':'synthetic quaternion supplied',
            'normalization_quantization_and_byte_write':'original native instructions',
            'live_server_test':False}
    (root/'outputs/physics-final/quaternion32-native-validation.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':main()
