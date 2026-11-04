"""Offline contracts for the explicitly experimental live physics trial."""
import unittest

from live_physics_trial import LivePhysicsTrial, LivePhysicsWalk, MAX_FRAME_DELTA
from test_world_snapshot import fixture as world_fixture


class PredictionStub:
    def __init__(self):
        self.calls = 0

    def advance(self, world, user_id, now):
        self.calls += 1
        return {
            "controller": {"predicted_state": {
                "position": [1.0, 1.0 - self.calls * 0.1, 3.0],
                "linear_velocity": [0.0, -self.calls, 0.0],
                "angular_velocity": [0.0, 0.0, 0.0],
            }},
            "server_confirmed": False,
            "network_packets_sent": 0,
        }


def ready_world():
    world, root_key = world_fixture()
    world.client_scope = 1
    world.schema["properties"].append({"name": "NetworkOwnerV3", "type": 34})
    world.schema["classes"][3]["properties"].append(len(world.schema["properties"]) - 1)
    root = world.instances[root_key]
    root["properties"]["Anchored"] = False
    root["properties"]["NetworkOwnerV3"] = 1
    root["properties"]["Name"] = b"HumanoidRootPart"
    world.touch(root_key, ["Anchored", "NetworkOwnerV3", "Name"])
    world.instances[(1, 2)]["properties"]["Character"] = (1, 3)
    world.touch((1, 2), ["Character"])
    return world, root_key


