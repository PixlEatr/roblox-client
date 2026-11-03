"""Drive normal track APIs from the local model of the received own character.

This creates no Animator and no animation asset IDs. It starts only the
animation references already configured on the current received avatar.
"""
import math
import threading

from character_animations import own_character_animations


_ROLE_ORDER = ('idle', 'walk', 'run', 'jump', 'fall', 'climb', 'swim', 'swimidle', 'sit')
_STATE_KEY = '__codexOwnedCharacterLocomotionV1'
_JUMP_TRACK_CHECK_SECONDS = 0.35


def _source(references, role, speed):
    """Make one ordinary Luau track transition using resolved numeric IDs."""
    # References have already passed animation_asset_id's strict digit check.
    rows = ','.join(f'[{r!r}]={ref!r}' for r, ref in sorted(references.items())
                    if r in _ROLE_ORDER)
    cleanup = '\n'.join(
        f'    if state.tracks[{r!r}] then pcall(function() state.tracks[{r!r}]:Destroy() end) end\n'
        f'    if state.assets and state.assets[{r!r}] then pcall(function() state.assets[{r!r}]:Destroy() end) end'
        for r in _ROLE_ORDER)
    selected = f'{role!r}' if role in _ROLE_ORDER else 'nil'
    rate = format(float(speed), '.7g')
    return f'''local slot = { _STATE_KEY!r }
local env = getgenv()
local state = env[slot]
local player = game:GetService("Players").LocalPlayer
local character = player and player.Character
local function stop_active(current)
    if type(current) == "table" and type(current.tracks) == "table" and current.active then
        local old = current.tracks[current.active]
        if old then pcall(function() old:Stop(0.12) end) end
        current.active = nil
    end
end
if not character or character.Parent == nil then stop_active(state); return false, "character_unavailable" end
if type(state) == "table" and state.owner == "codex_loc_v1" and (state.character ~= character or state.animator.Parent == nil) then
    stop_active(state)
{cleanup}
    state = nil
end
local humanoid = character:FindFirstChildOfClass("Humanoid")
local animator = humanoid and humanoid:FindFirstChildOfClass("Animator")
if not animator or animator.Parent == nil then stop_active(state); env[slot] = nil; return false, "received_animator_unavailable" end
if type(state) ~= "table" or state.owner ~= "codex_loc_v1" or state.character ~= character or state.animator ~= animator then
    stop_active(state)
    state = {{owner="codex_loc_v1", character=character, animator=animator, tracks={{}}, assets={{}}, active=nil, speed=nil}}
    env[slot] = state
end
local references = {{{rows}}}
local role = {selected}
if role == nil then
    stop_active(state)
    state.reason = "stopped"
    return true, state.reason
end
if references[role] == nil then
    stop_active(state)
    state.reason = "animation_role_unavailable"
    return true, state.reason
end
local rate = {rate}
local track = state.tracks[role]
local is_playing, jump_interrupted = false, false
if track then
    local valid, playing, position, length = pcall(function()
        return track.IsPlaying, track.TimePosition, track.Length
    end)
    if not valid or type(playing) ~= "boolean" or type(position) ~= "number" or type(length) ~= "number" then
        pcall(function() track:Destroy() end)
        state.tracks[role] = nil
        if state.active == role then
            state.active, state.speed = nil, nil
        end
        if state.assets and state.assets[role] then
            pcall(function() state.assets[role]:Destroy() end)
            state.assets[role] = nil
        end
        track = nil
    else
        is_playing = playing
        -- A jump can be interrupted by another local state transition. Resume
        -- that same one-shot only when it stopped before its authored end;
        -- a naturally completed jump stays nonlooping.
        jump_interrupted = role == "jump" and not playing and length > 0
            and position + 0.03 < length
    end
end
if not track then
    local animation = Instance.new("Animation")
    animation.AnimationId = references[role]
    local ok, loaded = pcall(function() return animator:LoadAnimation(animation) end)
    if not ok or not loaded then return false, "animation_load_failed" end
    track = loaded
    track.Looped = role ~= "jump"
    track.Priority = Enum.AnimationPriority.Core
    state.tracks[role] = track
    state.assets[role] = animation
end
if state.active ~= role then
    stop_active(state)
    track:Play(0.12, 1, rate)
    state.active, state.speed, state.reason = role, rate, nil
elseif role == "jump" and jump_interrupted then
    track:Play(0.12, 1, rate)
    state.speed, state.reason = rate, nil
elseif not is_playing and state.active == role and role ~= "jump" then
    track:Play(0.12, 1, rate)
    state.speed, state.reason = rate, nil
elseif state.speed == nil or math.abs(state.speed-rate) >= 0.05 then
    track:AdjustSpeed(rate)
    state.speed = rate
end
return true, role
'''


