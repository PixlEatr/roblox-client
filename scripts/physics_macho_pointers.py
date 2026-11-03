#!/usr/bin/env python3
"""Read-only Mach-O 64 pointer/fixup inventory for static vtable-reference searches.

Supports LC_DYLD_INFO(_ONLY) rebase and bind opcode streams plus raw 64-bit
preferred-address pointers. It never modifies the image and does not apply ASLR.
"""
from __future__ import annotations
import argparse, dataclasses, json, struct
from pathlib import Path
from typing import Iterable

MH_MAGIC_64 = 0xFEEDFACF
LC_SEGMENT_64 = 0x19
LC_DYLD_INFO = 0x22
LC_DYLD_INFO_ONLY = 0x80000022
REBASE = {0: 'DONE', 1:'SET_TYPE_IMM', 2:'SET_SEGMENT_AND_OFFSET_ULEB', 3:'ADD_ADDR_ULEB', 4:'ADD_ADDR_IMM_SCALED', 5:'DO_REBASE_IMM_TIMES', 6:'DO_REBASE_ULEB_TIMES', 7:'DO_REBASE_ADD_ADDR_ULEB', 8:'DO_REBASE_ULEB_TIMES_SKIPPING_ULEB'}
BIND = {0:'DONE',1:'SET_DYLIB_ORDINAL_IMM',2:'SET_DYLIB_ORDINAL_ULEB',3:'SET_DYLIB_SPECIAL_IMM',4:'SET_SYMBOL_TRAILING_FLAGS_IMM',5:'SET_TYPE_IMM',6:'SET_ADDEND_SLEB',7:'SET_SEGMENT_AND_OFFSET_ULEB',8:'ADD_ADDR_ULEB',9:'DO_BIND',10:'DO_BIND_ADD_ADDR_ULEB',11:'DO_BIND_ADD_ADDR_IMM_SCALED',12:'DO_BIND_ULEB_TIMES_SKIPPING_ULEB'}
@dataclasses.dataclass(frozen=True)
class Segment:
    name:str; vmaddr:int; vmsize:int; fileoff:int; filesize:int
@dataclasses.dataclass(frozen=True)
class Fixup:
    kind:str; segment:str; vmaddr:int; fileoff:int; symbol:str|None=None; ordinal:int|None=None; addend:int=0
