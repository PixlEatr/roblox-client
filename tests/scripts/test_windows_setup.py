"""Setup discovery tests using temporary dependencies; no compiler installation."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from setup_windows import find_native_prefix,compiler_environment
from platform_environment import runtime_environment


class WindowsSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='rove-win-')
        self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)

    def prefix(self,path):
        (path/'bin').mkdir(parents=True)
        (path/'bin/libcrypto-3-x64.dll').touch();(path/'bin/zstd.dll').touch()
        return path

    def test_dependency_discovery_checks_dlls_not_just_folder(self):
        stale=self.root/'stale';(stale/'bin').mkdir(parents=True)
        (self.root/'.rove-platform.json').write_text(json.dumps({'native_prefix':str(stale)}))
        expected=self.prefix(self.root/'vcpkg/installed/x64-windows')
        self.assertEqual(find_native_prefix(self.root,{'VCPKG_ROOT':str(self.root/'vcpkg')}),expected)

    def test_saved_dependency_prefix_can_contain_spaces(self):
        expected=self.prefix(self.root/'Windows libraries')
        (self.root/'.rove-platform.json').write_text(json.dumps({'native_prefix':str(expected)}))
        self.assertEqual(find_native_prefix(self.root,{}),expected)

    def test_compiler_environment_keeps_unicode_and_merges_path_case(self):
        installer=self.root/'Microsoft Visual Studio/Installer/vswhere.exe'
        installer.parent.mkdir(parents=True);installer.touch()
        compiler=self.root/'Visual Studio 測試'
        script=compiler/'VC/Auxiliary/Build/vcvars64.bat';script.parent.mkdir(parents=True);script.touch()
        replies=[SimpleNamespace(stdout=(str(compiler)+'\r\n').encode('utf-8')),
                 SimpleNamespace(stdout='Path=C:\\tools;C:\\compiler\r\nINCLUDE=C:\\標頭\r\n=C:=ignored\r\n'.encode('utf-16-le'))]
        with patch('setup_windows.shutil.which',return_value=None),patch('setup_windows.subprocess.run',side_effect=replies) as run:
            result=compiler_environment({'ProgramFiles(x86)':str(self.root),'PATH':'old'})
        self.assertEqual(result['PATH'],'C:\\tools;C:\\compiler')
        self.assertEqual(result['INCLUDE'],'C:\\標頭')
        self.assertNotIn('Path',result)
        self.assertNotIn('',result)
        self.assertIn(str(script),run.call_args.args[0][-1])

    def test_missing_build_tools_has_actionable_error(self):
        with patch('setup_windows.shutil.which',return_value=None),self.assertRaisesRegex(ValueError,r'Desktop development with C\+\+'):
            compiler_environment({'ProgramFiles(x86)':str(self.root)})

    def test_fresh_terminal_and_test_runner_restore_dll_path(self):
        prefix=self.root/'native libraries'
        (self.root/'.rove-platform.json').write_text(json.dumps({'native_prefix':str(prefix)}))
        original={'PATH':'existing'}
        env=runtime_environment(self.root,original)
        self.assertEqual(env['PATH'],str(prefix/'bin')+os.pathsep+'existing')
        self.assertEqual(env['PYTHONUTF8'],'1')
        self.assertEqual(original,{'PATH':'existing'})

    def test_corrupt_saved_settings_have_recovery_instruction(self):
        (self.root/'.rove-platform.json').write_text('[]')
        with self.assertRaisesRegex(ValueError,'Run setup again'):runtime_environment(self.root,{})


if __name__=='__main__':unittest.main()
