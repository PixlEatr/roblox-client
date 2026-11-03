"""Roblox's authenticated home recommendations and server-side experience search."""
import uuid
from urllib.parse import urlencode


def games_for_universes(api, ids):
    ids=list(dict.fromkeys(i for i in ids if type(i) is int and 0<i<2**53))[:40]
    places=[]
    for start in range(0,len(ids),20):
        rows=api.request('games.roblox.com','/v1/games?'+urlencode({'universeIds':','.join(map(str,ids[start:start+20]))})).get('data',[])
        lookup={row['id']:row.get('rootPlaceId') for row in rows}
        places.extend(lookup.get(identifier) for identifier in ids[start:start+20])
    result=[]
    for start in range(0,len(places),20):result.extend(api.game_details(places[start:start+20]))
    order={identifier:n for n,identifier in enumerate(ids)}
    result.sort(key=lambda game:order.get(game['universeId'],999))
    return result


def recommendations(api):
    def load():
        data=api.request('apis.roblox.com','/discovery-api/omni-recommendation','POST',
                         {'pageType':'Home','sessionId':str(uuid.uuid4())})
        sorts=data.get('sorts',[])
        def identifiers(topic):
            sort=next((s for s in sorts if s.get('topic')==topic),{})
            return [row['contentId'] for row in (sort.get('recommendationList') or [])
                    if row.get('contentType')=='Game' and type(row.get('contentId')) is int]
        recommended=games_for_universes(api,identifiers('Recommended For You')[:24])
        recent=games_for_universes(api,identifiers('Continue')[:12])
        return {'recommended':recommended,'continue':recent,'source':'Roblox Home discovery service'}
    return api.cached('roblox-home-recommendations',load,ttl=1800)


def search(api, query, cursor=''):
    if not isinstance(query,str) or not 1<=len(query.strip())<=100:
        raise ValueError('Search must contain 1 to 100 characters')
    if not isinstance(cursor,str) or len(cursor)>4096:
        raise ValueError('Invalid search cursor')
    query=query.strip()
    def load():
        data=api.request('apis.roblox.com','/search-api/omni-search?'+urlencode({
            'searchQuery':query,'pageToken':cursor,'sessionId':str(uuid.uuid4())}))
        rows=[item for group in data.get('searchResults',[]) for item in group.get('contents',[])
              if item.get('contentType') in ('Game',None) and type(item.get('rootPlaceId')) is int]
        games=[]
        for start in range(0,min(len(rows),40),20):games.extend(api.game_details([r['rootPlaceId'] for r in rows[start:start+20]]))
        return {'games':games,'query':data.get('filteredSearchQuery') or query,
                'nextCursor':data.get('nextPageToken'),'source':'Roblox Search'}
    return api.cached(('roblox-search',query,cursor),load,ttl=120)
