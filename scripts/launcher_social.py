"""Read-only Roblox connections and profile pages for the local launcher."""

def user_id(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or not 0 < int(value) < 2**53:
        raise ValueError('Invalid Roblox user ID')
    return int(value)


def people(api, value, kind='friends'):
    uid = user_id(value)
    if kind not in ('friends', 'followers', 'followings', 'requests'):
        raise ValueError('Invalid connection category')
    def load():
        if kind == 'requests':
            if uid != api.account()['id']:
                raise ValueError('Connection requests are only available for your account')
            endpoint = '/v1/my/friends/requests?limit=100&sortOrder=Desc'
        elif kind == 'friends':
            endpoint = f'/v1/users/{uid}/friends'
        else:
            endpoint = f'/v1/users/{uid}/{kind}?limit=100&sortOrder=Desc'
        data = api.request('friends.roblox.com', endpoint)
        rows = data.get('data', [])[:200]
        ids = [row.get('id') or row.get('userId') or (row.get('user') or {}).get('id') for row in rows]
        ids = [i for i in ids if type(i) is int and i>0]
        names, images = {}, {}
        for start in range(0,len(ids),100):
            part = ids[start:start+100]
            reply = api.request('users.roblox.com','/v1/users','POST',{'userIds':part,'excludeBannedUsers':True})
            names.update({x['id']:x for x in reply.get('data',[])})
            images.update(api.thumbnails(part))
        presence = {}
        warning = None
        for start in range(0,len(ids),50):
            try:
                reply = api.request('presence.roblox.com','/v1/presence/users','POST',{'userIds':ids[start:start+50]})
                presence.update({p['userId']:p for p in reply.get('userPresences',[])})
            except Exception:
                warning = 'Online status could not be loaded.'
                break
        result=[]
        for identifier in ids:
            user=names.get(identifier,{})
            p=presence.get(identifier,{})
            result.append({'id':identifier,'name':user.get('name') or str(identifier),
                           'displayName':user.get('displayName') or user.get('name') or str(identifier),
                           'avatar':images.get(identifier),'presence':p.get('userPresenceType'),
                           'location':p.get('lastLocation',''),'placeId':p.get('placeId')})
        return {'people':result,'nextCursor':data.get('nextPageCursor'),'warning':warning,'kind':kind,'userId':uid}
    return api.cached(('people',uid,kind),load)


def profile(api, value):
    uid=user_id(value)
    def load():
        user=api.request('users.roblox.com',f'/v1/users/{uid}')
        if not isinstance(user,dict) or user.get('id')!=uid:
            raise ValueError('Profile not found')
        own=api.account()['id']==uid
        friend=next((x for x in api.friends()['friends'] if x['id']==uid),None)
        counts={}
        for kind in ('friends','followers','followings'):
            try:
                data=api.request('friends.roblox.com',f'/v1/users/{uid}/{kind}/count')
                counts[kind]=data.get('count')
            except Exception:
                counts[kind]=None
        creations=[]
        creations_error=None
        try:
            data=api.request('games.roblox.com',f'/v2/users/{uid}/games?accessFilter=Public&sortOrder=Desc&limit=10')
            ids=[x.get('rootPlace',{}).get('id') for x in data.get('data',[])]
            creations=api.game_details(ids)
        except Exception:
            creations_error='Experiences could not be loaded for this profile.'
        return {'id':uid,'name':user.get('name',''),'displayName':user.get('displayName') or user.get('name',''),
                'description':user.get('description',''),'created':user.get('created'),
                'verified':bool(user.get('hasVerifiedBadge')),'banned':bool(user.get('isBanned')),
                'avatar':api.thumbnails([uid]).get(uid),
                'fullAvatar':api.thumbnails([uid], 'users/avatar', size='420x420').get(uid),
                'ownAccount':own,'isFriend':friend is not None,'presence':friend.get('presence') if friend else None,
                'location':friend.get('location','') if friend else '', 'placeId':friend.get('placeId') if friend else None,
                'counts':counts,'creations':creations,'creationsError':creations_error}
    return api.cached(('profile',uid),load,ttl=120)
