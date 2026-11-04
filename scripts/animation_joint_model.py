"""Combine authored clip playback with the received avatar's joint hierarchy."""
import math

from animation_pose_runtime import AnimationPoseRuntime
from animation_snapshot_rig import SnapshotAnimationRig
from character_animations import own_character_animations
from world_snapshot import instance_id


class AnimationJointModel:
    def __init__(self, user_id, clip_provider):
        self.user_id = user_id
        self.playback = AnimationPoseRuntime(clip_provider)
        self._world = None
        self._player_key = None
        self._character_key = None
        self._animator_key = None
        self._rig = None
        self._bind_after = 0.
        self._last_joint_pose = {}
        self._pose_changes = {'joint_pose_changes':0,'arm_pose_changes':0,'leg_pose_changes':0}

    def _bind(self, world, now, *, with_snapshot=False):
        player = world.instances.get(self._player_key) if self._world is world else None
        if player is None or player.get('properties',{}).get('UserId') != self.user_id:
            self._player_key = world.avatar(self.user_id).get('player_key')
            player = world.instances.get(self._player_key)
        character = player.get('properties',{}).get('Character') if player is not None else None
        changed = self._world is not world or character != self._character_key
        if changed:
            self._world,self._character_key = world,character
            self._animator_key,self._rig = None,None
            self._bind_after = 0.
            self._last_joint_pose = {}
            self._pose_changes = dict.fromkeys(self._pose_changes,0)
        if with_snapshot:
            # The received catalog is incremental. Do not compile the old
            # attachment-only rig when the authored snapshot supplies it.
            self._animator_key = own_character_animations(world,self.user_id)['animator_key']
        elif self._rig is not None and not self._rig.valid_for(world,self.user_id):
            self._rig = None
            self._bind_after = 0.
        if not with_snapshot and self._rig is None and now >= self._bind_after:
            self._bind_after = now+1.
            resolved = own_character_animations(world,self.user_id)
            self._animator_key = resolved['animator_key']
            if self._animator_key is not None:
                from animation_rig_pose import ReceivedAvatarRig
                self._rig = ReceivedAvatarRig(world,self.user_id)
        if not with_snapshot:
            self.playback.bind(instance_id(self._animator_key),generation=(id(world),self._character_key))

    def tick(self, *, world, physics_report, entries, now, snapshot=None):
        if world is None:
            return {'status':'world_unavailable','poses':{},'server_animation_confirmed':False}
        self._bind(world,now,with_snapshot=snapshot is not None)
        actor_id = instance_id(self._animator_key)
        if snapshot is not None:
            character_id = instance_id(world.avatar(self.user_id).get('character_key'))
            humanoids = {n['id'] for n in snapshot['nodes']
                if n.get('class_name') == 'Humanoid' and n.get('parent') == character_id}
            animators = [n['id'] for n in snapshot['nodes']
                if n.get('class_name') == 'Animator' and n.get('parent') in humanoids]
            actor_id = animators[0] if len(humanoids) == 1 and len(animators) == 1 else None
            self.playback.bind(actor_id,generation=(id(world),self._character_key))
        self.playback.consume(entries,now=now)
        sample = self.playback.sample(now)
        rig = self._rig
        if snapshot is not None:
            from animation_rig_pose import _enabled
            avatar = world.avatar(self.user_id)
            defaults = set()
            for node in snapshot['nodes']:
                if node.get('class_name') not in ('Motor6D','AnimationConstraint') or 'Enabled' in node.get('properties',{}):
                    continue
                key = self._world_key(world,node['id'])
                received = world.instances.get(key)
                if received is not None and _enabled(world,key,received):
                    defaults.add(node['id'])
            rig = SnapshotAnimationRig(snapshot, instance_id(avatar.get('character_key')),
                instance_id(avatar.get('root_key')), actor_id,
                enabled_defaults=defaults)
        if sample['status'] != 'ok' or rig is None:
            return {**sample,'poses':{},'rig_status':'not_ready' if rig is None else 'ready'}
        position = physics_report.get('predicted_position')
        yaw = physics_report.get('predicted_yaw')
        if (not isinstance(position,(tuple,list)) or len(position)!=3
                or any(type(v) not in (int,float) or not math.isfinite(v) for v in position)
                or type(yaw) not in (int,float) or not math.isfinite(yaw)):
            return {**sample,'status':'root_pose_unavailable','poses':{}}
        c,s = math.cos(yaw),math.sin(yaw)
        root = {'position':position,'rotation':[c,0.,s,0.,1.,0.,-s,0.,c]}
        result = rig.evaluate(root,sample)
        joint_transforms = result.get('joint_transforms',sample['poses'])
        if result.get('status') == 'ok':
            current = {path:tuple(pose['position'])+tuple(pose['rotation'])
                       for path,pose in joint_transforms.items()}
            changed = [path for path,frame in current.items()
                       if path in self._last_joint_pose
                       and any(abs(a-b)>1e-6 for a,b in zip(frame,self._last_joint_pose[path]))]
            if changed:
                self._pose_changes['joint_pose_changes'] += 1
                if any(any('arm' in name.lower() or 'hand' in name.lower() for name in path) for path in changed):
                    self._pose_changes['arm_pose_changes'] += 1
                if any(any('leg' in name.lower() or 'foot' in name.lower() for name in path) for path in changed):
                    self._pose_changes['leg_pose_changes'] += 1
            self._last_joint_pose = current
            native_id = lambda key: key if isinstance(key,str) else instance_id(key)
            result['engine_poses'] = {
                'character_id':native_id(rig.character_key),
                'root_id':native_id(rig.root_key),
                'frames':[{'id':native_id(key),'cframe':{'$cframe':{
                    'position':list(frame[0]),'rotation':list(frame[1])}}}
                    for key,frame in result['poses'].items()]}
            if snapshot is not None:
                # Existing consumers use World keys; local-only IDs stay strings.
                result['poses'] = {self._world_key(world,key):value for key,value in result['poses'].items()}
        return {**result,**self._pose_changes,'joint_transforms':joint_transforms,
                'sampled_tracks':sample['sampled_tracks'],
                'source':'actual_clip_local_joint_model',
                'server_animation_confirmed':False}

    @staticmethod
    def _world_key(world, identifier):
        try:
            scope, index = identifier.split(':')
            key = (int(scope), int(index))
        except (ValueError, AttributeError):
            return identifier
        return key if key in world.instances else identifier


__all__=['AnimationJointModel']
