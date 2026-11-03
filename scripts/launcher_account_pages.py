"""Real account pages; side effects are only invoked by explicit UI actions."""
import hashlib
from html.parser import HTMLParser
import json
import secrets
import time
import uuid
from urllib.parse import urlencode

from launcher_avatar import asset_images, wardrobe, CATEGORIES


def identifier(value):
    if type(value) is int and 0<value<2**53:return value
    if isinstance(value,str) and value.isascii() and value.isdigit() and 0<int(value)<2**53:return int(value)
    raise ValueError('Invalid item or user ID')


def cursor(value):
    if not isinstance(value,str) or len(value)>4096:raise ValueError('Invalid page cursor')
    return value


def messages(api,tab='Inbox',page='0'):
    if tab not in ('Inbox','Sent','Archive'):raise ValueError('Invalid message folder')
    if not isinstance(page,str) or not page.isdigit() or not 0<=int(page)<=1000:raise ValueError('Invalid message page')
    data=api.request('privatemessages.roblox.com','/v1/messages?'+urlencode({'messageTab':tab,'pageNumber':int(page),'pageSize':20}))
    return {'messages':data.get('collection',[]),'page':data.get('pageNumber',int(page)),
            'totalPages':data.get('totalPages',0),'total':data.get('totalCollectionSize',0),'tab':tab}


def message(api,value):
    data=api.request('privatemessages.roblox.com',f'/v1/messages/{identifier(value)}')
    class PlainText(HTMLParser):
        def __init__(self):super().__init__(convert_charrefs=True);self.parts=[];self.ignored=0
        def handle_starttag(self,tag,attrs):
            if tag in ('script','style'):self.ignored+=1
            if tag in ('br','p','div','li') and not self.ignored:self.parts.append('\n')
        def handle_endtag(self,tag):
            if tag in ('script','style'):self.ignored=max(0,self.ignored-1)
        def handle_data(self,text):
            if not self.ignored:self.parts.append(text)
    parser=PlainText();parser.feed(data.get('body',''));data=dict(data);data['plainBody']=''.join(parser.parts)
    return data


def message_action(api,data):
    if set(data)!={'id','action'} or data['action'] not in ('archive','unarchive','mark-read','mark-unread'):raise ValueError('Invalid message action')
    mid=identifier(data['id']);message(api,mid)
    api.request('privatemessages.roblox.com','/v1/messages/'+data['action'],'POST',{'messageIds':[mid]})
    return {'ok':True}


def inventory(api,category='hats',token=''):
    if category not in CATEGORIES or category=='recent':raise ValueError('Choose an inventory category')
    return wardrobe(api,category,cursor(token))


def trades(api,status='Inbound',token=''):
    if status not in ('Inbound','Outbound','Completed','Inactive'):raise ValueError('Invalid trade category')
    eligibility=None
    try:eligibility=api.request('trades.roblox.com','/v2/users/me/can-trade')
    except Exception:pass
    data=api.request('trades.roblox.com',f'/v1/trades/{status}?'+urlencode({'limit':25,'sortOrder':'Desc','cursor':cursor(token)}))
    return {'trades':data.get('data',[]),'nextCursor':data.get('nextPageCursor'),'status':status,'eligibility':eligibility}


def _offer_hash(detail):
    return hashlib.sha256(json.dumps({'offers':detail.get('offers'),'status':detail.get('status')},sort_keys=True).encode()).hexdigest()


def _save_quote(api,kind,payload):
    with api.lock:
        quotes=getattr(api,'action_quotes',{})
        quotes={key:value for key,value in quotes.items() if value['expires']>time.monotonic()}
        if len(quotes)>=30:raise ValueError('Too many pending reviews; close a review and try again shortly')
        token=secrets.token_urlsafe(24);quotes[token]={'kind':kind,'payload':payload,'expires':time.monotonic()+120};api.action_quotes=quotes
    return token


def _take_quote(api,token,kind):
    if not isinstance(token,str) or not 10<len(token)<200:raise ValueError('Invalid review token')
    with api.lock:quote=getattr(api,'action_quotes',{}).pop(token,None)
    if not quote or quote['kind']!=kind or quote['expires']<=time.monotonic():raise ValueError('This review expired. Open it again before confirming.')
    return quote['payload']


def trade_detail(api,value):
    tid=identifier(value);detail=api.request('trades.roblox.com',f'/v2/trades/{tid}')
    uid=api.account()['id']
    if not any(offer.get('user',{}).get('id')==uid for offer in detail.get('offers',[])):raise ValueError('This trade does not belong to your account')
    ids=[item.get('assetId') or item.get('id') for offer in detail.get('offers',[]) for item in offer.get('userAssets',[])];images=asset_images(api,[i for i in ids if type(i) is int])
    return {'trade':detail,'images':images,'reviewToken':_save_quote(api,'trade',{'id':tid,'hash':_offer_hash(detail)})}


