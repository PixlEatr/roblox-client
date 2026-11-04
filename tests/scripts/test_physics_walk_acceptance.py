import unittest

from physics_walk_acceptance import PhysicsFallDiagnostic, PhysicsWalkAcceptance


def make_walk(*, clean_leave=True, **kwargs):
    trial = PhysicsWalkAcceptance(**kwargs)
    trial.start_test(timestamp=0)
    for t in range(1, 62):
        state = {}
        if t == 1:
            state = dict(received_vertical_velocity_y=-8, anchored=False,
                         received_vertical_velocity_origin="received",
                         anchored_origin="received",
                         supported_by_received_geometry=False)
        elif t == 2:
            state = dict(received_vertical_velocity_y=0, anchored=False,
                         received_vertical_velocity_origin="received",
                         anchored_origin="received",
                         supported_by_received_geometry=True,
                         support_evidence="received_geometry:ground_probe")
        trial.add_received_pose((t * 0.2, 0, 0), timestamp=t,
                                source="received", own_root=True, **state)
    trial.command_stop(timestamp=61.1)
    trial.add_received_pose((12.2, 0, 0), timestamp=61.2,
                            source="received", own_root=True)
    trial.add_received_pose((12.3, 0, 0), timestamp=62.2,
                            source="received", own_root=True)
    if clean_leave:
        trial.mark_clean_leave(timestamp=62.3, source="transport_clean_leave")
    return trial


class PhysicsWalkAcceptanceTests(unittest.TestCase):
    def test_accepts_one_minute_of_received_motion_and_explicit_stable_stop(self):
        trial = make_walk()
        report = trial.report()
        self.assertTrue(report["accepted"])
        self.assertEqual(report["status"], "accepted_server_received_movement_trial")
        self.assertTrue(report["commanded_stop"])
        self.assertTrue(report["clean_transport_leave"])
        self.assertTrue(report["stop_stable_from_received_poses"])
        self.assertTrue(report["received_downward_velocity_while_unanchored"])
        self.assertTrue(report["received_geometry_landing_after_fall"])
        self.assertEqual(report["received_sample_count"], 63)
        self.assertGreaterEqual(report["active_movement_windows_of_12"], 6)
        self.assertFalse(report["local_prediction_used"])
        self.assertFalse(report["raw_positions_included"])
        self.assertNotIn("positions", report)

    def test_no_start_no_stop_short_duration_and_disconnect_never_pass(self):
        trial = PhysicsWalkAcceptance()
        self.assertFalse(trial.report()["accepted"])
        with self.assertRaisesRegex(RuntimeError, "start_test"):
            trial.add_received_pose((0, 0, 0), timestamp=0, source="received", own_root=True)

        short = PhysicsWalkAcceptance()
        short.start_test(timestamp=0)
        for t in range(1, 21):
            short.add_received_pose((t, 0, 0), timestamp=t, source="received", own_root=True)
        short.command_stop(timestamp=20.1)
        short.add_received_pose((20, 0, 0), timestamp=20.2, source="received", own_root=True)
        short.add_received_pose((20, 0, 0), timestamp=21, source="received", own_root=True)
        self.assertEqual(short.report()["status"], "incomplete_duration")

        interrupted = PhysicsWalkAcceptance()
        interrupted.start_test(timestamp=0)
        interrupted.add_received_pose((0, 0, 0), timestamp=0.1, source="received", own_root=True)
        interrupted.disconnect(timestamp=1)
        self.assertEqual(interrupted.report()["status"], "interrupted_disconnect")
        self.assertFalse(interrupted.report()["accepted"])

    def test_local_prediction_and_non_owned_root_cannot_be_used_as_evidence(self):
        trial = PhysicsWalkAcceptance()
        trial.start_test(timestamp=0)
        for source, own_root in (("local_prediction", True), ("received", False)):
            with self.assertRaises(ValueError):
                trial.add_received_pose((100, 0, 0), timestamp=1, source=source, own_root=own_root)
        self.assertEqual(trial.report()["received_sample_count"], 0)
        self.assertFalse(trial.report()["accepted"])

    def test_single_teleport_does_not_pass(self):
        trial = PhysicsWalkAcceptance()
        trial.start_test(timestamp=0)
        trial.add_received_pose((0, 0, 0), timestamp=0.1, source="received", own_root=True,
                                received_vertical_velocity_y=-8, anchored=False,
                                received_vertical_velocity_origin="received",
                                anchored_origin="received",
                                supported_by_received_geometry=False)
        trial.add_received_pose((1000, 0, 0), timestamp=0.2, source="received", own_root=True,
                                received_vertical_velocity_y=0, anchored=False,
                                received_vertical_velocity_origin="received",
                                anchored_origin="received",
                                supported_by_received_geometry=True,
                                support_evidence="received_geometry:ground_probe")
        trial.command_stop(timestamp=60.1)
        trial.add_received_pose((1000, 0, 0), timestamp=60.2, source="received", own_root=True)
        trial.add_received_pose((1000, 0, 0), timestamp=61.1, source="received", own_root=True)
        self.assertFalse(trial.report()["accepted"])
        self.assertEqual(trial.report()["status"], "insufficient_received_movement_evidence")
        self.assertGreater(trial.report()["implausible_speed_segments"], 0)

    def test_positions_alone_cannot_prove_fall_or_gravity(self):
        trial = PhysicsWalkAcceptance()
        trial.start_test(timestamp=0)
        for t in range(1, 62):
            trial.add_received_pose((t * 0.2, -t, 0), timestamp=t,
                                   source="received", own_root=True)
        trial.command_stop(timestamp=61.1)
        trial.add_received_pose((12.2, -61, 0), timestamp=61.2,
                                source="received", own_root=True)
        trial.add_received_pose((12.2, -61, 0), timestamp=62.2,
                                source="received", own_root=True)
        trial.mark_clean_leave(timestamp=62.3, source="transport_clean_leave")
        report = trial.report()
        self.assertFalse(report["accepted"])
        self.assertEqual(report["status"], "received_fall_not_confirmed")
        self.assertFalse(report["gravity_inferred_from_positions"])

    def test_vertical_falling_does_not_count_as_horizontal_walking_motion(self):
        trial = PhysicsWalkAcceptance()
        trial.start_test(timestamp=0)
        for t in range(1, 62):
            state = {}
            if t == 1:
                state = dict(received_vertical_velocity_y=-8, anchored=False,
                             received_vertical_velocity_origin="received",
                             anchored_origin="received",
                             supported_by_received_geometry=False)
            elif t == 2:
                state = dict(received_vertical_velocity_y=0, anchored=False,
                             received_vertical_velocity_origin="received",
                             anchored_origin="received",
                             supported_by_received_geometry=True,
                             support_evidence="received_geometry:ground_probe")
            trial.add_received_pose((0, -float(t), 0), timestamp=t,
                                    source="received", own_root=True, **state)
        trial.command_stop(timestamp=61.1)
        trial.add_received_pose((0, -61, 0), timestamp=61.2,
                                source="received", own_root=True)
        trial.add_received_pose((0, -61, 0), timestamp=62.2,
                                source="received", own_root=True)
        trial.mark_clean_leave(timestamp=62.3, source="transport_clean_leave")
        self.assertEqual(trial.report()["status"], "insufficient_horizontal_movement_evidence")

    def test_one_minute_trial_still_requires_transport_confirmed_clean_leave(self):
        trial = make_walk(clean_leave=False)
        self.assertFalse(trial.report()["accepted"])
        self.assertEqual(trial.report()["status"], "clean_transport_leave_not_confirmed")


