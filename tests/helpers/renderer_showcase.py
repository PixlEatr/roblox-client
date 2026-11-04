"""Authored offline scene for rendering QA; never joins or contacts Roblox."""
import copy
import math
import struct
import time
import zlib

from render_assets import fetch_visual_asset


def png(width, height, pixel):
    def chunk(kind, data):
        return struct.pack('>I', len(data))+kind+data+struct.pack('>I', zlib.crc32(kind+data)&0xffffffff)
    raw=b''.join(b'\0'+b''.join(bytes(pixel(x,y)) for x in range(width)) for y in range(height))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')


def make_showcase():
    parts=[]
    def part(name, pos, size, color, material='Plastic', shape=1, **extra):
        p={'id':name,'name':name,'class':'Part','position':{'$vector3':pos},'size':{'$vector3':size},'color':{'$color3':color},'material':material,'shape':shape,'transparency':0,**extra};parts.append(p);return p
    part('Grass',[0,-1,0],[160,2,160],[.31,.48,.22],'Grass')
    part('Plaza',[0,.05,0],[44,.15,36],[.68,.66,.60],'Concrete')
    part('Path',[0,.16,21],[8,.1,16],[.76,.75,.70],'Concrete')
    for i in range(9):
        part('Tile line '+str(i),[-20+i*5,.15,0],[.035,.03,36],[.38,.40,.38],'SmoothPlastic')
    part('Back wall',[0,3,-13],[30,6,1],[.62,.25,.15],'Brick')
    part('Wall cap',[0,6.2,-13],[31,.4,1.4],[.78,.75,.67],'Concrete')
    for x in (-15,15):
        part('Pillar '+str(x),[x,3,-13],[1.5,6.5,1.5],[.77,.75,.67],'Concrete')
    for x in (-11,11):
        for z in (-4,10):
            part(f'Tree trunk {x} {z}',[x,2.1,z],[1.1,3.8,1.1],[.35,.22,.11],'Wood',mesh={'class':'CylinderMesh'})
            part(f'Tree crown {x} {z}',[x,5.8,z],[6,6,6],[.22,.43,.17],'Grass',0)
    for i in range(4):
        part('Bench plank '+str(i),[-7,1.6,-6+i*.32],[6,.22,.27],[.55,.32,.13],'WoodPlanks')
    for x in (-9,-5):
        part('Bench leg '+str(x),[x,.8,-5.5],[.3,1.5,1.3],[.2,.22,.23],'Metal')
    part('Glass display',[8,2.3,-7],[5,4.5,.18],[.64,.89,1],'Glass',transparency=.68)
    for x in (5.5,10.5):part('Window post '+str(x),[x,2.3,-7],[.18,4.6,.28],[.25,.28,.3],'Metal')
    part('Sphere plinth',[3,.7,-6],[3,1.3,3],[.75,.74,.7],'Concrete')
    part('Polished sphere',[3,2.8,-6],[2.8,2.8,2.8],[.66,.72,.8],'Metal',0)
    part('Ramp',[-2,.9,-7],[4,1.5,5],[.30,.42,.62],'SmoothPlastic',**{'class':'WedgePart'})
    part('Neon marker',[8,.8,-5],[.3,1.5,.3],[.1,.75,1],'Neon')
    # A real mesh shipped with the installed Roblox app, loaded through the
    # same scoped visual-asset endpoint as received world assets.
    part('Mesh torso',[-7,1.8,6],[2,2,1],[.8,.55,.2],'SmoothPlastic',**{'class':'MeshPart','mesh':{'class':'MeshPart','id':'rbxasset://avatar/meshes/torso.mesh'},
        'appearance':{'colorMap':'rbxassetid://900003','normalMap':'rbxassetid://900004','roughnessMap':'rbxassetid://900005','metalnessMap':'rbxassetid://900006','alphaMode':0,'color':{'$color3':[1,1,1]}}})
    part('Mesh pedestal',[-7,.5,6],[3,1,3],[.53,.54,.55],'Concrete')
    clothes={'shirt':'rbxassetid://900001','pants':'rbxassetid://900002'}
    rig=[]
    for name,pos,size,color in [('Torso',[0,3,3],[2,2,1],[.9,.7,.45]),('Head',[0,4.5,3],[1.8,1,1],[.94,.73,.48]),('Left Arm',[-1.5,3,3],[1,2,1],[.94,.73,.48]),('Right Arm',[1.5,3,3],[1,2,1],[.94,.73,.48]),('Left Leg',[-.5,1,3],[1,2,1],[.25,.3,.4]),('Right Leg',[.5,1,3],[1,2,1],[.25,.3,.4])]:
        p=part(name,pos,size,color,'SmoothPlastic',own=True,characterPart=name,clothing=clothes)
        p['frame']={'$cframe':{'position':pos,'rotation':[-1,0,0,0,1,0,0,0,-1]}}
        if name=='Head':p['mesh']={'class':'SpecialMesh','type':0};p['decals']=[{'id':'face','texture':'rbxasset://textures/face.png','face':5,'color':{'$color3':[1,1,1]}}]
        rig.append((p,copy.deepcopy(pos)))
    part('Hat brim',[0,5.12,3],[2.3,.15,1.8],[.16,.17,.2],'Fabric',mesh={'class':'CylinderMesh'})
    part('Hat crown',[0,5.55,3],[1.55,.8,1.35],[.16,.17,.2],'Fabric',mesh={'class':'CylinderMesh'})
    frame={'version':2,'parts':parts,'focus':{'$vector3':[0,2.8,3]},'players':[{'name':'Offline avatar','local':True}],
           'lighting':{'ClockTime':14,'Brightness':2.4,'Ambient':{'$color3':[.34,.34,.34]},'OutdoorAmbient':{'$color3':[.65,.7,.8]},'EnvironmentDiffuseScale':.5,'EnvironmentSpecularScale':.8,'FogStart':80,'FogEnd':240,'FogColor':{'$color3':[.74,.83,.9]},'GlobalShadows':True},
           'gui':[],'meshBounds':0,'skippedParts':0,'skippedGui':0,'terrainUnsupported':False}
    start=time.monotonic()
    state={'moving':False,'sunset':False}
    def render():
        t=time.monotonic()-start
        for p,pos in rig:
            p['position']['$vector3']=list(pos)
            if state['moving'] and ('Arm' in p['name'] or 'Leg' in p['name']):
                angle=math.sin(t*5)*.5*(1 if p['name'].startswith('Left') else -1)
                c,s=math.cos(angle),math.sin(angle);p['frame']['$cframe']['rotation']=[-1,0,0,0,c,-s,0,-s,-c]
        frame['lighting']['ClockTime']=17.8 if state['sunset'] else 14
        return copy.deepcopy(frame)
    shirt=png(585,559,lambda x,y:(24+(x//8%2)*10,88+(y//8%2)*17,146+(x//8%2)*20,255))
    pants=png(585,559,lambda x,y:(34+x%3*2,42+y%3*2,59+x%4*2,255))
    surface={
        'rbxassetid://900003':png(64,64,lambda x,y:(26,94+(x//8+y//8)%2*35,154+(x//8+y//8)%2*35,255)),
        'rbxassetid://900004':png(64,64,lambda x,y:(128+(x%8==0)*12,128+(y%8==0)*12,254,255)),
        'rbxassetid://900005':png(64,64,lambda x,y:(150,150,150,255)),
        'rbxassetid://900006':png(64,64,lambda x,y:(30,30,30,255)),
    }
    def asset(ref,**kwargs):
        if ref=='rbxassetid://900001':return shirt
        if ref=='rbxassetid://900002':return pants
        if ref in surface:return surface[ref]
        if ref.startswith('rbxasset://'):return fetch_visual_asset(ref,**kwargs)
        raise ValueError('Network disabled in offline rendering fixture')
    return frame,render,asset,state
