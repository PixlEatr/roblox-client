"""Opt-in, bounded experimental own-character physics send trial.

This composes a restricted native-shaped packet from the offline codecs, then
passes it to a caller-supplied NativeSession.send_unreliable-compatible
function. Its route and branch choices remain explicit hypotheses. A send call
return is not evidence of server acceptance; received state is sampled
separately and is never overwritten with local prediction.
"""
from __future__ import annotations

import math

from physics_runtime_prediction import RuntimePhysicsPrediction
from world_snapshot import _node_snapshot


MAX_TRIAL_SECONDS = 2.0
MAX_TRIAL_FRAMES = 10
MAX_FRAME_DELTA = 0.25


def _finite_vector(value):
    return (isinstance(value, (list, tuple)) and len(value) == 3
            and all(not isinstance(v, bool) and isinstance(v, (int, float))
                    and math.isfinite(v) for v in value))


class LivePhysicsTrial:
    """One short experimental fall trial for the account's replicated avatar.

    The explicit ``experimental_enabled`` switch defaults off. `application=4`
    and `channel=6`, the packet branch, timestamp interpretation, and suffix
    state are candidate settings, not confirmed protocol facts. The caller
    root current record uses a scoped-reference suffix; a null reference selects
    the model's world coordinates. The local-space cap argument is retained for
    compatibility but does not select a root suffix. No auth, bytecode, ticket,
    or integrity behavior is touched here.

    ``use_predicted_humanoid_state`` explicitly selects the local controller's
    modeled state for the low five outer flag bits. The current model covers
    walking/falling; this does not assert native producer parity.

    `tick` expects the actual `world_replication.World`, the join UserId, a
    monotonic time and caller-computed millisecond timestamp, connection state,
    and a send_unreliable(channel, payload, application=...) callable. No live
    connection is opened by this module.
    """

    MAX_DURATION = MAX_TRIAL_SECONDS
    MAX_FRAMES = MAX_TRIAL_FRAMES
    CONTINUOUS = False

    def __init__(self, *, experimental_enabled=False, application=4, channel=6,
                 d76c_flags=0, local_space_max_send_index=None,
                 max_duration=0.5,
                 max_frames=MAX_TRIAL_FRAMES, prediction=None, settle_ticks=0,
                 halt_on_late_frame=True, use_predicted_humanoid_state=False,
                 use_predicted_rotation=False):
        if type(experimental_enabled) is not bool:
            raise ValueError("experimental_enabled must be an explicit boolean")
        if type(application) is not int or not 0 <= application <= 8:
            raise ValueError("application must be from 0 through 8")
        if type(channel) is not int or not 0 <= channel < 32:
            raise ValueError("channel must be from 0 through 31")
        if type(d76c_flags) is not int or not 0 <= d76c_flags <= 255:
            raise ValueError("d76c_flags must fit in one byte")
        if (local_space_max_send_index is not None
                and (type(local_space_max_send_index) is not int
                     or not 2 <= local_space_max_send_index <= 255)):
            raise ValueError("local-space cap must be an explicit integer from 2 through 255")
        if (isinstance(max_duration, bool) or not isinstance(max_duration, (int, float))
                or not math.isfinite(max_duration)
                or not 0 < max_duration <= self.MAX_DURATION):
            raise ValueError(f"max_duration must be finite and no greater than {self.MAX_DURATION} seconds")
        if type(max_frames) is not int or not 1 <= max_frames <= self.MAX_FRAMES:
            raise ValueError(f"max_frames must be from 1 through {self.MAX_FRAMES}")
        if type(settle_ticks) is not int or not 0 <= settle_ticks <= 20:
            raise ValueError("settle_ticks must be from 0 through 20")
        if type(halt_on_late_frame) is not bool:
            raise ValueError('halt_on_late_frame must be a boolean')
        if type(use_predicted_humanoid_state) is not bool:
            raise ValueError('use_predicted_humanoid_state must be a boolean')
        if type(use_predicted_rotation) is not bool:
            raise ValueError('use_predicted_rotation must be a boolean')
        if use_predicted_humanoid_state and d76c_flags & 0x1f:
            raise ValueError('predicted Humanoid state cannot override explicit low flag bits')

        self.experimental_enabled = experimental_enabled
        self.application = application
        self.channel = channel
        self.d76c_flags = d76c_flags
        self.use_predicted_humanoid_state = use_predicted_humanoid_state
        self.use_predicted_rotation = use_predicted_rotation
        self._rotation_selector = None if use_predicted_rotation else 1
        self._effective_flags = None if use_predicted_humanoid_state else d76c_flags
        self._humanoid_states_submitted = {}
        self.local_space_max_send_index = local_space_max_send_index
        self.max_duration = float(max_duration)
        self.max_frames = max_frames
        self.settle_ticks = settle_ticks
        self._ready_ticks = 0
        self._prediction_prepared = False
        self.halt_on_late_frame = halt_on_late_frame
        self._late_frame_count = 0
        self.prediction = prediction if prediction is not None else RuntimePhysicsPrediction(
            allow_network_constructor_defaults=experimental_enabled)
        self._started_at = None
        self._last_now = None
        self._identity = None
        self._world_identity = None
        self._last_owner_scope_match = False
        self._last_received_position = None
        self._last_received_at = None
        self._last_pose_revision = None
        self._pose_update_count = 0
        self._pose_sample_count = 0
        self._received_update_age = None
        self._received_position = None
        self._last_pose_change = False
        self._received_downward_observed = False
        self._sequence = 1
        self._send_attempts = 0
        self._send_returns = 0
        self._stopped = False
        self._status = "disabled_by_default" if not experimental_enabled else "waiting_for_own_root"
        self._stop_reason = None
        self._prediction_summary = {}
        self._binding_invalidated = False

    def _invalidate_own_binding(self):
        """Discard locally simulated state so a resumed binding starts received."""
        if self._binding_invalidated:
            return
        clear = getattr(self.prediction, "clear", None)
        if callable(clear):
            clear()
        self._prediction_prepared = False
        self._ready_ticks = 0
        self._last_received_position = None
        self._last_received_at = None
        self._last_pose_revision = None
        self._received_position = None
        self._last_pose_change = False
        self._binding_invalidated = True

    def stop(self, reason="stopped"):
        """Stop this trial permanently; the enclosing shared session stays open."""
        if self._stopped:
            return self.report()
        self._stopped = True
        self._status = "stopped"
        self._stop_reason = str(reason)
        return self.report()

    def tick(self, *, world, own_user_id, now, delta, timestamp_ms,
             connected, send_unreliable, received_update_age=None):
        """Observe the own avatar and, when every gate passes, submit one frame."""
        if self._stopped:
            return self.report()
        if not self.experimental_enabled:
            self._status = "disabled_by_default"
            return self.report()
        if connected is not True:
            return self.stop("disconnected")
        if not callable(send_unreliable):
            self._status = "send_callable_unavailable"
            return self.report()
        if (isinstance(now, bool) or not isinstance(now, (int, float))
                or not math.isfinite(now) or now < 0
                or self._last_now is not None and now <= self._last_now):
            return self.stop("invalid_monotonic_time")
        if (isinstance(delta, bool) or not isinstance(delta, (int, float))
                or not math.isfinite(delta) or not 0 <= delta <= MAX_FRAME_DELTA):
            return self.stop("frame_delta_out_of_bounds")
        if (self._started_at is not None and self._last_now is not None
                and float(now) - self._last_now > MAX_FRAME_DELTA):
            self._late_frame_count += 1
            if self.halt_on_late_frame:
                return self.stop("frame_delta_out_of_bounds")
        if type(timestamp_ms) is not int or not 0 <= timestamp_ms < 1 << 64:
            return self.stop("explicit_timestamp_callback_required")
        previous_now = self._last_now
        self._last_now = float(now)

        if self._world_identity is not None and world is not self._world_identity:
            return self.stop('own_world_or_identity_lost' if world is None else 'own_world_changed')
        if world is None or type(own_user_id) is not int:
            if self._identity is not None:
                return self.stop("own_world_or_identity_lost")
            self._status = "own_world_or_join_user_id_unavailable"
            return self.report()

        try:
            avatar = world.avatar(own_user_id)
            avatar_evidence = avatar.get("evidence", {})
            root_key = avatar.get("root_key")
            character_key = avatar.get("character_key")
            player_key = avatar.get("player_key")
            root = avatar.get("root")
            identity = (player_key, character_key, root_key)
            if self._identity is not None and identity != self._identity:
                if self.CONTINUOUS and player_key == self._identity[0]:
                    # A legitimate own-player Character/root replacement is
                    # rebound below after the fresh root passes all gates.
                    self._invalidate_own_binding()
                    self._identity = None
                else:
                    return self.stop("own_character_changed")
            self._last_owner_scope_match = False
            if not (avatar_evidence.get("game_account_accepted") is True
                    and avatar_evidence.get("player_spawned") is True
                    and avatar_evidence.get("character_model_resolved") is True
                    and avatar_evidence.get("root_part_resolved") is True
                    and avatar_evidence.get("root_position_finite") is True
                    and isinstance(root, dict)
                    and isinstance(root_key, tuple) and len(root_key) == 2):
                if self._identity is not None and not self.CONTINUOUS:
                    return self.stop("own_root_or_character_lost")
                if self.CONTINUOUS:
                    self._invalidate_own_binding()
                    self._identity = None
                self._status = "waiting_for_received_own_root"
                return self.report()
            root_node = _node_snapshot(world, root_key, root)
            anchored = root_node.get("properties", {}).get("Anchored")
            anchored_origin = root_node.get("origins", {}).get("Anchored")
            if type(anchored) is not bool or anchored is True or anchored_origin not in (
                    "received", "class_default"):
                if self._identity is not None and not self.CONTINUOUS:
                    return self.stop("anchoring_state_no_longer_known_false")
                if self.CONTINUOUS:
                    self._invalidate_own_binding()
                    self._identity = None
                self._status = "received_anchored_state_not_known_false"
                return self.report()
            scope = world.client_scope
            owner = root.get("properties", {}).get("NetworkOwnerV3")
            self._last_owner_scope_match = (type(scope) is int and type(owner) is int
                                            and owner == scope)
            if not self._last_owner_scope_match:
                if self._identity is not None and not self.CONTINUOUS:
                    return self.stop("owner_scope_match_lost")
                if self.CONTINUOUS:
                    self._invalidate_own_binding()
                    self._identity = None
                self._status = "observed_owner_scope_mismatch"
                return self.report()
            if (any(type(word) is not int or not 0 <= word <= 0xFFFFFFFF
                    for word in root_key)
                    or root_key[0] == 0xFFFFFFFF):
                if self._identity is not None and not self.CONTINUOUS:
                    return self.stop("own_root_reference_became_unencodable")
                self._status = "own_root_reference_key_unencodable"
                return self.report()
            position = avatar.get("position")
            if not _finite_vector(position):
                if self._identity is not None and not self.CONTINUOUS:
                    return self.stop('received_own_root_position_lost')
                if self.CONTINUOUS:
                    self._invalidate_own_binding()
                    self._identity = None
                self._status = "received_own_root_position_unavailable"
                return self.report()
            versions = world.property_versions.get(root_key, {})
            pose_revision = max(versions.get("ReplicationPV", 0),
                                versions.get("CFrame", 0))
        except (AttributeError, KeyError, TypeError, ValueError, IndexError):
            if self._identity is not None:
                return self.stop('own_world_snapshot_lost')
            self._status = "own_world_snapshot_unavailable"
            return self.report()

        if self._identity is None:
            self._identity = identity
            self._world_identity = world
            self._binding_invalidated = False

        # A stationary server-owned root may legitimately receive no later pose
        # field update. Source age is diagnostic only; eligibility instead
        # checks disconnect, character identity, owner scope, anchoring and dt.
        if (received_update_age is not None and not (
                not isinstance(received_update_age, bool)
                and isinstance(received_update_age, (int, float))
                and math.isfinite(received_update_age)
                and received_update_age >= 0)):
            received_update_age = None
        self._received_update_age = (float(received_update_age)
                                     if received_update_age is not None else None)

        self._pose_sample_count += 1
        if self._last_pose_revision is None or pose_revision != self._last_pose_revision:
            self._pose_update_count += 1
            self._last_pose_revision = pose_revision

        previous_position = self._last_received_position
        self._last_pose_change = bool(previous_position is not None
                                       and math.dist(previous_position, position) > 0.02)
        if (previous_position is not None and self._last_received_at is not None
                and float(now) > self._last_received_at
                and position[1] < previous_position[1] - 0.02):
            self._received_downward_observed = True
        self._last_received_position = tuple(float(v) for v in position)
        self._received_position = self._last_received_position
        self._last_received_at = float(now)

        if (not self.CONTINUOUS and self._started_at is not None
                and float(now)-self._started_at >= self.max_duration):
            return self.stop("trial_duration_reached")
        if not self.CONTINUOUS and self._send_attempts >= self.max_frames:
            return self.stop("trial_frame_limit_reached")

        if self._started_at is None and self.settle_ticks:
            prepare = getattr(self.prediction, 'prepare', None)
            if not self._prediction_prepared and callable(prepare):
                prepare(world, own_user_id, float(now))
                self._prediction_prepared = True
                self._ready_ticks = 0
                self._status = 'preparing_physics_prediction'
                return self.report()
            self._ready_ticks = (self._ready_ticks + 1
                                 if previous_now is not None
                                 and float(now)-previous_now <= MAX_FRAME_DELTA else 1)
            if self._ready_ticks < self.settle_ticks:
                self._status = "waiting_for_stable_replication_ticks"
                return self.report()

        local_prediction = self.prediction.advance(world, own_user_id, float(now))
        controller = local_prediction.get("controller") or {}
        predicted_state = controller.get("predicted_state") or {}
        predicted_position = predicted_state.get("position")
        linear_velocity = predicted_state.get("linear_velocity")
        angular_velocity = predicted_state.get("angular_velocity")
        self._prediction_summary = {
            'prediction_status': local_prediction.get('last_reason'),
            'predicted_position': predicted_position,
            'predicted_grounded': predicted_state.get('grounded'),
            'predicted_yaw': (predicted_state.get('yaw')
                              if isinstance(predicted_state.get('yaw'), (int, float))
                              and not isinstance(predicted_state.get('yaw'), bool)
                              and math.isfinite(predicted_state['yaw']) else None),
            'foot_offset': controller.get('foot_offset'),
            'foot_offset_origin': controller.get('foot_offset_origin'),
            'ground_collider_count': local_prediction.get('ground_collider_count', 0),
            'geometry_assumptions': local_prediction.get('geometry_assumptions', []),
        }
        if not (_finite_vector(predicted_position) and _finite_vector(linear_velocity)
                and _finite_vector(angular_velocity)):
            self._status = "local_gravity_prediction_unavailable"
            return self.report(local_prediction=local_prediction)

        effective_flags = self.d76c_flags
        if self.use_predicted_humanoid_state:
            # The matching native client reader identifies low5 as HumanoidState
            # and rejects values >=17. This policy uses our local ground-contact
            # model, not a claim of native producer parity or server acceptance.
            modeled_state = predicted_state.get('humanoid_state')
            state_value = modeled_state.get('value') if isinstance(modeled_state, dict) else None
            if (type(state_value) is not int or not 0 <= state_value < 17
                    or modeled_state.get('source') != 'local_physics_model'):
                return self.stop('predicted_humanoid_state_unavailable')
            effective_flags = (effective_flags & 0xe0) | state_value
        self._effective_flags = effective_flags

        try:
            # Keep ordinary client startup independent from this experimental
            # path; import codecs only after all live eligibility gates pass.
            from physics_header import LAYOUT_SEQUENCE_THEN_TAG
            from physics_history import CurrentRecord
            from physics_packet import TransformHeader, encode_physics_message
            from physics_rotation import RotationField, native_yaw_rotation
            from physics_root_record import RootRecordEnvelope, encode_instance_reference, encode_client_current_body

            timestamp = timestamp_ms
            rotation = (native_yaw_rotation(predicted_state.get('yaw'))
                        if self.use_predicted_rotation else RotationField(1, "selector", 1))
            self._rotation_selector = rotation.selector
            current = CurrentRecord(
                header=TransformHeader(rotation.selector, False, False, False, rotation.selector),
                position=tuple(predicted_position),
                rotation=rotation,
                linear_velocity=tuple(linear_velocity),
                angular_velocity=tuple(angular_velocity),
                velocity_mode='short' if self.d76c_flags & 0x40 else 'compact',
                reference_bytes=encode_instance_reference(0, None),
            )
            # These are deliberate experimental branch choices, not assertions
            # that the live native sender selected these internal conditions.
            body = encode_client_current_body(current, d76c_flags=effective_flags)
            root_record = RootRecordEnvelope(
                encode_instance_reference(scope, root_key), body
            ).encode(stream_available=True, record_enabled=True)
            packet = encode_physics_message(
                timestamp, self._sequence, (root_record,),
                layout=LAYOUT_SEQUENCE_THEN_TAG,
            )
        except (TypeError, ValueError, OverflowError) as exc:
            self._status = "experimental_packet_composition_failed"
            self._composition_error = type(exc).__name__
            return self.stop(self._status)

        if self._started_at is None:
            self._started_at = float(now)
        self._send_attempts += 1
        try:
            result = send_unreliable(self.channel, packet,
                                     application=self.application)
        except Exception:
            # NativeSession can fail after an ambiguous transport write. Never
            # retry the same candidate frame or keep this trial running.
            self._stopped = True
            self._status = "send_result_ambiguous_no_retry"
            self._stop_reason = self._status
            return self.report(local_prediction=local_prediction)
        if result is False:
            self._stopped = True
            self._status = "send_result_ambiguous_no_retry"
            self._stop_reason = self._status
            return self.report(local_prediction=local_prediction)

        self._send_returns += 1
        state_key = str(effective_flags & 0x1f)
        self._humanoid_states_submitted[state_key] = self._humanoid_states_submitted.get(state_key, 0) + 1
        self._sequence += 1
        self._status = "client_send_returned_server_acceptance_unconfirmed"
        return self.report(local_prediction=local_prediction,
                           last_packet_bytes=len(packet))

    def report(self, *, local_prediction=None, last_packet_bytes=None):
        """Return sanitized counters and observations without private IDs."""
        return {
            **self._prediction_summary,
            "mode": ("continuous_experimental_own_character" if self.CONTINUOUS and self.experimental_enabled
                     else "experimental_hypothesis" if self.experimental_enabled else "disabled"),
            "status": self._status,
            "stop_reason": self._stop_reason,
            "running": self.experimental_enabled and self._identity is not None and not self._stopped,
            "experimental_enabled": self.experimental_enabled,
            "application": self.application,
            "channel": self.channel,
            "application_channel_confirmed": False,
            "native_branch_confirmed": False,
            "native_client_role": 0,
            "body_layout": "client_direct_current",
            "body_layout_provenance": "pinned_client_constructor_and_DBB0_branch",
            "velocity_mode": 'short' if self.d76c_flags & 0x40 else 'compact',
            "effective_d76c_flags": self._effective_flags,
            "humanoid_state_source": ('local_physics_model' if self.use_predicted_humanoid_state
                                      else 'explicit_d76c_flags'),
            "humanoid_state_native_producer_parity_confirmed": False,
            "humanoid_states_submitted": dict(self._humanoid_states_submitted),
            "rotation_source": ('local_physics_model_yaw' if self.use_predicted_rotation
                                else 'explicit_identity'),
            "rotation_selector": self._rotation_selector,
            "current_suffix_branch_confirmed": False,
            "expected_root_reference_suffix_matches_parser": True,
            "current_suffix": "null_instance_reference",
            "current_suffix_provenance": "server_mode_root_reader_scoped_reference_branch",
            "packet_layout_confirmed": False,
            "owner_scope_match_observed": self._last_owner_scope_match,
            "ownership_semantics_universal": False,
            "received_pose_changed": self._last_pose_change,
            "received_pose_sample_count": self._pose_sample_count,
            "received_root_pose_update_count": self._pose_update_count,
            "received_pose_update_age_seconds": self._received_update_age,
            "received_position": self._received_position,
            "received_downward_motion_observed": self._received_downward_observed,
            "server_acceptance_confirmed": False,
            "local_prediction_used_as_received_evidence": False,
            "local_prediction": local_prediction if isinstance(local_prediction, dict) else None,
            "network_send_attempt_count": self._send_attempts,
            "client_send_return_count": self._send_returns,
            "network_send_count": self._send_returns,
            "server_update_count": 0,
            "packet_composition_error_kind": getattr(self, "_composition_error", None),
            "sequence": self._sequence,
            "last_packet_bytes": last_packet_bytes,
            "max_duration_seconds": None if self.CONTINUOUS else self.max_duration,
            "max_frames": None if self.CONTINUOUS else self.max_frames,
            "continuous_mode": self.CONTINUOUS and self.experimental_enabled,
            "own_root_evidence_observed": self._identity is not None,
            "server_acceptance_confirmed": False,
            "settle_ticks": self.settle_ticks,
            "ready_ticks": self._ready_ticks,
            "late_frame_count": self._late_frame_count,
            "late_frame_policy": ('stop' if self.halt_on_late_frame
                                  else 'cap_integration_at_250ms_discard_excess'),
            "max_frame_delta_seconds": MAX_FRAME_DELTA,
            "branch_assumptions": [
                "empty_motor_and_child_lists",
                "cached_block_absent",
                (f"D76C_flags_{self._effective_flags}" if self._effective_flags is not None
                 else "D76C_flags_waiting_for_local_humanoid_state"),
                "root_pose_uses_null_reference",
                ("modeled_yaw_native_quaternion" if self.use_predicted_rotation
                 else "identity_rotation_selector_1"),
                "sequence_then_tag_header_layout",
                "caller_monotonic_milliseconds_without_workspace_adjustment",
                "candidate_unreliable_transport_route",
            ],
            "route_hypothesis": "candidate native transport; application/channel mapping remains unconfirmed",
            "timestamp_hypothesis": "caller-supplied monotonic milliseconds, unadjusted",
        }


