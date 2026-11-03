"""Combine authored clip playback with the received avatar's joint hierarchy."""
import math

from animation_pose_runtime import AnimationPoseRuntime
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

    def _bind(self, world, now):
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
        if self._rig is not None and not self._rig.valid_for(world,self.user_id):
            self._rig = None
            self._bind_after = 0.
        if self._rig is None and now >= self._bind_after:
            self._bind_after = now+1.
            resolved = own_character_animations(world,self.user_id)
            self._animator_key = resolved['animator_key']
            if self._animator_key is not None:
                from animation_rig_pose import ReceivedAvatarRig
                self._rig = ReceivedAvatarRig(world,self.user_id)
        self.playback.bind(instance_id(self._animator_key),generation=(id(world),self._character_key))

    def tick(self, *, world, physics_report, entries, now):
        if world is None:
            return {'status':'world_unavailable','poses':{},'server_animation_confirmed':False}
        self._bind(world,now)
        self.playback.consume(entries,now=now)
        sample = self.playback.sample(now)
        if sample['status'] != 'ok' or self._rig is None:
            return {**sample,'poses':{},'rig_status':'not_ready' if self._rig is None else 'ready'}
        position = physics_report.get('predicted_position')
        yaw = physics_report.get('predicted_yaw')
        if (not isinstance(position,(tuple,list)) or len(position)!=3
                or any(type(v) not in (int,float) or not math.isfinite(v) for v in position)
                or type(yaw) not in (int,float) or not math.isfinite(yaw)):
            return {**sample,'status':'root_pose_unavailable','poses':{}}
        c,s = math.cos(yaw),math.sin(yaw)
        root = {'position':position,'rotation':[c,0.,s,0.,1.,0.,-s,0.,c]}
        result = self._rig.evaluate(root,sample)
        if result.get('status') == 'ok':
            current = {path:tuple(pose['position'])+tuple(pose['rotation'])
                       for path,pose in sample['poses'].items()}
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
            result['engine_poses'] = {
                'character_id':instance_id(self._rig.character_key),
                'root_id':instance_id(self._rig.root_key),
                'frames':[{'id':instance_id(key),'cframe':{'$cframe':{
                    'position':list(frame[0]),'rotation':list(frame[1])}}}
                    for key,frame in result['poses'].items()]}
        return {**result,**self._pose_changes,'joint_transforms':sample['poses'],
                'sampled_tracks':sample['sampled_tracks'],
                'source':'actual_clip_local_joint_model',
                'server_animation_confirmed':False}


__all__=['AnimationJointModel']
