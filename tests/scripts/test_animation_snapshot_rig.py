"""Canonical animation scaling, authored geometry, and native pose handoff."""
import copy
import unittest
from collections import deque
from types import SimpleNamespace

from animation_snapshot_rig import SnapshotAnimationRig
from animation_joint_model import AnimationJointModel
from authored_force_state import AuthoredForceState
from test_animation_pose_runtime import operation, IDENTITY


def frame(position=(0,0,0)):
    return {'$cframe': {'position':list(position),'rotation':list(IDENTITY)}}


def fixture(scale=1.):
    def node(i,kind,name,parent,**props):
        return {'id':f'1:{i}','class_name':kind,'name':name,
                'parent':f'1:{parent}' if parent else None,'properties':props}
    return {'nodes':[
        node(1,'Model','Character',8,Scale=scale),
        node(2,'Part','HumanoidRootPart',1,CFrame=frame(),Size={'$vector3':[2,2,1]}),
        node(3,'Humanoid','Humanoid',1),node(4,'Animator','Animator',3),
        node(5,'Part','Arm',1,CFrame=frame(),Size={'$vector3':[1,2,1]}),
        node(6,'Motor6D','Shoulder',2,Part0={'$ref':'1:2'},Part1={'$ref':'1:5'},
             C0=frame((0,2*scale,0)),C1=frame((0,-scale,0)),Enabled=True),
        node(7,'Player','Player',None,UserId=42,Character={'$ref':'1:1'}),
        node(8,'Workspace','Workspace',None)],'local_player':'1:7'}


def world_for(snapshot):
    classes=sorted({n['class_name'] for n in snapshot['nodes']})
    def key(identifier):return tuple(map(int,identifier.split(':'))) if identifier else None
    def decode(value):
        if isinstance(value,dict):
            if '$ref' in value:return key(value['$ref'])
            if '$cframe' in value:return copy.deepcopy(value['$cframe'])
        return copy.deepcopy(value)
    nodes={key(n['id']):{'class':classes.index(n['class_name']),
        'parent':key(n['parent']), 'properties':{
            **{k:decode(v) for k,v in n['properties'].items()},'Name':n['name']}}
        for n in snapshot['nodes']}
    world=SimpleNamespace(instances=nodes,schema={'classes':[{'name':c} for c in classes]},
        revision=0,actions=deque(),creation={})
    world.avatar=lambda user_id:{'player_key':(1,7),'character_key':(1,1),'root_key':(1,2),
        'evidence':{'player_spawned':user_id==42}}
    return world


SAMPLE={'status':'ok','poses':{('HumanoidRootPart','Arm'):{
    'position':[1.,0.,0.],'rotation':IDENTITY,'weight':1.}}}


