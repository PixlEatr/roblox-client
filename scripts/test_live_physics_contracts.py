"""Cross-module safety contracts for the experimental physics pipeline.

These tests exercise only local code. A staged payload is not evidence that a
Roblox server received or accepted it, and received displacement alone does
not establish walking, gravity, landing, or lack of anchoring.
"""
import unittest
import collections
from types import SimpleNamespace
from unittest.mock import Mock, patch

from physics_packet import PhysicsPacketError, TransformHeader, encode_transform_component
from physics_rotation import RotationField
from physics_session import PhysicsSession
from physics_walk_acceptance import PhysicsWalkAcceptance
from live_physics_trial import LivePhysicsTrial
from physics_root_record import encode_instance_reference
from persistent_runtime import PersistentClientScriptRuntime
from physics_runtime_prediction import RuntimePhysicsPrediction, own_character_snapshot
from test_world_snapshot import fixture as world_fixture


def live_world_fixture():
    world, root_key = world_fixture()
    owner_index = len(world.schema["properties"])
    world.schema["properties"].append({"name": "NetworkOwnerV3", "type": 34})
    world.schema["classes"][3]["properties"].append(owner_index)
    world.client_scope = root_key[0]
    root = world.instances[root_key]
    root["properties"]["Anchored"] = False
    root["properties"]["NetworkOwnerV3"] = world.client_scope
    world.touch(root_key, ["Anchored", "NetworkOwnerV3"])
    return world, root_key


def _tick_trial(trial, world, now, *, own_user_id=123, delta=0.1,
                timestamp_ms=1000, connected=True, sender=None,
                received_update_age=0.0):
    if sender is None:
        sender = lambda channel, payload, *, application: None
    return trial.tick(world=world, own_user_id=own_user_id, now=now,
                      delta=delta, timestamp_ms=timestamp_ms,
                      connected=connected, send_unreliable=sender,
                      received_update_age=received_update_age)


AUTHORITY = {
    "own_player_confirmed": True,
    "character_model_confirmed": True,
    "humanoid_confirmed": True,
    "root_part_confirmed": True,
    "root_in_workspace_confirmed": True,
    "assembly_root_confirmed": True,
    "streaming_region_confirmed": True,
    "anchored": False,
    "anchored_source": "server_replication",
    "ownership_granted": True,
    "ownership_source": "server_replication",
}

PROTOCOL = {
    "outbound_payload_confirmed": True,
    "application_channel_confirmed": True,
    "source": "test fixture; not native or live evidence",
}


class _Controller:
    def __init__(self):
        self.steps = 0

    def step(self, dt):
        self.steps += 1
        return {"step": self.steps}


class _Stager:
    def __init__(self):
        self.frames = []

    def stage(self, frame):
        self.frames.append(frame)

    def drain(self):
        self.frames.clear()


