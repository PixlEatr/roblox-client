"""Private source tracing preserves loadstring semantics and file boundaries."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from persistent_runtime import PersistentLuauHost
from test_cframe_transforms import SNAPSHOT


class SourceTraceTests(unittest.TestCase):
    def host(self, directory):
        with patch.dict(os.environ, {'RCR_TRACE_LOADED_SOURCES': str(directory)}):
            host=PersistentLuauHost(SNAPSHOT)
        self.addCleanup(host.close)
        return host

    def execute(self, host, source):
        result=host.call({'op':'execute','source':source,'timeout_ms':100})
        self.assertTrue(result['ok'],result)
        return result

    def test_exact_source_deduplicated_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            host=self.host(directory)
            result=self.execute(host,'return assert(loadstring("return 40+2"))()')
            self.assertEqual(result['values'],[42])
            self.execute(host,'return assert(loadstring("return 40+2"))()')
            files=list(Path(directory).iterdir())
            self.assertEqual(len(files),1)
            self.assertEqual(files[0].name,hashlib.sha256(b'return 40+2').hexdigest()+'.luau')
            self.assertEqual(files[0].read_bytes(),b'return 40+2')
            self.assertEqual(files[0].stat().st_mode&0o777,0o600)

    def test_disabled_missing_public_and_symlink_directories_do_not_affect_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'target';target.mkdir(mode=0o700)
            public=root/'public';public.mkdir(mode=0o755);public.chmod(0o755)
            link=root/'link';link.symlink_to(target,target_is_directory=True)
            for name in ('',root/'absent',public,link):
                host=self.host(name)
                result=self.execute(host,'return assert(loadstring("return 42"))()')
                self.assertEqual(result['values'],[42]);host.close()
            self.assertEqual(list(target.iterdir()),[])
            self.assertEqual(list(public.iterdir()),[])

    def test_existing_output_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);trace=root/'trace';trace.mkdir(mode=0o700)
            target=root/'sentinel';target.write_text('untouched')
            (trace/(hashlib.sha256(b'return 42').hexdigest()+'.luau')).symlink_to(target)
            host=self.host(trace)
            self.assertEqual(self.execute(host,'return assert(loadstring("return 42"))()')['values'],[42])
            self.assertEqual(target.read_text(),'untouched')

    def test_syntax_error_and_original_chunk_name_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            host=self.host(directory)
            result=self.execute(host,'local f,e=loadstring("local =", "=named-source");return f==nil,e')
            self.assertTrue(result['values'][0]);self.assertIn('named-source',result['values'][1])


if __name__=='__main__':unittest.main()
