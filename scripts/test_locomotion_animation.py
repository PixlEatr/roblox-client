import unittest
import copy
import re
from locomotion_animation import LocomotionAnimationDriver
from test_live_physics_trial import ready_world


def prediction(*, grounded=True, velocity=(0., 0., 0.), state='Running'):
    return {'running': True, 'local_prediction': {'controller': {'predicted_state': {
        'grounded': grounded, 'linear_velocity': list(velocity),
        'humanoid_state': {'name': state, 'source': 'local_physics_model'},
    }}}}


class LocomotionAnimationTests(unittest.TestCase):
    def setUp(self):
        self.world, _ = ready_world()
        self.classes = {}
        for name in ('Animator', 'LocalScript', 'Folder', 'Animation', 'HumanoidDescription'):
            self.classes[name] = len(self.world.schema['classes'])
            self.world.schema['classes'].append({'name': name, 'properties': []})
        self.world.instances[(1, 30)] = {
            'class': self.classes['Animator'], 'parent': (1, 5), 'properties': {}}
        self.world.instances[(1, 31)] = {
            'class': self.classes['LocalScript'], 'parent': (1, 3),
            'properties': {'Name': b'Animate'}}
        self.world.instances[(1, 32)] = {
            'class': self.classes['Folder'], 'parent': (1, 31),
            'properties': {'Name': b'walk'}}
        self.world.instances[(1, 33)] = {
            'class': self.classes['Animation'], 'parent': (1, 32),
            'properties': {'AnimationContent': b'rbxassetid://123'}}
        self.world.instances[(1, 34)] = {
            'class': self.classes['HumanoidDescription'], 'parent': (1, 3),
            'properties': {'IdleAnimation': 456, 'FallAnimation': 789}}
        self.world.instances[(1, 35)] = {'class': self.classes['LocalScript'],
                                        'parent': (1, 3), 'properties': {'Name': b'Animate'}}
        self.world.instances[(1, 36)] = {'class': self.classes['Folder'],
                                        'parent': (1, 35), 'properties': {'Name': b'run'}}
        self.world.instances[(1, 37)] = {'class': self.classes['Animation'],
                                        'parent': (1, 36),
                                        'properties': {'AnimationContent': b'rbxassetid://654'}}
        self.world.instances[(1, 44)] = {'class': self.classes['Folder'],
                                        'parent': (1, 35), 'properties': {'Name': b'jump'}}
        self.world.instances[(1, 45)] = {'class': self.classes['Animation'],
                                        'parent': (1, 44),
                                        'properties': {'AnimationContent': b'rbxassetid://777'}}
        self.world.instances[(1, 46)] = {'class': self.classes['LocalScript'],
                                        'parent': (1, 3), 'properties': {'Name': b'Animate'}}
        for offset, role in enumerate(('climb', 'swim', 'swimidle', 'sit'), 47):
            self.world.instances[(1, offset)] = {'class': self.classes['Folder'],
                                                'parent': (1, 46), 'properties': {'Name': role}}
            self.world.instances[(1, offset + 10)] = {'class': self.classes['Animation'],
                                                    'parent': (1, offset),
                                                    'properties': {'AnimationContent': f'rbxassetid://{offset}'.encode()}}
        self.received_before = copy.deepcopy(self.world.instances)
        self.driver = LocomotionAnimationDriver()
        self.calls = []

    def execute(self, source, *, timeout_ms, label):
        self.calls.append((source, timeout_ms, label))
        match = re.search(r"local role = (?:'([^']+)'|nil)", source)
        role = match.group(1) if match and match.group(1) else None
        stopped = role is None
        return {'ok': True, 'completed': True, 'values': [True, 'stopped' if stopped else role]}

    def tick(self, report, now):
        return self.driver.tick(world=self.world, own_user_id=123,
                                physics_report=report, now=now,
                                execute_source=self.execute)

    def test_gait_transitions_use_only_received_own_animator_and_avatar_assets(self):
        idle = self.tick(prediction(), 1.)
        self.assertEqual(idle['active_role'], 'idle')
        self.assertEqual(idle['asset_sources']['walk'], 'received_animate_configuration')
        self.assertEqual(idle['asset_sources']['idle'], 'received_humanoid_description')
        self.assertIn('Enum.AnimationPriority.Core', self.calls[-1][0])
        self.assertEqual(self.world.instances, self.received_before)  # Local playback leaves decoded state untouched.

        self.tick(prediction(), 1.05)
        self.assertEqual(len(self.calls), 1)  # Idle does not reload every physics frame.
        walk = self.tick(prediction(velocity=(8., 0., 0.)), 1.1)
        self.assertEqual(walk['active_role'], 'walk')
        self.assertIn('rbxassetid://123', self.calls[-1][0])
        run = self.tick(prediction(velocity=(15., 0., 0.)), 1.15)
        self.assertEqual(run['active_role'], 'run')
        jump = self.tick(prediction(grounded=False, velocity=(0., 8., 0.), state='Jumping'), 1.2)
        self.assertEqual(jump['active_role'], 'jump')
        fall = self.tick(prediction(grounded=False, velocity=(0., -10., 0.), state='Freefall'), 1.25)
        self.assertEqual(fall['active_role'], 'fall')
        self.assertEqual(fall['transition_attempts'], 5)
        self.assertTrue(all(timeout == 250 and label == '<locomotion-animation>'
                            for _, timeout, label in self.calls))
        self.assertFalse(fall['server_animation_confirmed'])

    def test_jump_state_tracks_ascent_through_freefall_apex_descent_and_landing(self):
        self.assertEqual(self.tick(prediction(grounded=False, velocity=(0., 8., 0.),
                                              state='Jumping'), 1.)['active_role'], 'jump')
        ascending = self.tick(prediction(grounded=False, velocity=(0., 5., 0.),
                                         state='Freefall'), 1.1)
        self.assertEqual(ascending['active_role'], 'jump')
        self.assertEqual(len(self.calls), 1)
        apex = self.tick(prediction(grounded=False, velocity=(0., 0., 0.),
                                    state='Freefall'), 1.2)
        self.assertEqual(apex['active_role'], 'jump')
        descending = self.tick(prediction(grounded=False, velocity=(0., -4., 0.),
                                          state='Freefall'), 1.3)
        self.assertEqual(descending['active_role'], 'fall')
        landed = self.tick(prediction(grounded=True, velocity=(0., 0., 0.),
                                      state='Running'), 1.4)
        self.assertEqual(landed['active_role'], 'idle')
        self.assertEqual(len(self.calls), 3)

    def test_jump_track_probe_is_bounded_and_does_not_count_as_new_transition(self):
        self.tick(prediction(grounded=False, velocity=(0., 8., 0.), state='Jumping'), 1.)
        self.tick(prediction(grounded=False, velocity=(0., 5., 0.), state='Freefall'), 1.2)
        self.assertEqual(len(self.calls), 1)
        checked = self.tick(prediction(grounded=False, velocity=(0., 4., 0.),
                                       state='Freefall'), 1.36)
        self.assertEqual(len(self.calls), 2)
        self.assertIn('jump_interrupted', self.calls[-1][0])
        self.assertEqual(checked['active_role'], 'jump')
        self.assertEqual(checked['local_transitions_succeeded'], 1)
        self.assertEqual(checked['transition_attempts'], 2)

    def test_airborne_jump_and_fall_speed_do_not_scale_with_horizontal_velocity(self):
        for horizontal in (.1, 4., 16., 32.):
            with self.subTest(horizontal=horizontal):
                jump = self.driver._pose(prediction(grounded=False,
                    velocity=(horizontal, 6., 0.), state='Freefall'))
                fall = self.driver._pose(prediction(grounded=False,
                    velocity=(horizontal, -6., 0.), state='Freefall'))
                self.assertEqual(jump[:2], ('jump', 1.))
                self.assertEqual(fall[:2], ('fall', 1.))

    def test_climb_swim_swimidle_and_sit_use_received_catalog_roles(self):
        cases = (('Climbing', (0., 1., 0.), 'climb'),
                 ('Swimming', (0., 0., 0.), 'swimidle'),
                 ('Swimming', (0., 0., -3.), 'swim'),
                 ('Seated', (0., 0., 0.), 'sit'))
        for index, (state, velocity, role) in enumerate(cases):
            with self.subTest(state=state, role=role):
                report = self.tick(prediction(grounded=False, velocity=velocity, state=state),
                                   1. + index * .1)
                self.assertEqual(report['active_role'], role)
                self.assertEqual(report['asset_sources'][role], 'received_animate_configuration')
                self.assertIn(f"local role = '{role}'", self.calls[-1][0])

    def test_missing_state_role_stops_previous_track_instead_of_using_fall(self):
        self.world.instances.pop((1, 47))
        self.world.instances.pop((1, 57))
        self.assertEqual(self.tick(prediction(velocity=(8., 0., 0.)), 1.)['active_role'], 'walk')
        missing = self.tick(prediction(grounded=False, velocity=(0., -4., 0.), state='Climbing'), 1.1)
        self.assertIsNone(missing['active_role'])
        self.assertEqual(missing['reason'], 'animation_role_unavailable')
        self.assertEqual(missing['local_stop_transitions'], 1)
        self.assertIn('local role = nil', self.calls[-1][0])
        self.assertNotIn("local role = 'fall'", self.calls[-1][0])

    def test_async_transition_submit_and_completion_never_wait_inside_tick(self):
        submissions = []

        def submit(source, *, timeout_ms, label):
            token = f'token-{len(submissions) + 1}'
            submissions.append((token, source, timeout_ms, label))
            return token

        first = self.driver.tick(world=self.world, own_user_id=123,
            physics_report=prediction(velocity=(8., 0., 0.)), now=1., submit_source=submit)
        self.assertIsNone(first['active_role'])
        self.assertTrue(first['transition_pending'])
        self.assertEqual(first['reason'], 'transition_pending')
        self.assertEqual(len(submissions), 1)

        waiting = self.driver.tick(world=self.world, own_user_id=123,
            physics_report=prediction(velocity=(8., 0., 0.)), now=1.1, submit_source=submit)
        self.assertTrue(waiting['transition_pending'])
        self.assertEqual(len(submissions), 1)
        self.assertFalse(self.driver.complete('wrong-token', {'ok': True, 'values': [True]}))
        self.assertTrue(self.driver.complete(submissions[0][0], {'ok': True, 'values': [True, 'walk']}))

        completed = self.driver.tick(world=self.world, own_user_id=123,
            physics_report=prediction(velocity=(8., 0., 0.)), now=1.2, submit_source=submit)
        self.assertEqual(completed['active_role'], 'walk')
        self.assertFalse(completed['transition_pending'])
        self.assertEqual(completed['local_transitions_succeeded'], 1)
        self.assertEqual(len(submissions), 1)
        self.assertEqual(submissions[0][2:], (250, '<locomotion-animation>'))

    def test_full_async_queue_retains_state_without_counting_a_failure(self):
        report = self.driver.tick(world=self.world, own_user_id=123,
            physics_report=prediction(velocity=(8., 0., 0.)), now=1.,
            submit_source=lambda *args, **kwargs: None)
        self.assertEqual(report['reason'], 'transition_queue_full')
        self.assertFalse(report['transition_pending'])
        self.assertEqual(report['transition_attempts'], 0)
        self.assertEqual(report['transition_failures'], 0)

    def test_dead_physics_state_stops_track_once(self):
        self.tick(prediction(velocity=(8., 0., 0.)), 1.)
        dead = prediction(state='Dead')
        stopped = self.tick(dead, 1.1)
        self.assertIsNone(stopped['active_role'])
        self.assertEqual(stopped['local_stop_transitions'], 1)
        self.tick(dead, 1.2)
        self.assertEqual(len(self.calls), 2)

    def test_respawn_rebinds_new_owned_animator_and_mismatched_player_never_runs(self):
        self.tick(prediction(), 1.)
        self.world.instances[(1, 35)] = {'class': 2, 'parent': (1, 6),
                                        'properties': {'PrimaryPart': (1, 36)}}
        self.world.instances[(1, 2)]['properties']['Character'] = (1, 35)
        self.world.instances[(1, 37)] = {'class': 3, 'parent': (1, 35),
                                        'properties': {'Name': b'HumanoidRootPart',
                                                       'Anchored': False,
                                                       'CFrame': {'position': [0., 2., 0.]}}}
        self.world.instances[(1, 38)] = {'class': 4, 'parent': (1, 35),
                                        'properties': {'Health': 100}}
        self.world.instances[(1, 39)] = {
            'class': self.classes['Animator'], 'parent': (1, 38), 'properties': {}}
        self.world.instances[(1, 40)] = {'class': self.classes['HumanoidDescription'],
                                        'parent': (1, 35),
                                        'properties': {'IdleAnimation': 888}}
        self.world.instances[(1, 41)] = {'class': self.classes['LocalScript'],
                                        'parent': (1, 35), 'properties': {'Name': b'Animate'}}
        self.world.instances[(1, 42)] = {'class': self.classes['Folder'],
                                        'parent': (1, 41), 'properties': {'Name': b'walk'}}
        self.world.instances[(1, 43)] = {'class': self.classes['Animation'],
                                        'parent': (1, 42),
                                        'properties': {'AnimationContent': b'rbxassetid://321'}}
        report = self.tick(prediction(), 1.1)
        self.assertEqual(report['active_role'], 'idle')
        self.assertEqual(report['own_animator_resolved'], True)
        self.assertIn('state.character ~= character', self.calls[-1][0])
        self.assertIn('stop_active(state)', self.calls[-1][0])

        self.world.instances[(1, 2)]['properties']['UserId'] = 456
        mismatch = self.tick(prediction(), 1.2)
        self.assertIsNone(mismatch['active_role'])
        self.assertEqual(mismatch['reason'], 'received_animator_unavailable')
        disconnected = self.driver.tick(world=self.world, own_user_id=123,
                                        physics_report=prediction(), now=1.3,
                                        execute_source=self.execute)
        self.assertEqual(disconnected['reason'], 'received_animator_unavailable')

    def test_invalid_physics_or_disconnection_has_no_false_server_claim(self):
        self.assertEqual(self.tick({'running': False}, 1.)['reason'], 'physics_not_running')
        disconnected = self.driver.tick(world=self.world, own_user_id=123,
                                        physics_report=prediction(), now=2.,
                                        execute_source=self.execute, connected=False)
        self.assertEqual(disconnected['reason'], 'disconnected')
        self.assertFalse(disconnected['server_animation_confirmed'])
        self.assertEqual(self.calls, [])

    def test_generated_transitions_run_in_real_luau_without_asset_fetch_or_network(self):
        from local_runtime import HOST, RUST_HOST, run_source
        from world_snapshot import make_world_snapshot
        if not HOST.is_file() and not RUST_HOST.is_file():
            self.skipTest('build the local Luau host first')
        from locomotion_animation import _source
        self.driver._bind(self.world, 123, 1.)

        def step(name, role, speed=1.):
            return f'local function {name}()\n{_source(self.driver._references, role, speed)}\nend\n'

        source = '\n'.join((
            step('idle', 'idle'), step('walk', 'walk', .5),
            step('run', 'run', 1.), step('jump', 'jump', 1.),
            step('fall', 'fall', 1.), step('jump_again', 'jump'), step('climb', 'climb'),
            step('swim', 'swim'), step('swimidle', 'swimidle'),
            step('sit', 'sit'), step('stop', None),
            '''local a=idle();local n1=getgenv().__codexOwnedCharacterLocomotionV1.tracks.idle~=nil
local b=walk();local n2=getgenv().__codexOwnedCharacterLocomotionV1.tracks.walk~=nil
local c=run();local n3=getgenv().__codexOwnedCharacterLocomotionV1.tracks.run~=nil
local d=jump();local n4=getgenv().__codexOwnedCharacterLocomotionV1.tracks.jump~=nil
local state=getgenv().__codexOwnedCharacterLocomotionV1
local old_jump=state.tracks.jump;old_jump:Destroy();local d2=jump()
local repaired=state.tracks.jump~=old_jump and state.tracks.jump.IsPlaying
local e=fall();local n5=getgenv().__codexOwnedCharacterLocomotionV1.tracks.fall~=nil
local g=climb();local n6=getgenv().__codexOwnedCharacterLocomotionV1.tracks.climb~=nil
local h=swim();local n7=getgenv().__codexOwnedCharacterLocomotionV1.tracks.swim~=nil
local i=swimidle();local n8=getgenv().__codexOwnedCharacterLocomotionV1.tracks.swimidle~=nil
local j=sit();local n9=getgenv().__codexOwnedCharacterLocomotionV1.tracks.sit~=nil
local f=stop()
local s=getgenv().__codexOwnedCharacterLocomotionV1
return a,b,c,d,d2,repaired,e,g,h,i,j,f,s.owner,s.reason,s.active,n1,n2,n3,n4,n5,n6,n7,n8,n9,
    type(s.tracks),s.tracks.idle~=nil,s.tracks.walk~=nil,
    s.character==game:GetService("Players").LocalPlayer.Character,
    s.animator==game:GetService("Players").LocalPlayer.Character.Humanoid.Animator
'''))
        result = run_source(source, world_snapshot=make_world_snapshot(self.world, 123))
        self.assertTrue(result['ok'], result.get('error'))
        self.assertEqual(result['values'], [True, True, True, True, True, True, True, True,
                                             True, True, True, True,
                                             'codex_loc_v1', 'stopped', None,
                                             True, True, True, True, True, True, True, True, True,
                                             'table', True, True, True, True])


if __name__ == '__main__':
    unittest.main()