class LocomotionAnimationDriver:
    """Submit transitions for locally modeled, received locomotion tracks.

    The selected pose comes from the same local physics model used for own-root
    prediction. It does not mark poses or animation playback as server evidence.
    """
    MIN_MOVE_SPEED = 0.35
    RUN_SPEED = 12.5

    def __init__(self):
        self._world = None
        self._player_key = None
        self._character_key = None
        self._animator_key = None
        self._references = {}
        self._sources = {}
        self._last_bind_attempt_at = -math.inf
        self._next_bind_at = 0.
        self._active_role = None
        self._speed = None
        self._last_reason = 'waiting_for_character'
        self._attempts = 0
        self._successes = 0
        self._failures = 0
        self._stops = 0
        self._retry_after = 0.
        self._pending = None
        self._completion = None
        self._next_jump_check_at = 0.
        self._completion_lock = threading.Lock()

    def complete(self, token, result):
        """Deliver one background Luau result for the currently queued transition."""
        with self._completion_lock:
            if self._pending is None or token != self._pending['token']:
                return False
            self._completion = result
            return True

    def _apply_result(self, pending, result, now):
        role, speed, reason = pending['role'], pending['speed'], pending['reason']
        if not isinstance(result, dict) or result.get('ok') is not True:
            self._failures += 1
            self._last_reason = 'luau_execution_failed'
            self._retry_after = now + 2.
            return False
        values = result.get('values')
        if role is None and isinstance(values, list) and values and values[0] is True:
            self._active_role = None
            self._speed = None
            self._stops += 1
            self._last_reason = reason or 'stopped'
            return True
        if not isinstance(values, list) or not values or values[0] is not True:
            self._active_role = None
            self._speed = None
            self._last_reason = (values[1] if isinstance(values, list) and len(values) > 1
                                 and isinstance(values[1], str) else reason or 'animation_unavailable')
            self._failures += 1
            self._retry_after = now + 2.
            return False
        self._active_role, self._speed = role, speed
        self._last_reason = 'locally_playing'
        if role == 'jump':
            self._next_jump_check_at = now + _JUMP_TRACK_CHECK_SECONDS
        else:
            self._next_jump_check_at = 0.
        if role is not None and not pending.get('track_check'):
            self._successes += 1
        return True

    def _finish_pending(self, now):
        with self._completion_lock:
            if self._pending is None or self._completion is None:
                return None
            pending, result = self._pending, self._completion
            self._pending = None
            self._completion = None
        return self._apply_result(pending, result, now)

    @staticmethod
    def _class(world, key):
        node = world.instances.get(key) if world is not None else None
        if not isinstance(node, dict):
            return None
        index = node.get('class')
        if type(index) is not int or not 0 <= index < len(world.schema['classes']):
            return None
        return world.schema['classes'][index]['name']

    def _character(self, world, user_id):
        if self._world is world and self._player_key in world.instances:
            player = world.instances[self._player_key]
            if (self._class(world, self._player_key) == 'Player'
                    and player.get('properties', {}).get('UserId') == user_id):
                return self._player_key, player.get('properties', {}).get('Character')
        avatar = world.avatar(user_id)
        self._player_key = avatar.get('player_key')
        self._world = world
        return self._player_key, avatar.get('character_key')

    def _bind(self, world, user_id, now):
        player_key, character_key = self._character(world, user_id)
        same_character = (character_key == self._character_key and character_key is not None
                          and character_key in world.instances)
        if same_character and self._animator_key in world.instances:
            animator = world.instances[self._animator_key]
            humanoid = world.instances.get(animator.get('parent'))
            if (self._class(world, self._animator_key) == 'Animator' and humanoid
                    and self._class(world, animator.get('parent')) == 'Humanoid'
                    and humanoid.get('parent') == character_key):
                return
        elif same_character and now < self._next_bind_at:
            return
        self._character_key = character_key
        self._animator_key = None
        self._references = {}
        self._sources = {}
        self._active_role = None
        self._speed = None
        self._next_jump_check_at = 0.
        with self._completion_lock:
            self._pending = None
            self._completion = None
        if self._class(world, character_key) != 'Model':
            self._last_reason = 'waiting_for_received_character'
            self._next_bind_at = now + 1.
            return
        self._last_bind_attempt_at = now
        resolved = own_character_animations(world, user_id)
        if resolved.get('character_key') != character_key:
            self._last_reason = 'own_character_changed_during_bind'
            self._next_bind_at = now + 1.
            return
        self._animator_key = resolved.get('animator_key')
        self._references = dict(resolved.get('references', {}))
        self._sources = dict(resolved.get('sources', {}))
        self._last_reason = ('ready' if self._animator_key is not None
                             else 'received_animator_unavailable')
        self._next_bind_at = now + (1. if self._animator_key is None else 5.)

    @classmethod
    def _pose(cls, report, previous_role=None):
        if not isinstance(report, dict) or report.get('running') is not True:
            return None, 0., 'physics_not_running'
        prediction = report.get('local_prediction')
        controller = prediction.get('controller') if isinstance(prediction, dict) else None
        state = controller.get('predicted_state') if isinstance(controller, dict) else None
        if not isinstance(state, dict):
            return None, 0., 'local_pose_unavailable'
        velocity = state.get('linear_velocity')
        if (not isinstance(velocity, (list, tuple)) or len(velocity) != 3
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in velocity)):
            return None, 0., 'local_velocity_unavailable'
        humanoid_state = state.get('humanoid_state', {}).get('name')
        if (humanoid_state == 'Dead'
                or not isinstance(state.get('grounded'), bool)):
            return None, 0., 'humanoid_not_alive'
        if humanoid_state == 'Seated':
            return 'sit', 1., None
        if humanoid_state == 'Climbing':
            return 'climb', 1., None
        if humanoid_state == 'Swimming':
            magnitude = math.sqrt(sum(component * component for component in velocity))
            return ('swimidle' if magnitude < cls.MIN_MOVE_SPEED else 'swim'), 1., None
        horizontal = math.hypot(velocity[0], velocity[2])
        if state['grounded']:
            role = ('run' if horizontal >= cls.RUN_SPEED else 'walk') if horizontal >= cls.MIN_MOVE_SPEED else 'idle'
        else:
            vertical = velocity[1]
            # Freefall often begins while the character is still ascending.
            # Keep the jump clip through ascent and retain the prior airborne
            # role in the tiny apex band to avoid jump/fall chatter.
            if vertical > 0.5:
                role = 'jump'
            elif vertical < -0.5:
                role = 'fall'
            elif previous_role in ('jump', 'fall'):
                role = previous_role
            else:
                role = 'jump' if humanoid_state == 'Jumping' else 'fall'
        rate = min(2., max(.25, horizontal / 16.)) if role in ('walk', 'run') else 1.
        return role, rate, None

    def tick(self, *, world, own_user_id, physics_report, now, execute_source=None,
             submit_source=None, connected=True):
        if execute_source is not None and not callable(execute_source):
            raise TypeError('execute_source must be callable')
        if submit_source is not None and not callable(submit_source):
            raise TypeError('submit_source must be callable')
        if execute_source is None and submit_source is None:
            raise TypeError('submit_source or execute_source must be callable')
        if type(now) not in (int, float) or not math.isfinite(now):
            raise ValueError('now must be finite')
        if not connected or world is None:
            self._active_role = None
            self._speed = None
            self._next_jump_check_at = 0.
            self._last_reason = 'disconnected'
            with self._completion_lock:
                self._pending = None
                self._completion = None
            return self.report()
        if self._world is not world:
            self._player_key = None
            self._character_key = None
            self._animator_key = None
            self._references = {}
            self._sources = {}
            self._active_role = None
            self._speed = None
            self._next_jump_check_at = 0.
            with self._completion_lock:
                self._pending = None
                self._completion = None
            self._world = world
        player = world.instances.get(self._player_key) if self._player_key else None
        if (player is None or self._class(world, self._player_key) != 'Player'
                or player.get('properties', {}).get('UserId') != own_user_id):
            self._player_key = None
            self._character_key = None
        current_character = (player.get('properties', {}).get('Character')
                             if player is not None else None)
        if (self._player_key is None or current_character != self._character_key
                or self._animator_key not in world.instances
                or now >= self._next_bind_at):
            self._bind(world, own_user_id, now)
        completion = self._finish_pending(now)
        if self._pending is not None:
            self._last_reason = 'transition_pending'
            return self.report()
        if completion is False:
            return self.report()
        role, speed, reason = self._pose(physics_report, self._active_role)
        if role is None:
            if self._active_role is None:
                self._last_reason = reason
                return self.report()
        if self._animator_key is None:
            self._last_reason = 'received_animator_unavailable'
            return self.report()
        if role is not None and role not in self._references:
            if role == 'run' and 'walk' in self._references:
                role = 'walk'
                speed = min(2., max(.25, speed * 1.5))
            else:
                role, reason = None, 'animation_role_unavailable'
        same_role = role == self._active_role
        speed_steady = role is None or self._speed is not None and abs(speed - self._speed) < .05
        track_check = (same_role and role == 'jump' and now >= self._next_jump_check_at)
        if same_role and speed_steady and not track_check:
            self._last_reason = 'steady'
            return self.report()
        if now < self._retry_after:
            self._last_reason = 'transition_retry_backoff'
            return self.report()
        source = _source(self._references, role, speed)
        pending = {'role': role, 'speed': speed, 'reason': reason,
                   'track_check': track_check}
        if submit_source is not None:
            try:
                token = submit_source(source, timeout_ms=250, label='<locomotion-animation>')
            except Exception as error:
                self._failures += 1
                self._last_reason = 'luau_submission_' + type(error).__name__
                self._retry_after = now + 2.
                return self.report()
            if token is None:
                self._last_reason = 'transition_queue_full'
                return self.report()
            with self._completion_lock:
                self._pending = {**pending, 'token': token}
                self._completion = None
            self._attempts += 1
            self._last_reason = 'transition_pending'
            return self.report()
        self._attempts += 1
        try:
            result = execute_source(source, timeout_ms=250, label='<locomotion-animation>')
        except Exception as error:
            self._failures += 1
            self._last_reason = 'luau_execution_' + type(error).__name__
            self._retry_after = now + 2.
            return self.report()
        self._apply_result(pending, result, now)
        return self.report()

    def report(self):
        return {'active_role': self._active_role, 'reason': self._last_reason,
                'own_animator_resolved': self._animator_key is not None,
                'configured_roles': sorted(self._references),
                'asset_sources': dict(self._sources), 'transition_attempts': self._attempts,
                'transition_pending': self._pending is not None,
                'local_transitions_succeeded': self._successes,
                'local_stop_transitions': self._stops, 'transition_failures': self._failures,
                'server_animation_confirmed': False}


__all__ = ['LocomotionAnimationDriver']
