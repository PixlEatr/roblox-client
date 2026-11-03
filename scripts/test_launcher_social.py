import unittest
from unittest.mock import Mock
from launcher_social import user_id, people, profile
from launcher import AttachedController


class SocialTests(unittest.TestCase):
    def test_user_ids_are_strict_and_exact(self):
        self.assertEqual(user_id('123'),123)
        for value in ('-1','0','../123','123?x=1','9007199254740992','１２３',True,123):
            with self.assertRaises(ValueError):user_id(value)

    def test_request_list_cannot_fetch_another_accounts_inbox(self):
        api=Mock();api.account.return_value={'id':123};api.cached.side_effect=lambda key,load:load()
        with self.assertRaisesRegex(ValueError,'only available for your account'):
            people(api,'456','requests')
        api.request.assert_not_called()

    def test_people_preserve_names_status_and_paging_without_mutations(self):
        api=Mock();api.cached.side_effect=lambda key,load:load();api.thumbnails.return_value={2:'https://t1.rbxcdn.com/avatar.png'}
        def request(host,path,method='GET',payload=None):
            if path.startswith('/v1/users/1/followers'):return {'data':[{'id':2}],'nextPageCursor':'next'}
            if host=='users.roblox.com':
                self.assertEqual(payload,{'userIds':[2],'excludeBannedUsers':True});return {'data':[{'id':2,'name':'FixtureUser','displayName':'Fixture'}]}
            if host=='presence.roblox.com':return {'userPresences':[{'userId':2,'userPresenceType':2,'placeId':1818,'lastLocation':'Fixture Place'}]}
            self.fail('Unexpected endpoint '+path)
        api.request.side_effect=request
        result=people(api,'1','followers')
        self.assertEqual(result['people'][0]['displayName'],'Fixture')
        self.assertEqual(result['people'][0]['placeId'],1818)
        self.assertEqual(result['nextCursor'],'next')
        self.assertTrue(all('/follow' not in call.args[1] or call.args[1].endswith('sortOrder=Desc') for call in api.request.call_args_list))

    def test_profile_contains_real_fields_and_keeps_empty_description(self):
        api=Mock();api.cached.side_effect=lambda key,load,ttl=0:load();api.account.return_value={'id':1};api.friends.return_value={'friends':[{'id':2,'presence':0}]};api.thumbnails.return_value={2:'https://t1.rbxcdn.com/avatar.png'};api.game_details.return_value=[]
        def request(host,path,*args):
            if host=='users.roblox.com':return {'id':2,'name':'User2','displayName':'Person','description':'','created':'2020-01-01T00:00:00Z'}
            if path.endswith('/count'):return {'count':7}
            if host=='games.roblox.com':return {'data':[]}
            self.fail(path)
        api.request.side_effect=request
        result=profile(api,'2')
        self.assertEqual(result['description'],'')
        self.assertTrue(result['isFriend']);self.assertFalse(result['ownAccount'])
        self.assertEqual(result['counts']['friends'],7)
        self.assertEqual(result['created'],'2020-01-01T00:00:00Z')
        self.assertEqual(result['creations'],[])

    def test_attach_refuses_nonlocal_or_credentialed_destinations(self):
        for url in ('https://example.com','http://192.168.1.5:5467','http://localhost:5467','file:///tmp/file','http://127.0.0.1:5467/other'):
            with self.assertRaises(ValueError):AttachedController(url)


if __name__=='__main__':unittest.main()
