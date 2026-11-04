#!/usr/bin/env python3
"""Offline integration checks for the USSI adapter and serialized artifacts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from persistent_runtime import PersistentLuauHost  # noqa: E402
from run_ussi import ExportSession, run_export  # noqa: E402
from validate_ussi_export import parse_export, validate  # noqa: E402

SNAPSHOT = ROOT / "tests/fixtures/ussi_snapshot.json"
MODEL_EXPECT = json.loads((ROOT / "tests/fixtures/ussi_model_export_expectations.json").read_text())
PLACE_EXPECT = json.loads((ROOT / "tests/fixtures/ussi_fullplace_export_expectations.json").read_text())
PAYLOAD_BYTES = 1_100_003


class UniversalSynSaveInstanceRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory(prefix="ussi-runner-test-")
        cls.temp = Path(cls.tempdir.name)
        cls.base_snapshot = json.loads(SNAPSHOT.read_text())
        cls.host = PersistentLuauHost(cls.base_snapshot, memory_mb=128)
        cls.session = ExportSession(host=cls.host)
        # Source is set through the same offline Luau API exposed to users.
        cls.session.execute('workspace.ExportFixture.SourceProbe.Source="return 17"; return true')

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "host"):
            cls.host.close()
        if hasattr(cls, "tempdir"):
            cls.tempdir.cleanup()

    def test_all_four_formats_and_failed_expectation_preserves_destination(self):
        cases = [
            ("fixture.rbxm", "Workspace/ExportFixture", MODEL_EXPECT),
            ("fixture.rbxmx", "Workspace/ExportFixture", MODEL_EXPECT),
            ("fullplace.rbxl", None, PLACE_EXPECT),
            ("fullplace.rbxlx", None, PLACE_EXPECT),
        ]
        outputs = {}
        for filename, object_path, expectation in cases:
            destination = self.temp / filename
            report = run_export(self.session, destination, object_path=object_path,
                                expectations=expectation)
            self.assertTrue(report["ok"], report)
            self.assertGreater(report["bytes"], 32)
            self.assertEqual(report["sha256"], hashlib.sha256(destination.read_bytes()).hexdigest())
            self.assertEqual(report["validation"]["expected_count"], len(expectation["instances"]))
            outputs[filename] = destination

        # A failed validation must not replace an existing destination with a
        # partial or unvalidated serialization.
        destination = outputs["fixture.rbxmx"]
        original = destination.read_bytes()
        invalid = {"instances": [{"path": "Workspace/ExportFixture/Probe",
                                   "class": "Folder"}]}
        with self.assertRaises(Exception):
            run_export(self.session, destination, object_path="Workspace/ExportFixture",
                       expectations=invalid)
        self.assertEqual(destination.read_bytes(), original)
        self.assertFalse(list(self.temp.glob("fixture.rbxmx.*.partial")))

    def test_console_export_survives_actual_world_journal_overrun(self):
        import collections
        import subprocess
        import threading
        from script_console import ScriptConsole, send_source
        from test_world_snapshot import fixture
        from world_snapshot import make_world_snapshot, make_world_delta
        world, root = fixture()
        # USSI intentionally omits player-character descendants by default.
        world.instances[(1, 2)]["properties"]["Character"] = None
        world.instances[root]["properties"]["CFrame"] = {
            "position": [1., 2., 3.], "rotation": [1.,0.,0.,0.,1.,0.,0.,0.,1.]}
        watermark = world.revision
        world.actions = collections.deque(maxlen=1)
        for name in (b'OldRoot', b'LatestRoot'):
            world.instances[root]['properties']['Name'] = name
            world.touch(root, ['Name'])
        with self.assertRaisesRegex(ValueError, 'journal was overrun'):
            make_world_delta(world, watermark)
        def failed_execute(*args, **kwargs):
            return make_world_delta(world, watermark)
        socket = self.temp/'export.sock'
        console = ScriptConsole(socket, failed_execute,
                                export_snapshot=lambda: make_world_snapshot(world, 123))
        stop = threading.Event()
        def poll():
            while not stop.is_set():
                console.poll()
                stop.wait(.001)
        thread = threading.Thread(target=poll)
        thread.start()
        try:
            self.assertFalse(send_source(socket, 'return true')['ok'])
            output = self.temp/'after-overrun.rbxmx'
            result = subprocess.run([sys.executable, str(SCRIPTS/'run_ussi.py'),
                                     '--socket', str(socket), '--object', 'Workspace/Model',
                                     '--output', str(output)], capture_output=True, text=True,
                                    timeout=45)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            validate(parse_export(output), {'instances': [
                {'path': 'Model/LatestRoot', 'class': 'Part',
                 'properties': {'CFrame': {'position': [1, 2, 3]}}}]})
            self.assertEqual(world.revision, watermark+2)
            self.assertFalse(console._export_snapshots)
        finally:
            stop.set()
            thread.join(3)
            console.close()

    def test_chunked_readback_for_xml_larger_than_one_megabyte(self):
        snapshot = json.loads(json.dumps(self.base_snapshot))
        unit = "A<& café Ω"
        payload = (unit * (PAYLOAD_BYTES // len(unit) + 1))[:PAYLOAD_BYTES]
        snapshot["nodes"].append({
            "id": "fixture:large-payload", "class_name": "StringValue",
            "name": "LargePayload", "parent": "fixture:model",
            "properties": {"Archivable": True, "Value": payload},
        })
        host = PersistentLuauHost(snapshot, memory_mb=128)
        try:
            session = ExportSession(host=host)
            session.execute('workspace.ExportFixture.SourceProbe.Source="return 17"; return true')
            destination = self.temp / "large-fixture.rbxmx"
            report = run_export(session, destination, object_path="Workspace/ExportFixture",
                                expectations=MODEL_EXPECT)
            data = destination.read_bytes()
            self.assertTrue(report["ok"], report)
            self.assertGreater(len(data), 1024 * 1024)
            parsed = parse_export(destination)
            result = validate(parsed, MODEL_EXPECT)
            self.assertEqual(result["format"], "xml")
            large = [node for node in parsed["instances"] if node["name"] == "LargePayload"]
            self.assertEqual(len(large), 1)
            self.assertEqual(large[0]["properties"].get("Value"), payload)
        finally:
            host.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
