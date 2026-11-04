import math
import json
import re
import unittest
from collections import deque
from pathlib import Path

from animation_rig_pose import (ReceivedAvatarRig, _rotate, _transpose,
                                evaluate_own_avatar)
from physics_rotation import (native_compact_selector_to_matrix,
                              native_property_selector_to_matrix)


def _frame(position=(0, 0, 0), yaw=0):
    c, s = math.cos(yaw), math.sin(yaw)
    return {"position": list(position), "rotation": [c, 0, s, 0, 1, 0, -s, 0, c]}


class _World:
    def __init__(self, nodes):
        self.instances = nodes
        classes = sorted({n["class_name"] for n in nodes.values()})
        self.schema = {"classes": [{"name": c} for c in classes]}
        indices = {c: i for i, c in enumerate(classes)}
        for n in nodes.values():
            n["class"] = indices[n.pop("class_name")]
        self.wire = {}
        self.creation = {}
        self.revision = 0
        self.actions = deque()

    def node(self, key):
        if isinstance(key, tuple):
            return self.wire.get(key)
        return self.instances.get(key)

    def avatar(self, user_id):
        return {"evidence": {"player_spawned": user_id == 42}, "player_key": "player",
                "character_key": "char", "root_key": "hrp"}


def _fixture():
    nodes = {
        "char": {"class_name": "Model", "parent": "workspace", "properties": {}},
        "workspace": {"class_name": "Workspace", "parent": None, "properties": {}},
        "hrp": {"class_name": "Part", "parent": "char", "properties": {"Name": b"HumanoidRootPart"}},
        "torso": {"class_name": "MeshPart", "parent": "char", "properties": {"Name": b"UpperTorso"}},
        "arm": {"class_name": "MeshPart", "parent": "char", "properties": {"Name": b"LeftUpperArm"}},
        "a_hrp": {"class_name": "Attachment", "parent": "hrp", "properties": {"CFrame": _frame((1, 2, 0), .2)}},
        "a_torso": {"class_name": "Attachment", "parent": "torso", "properties": {"CFrame": _frame((0, -1, 0), -.1)}},
        "a_torso2": {"class_name": "Attachment", "parent": "torso", "properties": {"CFrame": _frame((.2, .5, 0), .3)}},
        "a_arm": {"class_name": "Attachment", "parent": "arm", "properties": {"CFrame": _frame((0, -.7, .1), -.2)}},
        "j_torso": {"class_name": "AnimationConstraint", "parent": "char", "properties": {
            "Enabled": True}},
        "j_arm": {"class_name": "AnimationConstraint", "parent": "char", "properties": {
            "Enabled": True}},
        "hum": {"class_name": "Humanoid", "parent": "char", "properties": {}},
        "player": {"class_name": "Player", "parent": "players", "properties": {"UserId": 42, "Character": {"$ref": "char"}}},
        "players": {"class_name": "Players", "parent": "workspace", "properties": {}},
    }
    # World uses the wire representation for Instance references: (scope,index).
    ids = {key: (1, i+1) for i, key in enumerate(nodes)}
    nodes["j_torso"]["properties"].update(Attachment0=ids["a_hrp"], Attachment1=ids["a_torso"])
    nodes["j_arm"]["properties"].update(Attachment0=ids["a_torso2"], Attachment1=ids["a_arm"])
    nodes["char"]["properties"]["PrimaryPart"] = {"$ref": "hrp"}
    nodes["player"]["properties"]["Character"] = {"$ref": "char"}
    # Translate the synthetic instance references back to fixture keys via lookup shim.
    world = _World(nodes)
    world.wire = {ids[k]: world.instances[k] for k in world.instances}
    class _Instances(dict):
        def get(self, key, default=None):
            if isinstance(key, tuple):
                return world.wire.get(key, default)
            return super().get(key, default)
    world.instances = _Instances(world.instances)
    return world


