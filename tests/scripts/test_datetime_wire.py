import json
from pathlib import Path
import struct
import unittest

from persistent_runtime import PersistentLuauHost
from wire_reader import Reader, WireError
from world_replication import World
from world_snapshot import _encode


class DateTimeWireTests(unittest.TestCase):
    def read(self, data):
        return World({'classes': []}).value(Reader(data), {'type': 48}, set())

    def test_signed_milliseconds_and_following_field(self):
        for millis in (-17987443200000, -1, 0, 1793639017123, 253402300799999):
            with self.subTest(millis=millis):
                reader=Reader(struct.pack('<qB',millis,127))
                result=World({'classes': []}).value(reader, {'type': 48}, set())
                self.assertEqual(result, {'$datetime': millis})
                self.assertEqual(_encode(result,48), result)
                self.assertEqual(_encode([result],29), [result])
                self.assertEqual(reader.u8(),127)
                reader.finish()

    def test_truncated_and_out_of_range_are_rejected(self):
        for length in range(8):
            with self.assertRaises(WireError):self.read(bytes(length))
        for millis in (-17987443200001, 253402300800000, -(2**63), 2**63-1):
            with self.assertRaises(WireError):self.read(struct.pack('<q',millis))

    def test_received_nested_event_constructs_real_luau_datetime(self):
        snapshot=json.loads((Path(__file__).resolve().parents[2]/'tests/fixtures/world_snapshot.json').read_text())
        snapshot['nodes'].append({'id':'date:remote','class_name':'RemoteEvent','name':'ClockEvent',
                                 'parent':'scope:workspace','properties':{}})
        with PersistentLuauHost(snapshot) as host:
            result=host.call({'op':'execute','source':'''getgenv().seen=nil
workspace.ClockEvent.OnClientEvent:Connect(function(values) seen=values[1] end)
''','timeout_ms':100})
            self.assertTrue(result['ok'],result.get('error'))
            value=self.read(struct.pack('<q',1793639017123))
            result=host.call({'op':'update','delta':{'version':1,'delta':True,'sequence':100,
                'actions':[{'sequence':100,'kind':'event','id':'date:remote','name':'OnClientEvent',
                            'arguments':[_encode([value],29)]}]},'timeout_ms':100})
            self.assertTrue(result['ok'],result.get('error'))
            result=host.call({'op':'execute','source':'return typeof(seen),seen.UnixTimestampMillis','timeout_ms':100})
            self.assertTrue(result['ok'],result.get('error'))
            self.assertEqual(result['values'],['DateTime',1793639017123])


if __name__ == '__main__':unittest.main()
