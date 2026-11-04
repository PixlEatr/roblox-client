import copy
import unittest
from unittest.mock import Mock
from launcher_avatar import apply, version, cdn_url, wardrobe
from launcher_discovery import recommendations, search


class AvatarDiscoveryTests(unittest.TestCase):
    def api(self):
        api=Mock();api.cached.side_effect=lambda key,load,ttl=0:load();api.lock=__import__('threading').RLock();api.cache={};api.account.return_value={'id':1,'displayName':'Fixture'}
        return api

    def test_avatar_rejects_stale_update_before_posting(self):
        api=self.api();api.request.return_value={'avatarModel':{'assets':[{'id':5}]}}
        with self.assertRaisesRegex(ValueError,'changed elsewhere'):apply(api,{'assets':[5],'version':'old'})
        self.assertEqual(api.request.call_count,1)

    def test_avatar_rejects_unowned_new_asset(self):
        api=self.api();model={'assets':[{'id':5}]}
        api.request.side_effect=[{'avatarModel':model},False]
        with self.assertRaisesRegex(ValueError,'only wear items owned'):apply(api,{'assets':[5,6],'version':version(model)})
        self.assertTrue(all(len(c.args)<3 or c.args[2]!='POST' for c in api.request.call_args_list))

    def test_avatar_malformed_lists_never_reach_roblox(self):
        api=self.api()
        for ids in ([{}],[True],[1,1],['5'],list(range(1,52))):
            with self.assertRaises(ValueError):apply(api,{'assets':ids,'version':'x'})
        api.request.assert_not_called()

    def test_avatar_only_preserves_asset_metadata_and_verifies_ownership(self):
        api=self.api();model={'assets':[{'id':5,'name':'Old','meta':{'order':1}}]}
        def request(host,path,method='GET',payload=None):
            if path=='/v4/avatar':return {'avatarModel':model}
            if path.endswith('/is-owned'):return True
            if path=='/v2/avatar/set-wearing-assets':
                self.assertEqual(payload,{'assets':[{'id':5,'meta':{'order':1}},{'id':6}]});return {'success':True}
            if path.startswith('/v1/assets?'):return {'data':[]}
            self.fail(path)
        api.request.side_effect=request;api.thumbnails.return_value={}
        self.assertTrue(apply(api,{'assets':[5,6],'version':version(model)})['ok'])

    def test_avatar_resources_are_restricted_to_roblox_cdn(self):
        self.assertEqual(cdn_url('30DAY-'+'a'*32),'https://t5.rbxcdn.com/30DAY-'+'a'*32)
        for value in ('https://evil.example/model','file:///tmp/data','../../private','https://rbxcdn.com.evil.example/a'):
            with self.assertRaises(ValueError):cdn_url(value)

    def test_search_uses_roblox_results_and_real_cursor(self):
        api=self.api();api.request.return_value={'searchResults':[{'contents':[{'contentType':'Game','rootPlaceId':1818}]}],'filteredSearchQuery':'obby','nextPageToken':'next'};api.game_details.return_value=[{'placeId':1818,'name':'Returned by Roblox'}]
        data=search(api,'obby')
        self.assertEqual(data['games'][0]['name'],'Returned by Roblox');self.assertEqual(data['nextCursor'],'next')
        self.assertIn('searchQuery=obby',api.request.call_args.args[1])

    def test_recommendations_use_server_sort_order_including_continue(self):
        api=self.api()
        def request(host,path,method='GET',payload=None):
            if path=='/discovery-api/omni-recommendation':return {'sorts':[{'topic':'Recommended For You','recommendationList':[{'contentType':'Game','contentId':2},{'contentType':'Game','contentId':1}]},{'topic':'Continue','recommendationList':[{'contentType':'Game','contentId':3}]}]}
            if 'universeIds=2%2C1' in path:return {'data':[{'id':1,'rootPlaceId':10},{'id':2,'rootPlaceId':20}]}
            if 'universeIds=3' in path:return {'data':[{'id':3,'rootPlaceId':30}]}
            self.fail(path)
        api.request.side_effect=request
        api.game_details.side_effect=lambda ids:[{'placeId':i,'universeId':i//10} for i in ids]
        data=recommendations(api)
        self.assertEqual([x['placeId'] for x in data['recommended']],[20,10])
        self.assertEqual(data['continue'][0]['placeId'],30)

    def test_invalid_search_is_rejected_without_request(self):
        api=self.api()
        for query in ('',' '*10,'x'*101,None):
            with self.assertRaises(ValueError):search(api,query)
        api.request.assert_not_called()


if __name__=='__main__':unittest.main()