class AnimationSnapshotRigTests(unittest.TestCase):
    def evaluate(self,data,sample=SAMPLE):
        return SnapshotAnimationRig(data,'1:1','1:2','1:4').evaluate(frame((10,20,30)),sample)

    def test_scaled_translation_and_bind_frames_are_each_applied_once(self):
        for scale in (1.,2.,.5,3.,1.):
            data=fixture(scale);before=copy.deepcopy(data);sample_before=copy.deepcopy(SAMPLE)
            result=self.evaluate(data)
            self.assertEqual(result['status'],'ok',result)
            self.assertEqual(result['poses']['1:2'][0],(10.,20.,30.))
            self.assertEqual(result['poses']['1:5'][0],(10.+scale,20.+3*scale,30.))
            self.assertEqual(result['poses']['1:5'][1],tuple(IDENTITY))
            self.assertEqual(data,before);self.assertEqual(SAMPLE,sample_before)

    def test_rotation_and_weight_are_preserved(self):
        sample=copy.deepcopy(SAMPLE);pose=sample['poses'][('HumanoidRootPart','Arm')]
        pose['rotation']=[0,0,1,0,1,0,-1,0,0];pose['weight']=.5
        small=self.evaluate(fixture(1),sample);big=self.evaluate(fixture(2),sample)
        self.assertEqual(small['status'],'ok');self.assertEqual(big['status'],'ok')
        for a,b,root in zip(small['poses']['1:5'][0],big['poses']['1:5'][0],(10,20,30)):
            self.assertAlmostEqual(b-root,2*(a-root))
        self.assertEqual(small['poses']['1:5'][1],big['poses']['1:5'][1])

    def test_nested_canonical_scale_is_not_multiplied_by_ancestor_again(self):
        data=fixture(6);data['nodes'][0]['parent']='1:9'
        data['nodes'].append({'id':'1:9','class_name':'Model','parent':'1:8','properties':{'Scale':3}})
        result=self.evaluate(data)
        self.assertEqual(result['animation_model_scale'],6)
        self.assertEqual(result['poses']['1:5'][0],(16.,38.,30.))

    def test_invalid_scale_and_topology_return_no_pose(self):
        for scale in (0,-1,True,float('inf'),float('nan'),'2'):
            data=fixture();data['nodes'][0]['properties']['Scale']=scale
            result=self.evaluate(data);self.assertNotEqual(result['status'],'ok')
            self.assertEqual(result['poses'],{})
        for change in ('disabled','missing','duplicate','cycle'):
            data=fixture();joint=data['nodes'][5]
            if change=='disabled':joint['properties']['Enabled']=False
            elif change=='missing':joint['properties'].pop('C1')
            elif change=='duplicate':data['nodes'].append({**copy.deepcopy(joint),'id':'1:20'})
            else:joint['properties']['Part1']={'$ref':'1:2'}
            result=self.evaluate(data);self.assertNotEqual(result['status'],'ok');self.assertEqual(result['poses'],{})

    def test_animation_constraint_uses_authored_attachment_frames(self):
        data=fixture(2);joint=data['nodes'][5];joint['class_name']='AnimationConstraint'
        joint['properties']={'Attachment0':{'$ref':'1:10'},'Attachment1':{'$ref':'1:11'},'Enabled':True}
        for i,parent,position in [(10,'1:2',(0,4,0)),(11,'1:5',(0,-2,0))]:
            data['nodes'].append({'id':f'1:{i}','class_name':'Attachment','parent':parent,
                'properties':{'CFrame':frame(position)}})
        self.assertEqual(self.evaluate(data)['poses']['1:5'][0],(12.,26.,30.))

    def test_authored_scale_and_bind_edits_survive_unrelated_receipts(self):
        data=fixture();state=AuthoredForceState();before=copy.deepcopy(data)
        state.apply([{'kind':'set','id':identifier,'property':prop,'value':value}
            for identifier,prop,value in [('1:1','Scale',2),('1:6','C0',frame((0,4,0))),
                                         ('1:6','C1',frame((0,-2,0)))]],data)
        state.invalidate(data)
        self.assertEqual(self.evaluate(state.snapshot(data))['poses']['1:5'][0],(12.,26.,30.))
        self.assertEqual(data,before)
        data['nodes'][5]['property_revisions']={'C0':10}
        state.invalidate(data)
        self.assertEqual(self.evaluate(state.snapshot(data))['poses']['1:5'][0],(12.,24.,30.))

    def test_animation_projection_excludes_map_geometry_and_tracks_local_parent_changes(self):
        data=fixture(2);data['nodes'][1]['properties']['MeshGeometry']={'large':'unused'}
        data['nodes'].append({'id':'1:99','class_name':'Part','name':'Map','parent':'1:8',
            'properties':{'MeshGeometry':{'large':'unused'}}})
        before=copy.deepcopy(data);state=AuthoredForceState()
        projected=state.animation_snapshot(data,'1:1')
        self.assertNotIn('1:99',{n['id'] for n in projected['nodes']})
        self.assertTrue(all('MeshGeometry' not in n['properties'] for n in projected['nodes']))
        self.assertEqual(self.evaluate(projected)['poses']['1:5'][0],(12.,26.,30.))
        state.apply([{'kind':'set','id':'1:6','property':'Parent','value':{'$ref':'1:8'}}],data)
        self.assertNotEqual(self.evaluate(state.animation_snapshot(data,'1:1'))['status'],'ok')
        self.assertEqual(data,before)

    def test_missing_enabled_requires_complete_received_creation_evidence(self):
        data=fixture();data['nodes'][5]['properties'].pop('Enabled')
        self.assertNotEqual(self.evaluate(data)['status'],'ok')
        rig=SnapshotAnimationRig(data,'1:1','1:2','1:4',enabled_defaults={'1:6'})
        self.assertEqual(rig.evaluate(frame(),SAMPLE)['status'],'ok')
        data['nodes'][5]['properties']['Enabled']=False
        rig=SnapshotAnimationRig(data,'1:1','1:2','1:4',enabled_defaults={'1:6'})
        self.assertNotEqual(rig.evaluate(frame(),SAMPLE)['status'],'ok')

    def test_local_animator_keeps_playing_across_ticks_and_is_forgotten_on_removal(self):
        data=fixture();world=world_for(data);del world.instances[(1,4)]
        data['nodes'][3]['id']='local:animator'
        clip={'version':1,'kind':'KeyframeSequence','length':1.,'looped':True,
            'keyframes':[{'time':t,'poses':[{'path':['HumanoidRootPart','Arm'],
                'position':[t,0,0],'rotation':IDENTITY,'weight':1.}]} for t in (0.,1.)]}
        model=AnimationJointModel(42,lambda _:clip)
        def tick(now,entries=()):
            return model.tick(world=world,physics_report={'predicted_position':[0,0,0],
                'predicted_yaw':0.},entries=list(entries),now=now,snapshot=data)
        self.assertEqual(tick(0,[operation(1,actor='local:animator')])['status'],'ok')
        self.assertEqual(tick(.25)['poses'][(1,5)][0],(.25,3.,0.))
        data['nodes'].pop(3)
        removed=tick(.5);self.assertEqual(removed['poses'],{});self.assertNotIn('engine_poses',removed)

    def test_active_track_sees_scale_changes_without_replay_or_asset_mutation(self):
        data=fixture();world=world_for(data);before=copy.deepcopy(world.instances)
        clip={'version':1,'kind':'KeyframeSequence','length':1.,'looped':True,
            'keyframes':[{'time':t,'poses':[{'path':['HumanoidRootPart','Arm'],
                'position':[1+4*t,0,0],'rotation':IDENTITY,'weight':1.}]} for t in (0.,1.)]}
        clip_before=copy.deepcopy(clip)
        model=AnimationJointModel(42,lambda _:clip)
        def tick(now,scale,entries=()):
            return model.tick(world=world,physics_report={'predicted_position':[10,20,30],
                'predicted_yaw':0.},entries=list(entries),now=now,snapshot=fixture(scale))
        first=tick(0,1,[operation(1,actor='1:4')]);second=tick(.25,2);third=tick(.5,.5)
        for result in (first,second,third):self.assertEqual(result['status'],'ok',result)
        self.assertEqual(first['poses'][(1,5)][0],(11.,23.,30.))
        self.assertEqual(second['poses'][(1,5)][0],(14.,26.,30.))
        self.assertEqual(second['joint_transforms'][('HumanoidRootPart','Arm')]['position'],[4.,0.,0.])
        self.assertEqual(third['poses'][(1,5)][0],(11.5,21.5,30.))
        self.assertEqual(second['engine_poses']['character_id'],'1:1')
        self.assertEqual(world.instances,before);self.assertEqual(clip,clip_before)

    def test_native_scale_to_track_and_engine_pose_roundtrip(self):
        self.native_scale_roundtrip(local_rig=False)

    def test_native_created_parts_and_animator_scale_and_pose_roundtrip(self):
        self.native_scale_roundtrip(local_rig=True)

    def test_native_animation_constraint_scales_force_and_pose_roundtrip(self):
        self.native_scale_roundtrip(local_rig=False,constraint=True)

    def native_scale_roundtrip(self,*,local_rig,constraint=False):
        import persistent_runtime
        import test_cframe_transforms as native
        from animation_pose_runtime import AnimationPoseRuntime
        data=copy.deepcopy(native.SNAPSHOT)
        character='scope:character-1';root='scope:root-1';animator='scope:animator-scale'
        for node in data['nodes']:
            if node['id']==root:
                node['properties'].update(CFrame=frame(),Position={'$vector3':[0,0,0]},
                    Size={'$vector3':[2,2,1]},PivotOffset=frame(),Anchored=True)
        data['nodes'] += [
            {'id':animator,'class_name':'Animator','name':'Animator','parent':'scope:humanoid-1','properties':{}},
            {'id':'scope:arm-scale','class_name':'Part','name':'Arm','parent':character,
             'properties':{'CFrame':frame((0,3,0)),'Size':{'$vector3':[1,2,1]},'Anchored':False}},
            {'id':'scope:motor-scale','class_name':'Motor6D','name':'Shoulder','parent':root,
             'properties':{'Part0':{'$ref':root},'Part1':{'$ref':'scope:arm-scale'},
                 'C0':frame((0,2,0)),'C1':frame((0,-1,0)),'Enabled':True}}]
        if local_rig:
            data['nodes']=data['nodes'][:-3]
        if constraint:
            data['nodes'][-1].update(class_name='AnimationConstraint',properties={
                'Attachment0':{'$ref':'scope:at0-scale'},
                'Attachment1':{'$ref':'scope:at1-scale'},'Enabled':True,
                'MaxForce':100,'MaxTorque':200,'Transform':frame()})
            for identifier,parent,position in [('scope:at0-scale',root,(0,2,0)),
                    ('scope:at1-scale','scope:arm-scale',(0,-1,0))]:
                data['nodes'].append({'id':identifier,'class_name':'Attachment','parent':parent,
                    'properties':{'CFrame':frame(position)}})
        before=copy.deepcopy(data)
        clip={'version':1,'kind':'KeyframeSequence','length':1.,'looped':True,
            'keyframes':[{'time':t,'poses':[{'path':['HumanoidRootPart','Arm'],
                'position':[1,0,0],'rotation':IDENTITY,'weight':1.}]} for t in (0.,1.)]}
        host=persistent_runtime.PersistentLuauHost(data);self.addCleanup(host.close)
        state=AuthoredForceState();playback=AnimationPoseRuntime(lambda _:clip);playback.bind(animator)
        for now,scale in enumerate((2.,.5,1.)):
            source='''
local char=game:GetService("Players").LocalPlayer.Character
char.PrimaryPart=char.HumanoidRootPart
if not char:FindFirstChild("Arm") then
 local arm=Instance.new("Part",char);arm.Name="Arm";arm.Size=Vector3.new(1,2,1);arm.CFrame=CFrame.new(0,3,0)
 local motor=Instance.new("Motor6D",char.HumanoidRootPart)
 motor.Part0=char.HumanoidRootPart;motor.Part1=arm;motor.C0=CFrame.new(0,2,0);motor.C1=CFrame.new(0,-1,0)
 Instance.new("Animator",char.Humanoid)
end
char:ScaleTo(%s)
if not scaleTrack then
 local animation=Instance.new("Animation");animation.AnimationId="rbxassetid://1"
 scaleTrack=char.Humanoid.Animator:LoadAnimation(animation);scaleTrack.Looped=true;scaleTrack:Play(0,1,0)
end
return char:GetScale()
''' % scale
            if constraint:
                source=source.replace('return char:GetScale()',
                    'local joint=char.HumanoidRootPart.Shoulder\n'
                    'assert(math.abs(joint.MaxForce-100*'+str(scale)+'^3)<1e-5)\n'
                    'assert(math.abs(joint.MaxTorque-200*'+str(scale)+'^3)<1e-5)\n'
                    'return char:GetScale()')
            result=host.call({'op':'execute','source':source,'timeout_ms':1000})
            self.assertTrue(result['ok'],result.get('error'));self.assertFalse(result.get('joint_pose_errors'))
            self.assertEqual(result['values'],[scale])
            state.apply(result.get('mutations',[]),data)
            effective=state.snapshot(data)
            if local_rig:
                animator=next(n['id'] for n in effective['nodes'] if n['class_name']=='Animator')
            playback.bind(animator)
            playback.consume(result.get('animation_intents',[]),now=now)
            rig=SnapshotAnimationRig(effective,character,root,animator)
            posed=rig.evaluate(frame(),playback.sample(now))
            self.assertEqual(posed['status'],'ok',posed)
            arm_id=next(n['id'] for n in effective['nodes'] if n.get('name')=='Arm')
            self.assertEqual(posed['poses'][arm_id][0],(scale,3*scale,0.))
            poses={'character_id':character,'root_id':root,'frames':[
                {'id':key,'cframe':{'$cframe':{'position':list(p),'rotation':list(r)}}}
                for key,(p,r) in posed['poses'].items()]}
            updated=host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[],
                'engine_poses':poses},'timeout_ms':1000})
            self.assertTrue(updated['ok'],updated.get('error'))
            self.assertEqual(updated.get('engine_pose_status'),'applied')
            self.assertEqual(updated.get('server_actions'),0)
            checked=host.call({'op':'execute','source':
                'local p=game:GetService("Players").LocalPlayer.Character.Arm.Position;return p.X,p.Y,p.Z',
                'timeout_ms':1000})
            self.assertEqual(checked['values'],[scale,3*scale,0.])
        self.assertEqual(data,before)


if __name__=='__main__':unittest.main()
