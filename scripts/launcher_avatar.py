"""Authenticated avatar appearance, owned wardrobe, and explicit apply operations."""
import hashlib
import gzip
import io
import http.client
import json
import re
from urllib.parse import urlencode,urlsplit

CATEGORIES={'recent':None,'hats':8,'hair':41,'face-accessories':42,'neck':43,'shoulders':44,'front':45,'back':46,'waist':47,'shirts':11,'pants':12,'t-shirts':2,'faces':18,'heads':17,'layered-t-shirts':64,'layered-shirts':65,'layered-pants':66,'jackets':67,'sweaters':68,'shorts':69,'left-shoes':70,'right-shoes':71,'dresses':72,'dynamic-heads':79}


def version(model):
    return hashlib.sha256(json.dumps(model.get('assets',[]),sort_keys=True).encode()).hexdigest()


def asset_images(api,ids):
    if not ids:return {}
    data=api.request('thumbnails.roblox.com','/v1/assets?'+urlencode({'assetIds':','.join(map(str,ids[:100])),'size':'150x150','format':'Png','isCircular':'false'}))
    from launcher import image_url
    return {row['targetId']:image_url(row.get('imageUrl')) for row in data.get('data',[])}


def appearance(api,refresh=False):
    def load():
        user=api.account()
        model=api.request('avatar.roblox.com','/v4/avatar')['avatarModel']
        images=asset_images(api,[a['id'] for a in model.get('assets',[])])
        assets=[{'id':a['id'],'name':a.get('name','Asset'),'assetTypeId':a.get('assetType',{}).get('id'),
                 'assetType':a.get('assetType',{}).get('name','Item'),'image':images.get(a['id'])} for a in model.get('assets',[])]
        return {'userId':user['id'],'name':user['displayName'],'assets':assets,'version':version(model),
                'avatarType':model.get('playerAvatarType'),'scales':model.get('scales'),
                'preview':api.thumbnails([user['id']],'users/avatar',size='420x420').get(user['id'])}
    if refresh:
        with api.lock:api.cache.pop('avatar-appearance',None)
    return api.cached('avatar-appearance',load,ttl=60)


def wardrobe(api,category,cursor=''):
    if category not in CATEGORIES:raise ValueError('Unknown wardrobe category')
    if not isinstance(cursor,str) or len(cursor)>2048:raise ValueError('Invalid wardrobe cursor')
    if category=='recent':return {'items':appearance(api)['assets'],'nextCursor':None,'category':category}
    def load():
        uid=api.account()['id'];type_id=CATEGORIES[category]
        data=api.request('inventory.roblox.com',f'/v2/users/{uid}/inventory/{type_id}?'+urlencode({'limit':50,'sortOrder':'Desc','cursor':cursor}))
        rows=data.get('data',[])
        images=asset_images(api,[a['assetId'] for a in rows])
        return {'items':[{'id':a['assetId'],'name':a.get('assetName','Item'),'assetTypeId':type_id,
                          'assetType':category,'image':images.get(a['assetId'])} for a in rows],
                'nextCursor':data.get('nextPageCursor'),'category':category}
    return api.cached(('wardrobe',category,cursor),load,ttl=120)


def apply(api,data):
    if not isinstance(data,dict) or set(data)!={'assets','version'}:
        raise ValueError('Invalid avatar update')
    ids=data['assets']
    if not isinstance(ids,list) or len(ids)>50 or any(type(i) is not int or not 0<i<2**53 for i in ids) or len(ids)!=len(set(ids)):
        raise ValueError('Avatar assets must be a unique list of valid IDs')
    # Read again so an old browser tab cannot silently overwrite a newer outfit.
    current=api.request('avatar.roblox.com','/v4/avatar')['avatarModel']
    if data['version']!=version(current):raise ValueError('Your avatar changed elsewhere. Reload it before applying changes.')
    old={a['id']:a for a in current.get('assets',[])}
    uid=api.account()['id']
    for asset_id in ids:
        if asset_id not in old:
            owned=api.request('inventory.roblox.com',f'/v1/users/{uid}/items/Asset/{asset_id}/is-owned')
            if owned is not True:raise ValueError('You can only wear items owned by this account.')
    assets=[]
    for asset_id in ids:
        item={'id':asset_id}
        if old.get(asset_id,{}).get('meta') is not None:item['meta']=old[asset_id]['meta']
        assets.append(item)
    response=api.request('avatar.roblox.com','/v2/avatar/set-wearing-assets','POST',{'assets':assets})
    if isinstance(response,dict) and response.get('success') is False:raise ValueError('Roblox could not apply this outfit.')
    with api.lock:
        api.cache.pop('avatar-appearance',None);api.cache.pop('avatar-3d',None)
    return {'ok':True,'appearance':appearance(api,refresh=True)}


def cdn_url(value):
    from launcher import image_url
    if isinstance(value,str) and image_url(value):return value
    if isinstance(value,str) and re.fullmatch(r'(?:[0-9A-Z]+-)?[a-f0-9]{32,64}',value):
        bucket=31
        for character in value:bucket^=ord(character)
        return f'https://t{bucket%8}.rbxcdn.com/'+value
    raise ValueError('Roblox returned an invalid avatar resource')


def model3d(api):
    def load():
        uid=api.account()['id'];data=api.request('thumbnails.roblox.com',f'/v1/users/avatar-3d?userId={uid}')
        if data.get('state')!='Completed' or not data.get('imageUrl'):
            return {'state':data.get('state','Pending')}
        url=urlsplit(cdn_url(data['imageUrl']))
        connection=http.client.HTTPSConnection(url.hostname,timeout=15)
        try:
            connection.request('GET',url.path+('?' +url.query if url.query else ''))
            response=connection.getresponse();body=response.read(2*1024*1024+1)
            if response.status!=200 or len(body)>2*1024*1024:raise ValueError('Avatar geometry is unavailable')
        finally:connection.close()
        if body.startswith(b'\x1f\x8b'):
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:body=stream.read(2*1024*1024+1)
            if len(body)>2*1024*1024:raise ValueError('Avatar metadata exceeds the size limit')
        metadata=json.loads(body)
        return {'state':'Completed','obj':cdn_url(metadata['obj']),'mtl':cdn_url(metadata['mtl']),
                'textures':[cdn_url(t) for t in metadata.get('textures',[])],
                'camera':metadata.get('camera'),'aabb':metadata.get('aabb')}
    return api.cached('avatar-3d',load,ttl=3)
