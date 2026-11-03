"""Evaluate actual authored AnimationTrack operations into local joint poses.

This is a local animation model. It does not change received replication,
choose network authority, or submit packets.
"""
import collections
import math

from animation_intents import AnimationIntentOutbox
from animation_pose_sampler import (AnimationPoseSampler, AnimationPoseError,
    _rotation_matrix_to_quaternion, _quaternion_to_rotation_matrix, _slerp)


_IDENTITY = [1.,0.,0.,0.,1.,0.,0.,0.,1.]


def _mix(items):
    """Weighted translation and shortest-arc quaternion blend."""
    total = 0.
    position = [0.,0.,0.]
    quaternion = (0.,0.,0.,1.)
    for pose, weight in items:
        if weight <= 0:
            continue
        q = _rotation_matrix_to_quaternion(pose['rotation'])
        alpha = weight / (total + weight)
        quaternion = q if total == 0 else _slerp(quaternion, q, alpha)
        position = [a + (b-a)*alpha for a,b in zip(position,pose['position'])]
        total += weight
    return {'position':position, 'rotation':_quaternion_to_rotation_matrix(quaternion),
            'weight':1.}


class AnimationPoseRuntime:
    MAX_TRACKS = 256
    MAX_SAMPLERS = 32

    def __init__(self, clip_provider):
        if not callable(clip_provider):
            raise TypeError('clip_provider must be callable')
        self.clip_provider = clip_provider
        self._intents = AnimationIntentOutbox()
        self._actor = None
        self._tracks = {}
        self._samplers = collections.OrderedDict()
        self._last_now = None
        self._rejected_actor = 0

    def bind(self, animator_id, *, generation=None):
        actor = (animator_id, generation)
        if actor != self._actor:
            self._tracks.clear()
            self._samplers.clear()
            self._actor = actor

    def _clock(self, now):
        if type(now) not in (int,float) or not math.isfinite(now):
            raise ValueError('Animation clock must be finite')
        if self._last_now is not None and now < self._last_now:
            raise ValueError('Animation clock moved backward')
        self._last_now = float(now)

    def consume(self, entries, *, now):
        self._clock(now)
        self._intents.submit(entries)
        for entry in self._intents.drain():
            if self._actor is None or entry['animator_id'] != self._actor[0]:
                self._rejected_actor += 1
                continue
            track_id = entry['track_id']
            if track_id not in self._tracks and len(self._tracks) >= self.MAX_TRACKS:
                # Finished tracks are expendable; playing/fading tracks are not.
                for key,t in list(self._tracks.items()):
                    if not t['playing'] and self._weight(t,now) == 0:
                        del self._tracks[key]
                if len(self._tracks) >= self.MAX_TRACKS:
                    raise ValueError('Animation pose track budget exceeded')
            p = entry['parameters']
            s = p['effective_state']
            previous = self._tracks.get(track_id)
            fade = p.get('fade_time', 0.)
            if previous is not None and entry['kind'] not in ('Play','Stop','AdjustWeight'):
                # Metadata/speed/position writes do not restart an ongoing fade.
                fade_at, fade_from, fade_to, duration = (
                    previous['fade_at'], previous['fade_from'],
                    previous['fade_to'], previous['fade_duration'])
            else:
                fade_at, fade_from, fade_to, duration = (
                    now,s['weight_current'],s['weight_target'],fade)
            loaded_at = previous.get('loaded_at') if previous is not None else None
            self._tracks[track_id] = {
                'reference':entry['animation_reference'], 'playing':s['playing'],
                'priority':s['priority'], 'looped':s['looped'], 'speed':s['speed'],
                'position':s['time_position'], 'anchor_at':now,
                'fade_at':fade_at,'fade_from':fade_from,'fade_to':fade_to,
                'fade_duration':duration,'loaded_at':loaded_at,
            }

    @staticmethod
    def _weight(track, now):
        dt = max(0.,now-track['fade_at'])
        alpha = 1. if track['fade_duration'] <= 0 else min(1.,dt/track['fade_duration'])
        return max(0., min(1.,track['fade_from']+(track['fade_to']-track['fade_from'])*alpha))

    def _sampler(self, track):
        key = (track['reference'],track['looped'])
        sampler = self._samplers.get(key)
        if sampler is not None:
            self._samplers.move_to_end(key)
            return sampler
        clip = self.clip_provider(track['reference'])
        if clip is None:
            return None
        sampler = AnimationPoseSampler({**clip,'looped':track['looped']})
        self._samplers[key] = sampler
        while len(self._samplers) > self.MAX_SAMPLERS:
            self._samplers.popitem(last=False)
        return sampler

    def sample(self, now):
        self._clock(now)
        by_joint = collections.defaultdict(list)
        missing, errors, sampled = 0, set(), 0
        for track in self._tracks.values():
            weight = self._weight(track,now)
            if weight <= 0:
                continue
            try:
                sampler = self._sampler(track)
            except AnimationPoseError as error:
                errors.add(error.code)
                continue
            if sampler is None:
                missing += 1
                continue
            if track['loaded_at'] is None:
                track['loaded_at'] = now
            anchor = max(track['anchor_at'],track['loaded_at'])
            position = track['position'] + (max(0.,now-anchor)*track['speed'] if track['playing'] else 0.)
            if sampler.looped and sampler.length > 0:
                position %= sampler.length
            else:
                position = max(0.,min(sampler.length,position))
            result = sampler.sample(position)
            if result['status'] != 'ok':
                errors.add(result['status'])
                continue
            sampled += 1
            priority = -1 if track['priority'] == 1000 else track['priority']
            for path, pose in result['poses'].items():
                effective = weight * pose['weight']
                if effective > 0:
                    by_joint[path].append((priority,pose,effective))
        poses = {}
        for path, contributions in by_joint.items():
            layers = collections.defaultdict(list)
            for priority,pose,weight in contributions:
                layers[priority].append((pose,weight))
            remaining, blended = 1., []
            for priority in sorted(layers,reverse=True):
                layer = layers[priority]
                amount = sum(weight for _,weight in layer)
                used = min(remaining,amount)
                blended.append((_mix(layer),used))
                remaining -= used
                if remaining <= 1e-9:
                    break
            if remaining > 1e-9:
                blended.append(({'position':[0.,0.,0.],'rotation':_IDENTITY},remaining))
            poses[path] = _mix(blended)
        return {'status':('unsupported_clip' if errors else 'waiting_for_clips' if missing
                          else 'ok' if poses else 'no_active_pose'),
                'poses':poses,'sampled_tracks':sampled,'missing_clips':missing,
                'errors':sorted(errors),'rejected_actor_operations':self._rejected_actor,
                'model':'local_priority_weighted_slerp_subset',
                'server_animation_confirmed':False}


__all__ = ['AnimationPoseRuntime']
