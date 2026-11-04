"""Offline authored-Luau checks for HttpService:UrlEncode."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import local_runtime

if os.environ.get("ROBLOX_LUAU_HOST"):
    local_runtime.HOST = Path(os.environ["ROBLOX_LUAU_HOST"])
if os.environ.get("ROBLOX_LUAU_RUST_HOST"):
    local_runtime.RUST_HOST = Path(os.environ["ROBLOX_LUAU_RUST_HOST"])

HOSTS = [local_runtime.HOST, local_runtime.RUST_HOST]


@unittest.skipUnless(all(host.is_file() for host in HOSTS),
                     "build both native hosts first")
class UrlEncodeTests(unittest.TestCase):
    def execute(self, host, source):
        with tempfile.TemporaryDirectory(prefix="url-encode-test-", dir="/tmp") as folder:
            path = Path(folder)
            (path / "source.luau").write_text(source)
            (path / "snapshot.json").write_text(
                (ROOT / "tests/fixtures/world_snapshot.json").read_text())
            result = subprocess.run(
                [str(host), "--source", str(path / "source.luau"),
                 "--world-snapshot", str(path / "snapshot.json"),
                 "--timeout-ms", "1000"],
                capture_output=True, timeout=8)
        import json
        response = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, response)
        return response

    def run_both(self, source):
        return [self.execute(host, source) for host in HOSTS]

    def test_utf8_space_period_and_reserved_bytes(self):
        source = r'''
local http = game:GetService("HttpService")
local encoded = http:UrlEncode("Je suis allé au cinéma.")
local reserved = http:UrlEncode("-_.~!*'();:@&=+$,/?#%[]")
local nul = http:UrlEncode("a\0b")
return encoded, reserved, nul
'''
        for response in self.run_both(source):
            self.assertTrue(response["ok"], response)
            self.assertEqual(response["values"], [
                "Je%20suis%20all%C3%A9%20au%20cin%C3%A9ma%2E",
                "%2D%5F%2E%7E%21%2A%27%28%29%3B%3A%40%26%3D%2B%24%2C%2F%3F%23%25%5B%5D",
                "a%00b",
            ])

    def test_rejects_non_strings_wrong_receiver_and_oversize_input(self):
        source = '''
local http=game:GetService("HttpService")
local players=game:GetService("Players")
local nonstring=pcall(function()http:UrlEncode(42)end)
local wrong_receiver=pcall(function()http.UrlEncode(players,"x")end)
local oversized=pcall(function()http:UrlEncode(string.rep("x",128*1024+1))end)
return nonstring,wrong_receiver,oversized
'''
        for response in self.run_both(source):
            self.assertTrue(response["ok"], response)
            self.assertEqual(response["values"], [False, False, False])


if __name__ == "__main__":
    unittest.main()