class PhysicsFallDiagnosticTests(unittest.TestCase):
    def test_ten_received_frames_observe_fall_without_claiming_server_acceptance(self):
        diagnostic = PhysicsFallDiagnostic()
        diagnostic.start(timestamp=0)
        for frame in range(10):
            t = 0.5 * (frame + 1) / 10
            diagnostic.add_received_pose(
                (0, -t * 10, 0), timestamp=t, source="received", own_root=True,
                received_vertical_velocity_y=-10,
                received_vertical_velocity_origin="received",
                anchored=False, anchored_origin="received")
        report = diagnostic.report()
        self.assertTrue(report["diagnostic_window_complete"])
        self.assertEqual(report["status"], "received_downward_velocity_while_unanchored")
        self.assertTrue(report["received_root_pose_changed"])
        self.assertTrue(report["received_downward_pose_change_observed"])
        self.assertFalse(report["accepted"])
        self.assertFalse(report["server_acceptance_confirmed"])
        self.assertFalse(report["send_count_used_as_server_evidence"])
        self.assertEqual(report["client_send_return_count"], 0)

    def test_ten_client_send_returns_with_unchanged_received_pose_do_not_count_as_movement(self):
        diagnostic = PhysicsFallDiagnostic()
        diagnostic.start(timestamp=0)
        for _ in range(10):
            diagnostic.note_client_send_result(returned=True)
        for frame in range(10):
            t = 0.5 * (frame + 1) / 10
            diagnostic.add_received_pose((1, 2, 3), timestamp=t,
                                        source="received", own_root=True)
        report = diagnostic.report()
        self.assertTrue(report["diagnostic_window_complete"])
        self.assertEqual(report["status"], "no_received_root_pose_change_observed")
        self.assertFalse(report["received_root_pose_changed"])
        self.assertEqual(report["client_send_attempt_count"], 10)
        self.assertEqual(report["client_send_return_count"], 10)
        self.assertFalse(report["server_acceptance_confirmed"])
        self.assertFalse(report["send_count_used_as_server_evidence"])

    def test_local_pose_and_non_owned_root_are_rejected(self):
        diagnostic = PhysicsFallDiagnostic()
        diagnostic.start(timestamp=0)
        with self.assertRaises(ValueError):
            diagnostic.add_received_pose((0, 0, 0), timestamp=0.1,
                                         source="local_prediction", own_root=True)
        with self.assertRaises(ValueError):
            diagnostic.add_received_pose((0, 0, 0), timestamp=0.2,
                                         source="received", own_root=False)
        self.assertEqual(diagnostic.report()["received_pose_count"], 0)
        self.assertEqual(diagnostic.report()["rejected_pose_count"], 2)