class LivePhysicsContractTests(unittest.TestCase):
    def test_unconfirmed_authority_cannot_advance_or_stage(self):
        controller, stager = _Controller(), _Stager()
        session = PhysicsSession(
            controller, lambda state: repr(state).encode(), stager,
            protocol_evidence=PROTOCOL, enabled=True,
        )
        self.assertTrue(session.start()["ok"])
        result = session.tick(0.1, authority_evidence={**AUTHORITY, "ownership_granted": False})
        self.assertEqual(result["reason"], "authority_unconfirmed")
        self.assertEqual(controller.steps, 0)
        self.assertEqual(stager.frames, [])
        self.assertEqual(session.summary()["network_send_count"], 0)

    def test_local_staging_never_counts_as_server_send_or_acceptance(self):
        stager = _Stager()
        session = PhysicsSession(
            _Controller(), lambda state: repr(state).encode(), stager,
            protocol_evidence=PROTOCOL, enabled=True,
        )
        session.start()
        result = session.tick(0.1, authority_evidence=AUTHORITY)
        self.assertEqual(result["reason"], "locally_staged_only")
        self.assertEqual(session.summary()["staged_count"], 1)
        self.assertEqual(session.summary()["network_send_count"], 0)
        self.assertFalse(result["summary"].get("server_acceptance_confirmed", False))

    def test_local_prediction_cannot_enter_received_movement_trial(self):
        trial = PhysicsWalkAcceptance()
        trial.start_test(timestamp=0)
        with self.assertRaises(ValueError):
            trial.add_received_pose((0, 0, 0), timestamp=1, source="predicted", own_root=True)
        self.assertEqual(trial.report()["status"], "incomplete_not_stopped")
        self.assertEqual(trial.report()["received_sample_count"], 0)

    def test_received_positions_alone_do_not_confirm_fall_or_landing(self):
        trial = PhysicsWalkAcceptance()
        trial.start_test(timestamp=0)
        # Ten seconds of plausible received displacement in every active window.
        for second in range(1, 62):
            trial.add_received_pose((second * 0.2, 0, 0), timestamp=second,
                                    source="received", own_root=True)
        trial.command_stop(timestamp=61.5)
        trial.add_received_pose((12.2, 0, 0), timestamp=61.7,
                                source="received", own_root=True)
        trial.add_received_pose((12.2, 0, 0), timestamp=62.3,
                                source="received", own_root=True)
        trial.add_received_pose((12.2, 0, 0), timestamp=62.9,
                                source="received", own_root=True)
        report = trial.report()
        self.assertFalse(report["accepted"])
        self.assertEqual(report["status"], "received_fall_not_confirmed")
        self.assertFalse(report["local_prediction_used"])
        self.assertFalse(report["received_downward_velocity_while_unanchored"])
        self.assertFalse(report["received_geometry_landing_after_fall"])
        self.assertFalse(report["gravity_inferred_from_positions"])
        self.assertFalse(report["gait_inferred_from_root_motion"])

    def test_unverified_outbound_seven_float_rotation_fails_closed(self):
        # The exported sender fallback is a six-byte compact orientation.
        # A type-57 seven-float field is receive-side evidence only until the
        # outbound fallback's exact semantics are proven.
        with self.assertRaises(PhysicsPacketError):
            encode_transform_component(
                header=TransformHeader(31, True, True, True, 0xFF),
                position_value=None,
                rotation=RotationField(31, "float32x7", (1, 0, 0, 0, 1, 0, 0)),
                linear_velocity=None,
                angular_velocity=None,
                velocity_mode="compact",
            )

    def test_runtime_record_predicts_gravity_from_fixture_without_world_mutation(self):
        world, root_key = world_fixture()
        before_instances = __import__("copy").deepcopy(world.instances)
        before_revision = world.revision
        runtime = PersistentClientScriptRuntime(lambda: world, 123)
        runtime.world = world
        clock = Mock(side_effect=(100.0, 100.1))
        with patch("persistent_runtime.time", SimpleNamespace(monotonic=clock)):
            first = runtime._record({"ok": True})
            second = runtime._record({"ok": True})

        self.assertEqual(first["local_movement"]["mode"], "local_prediction")
        self.assertFalse(first["local_movement"]["server_confirmed"])
        self.assertEqual(second["local_movement"]["network_packets_sent"], 0)
        self.assertLess(second["local_movement"]["predicted_position"][1],
                        first["local_movement"]["predicted_position"][1])
        self.assertTrue(second["local_movement"]["controller"]["received_state_modified"] is False)
        self.assertEqual(world.instances, before_instances)
        self.assertEqual(world.revision, before_revision)
        self.assertIn(root_key, world.instances)

    def test_runtime_prediction_exports_only_own_character_subset(self):
        world, _ = world_fixture()
        snapshot = own_character_snapshot(world, 123)
        names = {node["class_name"] for node in snapshot["nodes"]}
        ids = {node["id"] for node in snapshot["nodes"]}
        self.assertEqual(names, {"Player", "Model", "Part", "Humanoid"})
        self.assertEqual(ids, {"1:2", "1:3", "1:4", "1:5"})
        self.assertNotIn("script_sources", snapshot)
        self.assertNotIn("class_events", snapshot)
        self.assertNotIn("services", snapshot)
        root = next(node for node in snapshot["nodes"] if node["id"] == "1:4")
        self.assertIs(root["properties"]["Anchored"], False)
        self.assertEqual(root["origins"]["Anchored"], "class_default")

    def test_runtime_record_routes_luau_movement_intent_to_local_prediction(self):
        world, _ = world_fixture()
        runtime = PersistentClientScriptRuntime(lambda: world, 123)
        runtime.world = world
        intent = {"sequence": 1, "kind": "Move", "instance_id": "1:5",
                  "vector": [1, 0, 0], "relative_to_camera": False}
        clock = Mock(side_effect=(100.0, 100.1))
        with patch("persistent_runtime.time", SimpleNamespace(monotonic=clock)):
            first = runtime._record({"ok": True, "movement_intents": [intent]})
            second = runtime._record({"ok": True})

        before = first["local_movement"]["predicted_position"]
        after = second["local_movement"]["predicted_position"]
        self.assertEqual(first["local_movement"]["accepted_intents"], 1)
        self.assertGreater(after[0], before[0])
        self.assertEqual(second["local_movement"]["network_packets_sent"], 0)

    def test_runtime_record_deactivates_when_character_reference_is_removed(self):
        world, _ = world_fixture()
        runtime = PersistentClientScriptRuntime(lambda: world, 123)
        runtime.world = world
        clock = Mock(side_effect=(100.0, 100.1))
        with patch("persistent_runtime.time", SimpleNamespace(monotonic=clock)):
            runtime._record({"ok": True})
            player_key = (1, 2)
            world.instances[player_key]["properties"]["Character"] = None
            world.touch(player_key, ["Character"])
            result = runtime._record({"ok": True})
        movement = result["local_movement"]
        self.assertFalse(movement["connected"])
        self.assertIsNone(movement["predicted_position"])
        self.assertEqual(movement["network_packets_sent"], 0)
        self.assertIsNone(world.instances[player_key]["properties"]["Character"])

    def test_unrelated_world_revisions_do_not_rescan_avatar_or_ground(self):
        world, _ = world_fixture()
        prediction = RuntimePhysicsPrediction()
        import physics_runtime_prediction as prediction_module
        with patch.object(prediction_module, "extract_ground",
                          wraps=prediction_module.extract_ground) as extract:
            prediction.advance(world, 123, 0.0)
            self.assertEqual(extract.call_count, 1)
            workspace = (1, 6)
            world.instances[workspace]["properties"]["Name"] = b"Workspace"
            world.touch(workspace, ["Name"])
            with patch.object(world, "avatar", side_effect=AssertionError("unexpected full avatar scan")):
                result = prediction.advance(world, 123, 0.1)
            self.assertEqual(extract.call_count, 1)
        self.assertTrue(result["connected"])

    def test_child_anchor_and_geometry_journal_changes_refresh_precisely(self):
        world, root_key = world_fixture()
        prediction = RuntimePhysicsPrediction()
        import physics_runtime_prediction as prediction_module
        with patch.object(prediction_module, "extract_ground",
                          wraps=prediction_module.extract_ground) as extract:
            prediction.advance(world, 123, 0.0)

            # A newly replicated direct character child enters the cached view.
            child_key = (1, 20)
            world.instances[child_key] = {"class": 3, "parent": (1, 3),
                                          "properties": {"Name": b"AccessoryHandle"}}
            world.creation[child_key] = {"fresh": True, "complete": True,
                                         "property_lists": 2, "ordinary": True}
            world.touch(child_key, ["Name", "Parent"], created=True)
            prediction.advance(world, 123, 0.1)
            self.assertIn("1:20", {node["id"] for node in prediction.bridge.snapshot["nodes"]})
            self.assertEqual(extract.call_count, 1)

            moved_child = (1, 22)
            world.instances[moved_child] = {"class": 3, "parent": (1, 6),
                                            "properties": {"Name": b"ReparentedPart"}}
            world.creation[moved_child] = {"fresh": True, "complete": True,
                                           "property_lists": 2, "ordinary": True}
            world.touch(moved_child, ["Name", "Parent"], created=True)
            prediction.advance(world, 123, 0.12)
            world.instances[moved_child]["parent"] = (1, 3)
            world.touch(moved_child, ["Parent"])
            prediction.advance(world, 123, 0.14)
            self.assertIn("1:22", {node["id"] for node in prediction.bridge.snapshot["nodes"]})

            humanoid_key = (1, 5)
            world.instances[humanoid_key]["properties"]["WalkSpeed"] = 32.0
            world.touch(humanoid_key, ["WalkSpeed"])
            prediction.advance(world, 123, 0.16)
            self.assertEqual(prediction.bridge.controller.controller.walk_speed, 32.0)

            # Received Anchored=true deactivates movement, then false permits it.
            world.instances[root_key]["properties"]["Anchored"] = True
            world.touch(root_key, ["Anchored"])
            anchored = prediction.advance(world, 123, 0.2)
            self.assertIsNone(anchored["predicted_position"])
            world.instances[root_key]["properties"]["Anchored"] = False
            world.touch(root_key, ["Anchored"])
            unanchored = prediction.advance(world, 123, 0.3)
            self.assertIsNotNone(unanchored["predicted_position"])
            self.assertEqual(extract.call_count, 2)

            # A world Part change marks collision geometry dirty; throttle is
            # respected, then a later tick rebuilds from the journal change.
            # Ground extraction requires an explicitly received Block shape;
            # this synthetic schema fixture supplies that proof.
            shape_index = len(world.schema["properties"])
            size_index = shape_index + 1
            world.schema["properties"].append({"name": "shape", "type": 7, "enum": 0})
            world.schema["properties"].append({"name": "size", "type": 22})
            world.schema.setdefault("enums", []).append({"name": "PartType"})
            world.schema["classes"][3]["properties"].append(shape_index)
            world.schema["classes"][3]["properties"].append(size_index)
            floor_key = (1, 21)
            world.instances[floor_key] = {"class": 3, "parent": (1, 6), "properties": {
                "Name": b"Floor", "Anchored": True, "CanCollide": True,
                "shape": 1,
                "size": [20.0, 1.0, 20.0],
                "CFrame": {"position": [0.0, -1.0, 0.0],
                           "rotation": [1., 0., 0., 0., 1., 0., 0., 0., 1.]},
            }}
            world.creation[floor_key] = {"fresh": True, "complete": True,
                                         "property_lists": 2, "ordinary": True}
            world.touch(floor_key, ["Name", "Anchored", "CanCollide", "shape", "size", "CFrame", "Parent"],
                        created=True)
            prediction.advance(world, 123, 0.4)
            self.assertEqual(extract.call_count, 2)
            prediction.advance(world, 123, 0.6)
            self.assertEqual(extract.call_count, 3)
            self.assertEqual(prediction.report()["ground_collider_count"], 1)

    def test_character_replacement_uses_journal_and_preserves_own_scope(self):
        world, _ = world_fixture()
        prediction = RuntimePhysicsPrediction()
        prediction.advance(world, 123, 0.0)
        model_key, root_key, humanoid_key = (1, 30), (1, 31), (1, 32)
        model = {"class": 2, "parent": (1, 6), "properties": {"PrimaryPart": root_key}}
        root = {"class": 3, "parent": model_key,
                "properties": {"Name": b"HumanoidRootPart",
                               "CFrame": {"position": [8., 10., 12.], "rotation_id": 1}}}
        humanoid = {"class": 4, "parent": model_key, "properties": {}}
        for key, instance in ((model_key, model), (root_key, root), (humanoid_key, humanoid)):
            world.instances[key] = instance
            world.creation[key] = {"fresh": True, "complete": True,
                                   "property_lists": 2, "ordinary": True}
            world.touch(key, list(instance["properties"]), created=True)
        world.instances[(1, 2)]["properties"]["Character"] = model_key
        world.touch((1, 2), ["Character"])

        result = prediction.advance(world, 123, 0.1)
        ids = {node["id"] for node in prediction.bridge.snapshot["nodes"]}
        self.assertEqual(ids, {"1:2", "1:30", "1:31", "1:32"})
        self.assertEqual(result["controller"]["character_id"], "1:30")
        self.assertEqual(result["network_packets_sent"], 0)
        self.assertEqual(world.instances[(1, 2)]["properties"]["Character"], model_key)

    def test_action_journal_overrun_falls_back_to_full_refresh(self):
        world, _ = world_fixture()
        prediction = RuntimePhysicsPrediction()
        prediction.advance(world, 123, 0.0)
        world.actions = collections.deque(world.actions, maxlen=2)
        unrelated = (1, 6)
        for label in (b"a", b"b", b"c"):
            world.instances[unrelated]["properties"]["Name"] = label
            world.touch(unrelated, ["Name"])
        with patch.object(world, "avatar", wraps=world.avatar) as avatar:
            result = prediction.advance(world, 123, 0.1)
        self.assertEqual(avatar.call_count, 1)
        self.assertTrue(result["connected"])

    def _live_trial(self, **kwargs):
        return LivePhysicsTrial(experimental_enabled=True,
                                 local_space_max_send_index=3, **kwargs)

    def test_live_trial_requires_actual_own_player_root_anchor_and_scope_owner(self):
        world, root_key = live_world_fixture()
        calls = []
        trial = self._live_trial()

        wrong_user = _tick_trial(trial, world, 10.0, own_user_id=999,
                                 sender=lambda *a, **k: calls.append((a, k)))
        self.assertEqual(wrong_user["network_send_attempt_count"], 0)
        self.assertEqual(calls, [])

        world.instances[root_key]["properties"]["NetworkOwnerV3"] = world.client_scope + 1
        world.touch(root_key, ["NetworkOwnerV3"])
        mismatch = _tick_trial(trial, world, 10.1,
                               sender=lambda *a, **k: calls.append((a, k)))
        self.assertEqual(mismatch["status"], "observed_owner_scope_mismatch")
        self.assertEqual(calls, [])

        world.instances[root_key]["properties"]["NetworkOwnerV3"] = world.client_scope
        world.instances[root_key]["properties"]["Anchored"] = True
        world.touch(root_key, ["NetworkOwnerV3", "Anchored"])
        anchored = _tick_trial(trial, world, 10.2,
                               sender=lambda *a, **k: calls.append((a, k)))
        self.assertEqual(anchored["status"], "received_anchored_state_not_known_false")
        self.assertEqual(calls, [])

        world.instances[root_key]["properties"]["Anchored"] = False
        world.touch(root_key, ["Anchored"])
        success = _tick_trial(trial, world, 10.3,
                              sender=lambda channel, payload, *, application:
                              calls.append((channel, payload, application)))
        self.assertEqual(success["network_send_attempt_count"], 1)
        self.assertEqual(success["client_send_return_count"], 1)
        self.assertEqual(success["server_update_count"], 0)
        self.assertFalse(success["server_acceptance_confirmed"])
        self.assertTrue(success["owner_scope_match_observed"])
        self.assertEqual(len(calls), 1)
        self.assertIn(encode_instance_reference(world.client_scope, root_key), calls[0][1])

    def test_live_trial_never_interprets_client_send_return_as_server_update(self):
        world, root_key = live_world_fixture()
        trial = self._live_trial()
        first = _tick_trial(trial, world, 1.0)
        self.assertEqual((first["network_send_attempt_count"],
                          first["client_send_return_count"],
                          first["server_update_count"]), (1, 1, 0))
        self.assertEqual(first["received_pose_sample_count"], 1)
        self.assertEqual(first["received_root_pose_update_count"], 1)
        self.assertFalse(first["server_acceptance_confirmed"])

        second = _tick_trial(trial, world, 1.1, timestamp_ms=1100,
                             received_update_age=0.7)
        self.assertEqual(second["received_pose_sample_count"], 2)
        self.assertEqual(second["received_root_pose_update_count"], 1)
        self.assertEqual(second["network_send_attempt_count"], 2)
        self.assertEqual(second["server_update_count"], 0)

        # A new received transform version increments only the received counter.
        world.instances[root_key]["properties"]["CFrame"] = {
            "position": [1., 2.5, 3.], "rotation_id": 1}
        world.touch(root_key, ["CFrame"])
        third = _tick_trial(trial, world, 1.2, timestamp_ms=1200)
        self.assertEqual(third["received_pose_sample_count"], 3)
        self.assertEqual(third["received_root_pose_update_count"], 2)
        self.assertEqual(third["network_send_attempt_count"], 3)
        self.assertEqual(third["server_update_count"], 0)

    def test_live_trial_is_terminal_on_send_exception_without_retry(self):
        world, _ = live_world_fixture()
        trial = self._live_trial()
        calls = []
        def ambiguous_send(*args, **kwargs):
            calls.append((args, kwargs))
            raise OSError("transport result ambiguous")

        first = _tick_trial(trial, world, 1.0, sender=ambiguous_send)
        second = _tick_trial(trial, world, 1.1, timestamp_ms=1100,
                             sender=ambiguous_send)
        self.assertEqual(first["status"], "send_result_ambiguous_no_retry")
        self.assertEqual(first["network_send_attempt_count"], 1)
        self.assertEqual(first["client_send_return_count"], 0)
        self.assertEqual(second["network_send_attempt_count"], 1)
        self.assertEqual(len(calls), 1)

    def test_live_trial_stops_on_clock_delta_disconnect_owner_anchor_or_respawn(self):
        world, root_key = live_world_fixture()
        for reason, mutate, next_kwargs in (
                ("invalid_monotonic_time", None,
                 {"now": 1.0, "delta": 0.1}),
                ("frame_delta_out_of_bounds", None,
                 {"now": 1.1, "delta": 0.251}),
                ("disconnected", None,
                 {"now": 1.1, "connected": False}),
                ("owner_scope_match_lost", "owner",
                 {"now": 1.1}),
                ("anchoring_state_no_longer_known_false", "anchor",
                 {"now": 1.1}),
                ("own_character_changed", "respawn",
                 {"now": 1.1}),):
            with self.subTest(reason=reason):
                current, key = live_world_fixture()
                trial = self._live_trial()
                _tick_trial(trial, current, 1.0)
                if mutate == "owner":
                    current.instances[key]["properties"]["NetworkOwnerV3"] = current.client_scope + 1
                    current.touch(key, ["NetworkOwnerV3"])
                elif mutate == "anchor":
                    current.instances[key]["properties"]["Anchored"] = True
                    current.touch(key, ["Anchored"])
                elif mutate == "respawn":
                    current.instances[(1, 2)]["properties"]["Character"] = None
                    current.touch((1, 2), ["Character"])
                if reason == "invalid_monotonic_time":
                    next_kwargs["now"] = 1.0
                result = _tick_trial(trial, current, **{
                    "own_user_id": 123, "timestamp_ms": 1100,
                    "connected": True, "sender": None,
                    "received_update_age": 0.0, **next_kwargs})
                self.assertEqual(result["status"], "stopped")
                self.assertEqual(result["stop_reason"], reason)
                self.assertEqual(result["network_send_attempt_count"], 1)

    def test_live_trial_stops_if_world_disappears_after_first_send(self):
        world, _ = live_world_fixture()
        trial = self._live_trial()
        first = _tick_trial(trial, world, 1.0)
        result = _tick_trial(trial, None, 1.1, timestamp_ms=1100)
        self.assertEqual(first["network_send_attempt_count"], 1)
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["stop_reason"], "own_world_or_identity_lost")
        self.assertEqual(result["network_send_attempt_count"], 1)

    def test_live_trial_honors_half_second_and_ten_frame_caps(self):
        world, _ = live_world_fixture()
        trial = self._live_trial()
        for frame in range(1, 11):
            result = _tick_trial(trial, world, 1.0 + (frame - 1) * 0.01,
                                 delta=0.01, timestamp_ms=1000 + frame)
        self.assertEqual(result["network_send_attempt_count"], 10)
        bounded = _tick_trial(trial, world, 1.1, delta=0.01, timestamp_ms=1011)
        self.assertEqual(bounded["status"], "stopped")
        self.assertEqual(bounded["network_send_attempt_count"], 10)

        world2, _ = live_world_fixture()
        duration_trial = self._live_trial(max_duration=0.25)
        _tick_trial(duration_trial, world2, 2.0, delta=0.125, timestamp_ms=2000)
        _tick_trial(duration_trial, world2, 2.125, delta=0.125, timestamp_ms=2125)
        ended = _tick_trial(duration_trial, world2, 2.25, delta=0.125, timestamp_ms=2250)
        self.assertEqual(ended["stop_reason"], "trial_duration_reached")
        self.assertEqual(ended["network_send_attempt_count"], 2)


if __name__ == "__main__":
    unittest.main()