class AnimationRigPoseTests(unittest.TestCase):
    def test_type27_property_selector_uses_its_own_axis_pair_numbering(self):
        from animation_rig_pose import _frame
        identity = (1., 0., 0., 0., 1., 0., 0., 0., 1.)
        self.assertEqual(_frame({"position": [0, 0, 0], "rotation_id": 2}, "property"),
                         ((0., 0., 0.), identity))
        # Property ID 3 maps directly to axis-pair code 2, while the old
        # erroneous property->physics-selector path treated it as physics ID 3.
        property3 = native_property_selector_to_matrix(3)
        self.assertEqual(property3, native_compact_selector_to_matrix(2))
        self.assertNotEqual(property3, native_compact_selector_to_matrix(3))

    def test_asymmetric_attachment_chain_composes_from_translated_yawed_root(self):
        world = _fixture()
        delta_torso = _frame((.3, .1, -.2), .25)
        delta_arm = _frame((.1, -.2, .4), -.45)
        sample = {"poses": {
            ("UpperTorso",): {**delta_torso, "weight": 1.0},
            ("UpperTorso", "LeftUpperArm"): {**delta_arm, "weight": 1.0},
        }}
        root = _frame((7, 3, -5), .8)
        got = evaluate_own_avatar(world, 42, root, sample)
        self.assertEqual(got["status"], "ok")
        self.assertEqual(got["resolved_bodyparts"], 2)
        self.assertEqual(got["poses"]["hrp"], (tuple(root["position"]), tuple(root["rotation"])))

        # Independently exercise the documented attachment-space formula.
        from animation_rig_pose import _compose, _frame as _rig_frame, _inverse
        a0 = _rig_frame(world.instances["a_hrp"]["properties"]["CFrame"], "a0")
        inv_a1 = _inverse(_rig_frame(world.instances["a_torso"]["properties"]["CFrame"], "a1"))
        expected_torso = _compose(_compose(_compose(got["poses"]["hrp"], a0), _rig_frame(delta_torso, "delta")), inv_a1)
        for actual, expected in zip(got["poses"]["torso"], expected_torso):
            for av, ev in zip(actual, expected):
                self.assertAlmostEqual(av, ev, places=8)

    def test_refuses_missing_received_constraint(self):
        world = _fixture()
        del world.instances["j_arm"]
        got = evaluate_own_avatar(world, 42, _frame(), {"poses": {
            ("UpperTorso",): _frame(),
            ("UpperTorso", "LeftUpperArm"): _frame(),
        }})
        self.assertEqual(got["status"], "received_animation_joint_missing")
        self.assertEqual(got["poses"], {})

    def test_ignores_non_own_avatar_request(self):
        got = evaluate_own_avatar(_fixture(), 7, _frame(), {"poses": {("UpperTorso",): _frame()}})
        self.assertEqual(got["status"], "own_character_unavailable")

    def test_bound_rig_reuses_topology_across_physics_root_updates(self):
        world = _fixture()
        rig = ReceivedAvatarRig(world, 42)
        self.assertTrue(rig.valid_for(world, 42))
        world.revision += 1
        world.actions.append({"sequence": world.revision, "kind": "set", "key": "hrp",
                              "fields": {"CFrame": _frame((1, 0, 0))}})
        self.assertTrue(rig.valid_for(world, 42))
        sample = {"poses": {("UpperTorso",): _frame()}}
        self.assertEqual(rig.evaluate(_frame((3, 1, 2), .6), sample)["status"], "ok")

    def test_bound_rig_invalidates_on_received_attachment_bind_change(self):
        world = _fixture()
        rig = ReceivedAvatarRig(world, 42)
        world.revision += 1
        world.actions.append({"sequence": world.revision, "kind": "set", "key": "a_hrp",
                              "fields": {"CFrame": _frame((99, 0, 0))}})
        self.assertFalse(rig.valid_for(world, 42))
        self.assertEqual(rig.evaluate(_frame(), {"poses": {("UpperTorso",): _frame()}})["status"],
                         "rig_topology_or_bind_changed")
        self.assertTrue(rig.refresh())

    def test_actual_owned_capture_has_received_animation_constraint_topology(self):
        root = Path(__file__).resolve().parents[2]
        report_path = root / "outputs/gamejoin/owned-animation-slot-probe3.json"
        last_join = root / "secrets/last-join.json"
        if not report_path.exists() or not last_join.exists():
            self.skipTest("private offline capture or last-join context is unavailable")
        # Keep the account identifier private: derive it locally, never display it.
        values = json.loads(last_join.read_text())
        serialized = json.dumps(values, ensure_ascii=True)
        matches = re.findall(r"[\\\"']?UserId[\\\"']?\s*[:=]\s*[\\\"']?(\d+)", serialized)
        if not matches:
            self.skipTest("last-join context has no user ID")
        from world_snapshot import replay_world
        world = None
        user_id = None
        for candidate in reversed(list(dict.fromkeys(matches))):
            try:
                world = replay_world(report_path, int(candidate))
                user_id = int(candidate)
                break
            except ValueError:
                continue
        if world is None:
            self.skipTest("last-join user IDs do not match the offline capture")
        avatar = world.avatar(user_id)
        char = avatar["character_key"]
        def inside(key):
            seen = set()
            node = world.instances.get(key)
            while node is not None and node.get("parent") is not None:
                parent = node.get("parent")
                if parent == char:
                    return True
                if parent in seen:
                    return False
                seen.add(parent)
                node = world.instances.get(parent)
            return False
        constraint_count = 0
        bound_count = 0
        joint_offset_match_count = 0
        joint_offset_errors = []
        for key, node in world.instances.items():
            if not inside(key) or world.schema["classes"][node["class"]]["name"] != "AnimationConstraint":
                continue
            constraint_count += 1
            props = node["properties"]
            a0 = props.get("Attachment0")
            a1 = props.get("Attachment1")
            if a0 is None or a1 is None:
                continue
            first, second = world.instances.get(a0), world.instances.get(a1)
            if (first is not None and second is not None
                    and "CFrame" in first["properties"] and "CFrame" in second["properties"]):
                bound_count += 1
                attachment1_parent = second.get("parent")
                child = world.instances.get(attachment1_parent)
                if (child is not None and child["properties"].get("HasJointOffset") is True
                        and isinstance(child["properties"].get("JointOffset"), (list, tuple))):
                    offset = child["properties"]["JointOffset"]
                    position = second["properties"]["CFrame"]["position"]
                    joint_offset_errors.append(math.dist(offset, position))
                    joint_offset_match_count += 1
        self.assertTrue(avatar["evidence"]["player_spawned"])
        self.assertGreater(constraint_count, 0)
        self.assertGreater(bound_count, 0)
        self.assertEqual(joint_offset_match_count, 15)
        self.assertLess(max(joint_offset_errors), 1e-7)
        clip_cache = Path.home() / ".cache/roblox-client-research/animation-poses/run.json"
        if clip_cache.exists():
            clip = json.loads(clip_cache.read_text())
            frames = clip.get("keyframes")
            if frames and frames[0].get("poses"):
                rig = ReceivedAvatarRig(world, user_id)
                root_cframe = avatar["root"]["properties"].get("CFrame")
                identity = [1., 0., 0., 0., 1., 0., 0., 0., 1.]
                neutral_sample = {tuple(pose["path"]): {
                    "position": [0., 0., 0.], "rotation": identity, "weight": 1.}
                    for pose in frames[0]["poses"]}
                neutral = rig.evaluate(root_cframe, {"poses": neutral_sample})
                self.assertEqual(neutral["status"], "ok")
                neutral_position_errors = []
                for keys in rig.part_by_name.values():
                    for key in keys:
                        expected = neutral["poses"].get(key)
                        received = world.instances[key]["properties"].get("CFrame")
                        if expected is None or not isinstance(received, dict):
                            continue
                        local_delta = _rotate(_transpose(expected[1]), tuple(
                            received["position"][i] - expected[0][i] for i in range(3)))
                        neutral_position_errors.append(math.dist(local_delta, (0., 0., 0.)))
                self.assertEqual(len(neutral_position_errors), 16)
                self.assertLess(max(neutral_position_errors), 1e-4)
                actual_sample = {tuple(pose["path"]): pose for pose in frames[0]["poses"]}
                evaluated = evaluate_own_avatar(
                    world, user_id, root_cframe,
                    {"poses": actual_sample})
                self.assertEqual(evaluated["status"], "ok")
                self.assertEqual(evaluated["rig_edges"], 15)
                self.assertEqual(evaluated["resolved_bodyparts"], 15)


if __name__ == "__main__":
    unittest.main()