class PhysicsWalkAcceptanceRegressionTests(unittest.TestCase):
    def test_early_movement_followed_by_idle_time_is_not_a_walk_trial(self):
        trial = PhysicsWalkAcceptance()
        trial.start_test(timestamp=0)
        for t in range(1, 62):
            x = min(t, 5) * 1.0
            state = {}
            if t == 1:
                state = dict(received_vertical_velocity_y=-8, anchored=False,
                             received_vertical_velocity_origin="received",
                             anchored_origin="received",
                             supported_by_received_geometry=False)
            elif t == 2:
                state = dict(received_vertical_velocity_y=0, anchored=False,
                             received_vertical_velocity_origin="received",
                             anchored_origin="received",
                             supported_by_received_geometry=True,
                             support_evidence="received_geometry:ground_probe")
            trial.add_received_pose((x, 0, 0), timestamp=t, source="received", own_root=True, **state)
        trial.command_stop(timestamp=61.1)
        trial.add_received_pose((5, 0, 0), timestamp=61.2, source="received", own_root=True)
        trial.add_received_pose((5, 0, 0), timestamp=62.0, source="received", own_root=True)
        self.assertFalse(trial.report()["accepted"])
        self.assertEqual(trial.report()["status"], "movement_not_sustained_across_trial")

    def test_stop_requires_server_received_settling_samples(self):
        trial = PhysicsWalkAcceptance()
        trial.start_test(timestamp=0)
        for t in range(1, 62):
            state = {}
            if t == 1:
                state = dict(received_vertical_velocity_y=-8, anchored=False,
                             received_vertical_velocity_origin="received",
                             anchored_origin="received",
                             supported_by_received_geometry=False)
            elif t == 2:
                state = dict(received_vertical_velocity_y=0, anchored=False,
                             received_vertical_velocity_origin="received",
                             anchored_origin="received",
                             supported_by_received_geometry=True,
                             support_evidence="received_geometry:ground_probe")
            trial.add_received_pose((t * 0.2, 0, 0), timestamp=t, source="received", own_root=True, **state)
        trial.command_stop(timestamp=61.1)
        trial.add_received_pose((12.4, 0, 0), timestamp=61.2, source="received", own_root=True)
        trial.add_received_pose((20, 0, 0), timestamp=62, source="received", own_root=True)
        self.assertFalse(trial.report()["accepted"])
        self.assertEqual(trial.report()["status"], "stop_not_confirmed_by_received_poses")

    def test_corrections_are_counted_as_events_not_movement_samples(self):
        trial = PhysicsWalkAcceptance()
        trial.start_test(timestamp=0)
        trial.add_received_pose((0, 0, 0), timestamp=0.1, source="received", own_root=True)
        trial.note_server_correction(timestamp=0.2)
        self.assertEqual(trial.report()["server_correction_count"], 1)
        self.assertEqual(trial.report()["received_sample_count"], 1)

    def test_bounds_configuration_and_event_order(self):
        with self.assertRaises(ValueError):
            PhysicsWalkAcceptance(trial_seconds=10)
        trial = PhysicsWalkAcceptance()
        with self.assertRaises(RuntimeError):
            trial.command_stop(timestamp=1)
        trial.start_test(timestamp=1)
        with self.assertRaises(ValueError):
            trial.add_received_pose((0, 0, 0), timestamp=1, source="received", own_root=True)


if __name__ == "__main__":
    unittest.main()
