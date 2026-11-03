import copy
import threading
import unittest
from unittest.mock import Mock
from launcher_account_pages import purchase_quote,purchase,trade_action,_save_quote,message,message_action,community_action,shop


class AccountPagesTests(unittest.TestCase):
    def setup_api(self):
        api=Mock();api.lock=threading.RLock();api.cache={};api.action_quotes={};api.account.return_value={'id':1}
        self.detail={'AssetTypeId':11,'ProductId':99,'PriceInRobux':5,'Creator':{'CreatorTargetId':10},'IsForSale':True,'Name':'Fixture Shirt'}
        self.writes=[]
        def request(host,path,method='GET',payload=None):
            if path=='/v2/assets/7/details':return copy.deepcopy(self.detail)
            if path.endswith('/is-owned'):return False
            if path=='/v1/user/currency':return {'robux':10}
            if path.startswith('/v1/assets?'):return {'data':[]}
            if path=='/v1/purchases/products/99':self.writes.append(payload);return {'purchased':True}
            self.fail(host+path)
        api.request.side_effect=request
        return api

    def test_purchase_requires_review_then_exact_price_seller_and_single_use(self):
        api=self.setup_api();quote=purchase_quote(api,{'assetId':7})
        self.assertEqual(quote['price'],5);self.assertFalse(self.writes)
        self.assertTrue(purchase(api,{'token':quote['token'],'confirmed':True})['ok'])
        self.assertEqual(self.writes,[{'expectedCurrency':1,'expectedPrice':5,'expectedSellerId':10}])
        with self.assertRaisesRegex(ValueError,'expired'):purchase(api,{'token':quote['token'],'confirmed':True})
        self.assertEqual(len(self.writes),1)

    def test_modern_clothing_uses_bound_purchaser_and_idempotency(self):
        api=self.setup_api();self.detail.update({'CollectibleItemId':'11111111-1111-4111-8111-111111111111','CollectibleProductId':'22222222-2222-4222-8222-222222222222'})
        self.detail['Creator']['CreatorType']='Group'
        original=api.request.side_effect
        calls=[]
        def request(host,path,method='GET',payload=None):
            if path.endswith('/purchase-item'):
                calls.append((host,path,payload));return {'purchased':True}
            return original(host,path,method,payload)
        api.request.side_effect=request
        quote=purchase_quote(api,{'assetId':7})
        self.assertTrue(purchase(api,{'token':quote['token'],'confirmed':True})['ok'])
        self.assertEqual(calls[0][0],'apis.roblox.com')
        payload=calls[0][2]
        self.assertEqual(payload['expectedPurchaserId'],1)
        self.assertEqual(payload['expectedSellerType'],'Group')
        self.assertEqual(payload['expectedPrice'],5)
        __import__('uuid').UUID(payload['idempotencyKey'])

    def test_changed_price_prevents_purchase(self):
        api=self.setup_api();quote=purchase_quote(api,{'assetId':7});self.detail['PriceInRobux']=6
        with self.assertRaisesRegex(ValueError,'price or seller changed'):purchase(api,{'token':quote['token'],'confirmed':True})
        self.assertFalse(self.writes)

    def test_confirmation_must_be_explicit_boolean(self):
        api=self.setup_api()
        for value in (False,1,'true',None):
            with self.assertRaises(ValueError):purchase(api,{'token':'fake','confirmed':value})
        api.request.assert_not_called()

    def test_limited_resale_is_not_sent_to_standard_checkout(self):
        api=self.setup_api();self.detail['IsLimited']=True
        with self.assertRaisesRegex(ValueError,'Limited'):purchase_quote(api,{'assetId':7})
        self.assertFalse(self.writes)

    def test_changed_trade_offer_prevents_accept(self):
        api=self.setup_api();token=_save_quote(api,'trade',{'id':7,'hash':'different'})
        api.request.side_effect=lambda *args,**kw:{'offers':[],'status':'Open'}
        with self.assertRaisesRegex(ValueError,'trade changed'):trade_action(api,{'token':token,'action':'accept','confirmed':True})
        self.assertTrue(all(len(call.args)<3 or call.args[2]!='POST' for call in api.request.call_args_list))

    def test_message_html_is_plain_text_without_running_or_loading_resources(self):
        api=self.setup_api();api.request.side_effect=lambda *a,**kw:{'body':'<p>Hello &amp; welcome</p><script>bad()</script><img src="https://evil.test/track">'}
        data=message(api,7)
        self.assertIn('Hello & welcome',data['plainBody']);self.assertNotIn('bad()',data['plainBody']);self.assertNotIn('evil.test',data['plainBody'])
        self.assertEqual(api.request.call_count,1)

    def test_message_actions_are_not_arbitrary_send_endpoints(self):
        api=self.setup_api()
        with self.assertRaises(ValueError):message_action(api,{'id':7,'action':'send'})
        api.request.assert_not_called()

    def test_community_actions_require_confirmation(self):
        api=self.setup_api()
        with self.assertRaises(ValueError):community_action(api,{'id':7,'action':'leave','confirmed':False})
        api.request.assert_not_called()


if __name__=='__main__':unittest.main()