class LivePhysicsTrialTests(unittest.TestCase):
    def test_walk_window_keeps_owner_checks_and_finite_duration_after_delays(self):
        world, root_key = ready_world()
        trial = LivePhysicsWalk(experimental_enabled=True,
                                local_space_max_send_index=255,
                                prediction=PredictionStub())
        sent = []
        send = lambda *args, **kwargs: sent.append((args, kwargs))
        for now in (10., 10.05, 10.1):
            initial = self.tick(trial, world, send, now=now)
        delayed = self.tick(trial, world, send, now=13., delta=.25)
        self.assertEqual(delayed['network_send_count'], 2)
        self.assertEqual(delayed['max_duration_seconds'], 75.)
        self.assertEqual(delayed['max_frames'], 1500)
        world.instances[root_key]['properties']['NetworkOwnerV3'] = 2
        lost = self.tick(trial, world, send, now=13.05)
        self.assertEqual(lost['stop_reason'], 'owner_scope_match_lost')
        self.assertEqual(len(sent), 2)

        world, _ = ready_world()
        bounded = LivePhysicsWalk(experimental_enabled=True,
                                  local_space_max_send_index=255,
                                  prediction=PredictionStub())
        for now in (10., 10.05, 10.1):
            self.tick(bounded, world, send, now=now)
        expired = self.tick(bounded, world, send, now=85.1, delta=.25)
        self.assertEqual(expired['stop_reason'], 'trial_duration_reached')
        self.assertFalse(expired['server_acceptance_confirmed'])

    def test_late_frame_recovery_caps_real_integration_and_keeps_frame_limit(self):
        world, _ = ready_world()
        trial = LivePhysicsTrial(experimental_enabled=True,
                                 local_space_max_send_index=255,
                                 max_frames=3, max_duration=1.5,
                                 halt_on_late_frame=False)
        sent = []
        send = lambda *args, **kwargs: sent.append((args, kwargs))
        self.tick(trial, world, send, now=10., delta=0.)
        delayed = self.tick(trial, world, send, now=10.6, delta=.25)
        self.assertEqual(delayed['late_frame_count'], 1)
        self.assertIsNone(delayed['stop_reason'])
        self.assertAlmostEqual(delayed['local_prediction']['simulated_seconds'], .25)
        final_frame = self.tick(trial, world, send, now=10.65)
        self.assertEqual(final_frame['network_send_count'], 3)
        stopped = self.tick(trial, world, send, now=10.7)
        self.assertEqual(stopped['stop_reason'], 'trial_frame_limit_reached')
        self.assertEqual(len(sent), 3)
        self.assertFalse(final_frame['server_acceptance_confirmed'])

    def test_real_prediction_is_warmed_before_stability_window(self):
        world, _ = ready_world()
        trial = LivePhysicsTrial(experimental_enabled=True,
                                 local_space_max_send_index=255, settle_ticks=3)
        sent = []
        send = lambda *args, **kwargs: sent.append((args, kwargs))
        prepared = self.tick(trial, world, send, now=10.)
        self.assertEqual(prepared['status'], 'preparing_physics_prediction')
        self.assertEqual(sent, [])
        self.tick(trial, world, send, now=11., delta=.25)
        self.tick(trial, world, send, now=11.05)
        initial = self.tick(trial, world, send, now=11.1)
        later = self.tick(trial, world, send, now=11.15)
        self.assertEqual(len(sent), 2)
        self.assertEqual(initial['predicted_position'], [1., 2., 3.])
        self.assertLess(later['predicted_position'][1], initial['predicted_position'][1])
        self.assertIsNone(later['stop_reason'])

    def test_startup_pause_restarts_settling_without_advancing_prediction(self):
        trial, prediction = self.make_trial(settle_ticks=3)
        world, _ = ready_world()
        sent = []
        send = lambda *args, **kwargs: sent.append((args, kwargs))
        self.tick(trial, world, send, now=10.)
        self.tick(trial, world, send, now=10.05)
        delayed = self.tick(trial, world, send, now=11., delta=.25)
        self.assertEqual(delayed['ready_ticks'], 1)
        self.assertEqual(delayed['status'], 'waiting_for_stable_replication_ticks')
        self.assertEqual(prediction.calls, 0)
        self.assertEqual(sent, [])
        self.tick(trial, world, send, now=11.05)
        started = self.tick(trial, world, send, now=11.1)
        self.assertEqual(started['network_send_count'], 1)
        self.assertEqual(prediction.calls, 1)
        stopped = self.tick(trial, world, send, now=12., delta=.25)
        self.assertEqual(stopped['stop_reason'], 'frame_delta_out_of_bounds')
        self.assertEqual(len(sent), 1)

    def test_settle_tick_count_must_be_a_bounded_integer(self):
        for invalid in (True, -1, 21, 1.5):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                self.make_trial(settle_ticks=invalid)

    def test_join_loading_delay_does_not_stop_an_unstarted_trial(self):
        trial, _ = self.make_trial()
        calls = []
        send = lambda *args, **kwargs: calls.append((args, kwargs))
        waiting = self.tick(trial, None, send, now=1.0)
        self.assertEqual(waiting['network_send_attempt_count'], 0)
        world, _ = ready_world()
        started = self.tick(trial, world, send, now=36.0, delta=.25)
        self.assertEqual(started['network_send_attempt_count'], 1)
        self.assertIsNone(started['stop_reason'])
        self.assertTrue(started['running'])

    def test_root_before_humanoid_does_not_consume_trial_window(self):
        trial, _ = self.make_trial()
        world, _ = ready_world()
        humanoid = world.instances.pop((1, 5))
        world.touch((1, 5), deleted=True)
        calls = []
        send = lambda *args, **kwargs: calls.append((args, kwargs))
        waiting = self.tick(trial, world, send, now=1.)
        self.assertEqual(waiting['network_send_attempt_count'], 0)
        world.instances[(1, 5)] = humanoid
        world.touch((1, 5), created=True)
        started = self.tick(trial, world, send, now=15., delta=.25)
        self.assertEqual(started['network_send_attempt_count'], 1)
        self.assertIsNone(started['stop_reason'])

    def test_real_controller_composes_gravity_frames_from_received_world(self):
        world, root_key = ready_world()
        received_position = list(world.instances[root_key]['properties']['CFrame']['position'])
        trial = LivePhysicsTrial(experimental_enabled=True,
                                 local_space_max_send_index=255)
        sent = []
        send = lambda *args, **kwargs: sent.append((args, kwargs))
        self.tick(trial, world, send, now=10.)
        result = self.tick(trial, world, send, now=10.05)
        self.assertEqual(result['network_send_count'], 2)
        state = result['local_prediction']['controller']['predicted_state']
        self.assertLess(state['linear_velocity'][1], 0.)
        self.assertEqual(world.instances[root_key]['properties']['CFrame']['position'], received_position)
        self.assertFalse(result['server_acceptance_confirmed'])

    def make_trial(self, **kwargs):
        prediction = PredictionStub()
        trial = LivePhysicsTrial(experimental_enabled=True,
                                 local_space_max_send_index=255,
                                 prediction=prediction, **kwargs)
        return trial, prediction

    def tick(self, trial, world, send, *, now=10.0, delta=0.05, connected=True):
        return trial.tick(world=world, own_user_id=123, now=now, delta=delta,
                          timestamp_ms=int(now * 1000), connected=connected,
                          send_unreliable=send)

    def test_default_is_disabled_and_does_not_advance_or_send(self):
        world, _ = ready_world()
        prediction = PredictionStub()
        trial = LivePhysicsTrial(prediction=prediction,
                                 local_space_max_send_index=255)
        calls = []
        result = trial.tick(world=world, own_user_id=123, now=10., delta=.05,
                            timestamp_ms=10000, connected=True,
                            send_unreliable=lambda *a, **k: calls.append((a, k)))
        self.assertEqual(result["mode"], "disabled")
        self.assertEqual(prediction.calls, 0)
        self.assertEqual(calls, [])
        self.assertFalse(result["server_acceptance_confirmed"])

    def test_builds_candidate_packet_for_userid_selected_root_and_reports_hypotheses(self):
        world, _ = ready_world()
        trial, prediction = self.make_trial()
        calls = []

        def send(channel, payload, *, application):
            calls.append((application, channel, payload))

        result = self.tick(trial, world, send)
        self.assertEqual(prediction.calls, 1)
        self.assertEqual(len(calls), 1)
        application, channel, payload = calls[0]
        self.assertEqual((application, channel), (4, 6))
        self.assertEqual(payload[0], 0x1B)
        self.assertTrue(payload.endswith(b"\x00"))
        self.assertEqual(result["network_send_attempt_count"], 1)
        self.assertEqual(result["client_send_return_count"], 1)
        self.assertEqual(result["server_update_count"], 0)
        self.assertFalse(result["server_acceptance_confirmed"])
        self.assertFalse(result["application_channel_confirmed"])
        self.assertFalse(result["native_branch_confirmed"])
        self.assertFalse(result["packet_layout_confirmed"])
        self.assertFalse(result["local_prediction_used_as_received_evidence"])
        self.assertEqual(result['native_client_role'], 0)
        self.assertEqual(result['body_layout'], 'client_direct_current')
        self.assertNotIn("sender_mode_1_no_history", result["branch_assumptions"])

    def test_emitted_client_body_decodes_without_a_server_history_prefix(self):
        from physics_packet import decode_transform_component
        from physics_root_record import encode_instance_reference
        for flags in (0, 0x20, 0x40, 0x60):
            with self.subTest(flags=flags):
                world, root = ready_world()
                trial, _ = self.make_trial(d76c_flags=flags)
                sent = []
                result = self.tick(trial, world, lambda channel, payload, **kw: sent.append(payload))
                self.assertEqual(result['network_send_count'], 1)
                payload = sent[0]
                prefix = (encode_instance_reference(world.client_scope, root)
                          + b'\x00\x01' + bytes((flags, 0)))
                self.assertEqual(payload[18:18+len(prefix)], prefix)
                tail = b'\x00\x00' if flags & 0x20 else b'\x00\x00\x00'
                self.assertTrue(payload.endswith(tail))
                mode = 'short' if flags & 0x40 else 'compact'
                decoded = decode_transform_component(
                    payload[18+len(prefix):-len(tail)], velocity_mode=mode)
                for actual, expected in zip(decoded.position, (1., .9, 3.)):
                    self.assertAlmostEqual(actual, expected, delta=.1)
                self.assertEqual(result['velocity_mode'], mode)
                from wire_reader import Reader
                suffix = Reader(payload[-len(tail):])
                self.assertIsNone(world.guid(suffix))  # root reference frame
                if not flags & 0x20:
                    self.assertIsNone(world.guid(suffix))  # child terminator
                self.assertIsNone(world.guid(suffix))  # outer root terminator
                suffix.finish()

    def test_old_child_index_suffix_is_not_a_complete_root_reference(self):
        from wire_reader import Reader, WireError
        world, _ = ready_world()
        # FF 00 is a nonzero scoped-reference tag (127), which then requires
        # four ID bytes. It cannot stand for a null root reference followed by
        # the two list terminators. The old three-byte tail was truncated.
        with self.assertRaises(WireError):
            world.guid(Reader(b'\xff\x00\x00'))

    def test_modeled_humanoid_state_changes_wire_low_bits_and_preserves_controls(self):
        from physics_root_record import encode_instance_reference

        class StatePrediction(PredictionStub):
            def advance(self, *args):
                result = super().advance(*args)
                result['controller']['predicted_state']['humanoid_state'] = {
                    'value': 5 if self.calls == 1 else 8,
                    'source': 'local_physics_model',
                }
                return result

        world, root = ready_world()
        trial = LivePhysicsTrial(experimental_enabled=True, prediction=StatePrediction(),
                                 local_space_max_send_index=255, d76c_flags=0x60,
                                 use_predicted_humanoid_state=True)
        packets = []
        send = lambda channel, payload, **kw: packets.append(payload)
        self.tick(trial, world, send)
        result = self.tick(trial, world, send, now=10.05)
        flags_offset = 18 + len(encode_instance_reference(world.client_scope, root)) + 2
        self.assertEqual([packet[flags_offset] for packet in packets], [0x65, 0x68])
        self.assertEqual(result['humanoid_states_submitted'], {'5': 1, '8': 1})
        self.assertEqual(result['velocity_mode'], 'short')
        self.assertFalse(result['server_acceptance_confirmed'])
        self.assertFalse(result['humanoid_state_native_producer_parity_confirmed'])

    def test_missing_modeled_humanoid_state_does_not_fall_back_to_falling_down(self):
        trial, _ = self.make_trial(use_predicted_humanoid_state=True)
        world, _ = ready_world()
        packets = []
        result = self.tick(trial, world, lambda *a, **kw: packets.append(a))
        self.assertEqual(result['stop_reason'], 'predicted_humanoid_state_unavailable')
        self.assertEqual(packets, [])
        self.assertEqual(result['humanoid_states_submitted'], {})

    def test_missing_model_yaw_does_not_silently_send_identity(self):
        trial, _ = self.make_trial(use_predicted_rotation=True)
        world, _ = ready_world()
        packets = []
        result = self.tick(trial, world, lambda *a, **kw: packets.append(a))
        self.assertEqual(result['stop_reason'], 'experimental_packet_composition_failed')
        self.assertEqual(result['packet_composition_error_kind'], 'RotationCodecError')
        self.assertEqual(packets, [])

    def test_predicted_state_selection_requires_explicit_nonconflicting_configuration(self):
        for flags, enabled in ((0, 1), (5, True)):
            with self.subTest(flags=flags, enabled=enabled), self.assertRaises(ValueError):
                self.make_trial(d76c_flags=flags, use_predicted_humanoid_state=enabled)

    def test_owner_scope_mismatch_blocks_prediction_and_send(self):
        world, root_key = ready_world()
        world.instances[root_key]["properties"]["NetworkOwnerV3"] = 99
        trial, prediction = self.make_trial()
        calls = []
        result = self.tick(trial, world,
                           lambda *a, **k: calls.append((a, k)))
        self.assertEqual(result["status"], "observed_owner_scope_mismatch")
        self.assertFalse(result["owner_scope_match_observed"])
        self.assertEqual(prediction.calls, 0)
        self.assertEqual(calls, [])

    def test_received_anchored_true_blocks_send(self):
        world, root_key = ready_world()
        world.instances[root_key]["properties"]["Anchored"] = True
        world.touch(root_key, ["Anchored"])
        trial, prediction = self.make_trial()
        calls = []
        result = self.tick(trial, world,
                           lambda *a, **k: calls.append((a, k)))
        self.assertEqual(result["status"], "received_anchored_state_not_known_false")
        self.assertEqual(prediction.calls, 0)
        self.assertEqual(calls, [])

    def test_world_space_root_reference_needs_no_local_space_cache_cap(self):
        world, _ = ready_world()
        trial = LivePhysicsTrial(experimental_enabled=True, prediction=PredictionStub())
        calls = []
        result = self.tick(trial, world,
                           lambda *a, **k: calls.append((a, k)))
        self.assertEqual(result['network_send_count'], 1)
        self.assertEqual(result['current_suffix'], 'null_instance_reference')
        self.assertTrue(result['expected_root_reference_suffix_matches_parser'])
        self.assertFalse(result['current_suffix_branch_confirmed'])
        self.assertEqual(len(calls), 1)

    def test_send_exception_is_ambiguous_and_never_retried(self):
        world, _ = ready_world()
        trial, _ = self.make_trial()
        calls = []

        def fail_after_call(*_args, **_kwargs):
            calls.append(1)
            raise OSError("delivery unknown")

        first = self.tick(trial, world, fail_after_call)
        second = self.tick(trial, world, fail_after_call, now=10.05)
        self.assertEqual(first["status"], "send_result_ambiguous_no_retry")
        self.assertEqual(second["network_send_attempt_count"], 1)
        self.assertEqual(calls, [1])
        self.assertEqual(second["network_send_count"], 0)
        self.assertFalse(second["server_acceptance_confirmed"])

    def test_disconnect_and_character_replacement_stop_trial(self):
        world, root_key = ready_world()
        trial, _ = self.make_trial()
        calls = []
        send = lambda *a, **k: calls.append((a, k))
        self.tick(trial, world, send)
        disconnected = self.tick(trial, world, send, now=10.05, connected=False)
        self.assertEqual(disconnected["stop_reason"], "disconnected")
        self.assertEqual(len(calls), 1)

        world, root_key = ready_world()
        trial, _ = self.make_trial()
        self.tick(trial, world, send)
        # Change the own Character reference; the new root need not have arrived
        # yet for the old trial to stop.
        world.instances[(1, 2)]["properties"]["Character"] = (1, 33)
        world.touch((1, 2), ["Character"])
        changed = self.tick(trial, world, send, now=10.05)
        self.assertEqual(changed["stop_reason"], "own_character_changed")

    def test_bounds_hold_for_duration_frames_and_delta(self):
        world, _ = ready_world()
        trial, _ = self.make_trial()
        calls = []
        send = lambda *a, **k: calls.append((a, k))
        for i in range(10):
            result = self.tick(trial, world, send, now=10.0 + i * 0.05)
        self.assertEqual(result["network_send_attempt_count"], 10)
        self.assertEqual(len(calls), 10)
        bounded = self.tick(trial, world, send, now=10.5)
        self.assertEqual(bounded["stop_reason"], "trial_duration_reached")
        self.assertEqual(len(calls), 10)

        world, _ = ready_world()
        trial, _ = self.make_trial()
        calls = []
        rejected = self.tick(trial, world, lambda *a, **k: calls.append((a, k)),
                             delta=MAX_FRAME_DELTA + 0.001)
        self.assertEqual(rejected["stop_reason"], "frame_delta_out_of_bounds")
        self.assertEqual(calls, [])

        world, _ = ready_world()
        trial, _ = self.make_trial()
        calls = []
        send = lambda *a, **k: calls.append((a, k))
        self.tick(trial, world, send, now=10.0, delta=0.0)
        late = self.tick(trial, world, send, now=10.3, delta=0.25)
        self.assertEqual(late["stop_reason"], "frame_delta_out_of_bounds")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
