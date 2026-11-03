"""Verify category APIs tag real allocations in the pinned VM."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
VENDOR=ROOT/'vendor/luau'
BUILD=ROOT/'runtime/build'


class MemoryCategoryNativeTests(unittest.TestCase):
    def test_real_allocation_attribution_inheritance_and_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'memory-category-test'
            command=[os.environ.get('CXX','c++'),'-std=c++17','-O2']
            if sys.platform == 'darwin':
                architectures=subprocess.check_output(['lipo','-archs',str(BUILD/'libLuau.VM.a')],text=True).split()
                if len(architectures)==1:command+=['-arch',architectures[0]]
            for part in ['VM','Compiler','Ast','Config','Common']:
                command+=['-I',str(VENDOR/part/'include')]
            command+=['-I',str(VENDOR/'VM/src'),str(ROOT/'runtime/test_memory_categories.cpp')]
            command+=[str(BUILD/('lib'+name+'.a')) for name in ['Luau.Compiler','Luau.VM','Luau.Bytecode','Luau.Config','Luau.Ast','Luau.Common']]
            command+=['-pthread','-o',str(target)]
            compiled=subprocess.run(command,capture_output=True,text=True,timeout=60)
            self.assertEqual(compiled.returncode,0,compiled.stderr)
            result=subprocess.run([str(target)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('native memory allocation attribution passed',result.stdout)


if __name__=='__main__':unittest.main()
