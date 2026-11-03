import base64
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import join_allocation
import probe_transport
from roblox_session import SessionError


class AllocationTargetScopeTests(unittest.TestCase):
    def test_explicit_private_target_preserves_other_frontend_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'outputs/gamejoin').mkdir(parents=True)
            (root/'secrets').mkdir()
            selected = {'selection':'user_selected_place', 'is_playable':True, 'place_id':11}
            target = {'selection':'user_selected_private_server', 'is_playable':True, 'place_id':22}
            selection_file = root/'outputs/gamejoin/target-selection.json'
            selection_file.write_text(json.dumps(selected))
            (root/'secrets/private-server-target.json').write_text(json.dumps({
                'status':'Valid', 'placeId':22, 'linkCode':'synthetic-test-code',
                'ownerUserId':7, 'privateServerId':'synthetic-invite'}))
            join = {'PrivateServerID':'synthetic-server', 'PrivateServerOwnerID':7,
                    'PlaceId':22, 'NetStackConfig':json.dumps({'port':1234,
                        'certHashes':[base64.b64encode(bytes(32)).decode()]}),
                    'UdmuxEndpoints':[{'Address':'8.8.8.8'}], 'MachineAddress':'10.0.0.1',
                    'NetStackPort':1234, 'NetStackTokenValue':base64.b64encode(b'synthetic').decode()}
            reply = {'http_status':200, 'challenge_present':False,
                     'data':{'status':2, 'joinScript':join}}
            with patch.object(join_allocation, 'ROOT', root), \
                 patch.object(probe_transport, 'ROOT', root), \
                 patch.object(join_allocation, 'RobloxSession') as session, \
                 redirect_stdout(io.StringIO()):
                session.return_value.request.return_value = reply
                self.assertEqual(join_allocation.main(target=target), 0)
                args = session.return_value.request.call_args.args
                self.assertEqual(args[1], '/v1/join-private-game')
                self.assertEqual(args[3]['placeId'], 22)
                self.assertEqual(args[3]['linkCode'], 'synthetic-test-code')
                self.assertEqual(probe_transport.allocation(target=target)[0], 22)
                with self.assertRaises(SessionError):
                    probe_transport.allocation()
            self.assertEqual(json.loads(selection_file.read_text()), selected)
            self.assertEqual(target, {'selection':'user_selected_private_server',
                                     'is_playable':True, 'place_id':22})

    def test_override_still_requires_verified_matching_private_invite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'secrets').mkdir()
            (root/'secrets/private-server-target.json').write_text(json.dumps({
                'status':'Valid', 'placeId':99}))
            target = {'selection':'user_selected_private_server', 'is_playable':True, 'place_id':22}
            with patch.object(join_allocation, 'ROOT', root), \
                 patch.object(join_allocation, 'RobloxSession') as session:
                with self.assertRaises(SessionError):
                    join_allocation.main(target=target)
                session.return_value.request.assert_not_called()
                with self.assertRaises(SessionError):
                    join_allocation.main(target={**target, 'is_playable':False})


if __name__ == '__main__':
    unittest.main()