@dataclasses.dataclass
class Image:
    data:bytes; segments:list[Segment]; rebase:tuple[int,int]|None; bind:tuple[int,int]|None; weak_bind:tuple[int,int]|None; lazy_bind:tuple[int,int]|None; chained:tuple[int,int]|None
    @classmethod
    def parse(cls,data:bytes)->'Image':
        if len(data)<32 or struct.unpack_from('<I',data)[0]!=MH_MAGIC_64: raise ValueError('expected little-endian thin Mach-O 64')
        _,_,_,_,ncmds,sizeofcmds,*_=struct.unpack_from('<IiiIIIII',data,0)
        end=32+sizeofcmds
        if end>len(data): raise ValueError('load commands exceed file')
        segs=[]; dyld=None; chained=None; pos=32
        for _ in range(ncmds):
            if pos+8>end: raise ValueError('truncated load command')
            cmd,sz=struct.unpack_from('<II',data,pos)
            if sz<8 or pos+sz>end: raise ValueError('invalid load command size')
            if cmd==LC_SEGMENT_64:
                if sz<72: raise ValueError('short LC_SEGMENT_64')
                name=data[pos+8:pos+24].split(b'\0',1)[0].decode('ascii','replace')
                vmaddr,vmsize,fileoff,filesize=struct.unpack_from('<QQQQ',data,pos+24)
                if fileoff+filesize>len(data): raise ValueError('segment exceeds file')
                segs.append(Segment(name,vmaddr,vmsize,fileoff,filesize))
            elif cmd in (LC_DYLD_INFO,LC_DYLD_INFO_ONLY):
                if sz<48: raise ValueError('short LC_DYLD_INFO')
                vals=struct.unpack_from('<10I',data,pos+8); dyld=(vals[0:2],vals[2:4],vals[4:6],vals[6:8])
            elif cmd==0x80000034: # LC_DYLD_CHAINED_FIXUPS
                if sz<16: raise ValueError('short LC_DYLD_CHAINED_FIXUPS')
                chained=struct.unpack_from('<II',data,pos+8)
            pos+=sz
        if pos!=end: raise ValueError('load command count/size mismatch')
        return cls(data,segs,*(dyld or (None,None,None,None)),chained)
    def vm_to_off(self,segidx:int,off:int)->int:
        if not 0<=segidx<len(self.segments): raise ValueError(f'segment index {segidx} out of range')
        s=self.segments[segidx]
        if off+8>s.vmsize or off+8>s.filesize: raise ValueError(f'fixup location outside file-backed segment {s.name} index={segidx} offset={off:#x} filesize={s.filesize:#x} vmsize={s.vmsize:#x}')
        return s.fileoff+off
    def rebase_fixups(self)->list[Fixup]:
        if not self.rebase:return []
        start,size=self.rebase; stream=self.data[start:start+size]; p=0; seg=0; off=0; out=[]
        def uleb():
            nonlocal p
            v=shift=0
            while True:
                if p>=len(stream):raise ValueError('truncated ULEB128')
                b=stream[p];p+=1;v|=(b&127)<<shift
                if b<128:return v
                shift+=7
                if shift>63:raise ValueError('ULEB128 too large')
        def emit():
            nonlocal off
            fo=self.vm_to_off(seg,off); s=self.segments[seg]; out.append(Fixup('rebase',s.name,s.vmaddr+off,fo)); off+=8
        while p<len(stream):
            byte=stream[p];p+=1; op=byte>>4; imm=byte&15
            if op==0: break
            if op==1: continue # pointer type (image uses pointer fixups)
            if op==2: seg=imm;off=uleb()
            elif op==3: off=(off+uleb()) & 0xFFFFFFFFFFFFFFFF
            elif op==4: off=(off+imm*8) & 0xFFFFFFFFFFFFFFFF
            elif op==5:
                for _ in range(imm):emit()
            elif op==6:
                for _ in range(uleb()):emit()
            elif op==7:
                emit();off+=uleb()
            elif op==8:
                count,skip=uleb(),uleb()
                for _ in range(count):emit();off=(off+skip) & 0xFFFFFFFFFFFFFFFF
            else: raise ValueError(f'unknown rebase opcode {op}')
        return out
    def bind_fixups(self,stream_range:tuple[int,int]|None,lazy:bool=False,weak:bool=False)->list[Fixup]:
        if not stream_range:return []
        start,size=stream_range; stream=self.data[start:start+size]; p=0; seg=0; off=0; sym=None; ordn=0; addend=0; out=[]
        def uleb():
            nonlocal p
            v=shift=0
            while True:
                if p>=len(stream):raise ValueError('truncated bind ULEB128')
                b=stream[p];p+=1;v|=(b&127)<<shift
                if b<128:return v
                shift+=7
                if shift>63:raise ValueError('bind ULEB128 too large')
        def sleb():
            nonlocal p
            v=shift=0
            while True:
                if p>=len(stream):raise ValueError('truncated SLEB128')
                b=stream[p];p+=1;v|=(b&127)<<shift;shift+=7
                if b<128:
                    if b&64:v-=1<<shift
                    return v
                if shift>63:raise ValueError('SLEB128 too large')
        def emit():
            nonlocal off
            fo=self.vm_to_off(seg,off);s=self.segments[seg];out.append(Fixup('lazy_bind' if lazy else ('weak_bind' if weak else 'bind'),s.name,s.vmaddr+off,fo,sym,ordn,addend));off+=8
        while p<len(stream):
            b=stream[p];p+=1;op=b>>4;imm=b&15
            if op==0:
                # DONE separates lazy-bind records; reset state and continue.
                if lazy:
                    seg=0;off=0;sym=None;ordn=0;addend=0;continue
                break
            if op==1:ordn=imm
            elif op==2:ordn=uleb()
            elif op==3:ordn=imm-16 if imm else 0
            elif op==4:
                q=stream.find(b'\0',p)
                if q<0:raise ValueError('unterminated bind symbol')
                sym=stream[p:q].decode('utf-8','replace');p=q+1
            elif op==5: pass # pointer type
            elif op==6:addend=sleb()
            elif op==7:seg=imm;off=uleb()
            elif op==8:off=(off+uleb()) & 0xFFFFFFFFFFFFFFFF
            elif op==9:emit()
            elif op==10:emit();off=(off+uleb()) & 0xFFFFFFFFFFFFFFFF
            elif op==11:emit();off=(off+imm*8) & 0xFFFFFFFFFFFFFFFF
            elif op==12:
                count,skip=uleb(),uleb()
                for _ in range(count):emit();off=(off+skip) & 0xFFFFFFFFFFFFFFFF
            elif op==13:
                # threaded binds require pointer-chain decoding and are intentionally surfaced as unsupported.
                sub=imm
                if sub==0: _=uleb()
                elif sub==1: raise ValueError('BIND_OPCODE_THREADED_APPLY requires chained pointer decoding')
                else: raise ValueError(f'unknown threaded bind subopcode {sub}')
            else:raise ValueError(f'unknown bind opcode {op}')
        return out
    def all_fixups(self)->list[Fixup]:
        return self.rebase_fixups()+self.bind_fixups(self.bind)+self.bind_fixups(self.weak_bind,weak=True)+self.bind_fixups(self.lazy_bind,True)
    def raw_references(self,targets:Iterable[int])->dict[int,list[int]]:
        wanted=set(targets); found={t:[] for t in wanted}
        for s in self.segments:
            end=s.fileoff+s.filesize
            for fo in range(s.fileoff, max(s.fileoff,end-7), 8):
                val=struct.unpack_from('<Q',self.data,fo)[0]
                if val in wanted:found[val].append(fo)
        return found

def inventory(path:Path)->dict:
    data=path.read_bytes(); im=Image.parse(data); fx=im.all_fixups()
    return {'path':str(path),'bytes':len(data),'segments':[dataclasses.asdict(s) for s in im.segments],
            'fixups':{'rebase':sum(x.kind=='rebase' for x in fx),'bind':sum(x.kind=='bind' for x in fx),'weak_bind':sum(x.kind=='weak_bind' for x in fx),'lazy_bind':sum(x.kind=='lazy_bind' for x in fx)},
            'chained_fixups':im.chained,'rebase_stream':im.rebase,'bind_stream':im.bind,'weak_bind_stream':im.weak_bind,'lazy_bind_stream':im.lazy_bind}

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('image',type=Path);ap.add_argument('--targets',help='comma-separated preferred VM addresses to find as raw 64-bit references');ap.add_argument('--json',action='store_true');a=ap.parse_args(); d=inventory(a.image); im=Image.parse(a.image.read_bytes())
    if a.targets:
        targets=[int(x,0) for x in a.targets.split(',')];d['raw_references']={hex(k):[hex(x) for x in v] for k,v in im.raw_references(targets).items()}
    print(json.dumps(d,indent=2) if a.json else json.dumps(d,indent=2))
if __name__=='__main__':main()