class LivePhysicsWalk(LivePhysicsTrial):
    """Finite own-avatar sender window for the one-minute Luau exercise."""
    MAX_DURATION = 75.
    MAX_FRAMES = 1500

    def __init__(self, *, experimental_enabled=False,
                 local_space_max_send_index=None, prediction=None,
                 use_predicted_humanoid_state=False, use_predicted_rotation=False):
        super().__init__(experimental_enabled=experimental_enabled,
                         local_space_max_send_index=local_space_max_send_index,
                         prediction=prediction, max_duration=75., max_frames=1500,
                         settle_ticks=3, halt_on_late_frame=False,
                         use_predicted_humanoid_state=use_predicted_humanoid_state,
                         use_predicted_rotation=use_predicted_rotation)


class LiveCharacterPhysics(LivePhysicsTrial):
    """Continuous, opt-in gravity sender for the currently eligible own root.

    It has no elapsed-time or frame-count expiry. It still applies the shared
    250 ms integration cap and validates identity, anchoring, scope ownership,
    and received root evidence on every send tick. Temporary own-root
    ineligibility waits for recovery; transport/protocol failures remain sticky.
    """
    CONTINUOUS = True

    def __init__(self, *, experimental_enabled=False, prediction=None,
                 use_predicted_humanoid_state=False, use_predicted_rotation=False):
        # Valid constructor values retain the bounded base-class validation;
        # expiry checks and limits are disabled explicitly by CONTINUOUS.
        super().__init__(experimental_enabled=experimental_enabled,
                         max_duration=MAX_TRIAL_SECONDS,
                         max_frames=MAX_TRIAL_FRAMES,
                         prediction=prediction, halt_on_late_frame=False,
                         use_predicted_humanoid_state=use_predicted_humanoid_state,
                         use_predicted_rotation=use_predicted_rotation)


__all__ = ["LivePhysicsTrial", "LivePhysicsWalk", "LiveCharacterPhysics", "MAX_TRIAL_SECONDS", "MAX_TRIAL_FRAMES",
           "MAX_FRAME_DELTA"]