def trade_action(api,data):
    if set(data)!={'token','action','confirmed'} or data['confirmed'] is not True or data['action'] not in ('accept','decline'):raise ValueError('Review and confirm the trade action first')
    review=_take_quote(api,data['token'],'trade')
    detail=api.request('trades.roblox.com',f'/v2/trades/{review["id"]}')
    if _offer_hash(detail)!=review['hash']:raise ValueError('The trade changed. Review the current offer again.')
    api.request('trades.roblox.com',f'/v1/trades/{review["id"]}/{data["action"]}','POST',{})
    return {'ok':True}


def group_images(api,ids):
    if not ids:return {}
    from launcher import image_url
    data=api.request('thumbnails.roblox.com','/v1/groups/icons?'+urlencode({'groupIds':','.join(map(str,ids[:100])),'size':'150x150','format':'Png','isCircular':'false'}))
    return {row['targetId']:image_url(row.get('imageUrl')) for row in data.get('data',[])}


def communities(api,query=''):
    if not isinstance(query,str) or len(query)>100:raise ValueError('Community search is too long')
    uid=api.account()['id'];own=api.request('groups.roblox.com',f'/v1/users/{uid}/groups/roles').get('data',[])
    own_map={row['group']['id']:row for row in own}
    rows=[row['group'] for row in own]
    if query.strip():rows=api.request('groups.roblox.com','/v1/groups/search?'+urlencode({'keyword':query.strip(),'limit':25,'prioritizeExactMatch':'true'})).get('data',[])
    images=group_images(api,[row['id'] for row in rows])
    return {'groups':[{'id':row['id'],'name':row.get('name',''),'description':row.get('description',''),
                       'members':row.get('memberCount'),'image':images.get(row['id']),
                       'joined':row['id'] in own_map,'role':own_map.get(row['id'],{}).get('role',{}).get('name')} for row in rows]}


def community(api,value):
    gid=identifier(value);detail=api.request('groups.roblox.com',f'/v1/groups/{gid}')
    own=communities(api)['groups'];joined=next((row for row in own if row['id']==gid),None)
    wall=[];wall_error=None
    try:wall=api.request('groups.roblox.com',f'/v2/groups/{gid}/wall/posts?sortOrder=Desc&limit=10').get('data',[])
    except Exception:wall_error='The community wall is unavailable.'
    return {'group':detail,'image':group_images(api,[gid]).get(gid),'joined':joined is not None,
            'role':joined.get('role') if joined else None,'wall':wall,'wallError':wall_error}


def community_action(api,data):
    if set(data)!={'id','action','confirmed'} or data['confirmed'] is not True or data['action'] not in ('join','leave'):raise ValueError('Confirm the community action first')
    gid=identifier(data['id']);uid=api.account()['id']
    if data['action']=='join':api.request('groups.roblox.com',f'/v1/groups/{gid}/users','POST',{})
    else:api.request('groups.roblox.com',f'/v1/groups/{gid}/users/{uid}','DELETE')
    # Verify membership instead of claiming an unconfirmed join/leave succeeded.
    joined=any(row['id']==gid for row in communities(api)['groups'])
    if joined!=(data['action']=='join'):raise ValueError('Roblox has not completed the membership change. A join request may require approval.')
    return {'ok':True,'joined':joined}


SHOP_TYPES={'shirts':(56,11),'pants':(57,12),'t-shirts':(55,2),'layered-t-shirts':(58,64),'layered-shirts':(59,65),'layered-pants':(60,66),'jackets':(61,67),'sweaters':(62,68),'shorts':(63,69),'dresses':(65,72)}


def shop(api,query='',category='shirts',token=''):
    if category not in SHOP_TYPES or not isinstance(query,str) or len(query)>100:raise ValueError('Invalid clothing search')
    data=api.request('catalog.roblox.com','/v1/search/items/details?'+urlencode({'Category':3,'Subcategory':SHOP_TYPES[category][0],'Keyword':query,'Limit':30,'Cursor':cursor(token)}))
    rows=[row for row in data.get('data',[]) if row.get('itemType')=='Asset' and row.get('assetType')==SHOP_TYPES[category][1]]
    images=asset_images(api,[row['id'] for row in rows])
    return {'items':[{'id':row['id'],'name':row.get('name','Item'),'price':row.get('price'),
                      'creator':row.get('creatorName',''),'image':images.get(row['id']),
                      'assetTypeId':row.get('assetType')} for row in rows], 'nextCursor':data.get('nextPageCursor')}


