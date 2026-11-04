import unittest
from types import SimpleNamespace
from unittest.mock import patch

import game_session


class GameSessionPhysicsTests(unittest.TestCase):
    def ticket_args(self, *extra):
        return ['--protocol-sync', '--luau-range', '--ticket', *extra]

    def test_physics_requires_ticket_and_is_exclusive_with_bounded_modes(self):
        with self.assertRaises(SystemExit):
            game_session.parse_args(['--physics'])
        with self.assertRaises(SystemExit):
            game_session.parse_args(self.ticket_args('--physics', '--experimental-physics-fall'))
        with self.assertRaises(SystemExit):
            game_session.parse_args(self.ticket_args('--physics', '--experimental-physics-walk'))
        self.assertTrue(game_session.parse_args(self.ticket_args('--physics')).physics)

    def test_visual_mesh_collisions_require_physics_and_are_explicit(self):
        with self.assertRaises(SystemExit):
            game_session.parse_args(self.ticket_args('--visual-mesh-collisions'))
        args=game_session.parse_args(self.ticket_args('--physics','--visual-mesh-collisions'))
        self.assertTrue(args.visual_mesh_collisions)
        runtime=SimpleNamespace(movement_preview=None)
        sentinel=SimpleNamespace(prediction=object())
        with patch('live_character_physics.LiveCharacterPhysics',return_value=sentinel) as driver:
            game_session._create_physics_driver(args,runtime)
        self.assertIs(runtime.movement_preview,sentinel.prediction)
        self.assertTrue(driver.call_args.kwargs['allow_visual_mesh_approximation'])

    def test_minute_walk_is_explicit_and_requires_continuous_physics(self):
        with self.assertRaises(SystemExit):
            game_session.parse_args(self.ticket_args('--walk-for-minute'))
        with self.assertRaises(SystemExit):
            game_session.parse_args(['--walk-for-minute'])
        args = game_session.parse_args(self.ticket_args('--physics', '--walk-for-minute'))
        self.assertTrue(args.physics)
        self.assertTrue(args.walk_for_minute)

    def test_normal_driver_selected_and_persistent_luau_shares_prediction(self):
        runtime = SimpleNamespace(movement_preview=None)
        args = SimpleNamespace(physics=True, experimental_physics_fall=False,
                               experimental_physics_walk=False, persistent_scripts=True)
        sentinel = SimpleNamespace(prediction=object())
        with patch('live_character_physics.LiveCharacterPhysics',
                   return_value=sentinel) as driver:
            physics, walk, report_key = game_session._create_physics_driver(args, runtime)
        self.assertIs(physics, sentinel)
        self.assertIsNone(walk)
        self.assertEqual(report_key, 'character_physics')
        self.assertIs(runtime.movement_preview, sentinel.prediction)
        driver.assert_called_once_with(experimental_enabled=True,
                                       use_predicted_humanoid_state=True,
                                       use_predicted_rotation=True)

    def test_minute_walk_uses_normal_driver_and_shared_prediction(self):
        runtime = SimpleNamespace(movement_preview=None)
        args = SimpleNamespace(physics=True, experimental_physics_fall=False,
                               experimental_physics_walk=False, walk_for_minute=True,
                               persistent_scripts=True)
        sentinel = SimpleNamespace(prediction=object())
        with patch('live_character_physics.LiveCharacterPhysics',
                   return_value=sentinel), \
             patch('physics_walk_exercise.PhysicsWalkExercise') as exercise:
            physics, walk, report_key = game_session._create_physics_driver(
                args, runtime, 'walk source')
        self.assertIs(physics, sentinel)
        self.assertIs(walk, exercise.return_value)
        self.assertEqual(report_key, 'character_physics')
        self.assertIs(runtime.movement_preview, sentinel.prediction)
        exercise.assert_called_once_with('walk source')

    def test_no_physics_selects_no_driver(self):
        runtime = SimpleNamespace(movement_preview=None)
        args = SimpleNamespace(physics=False, experimental_physics_fall=False,
                               experimental_physics_walk=False, persistent_scripts=True)
        self.assertEqual(game_session._create_physics_driver(args, runtime),
                         (None, None, 'experimental_physics'))
        self.assertIsNone(runtime.movement_preview)


if __name__ == '__main__':
    unittest.main()
