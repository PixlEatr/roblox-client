"""Cross-platform terminal primitives, using temporary files and no account."""
import json
import contextlib
import io
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

from platform_files import open_nofollow, read_private, protect_private
from platform_process import executable, group_options, stop_group
from script_console import ScriptConsole, send_source
from client_session_lease import acquire_session_lease, SessionLeaseBusy


class PlatformPortabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='rove-port-')
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_private_file_roundtrip_and_size_limit(self):
        path = self.root/'data'
        fd = open_nofollow(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL)
        with os.fdopen(fd,'wb') as output: output.write(b'fixture\x00data')
        self.assertEqual(read_private(path,100), b'fixture\x00data')
        with self.assertRaises(ValueError): read_private(path,2)
        with self.assertRaises(FileExistsError): open_nofollow(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY)

    def test_protect_existing_private_file(self):
        path=self.root/'data';path.write_text('fixture')
        protect_private(path)
        self.assertEqual(read_private(path,100),b'fixture')

    @unittest.skipIf(os.name=='nt','Windows symlink creation needs a separate OS privilege')
    def test_symlink_is_rejected(self):
        path=self.root/'data';path.write_text('fixture');path.chmod(0o600)
        link=self.root/'link';link.symlink_to(path)
        with self.assertRaises((OSError,ValueError)):read_private(link,100)

    def console(self):
        path=self.root/'console.sock'
        received=[]
        def execute(source,**kwargs):
            received.append(source);return {'ok':True,'values':[source]}
        console=ScriptConsole(path,execute)
        stop=threading.Event()
        def poll():
            while not stop.is_set():console.poll();stop.wait(.001)
        thread=threading.Thread(target=poll);thread.start()
        def close():stop.set();thread.join(2);console.close()
        self.addCleanup(close)
        return path,received

    def test_console_unicode_roundtrip_and_exclusive_owner(self):
        path,received=self.console()
        result=send_source(path,'return "hello 🌍"')
        self.assertEqual(result['values'],['return "hello 🌍"'])
        self.assertEqual(len(received),1)
        with self.assertRaises(ValueError):ScriptConsole(path,lambda *_:None)

    @unittest.skipUnless(os.name=='nt','Windows authenticated loopback transport')
    def test_console_rejects_missing_and_wrong_token(self):
        from local_transport import endpoint
        path,received=self.console();value=endpoint(path)
        for request in ({'source':'bad'},{'source':'bad','_console_token':'0'*64}):
            with socket.create_connection(('127.0.0.1',value['port']),2) as conn:
                conn.sendall(json.dumps(request).encode()+b'\n')
                response=conn.recv(65536)
            self.assertFalse(json.loads(response)['ok'])
        self.assertEqual(received,[])

    def test_session_lease_excludes_another_process_and_releases(self):
        (self.root/'secrets').mkdir()
        scripts=Path(__file__).resolve().parents[2]/'scripts'
        code='from client_session_lease import acquire_session_lease; import sys,time\nwith acquire_session_lease(sys.argv[1]):\n print("ready",flush=True)\n time.sleep(60)'
        child=subprocess.Popen([sys.executable,'-c',code,str(self.root)],cwd=scripts,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,**group_options())
        try:
            self.assertEqual(child.stdout.readline().strip(),'ready')
            with self.assertRaises(SessionLeaseBusy):
                with acquire_session_lease(self.root):pass
            stop_group(child,force=True);child.wait(timeout=5)
            with acquire_session_lease(self.root):pass
        finally:
            if child.poll() is None:stop_group(child,force=True);child.wait(timeout=5)
            child.stdout.close();child.stderr.close()

    def test_terminal_help_imports_without_account_or_network(self):
        result=subprocess.run([sys.executable,str(Path(__file__).resolve().parents[2]/'scripts/terminal_client.py'),'--help'],
            capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('--no-web',result.stdout)

    def test_worker_executable_suffix(self):
        self.assertEqual(executable('luau-host'),'luau-host.exe' if os.name=='nt' else 'luau-host')

    def keyboard(self, characters):
        from platform_input import WindowsLineInput
        reader = WindowsLineInput.__new__(WindowsLineInput)
        reader.characters=[]; reader.special=False; reader.high_surrogate=''
        class Keyboard:
            pending=list(characters)
            def kbhit(self): return bool(self.pending)
            def getwch(self): return self.pending.pop(0)
        reader.keyboard=Keyboard()
        return reader

    def test_windows_line_editing_and_unicode(self):
        reader=self.keyboard('ab\bc\x00K\ud83c\udf0d\r')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(reader.read(), 'ac🌍\n'.encode())
            self.assertIsNone(reader.read())
        self.assertEqual(reader.characters, [])

    def test_windows_line_interrupt_and_eof(self):
        with self.assertRaises(KeyboardInterrupt): self.keyboard('\x03').read()
        self.assertEqual(self.keyboard('\x1a').read(), b'')


if __name__=='__main__':unittest.main()