def product(api,value):
    aid=identifier(value);detail=api.request('economy.roblox.com',f'/v2/assets/{aid}/details')
    if detail.get('AssetTypeId') not in (2,8,11,12,17,18,41,42,43,44,45,46,47,64,65,66,67,68,69,70,71,72,76,77,79,88,89,90):raise ValueError('This checkout supports wearable avatar items only')
    if detail.get('IsLimited') or detail.get('IsLimitedUnique') or detail.get('CollectiblesItemDetails',{}).get('IsLimited'):raise ValueError('Limited and resale items require Roblox’s own marketplace checkout')
    return detail


def product_terms(detail):
    creator=detail.get('Creator',{})
    return {'productId':detail.get('ProductId'),'price':detail.get('PriceInRobux'),
            'seller':creator.get('CreatorTargetId') or creator.get('Id'), 'sellerType':creator.get('CreatorType'),
            'collectibleItemId':detail.get('CollectibleItemId'),'collectibleProductId':detail.get('CollectibleProductId'),
            'forSale':detail.get('IsForSale')}


def purchase_quote(api,data):
    if set(data)!={'assetId'}:raise ValueError('Invalid purchase preview')
    aid=identifier(data['assetId']);detail=product(api,aid);terms=product_terms(detail)
    uid=api.account()['id'];owned=api.request('inventory.roblox.com',f'/v1/users/{uid}/items/Asset/{aid}/is-owned') is True
    balance=api.request('economy.roblox.com','/v1/user/currency').get('robux')
    valid=type(terms['price']) is int and terms['price']>=0 and type(terms['seller']) is int and terms['forSale'] is True
    if terms['collectibleItemId']:
        try:
            uuid.UUID(terms['collectibleItemId']);uuid.UUID(terms['collectibleProductId'])
            valid=valid and terms['sellerType'] in ('User','Group')
        except (ValueError,TypeError,AttributeError):valid=False
    else:valid=valid and type(terms['productId']) is int
    token=_save_quote(api,'purchase',{'assetId':aid,'terms':terms,'idempotency':str(uuid.uuid4())}) if valid and not owned else None
    return {'assetId':aid,'name':detail.get('Name','Clothing item'),'description':detail.get('Description',''),
            'image':asset_images(api,[aid]).get(aid),'price':terms['price'],'balance':balance,
            'owned':owned,'forSale':valid,'token':token}


def purchase(api,data):
    if set(data)!={'token','confirmed'} or data['confirmed'] is not True:raise ValueError('Review and confirm this purchase first')
    quote=_take_quote(api,data['token'],'purchase');aid=quote['assetId'];detail=product(api,aid)
    if product_terms(detail)!=quote['terms']:raise ValueError('The price or seller changed. Review the item again.')
    uid=api.account()['id']
    if api.request('inventory.roblox.com',f'/v1/users/{uid}/items/Asset/{aid}/is-owned') is True:raise ValueError('You already own this item')
    balance=api.request('economy.roblox.com','/v1/user/currency').get('robux')
    if type(balance) is not int or balance<quote['terms']['price']:raise ValueError('Insufficient Robux')
    terms=quote['terms']
    if terms['collectibleItemId']:
        result=api.request('apis.roblox.com',f'/marketplace-sales/v1/item/{terms["collectibleItemId"]}/purchase-item','POST',
                           {'collectibleItemId':terms['collectibleItemId'],'collectibleProductId':terms['collectibleProductId'],
                            'expectedCurrency':1,'expectedPrice':terms['price'],'expectedPurchaserId':uid,
                            'expectedPurchaserType':'User','expectedSellerId':terms['seller'],'expectedSellerType':terms['sellerType'],
                            'idempotencyKey':quote['idempotency'],'rentalOptionDays':None})
    else:
        result=api.request('economy.roblox.com',f'/v1/purchases/products/{terms["productId"]}','POST',
                           {'expectedCurrency':1,'expectedPrice':terms['price'],'expectedSellerId':terms['seller']})
    if isinstance(result,dict) and result.get('reason')=='TwoStepVerificationRequired':
        raise ValueError('Roblox requires two-step verification. Complete the purchase through Roblox’s official checkout.')
    if not isinstance(result,dict) or result.get('purchased') is not True:
        owned=api.request('inventory.roblox.com',f'/v1/users/{uid}/items/Asset/{aid}/is-owned') is True
        if not owned:raise ValueError('Purchase is not confirmed. Check your inventory and transactions before retrying.')
    with api.lock:
        for key in list(api.cache):
            if isinstance(key,tuple) and key[0]=='wardrobe':api.cache.pop(key,None)
    return {'ok':True,'assetId':aid,'balance':api.request('economy.roblox.com','/v1/user/currency').get('robux')}
