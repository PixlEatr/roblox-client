"""Validated interpolation for decoded Roblox KeyframeSequence Pose channels.

Matrices use the decoder's flat row-major CFrame orientation layout. Channels
are keyed by their full Pose path, so missing joints in a keyframe do not erase
or accidentally interpolate another joint's sample.
"""
from __future__ import annotations

import bisect
import math


MAX_KEYFRAMES = 20_000
MAX_POSES = 100_000
MAX_PATH_DEPTH = 64
MAX_NAME_LENGTH = 256
MAX_TIME = 86_400.0
ROTATION_TOLERANCE = 2e-3


class AnimationPoseError(ValueError):
    """A decoded clip cannot be safely sampled; ``code`` is fixed diagnostic text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _finite_number(value, code='invalid_pose_clip'):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AnimationPoseError(code)
    result = float(value)
    if not math.isfinite(result):
        raise AnimationPoseError(code)
    return result


def _rotation_matrix_to_quaternion(m):
    # Standard row-major matrix extraction, matching the CFrame convention in
    # animation-codec/src/main.rs and scripts/physics_rotation.py.
    m00,m01,m02,m10,m11,m12,m20,m21,m22 = m
    trace = m00 + m11 + m22
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2.0
        q = ((m21-m12)/s, (m02-m20)/s, (m10-m01)/s, 0.25*s)
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(max(0.0, 1.0+m00-m11-m22))*2.0
        q = (0.25*s, (m01+m10)/s, (m02+m20)/s, (m21-m12)/s)
    elif m11 > m22:
        s = math.sqrt(max(0.0, 1.0+m11-m00-m22))*2.0
        q = ((m01+m10)/s, 0.25*s, (m12+m21)/s, (m02-m20)/s)
    else:
        s = math.sqrt(max(0.0, 1.0+m22-m00-m11))*2.0
        q = ((m02+m20)/s, (m12+m21)/s, 0.25*s, (m10-m01)/s)
    norm = math.sqrt(sum(x*x for x in q))
    if norm < 1e-12 or not math.isfinite(norm):
        raise AnimationPoseError('invalid_pose_rotation')
    return tuple(x/norm for x in q)


def _quaternion_to_rotation_matrix(q):
    x,y,z,w=q
    xx, yy, zz = x*x, y*y, z*z
    xy, xz, yz = x*y, x*z, y*z
    wx, wy, wz = w*x, w*y, w*z
    return [1-2*(yy+zz), 2*(xy-wz), 2*(xz+wy),
            2*(xy+wz), 1-2*(xx+zz), 2*(yz-wx),
            2*(xz-wy), 2*(yz+wx), 1-2*(xx+yy)]


def _slerp(a, b, alpha):
    dot = sum(x*y for x,y in zip(a,b))
    if dot < 0.0:  # q and -q encode the same rotation; take the short arc.
        b = tuple(-x for x in b)
        dot = -dot
    dot = min(1.0, max(-1.0, dot))
    if dot > 0.9995:
        q = tuple(x + alpha*(y-x) for x,y in zip(a,b))
        norm = math.sqrt(sum(x*x for x in q))
        return tuple(x/norm for x in q)
    theta = math.acos(dot)
    sin_theta = math.sin(theta)
    wa = math.sin((1.0-alpha)*theta)/sin_theta
    wb = math.sin(alpha*theta)/sin_theta
    return tuple(wa*x+wb*y for x,y in zip(a,b))


class AnimationPoseSampler:
    """Sample decoded clips as ``{path_tuple: {position,rotation,weight}}``.

    Linear easing (enum 0 or omitted) is supported. Nonlinear styles are
    surfaced as an explicit unsupported status rather than approximated.
    ``looped`` clips wrap by their declared length; non-looped clips clamp.
    For each joint, interpolation uses that joint's own sparse key samples.
    """

    def __init__(self, clip):
        if not isinstance(clip, dict) or clip.get('kind') != 'KeyframeSequence' or clip.get('version') != 1:
            raise AnimationPoseError('invalid_pose_clip')
        frames=clip.get('keyframes')
        if not isinstance(frames,list) or not frames or len(frames)>MAX_KEYFRAMES:
            raise AnimationPoseError('invalid_pose_keyframes')
        if type(clip.get('looped')) is not bool:
            raise AnimationPoseError('invalid_pose_loop_mode')
        self.looped = clip['looped']
        length=_finite_number(clip.get('length'), 'invalid_pose_length')
        if not 0 <= length <= MAX_TIME:
            raise AnimationPoseError('invalid_pose_length')
        self.length=length
        self._tracks={}
        total=0
        last_time=-1.0
        for frame in frames:
            if not isinstance(frame,dict):
                raise AnimationPoseError('invalid_pose_keyframe')
            t=_finite_number(frame.get('time'), 'invalid_pose_keyframe')
            if not 0 <= t <= min(MAX_TIME, length) or t < last_time:
                raise AnimationPoseError('invalid_pose_keyframe')
            last_time=t
            poses=frame.get('poses')
            if not isinstance(poses,list):
                raise AnimationPoseError('invalid_pose_keyframe')
            total += len(poses)
            if total>MAX_POSES:
                raise AnimationPoseError('pose_count_limit')
            seen=set()
            for pose in poses:
                if not isinstance(pose,dict):
                    raise AnimationPoseError('invalid_pose')
                path=pose.get('path')
                if (not isinstance(path,list) or not 0<len(path)<=MAX_PATH_DEPTH
                        or any(not isinstance(part,str) or not part or len(part)>MAX_NAME_LENGTH for part in path)):
                    raise AnimationPoseError('invalid_pose_path')
                key=tuple(path)
                if key in seen:
                    raise AnimationPoseError('duplicate_pose_path')
                seen.add(key)
                pos=pose.get('position')
                rot=pose.get('rotation')
                if not isinstance(pos,list) or len(pos)!=3 or not isinstance(rot,list) or len(rot)!=9:
                    raise AnimationPoseError('invalid_pose_frame')
                pos=tuple(_finite_number(x, 'invalid_pose_frame') for x in pos)
                rot=tuple(_finite_number(x, 'invalid_pose_rotation') for x in rot)
                if any(abs(x)>MAX_TIME for x in pos):
                    raise AnimationPoseError('invalid_pose_frame')
                # Reject malformed/sheared/reflected transforms; do not repair them.
                rows=(rot[:3],rot[3:6],rot[6:9])
                if any(abs(sum(x*y for x,y in zip(a,b))-(1.0 if i==j else 0.0))>ROTATION_TOLERANCE
                       for i,a in enumerate(rows) for j,b in enumerate(rows)):
                    raise AnimationPoseError('invalid_pose_rotation')
                det=(rot[0]*(rot[4]*rot[8]-rot[5]*rot[7])
                     -rot[1]*(rot[3]*rot[8]-rot[5]*rot[6])
                     +rot[2]*(rot[3]*rot[7]-rot[4]*rot[6]))
                if abs(det-1.0)>ROTATION_TOLERANCE:
                    raise AnimationPoseError('invalid_pose_rotation')
                weight=_finite_number(pose.get('weight',1.0), 'invalid_pose_weight')
                if not 0.0 <= weight <= 1.0:
                    raise AnimationPoseError('invalid_pose_weight')
                style=pose.get('easing_style')
                direction=pose.get('easing_direction')
                if style is not None and (type(style) is not int or style != 0):
                    raise AnimationPoseError('unsupported_easing_style')
                if direction is not None and (type(direction) is not int or direction not in (0,1,2)):
                    raise AnimationPoseError('unsupported_easing_direction')
                self._tracks.setdefault(key,[]).append((t,pos,_rotation_matrix_to_quaternion(rot),weight))
        self._times={key:[item[0] for item in track] for key,track in self._tracks.items()}

    def sample(self, time_seconds):
        """Return a status and a path-to-local-CFrame map for the requested time."""
        try:
            t=_finite_number(time_seconds, 'invalid_sample_time')
            if abs(t)>MAX_TIME:
                raise AnimationPoseError('invalid_sample_time')
        except AnimationPoseError as error:
            return {'status':error.code,'poses':{},'time':None}
        if self.looped and self.length>0:
            t=t % self.length
        else:
            t=min(max(t,0.0),self.length)
        output={}
        for path,track in self._tracks.items():
            times=self._times[path]
            if len(track)==1:
                left=right=track[0]; alpha=0.0
            else:
                i=bisect.bisect_right(times,t)
                if i==0:
                    if self.looped and self.length>0:
                        left,right=track[-1],track[0]
                        a_time,b_time=left[0]-self.length,right[0]
                        sample_t=t
                        alpha=0.0 if b_time==a_time else (sample_t-a_time)/(b_time-a_time)
                    else:
                        left=right=track[0]; alpha=0.0
                elif i==len(track):
                    if self.looped and self.length>0:
                        left,right=track[-1],track[0]
                        a_time,b_time=left[0],right[0]+self.length
                        alpha=0.0 if b_time==a_time else (t-a_time)/(b_time-a_time)
                    else:
                        left=right=track[-1]; alpha=0.0
                else:
                    left,right=track[i-1],track[i]
                    alpha=0.0 if right[0]==left[0] else (t-left[0])/(right[0]-left[0])
            pos=tuple(a+(b-a)*alpha for a,b in zip(left[1],right[1]))
            quat=_slerp(left[2],right[2],alpha)
            weight=left[3]+(right[3]-left[3])*alpha
            output[path]={'position':list(pos),'rotation':_quaternion_to_rotation_matrix(quat),'weight':weight}
        return {'status':'ok','poses':output,'time':t}
