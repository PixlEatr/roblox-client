from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import join_allocation
from roblox_session import SessionError


class PrivateJoinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'secrets').mkdir(mode=0o700)
        (self.root / 'outputs/gamejoin').mkdir(parents=True)
        (self.root / 'outputs/gamejoin/target-selection.json').write_text(json.dumps({
            'selection': 'user_selected_private_server', 'is_playable': True, 'place_id': 123}))
        (self.root / 'secrets/private-server-target.json').write_text(json.dumps({
            'status': 'Valid', 'placeId': 123, 'ownerUserId': 456,
            'privateServerId': 789, 'linkCode': 'synthetic-invite'}))

    def run_allocation(self, body):
        session = Mock()
        session.request.return_value = {'http_status': 200, 'challenge_present': False, 'data': body}
        with patch.object(join_allocation, 'ROOT', self.root), patch.object(join_allocation, 'RobloxSession', return_value=session), redirect_stdout(io.StringIO()):
            code = join_allocation.main()
        return code, session

    def test_ready_private_allocation_uses_invite_and_saves_binding(self):
        code, session = self.run_allocation({'status': 2, 'joinScript': {
            'PlaceId': 123, 'PrivateServerOwnerID': 456, 'PrivateServerID': 'synthetic-private-server'}})
        self.assertEqual(code, 0)
        args = session.request.call_args.args
        self.assertEqual(args[:3], ('gamejoin.roblox.com', '/v1/join-private-game', 'POST'))
        self.assertEqual(args[3]['linkCode'], 'synthetic-invite')
        saved = json.loads((self.root / 'secrets/last-join.json').read_text())
        self.assertEqual(saved['private_server_invite_id'], 789)
        public = (self.root / 'outputs/gamejoin/allocation-check.json').read_text()
        self.assertNotIn('synthetic-invite', public)
        self.assertNotIn('synthetic-private-server', public)

    def test_privacy_denial_cannot_be_used_as_an_allocation(self):
        code, session = self.run_allocation({'status': 21, 'message': 'User privacy does not allow join.', 'joinScript': None})
        self.assertEqual(code, 2)
        self.assertEqual(session.request.call_count, 1)
        self.assertFalse((self.root / 'secrets/last-join.json').exists())
        report = json.loads((self.root / 'outputs/gamejoin/allocation-check.json').read_text())
        self.assertTrue(report['user_privacy_blocks_join'])

    def test_wrong_owner_or_place_allocation_is_rejected(self):
        for owner, place in ((999, 123), (456, 999)):
            with self.assertRaises(SessionError):
                self.run_allocation({'status': 2, 'joinScript': {
                    'PlaceId': place, 'PrivateServerOwnerID': owner, 'PrivateServerID': 'synthetic'}})
            self.assertFalse((self.root / 'secrets/last-join.json').exists())


if __name__ == '__main__': unittest.main()
