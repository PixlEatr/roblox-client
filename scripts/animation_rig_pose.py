"""Forward-kinematics for an explicitly received own-avatar rig and clip.

This module is intentionally data-only. It consumes the replicated World and
the real pose samples returned by animation_pose_sampler; it does not invent a
rig, modify World, execute Luau, or send anything to a Roblox server.

Pose rotations use the sampler's documented row-major 3x3 convention. A clip
pose is applied at the received attachment pivot:
    childWorld = parentWorld * Attachment0.CFrame * poseDelta * inverse(Attachment1.CFrame)
The root part is pinned to the caller-supplied physics CFrame.
"""
from __future__ import annotations

import math
from collections import defaultdict
from functools import lru_cache
import json
from pathlib import Path
from typing import Any


class RigPoseError(ValueError):
    """Raised when received topology or clip data cannot be evaluated safely."""


_BODY_NAMES = {
    "humanoidrootpart", "head", "torso", "leftarm", "rightarm", "leftleg", "rightleg",
    "lowertorso", "uppertorso", "leftupperarm", "leftlowerarm", "lefthand",
    "rightupperarm", "rightlowerarm", "righthand", "leftupperleg", "leftlowerleg",
    "leftfoot", "rightupperleg", "rightlowerleg", "rightfoot",
}
_ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def _constructors():
    return json.loads((_ROOT / "runtime/class-constructors-745.json").read_text())["classes"]


def _enabled(world, key, node):
    """Use only explicit Enabled or a complete fresh native-create default."""
    explicit = node["properties"].get("Enabled")
    if type(explicit) is bool:
        return explicit
    creation = world.creation.get(key, {})
    if not (creation.get("fresh") and creation.get("complete")):
        return False
    try:
        value = _constructors()[_class(world, node)]["properties"]["Enabled"]["value"]
    except (OSError, KeyError, TypeError, ValueError):
        return False
    return type(value) is bool and value


def _finite_vector(value: Any, count: int, label: str) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != count:
        raise RigPoseError(f"{label}_missing_or_invalid")
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in value):
        raise RigPoseError(f"{label}_nonfinite")
    return tuple(float(v) for v in value)


def _mul(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(sum(a[r*3+k] * b[k*3+c] for k in range(3))
                 for r in range(3) for c in range(3))


def _transpose(a: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(a[c*3+r] for r in range(3) for c in range(3))


def _rotate(r: tuple[float, ...], p: tuple[float, ...]) -> tuple[float, float, float]:
    # CFrame matrices are row-major and multiply column position vectors.
    return tuple(sum(r[row*3+c] * p[c] for c in range(3)) for row in range(3))


def _compose(a, b):
    rp = _rotate(a[1], b[0])
    return (tuple(a[0][i] + rp[i] for i in range(3)), _mul(a[1], b[1]))


def _inverse(a):
    rt = _transpose(a[1])
    p = _rotate(rt, tuple(-x for x in a[0]))
    return (p, rt)


def _quat_matrix(q):
    x, y, z, w = _finite_vector(q, 4, "rotation")
    n = math.sqrt(x*x+y*y+z*z+w*w)
    if n < 1e-12:
        raise RigPoseError("rotation_zero_quaternion")
    x, y, z, w = (v/n for v in (x, y, z, w))
    return (1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w),
            2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w),
            2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y))


def _matrix_quaternion(m):
    t = m[0] + m[4] + m[8]
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        q = ((m[7]-m[5])/s, (m[2]-m[6])/s, (m[3]-m[1])/s, s/4)
    elif m[0] > m[4] and m[0] > m[8]:
        s = math.sqrt(1+m[0]-m[4]-m[8])*2
        q = (s/4, (m[1]+m[3])/s, (m[2]+m[6])/s, (m[7]-m[5])/s)
    elif m[4] > m[8]:
        s = math.sqrt(1+m[4]-m[0]-m[8])*2
        q = ((m[1]+m[3])/s, s/4, (m[5]+m[7])/s, (m[2]-m[6])/s)
    else:
        s = math.sqrt(1+m[8]-m[0]-m[4])*2
        q = ((m[2]+m[6])/s, (m[5]+m[7])/s, s/4, (m[3]-m[1])/s)
    n = math.sqrt(sum(x*x for x in q))
    return tuple(x/n for x in q)


def _blend_delta(frame, weight):
    if weight == 1:
        return frame
    p, matrix = frame
    q = _matrix_quaternion(matrix)
    # Slerp identity to the sampled rotation and linearly blend translation.
    if q[3] < 0:
        q = tuple(-x for x in q)
    dot = max(-1., min(1., q[3]))
    if dot > .9995:
        blended = (q[0]*weight, q[1]*weight, q[2]*weight, 1+(q[3]-1)*weight)
        n = math.sqrt(sum(x*x for x in blended))
        blended = tuple(x/n for x in blended)
    else:
        theta = math.acos(dot)
        scale = math.sin(weight*theta)/math.sin(theta)
        blended = (q[0]*scale, q[1]*scale, q[2]*scale, math.cos(weight*theta))
    return (tuple(v*weight for v in p), _quat_matrix(blended))


def _frame(value: Any, label: str):
    """Decode a received CFrame or sampler pose, rejecting opaque rotations."""
    if isinstance(value, dict) and "$cframe" in value:
        value = value["$cframe"]
    if isinstance(value, dict):
        p = _finite_vector(value.get("position"), 3, label + "_position")
        if "rotation_matrix" in value:
            r = _finite_vector(value["rotation_matrix"], 9, label + "_rotation")
        elif "rotation" in value and isinstance(value["rotation"], (list, tuple)):
            r = _finite_vector(value["rotation"], 9, label + "_rotation")
        elif "quaternion" in value:
            r = _quat_matrix(value["quaternion"])
        elif value.get("rotation_id") in range(1, 37):
            # Type-27 property selectors use selector-1 axis-pair numbering,
            # distinct from the native physics compact-selector table.
            from physics_rotation import native_property_selector_to_matrix
            r = native_property_selector_to_matrix(value["rotation_id"])
        elif value.get("rotation_id") == 0 and isinstance(value.get("packed_rotation"), str):
            # Six-byte quaternion is the native compact CFrame representation.
            from physics_rotation import decode_native_quaternion
            try:
                q, _ = decode_native_quaternion(bytes.fromhex(value["packed_rotation"]))
            except (ValueError, TypeError):
                raise RigPoseError(label + "_rotation_unavailable") from None
            r = _quat_matrix(q)
        else:
            raise RigPoseError(label + "_rotation_unavailable")
        return p, r
    # Sampler sample convenience form: {'position': ..., 'rotation': ...}.
    raise RigPoseError(label + "_missing_or_invalid")


def _ref(value):
    if isinstance(value, tuple) and len(value) == 2:
        return value
    if isinstance(value, dict) and "$ref" in value:
        text = value["$ref"]
        if isinstance(text, str) and ":" in text:
            a, b = text.split(":", 1)
            if a.isdigit() and b.isdigit():
                return int(a), int(b)
        elif isinstance(text, str):
            return text
    return None


def _name(value: Any) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return str(value).casefold().replace(" ", "").replace("_", "")


def _class(world, instance):
    return world.schema["classes"][instance["class"]]["name"]


def _attachment_part(world, key, character_key, seen=None):
    seen = set() if seen is None else seen
    if key in seen:
        raise RigPoseError("attachment_ancestry_cycle")
    seen.add(key)
    node = world.instances.get(key)
    if node is None:
        raise RigPoseError("attachment_endpoint_unresolved")
    parent = node.get("parent")
    if parent == character_key:
        return None
    parent_node = world.instances.get(parent)
    if parent_node is None:
        raise RigPoseError("attachment_parent_unresolved")
    if _class(world, parent_node) in ("Part", "MeshPart"):
        return parent
    return _attachment_part(world, parent, character_key, seen)


def _descendant(world, key, ancestor):
    seen = set()
    node = world.instances.get(key)
    while node is not None and node.get("parent") is not None:
        parent = node.get("parent")
        if parent == ancestor:
            return True
        if parent in seen:
            raise RigPoseError("world_ancestry_cycle")
        seen.add(parent)
        node = world.instances.get(parent)
    return False


class ReceivedAvatarRig:
    """Compile one received avatar rig once, then evaluate samples cheaply.

    Construction does the only full-world pass. ``valid_for`` checks the owner
    identity directly and consumes only World.actions newer than its cursor; PV
    and ordinary part CFrame updates do not invalidate bind topology. Any
    relevant edit or action-journal overrun makes this compiled rig stale.
    Call ``refresh`` (or construct another instance) after a stale result.
    """

    RELEVANT_CREATE_CLASSES = frozenset(("Part", "MeshPart", "Attachment", "AnimationConstraint"))
    WATCHED_FIELDS = {
        "Player": frozenset(("UserId", "Character")),
        "Model": frozenset(("Parent", "PrimaryPart")),
        "Part": frozenset(("Parent", "Name")),
        "MeshPart": frozenset(("Parent", "Name")),
        "Attachment": frozenset(("Parent", "Name", "CFrame", "Position", "Orientation", "Axis", "SecondaryAxis")),
        "AnimationConstraint": frozenset(("Parent", "Attachment0", "Attachment1", "Enabled")),
    }

    def __init__(self, world, user_id: int):
        self.world = world
        self.user_id = user_id
        self.valid = False
        self.error = "invalid_user_id"
        self.cursor = getattr(world, "revision", 0)
        self.player_key = self.character_key = self.root_key = None
        self.part_by_name = defaultdict(list)
        self.binds = {}
        self.watched = set()
        self.watched_ancestors = set()
        self.subtree = set()
        self._compile()

    def _compile(self):
        if type(self.user_id) is not int or self.user_id <= 0:
            return
        try:
            avatar = self.world.avatar(self.user_id)
            if not avatar.get("evidence", {}).get("player_spawned"):
                raise RigPoseError("own_character_unavailable")
            self.player_key = avatar["player_key"]
            self.character_key = avatar["character_key"]
            self.root_key = avatar["root_key"]
            children = defaultdict(list)
            for key, node in self.world.instances.items():
                children[node.get("parent")].append(key)
            pending, seen = [self.character_key], set()
            while pending:
                key = pending.pop()
                if key in seen:
                    raise RigPoseError("world_ancestry_cycle")
                seen.add(key)
                node = self.world.instances.get(key)
                if node is None:
                    raise RigPoseError("character_node_missing")
                pending.extend(children.get(key, ()))
            self.subtree = seen
            nodes = {key: self.world.instances[key] for key in seen}
            key_by_node = {id(node): key for key, node in nodes.items()}
            self.watched.update((self.player_key, self.character_key, self.root_key))
            for key, node in nodes.items():
                cls = _class(self.world, node)
                if cls == "Attachment":
                    self.watched.add(key)
                if cls == "AnimationConstraint":
                    self.watched.add(key)
                if cls in ("Part", "MeshPart"):
                    name = _name(node["properties"].get("Name", b""))
                    if name in _BODY_NAMES:
                        self.part_by_name[name].append(key)
                        self.watched.add(key)

            for key, node in nodes.items():
                if _class(self.world, node) != "AnimationConstraint" or not _enabled(self.world, key, node):
                    continue
                props = node["properties"]
                a0_key, a1_key = _ref(props.get("Attachment0")), _ref(props.get("Attachment1"))
                if a0_key is None or a1_key is None:
                    continue
                p0 = _attachment_part(self.world, a0_key, self.character_key)
                p1 = _attachment_part(self.world, a1_key, self.character_key)
                if p0 is None or p1 is None or p0 == p1:
                    continue
                node0, node1 = self.world.instances.get(a0_key), self.world.instances.get(a1_key)
                if (node0 is None or node1 is None or id(node0) not in key_by_node
                        or id(node1) not in key_by_node):
                    continue
                at0 = node0["properties"].get("CFrame")
                at1 = node1["properties"].get("CFrame")
                if at0 is None or at1 is None:
                    continue
                try:
                    frame0 = _frame(at0, "attachment0")
                    inverse1 = _inverse(_frame(at1, "attachment1"))
                except RigPoseError:
                    continue
                pair = frozenset((p0, p1))
                if pair in self.binds:
                    raise RigPoseError("ambiguous_animation_constraint")
                self.binds[pair] = (p0, p1, frame0, inverse1)
                self.watched.update((key_by_node[id(node0)], key_by_node[id(node1)]))
            self.watched_ancestors = set(self.watched)
            for watched_key in tuple(self.watched):
                node = self.world.instances.get(watched_key)
                seen_ancestors = set()
                while node is not None:
                    parent = node.get("parent")
                    if parent is None or parent == self.character_key:
                        break
                    if parent in seen_ancestors:
                        raise RigPoseError("world_ancestry_cycle")
                    seen_ancestors.add(parent)
                    self.watched_ancestors.add(parent)
                    node = self.world.instances.get(parent)
            component = {}
            def find(part):
                component.setdefault(part, part)
                while component[part] != part:
                    component[part] = component[component[part]]
                    part = component[part]
                return part
            for p0, p1, _, _ in self.binds.values():
                r0, r1 = find(p0), find(p1)
                if r0 == r1:
                    raise RigPoseError("animation_joint_topology_cycle")
                component[r1] = r0
            self.cursor = getattr(self.world, "revision", self.cursor)
            self.error = "ok"
            self.valid = True
        except RigPoseError as error:
            self.error = str(error)

    def refresh(self, world=None, user_id=None):
        """Recompile after relevant replication invalidates this cached rig."""
        if world is not None:
            self.world = world
        if user_id is not None:
            self.user_id = user_id
        self.valid = False
        self.part_by_name = defaultdict(list)
        self.binds = {}
        self.watched = set()
        self.watched_ancestors = set()
        self.subtree = set()
        self._compile()
        return self.valid

    def valid_for(self, world, user_id: int) -> bool:
        if not self.valid or world is not self.world or user_id != self.user_id:
            return False
        player = world.instances.get(self.player_key)
        character = world.instances.get(self.character_key)
        if (player is None or character is None
                or player["properties"].get("UserId") != self.user_id
                or _ref(player["properties"].get("Character")) != self.character_key
                or _ref(character["properties"].get("PrimaryPart")) != self.root_key
                or world.instances.get(self.root_key) is None):
            self.valid = False
            self.error = "own_character_or_root_changed"
            return False
        actions = world.actions
        if actions:
            if self.cursor < actions[0].get("sequence", 0) - 1:
                self.valid = False
                self.error = "rig_change_journal_overrun"
                return False
            for action in reversed(actions):
                if action.get("sequence", 0) <= self.cursor:
                    break
                key = action.get("key")
                kind = action.get("kind")
                if kind == "remove" and key in self.watched_ancestors:
                    self.valid = False
                    self.error = "rig_node_removed"
                    return False
                if kind == "create":
                    created = action.get("instance", {})
                    try:
                        cls = _class(world, created)
                    except (KeyError, TypeError, IndexError):
                        cls = None
                    if cls in self.RELEVANT_CREATE_CLASSES and key not in self.watched:
                        parent = created.get("parent")
                        if parent in self.subtree or _descendant(world, key, self.character_key):
                            self.valid = False
                            self.error = "rig_node_added"
                            return False
                if key in self.watched and kind == "set":
                    cls = _class(world, world.instances.get(key, {"class": -1})) if key in world.instances else None
                    fields = action.get("fields", {})
                    if cls in self.WATCHED_FIELDS and self.WATCHED_FIELDS[cls].intersection(fields):
                        self.valid = False
                        self.error = "rig_topology_or_bind_changed"
                        return False
            self.cursor = getattr(world, "revision", self.cursor)
        return True

    def evaluate(self, root_cframe: Any, pose_sample: dict[str, Any]) -> dict[str, Any]:
        if not self.valid_for(self.world, self.user_id):
            return {"status": self.error, "poses": {}, "resolved_bodyparts": 0}
        try:
            root = _frame(root_cframe, "physics_root")
            poses = pose_sample.get("poses") if isinstance(pose_sample, dict) else None
            if not isinstance(poses, dict) or not poses:
                raise RigPoseError("clip_poses_missing")
            path_parts, path_parent, path_frames = {}, {}, {}
            for path, sample in poses.items():
                if not isinstance(path, (tuple, list)) or not path:
                    raise RigPoseError("invalid_pose_path")
                path = tuple(path)
                if len(path) > 32 or any(not isinstance(s, str) for s in path):
                    raise RigPoseError("invalid_pose_path")
                part_ids = []
                for segment in path:
                    matches = self.part_by_name.get(_name(segment), [])
                    if len(matches) != 1:
                        raise RigPoseError("pose_bodypart_missing" if not matches else "pose_bodypart_ambiguous")
                    part_ids.append(matches[0])
                if len(set(part_ids)) != len(part_ids):
                    raise RigPoseError("pose_path_cycle")
                if path in path_parts:
                    raise RigPoseError("duplicate_pose_path")
                if part_ids[-1] in path_parts.values():
                    raise RigPoseError("ambiguous_pose_bodypart")
                delta = _frame(sample, "pose")
                weight = sample.get("weight", 1.)
                if type(weight) not in (int, float) or not math.isfinite(weight) or not 0 <= weight <= 1:
                    raise RigPoseError("invalid_pose_weight")
                path_parts[path] = part_ids[-1]
                path_parent[path] = (path[:-1], part_ids[-2] if len(part_ids) > 1 else self.root_key)
                path_frames[path] = delta
            result, pending = {self.root_key: root}, list(poses)
            while pending:
                progress = False
                for path in pending[:]:
                    target = path_parts[path]
                    parent_path, parent_part = path_parent[path]
                    if parent_part not in result:
                        if parent_path and parent_path not in path_parts:
                            raise RigPoseError("pose_parent_missing")
                        continue
                    if target == self.root_key:
                        result[self.root_key] = root
                    else:
                        joint = self.binds.get(frozenset((parent_part, target)))
                        if joint is None:
                            raise RigPoseError("received_animation_joint_missing")
                        j0, _, frame0, inverse1 = joint
                        if j0 != parent_part:
                            frame0, inverse1 = _inverse(inverse1), _inverse(frame0)
                        delta = _blend_delta(path_frames[path], poses[path].get("weight", 1.))
                        result[target] = _compose(_compose(_compose(result[parent_part], frame0), delta), inverse1)
                    pending.remove(path)
                    progress = True
                if not progress:
                    raise RigPoseError("pose_hierarchy_cycle_or_unrooted")
            return {"status": "ok", "poses": result, "resolved_bodyparts": len(result)-1,
                    "rig_edges": len(self.binds), "root_fixed": True}
        except RigPoseError as error:
            return {"status": str(error), "poses": {}, "resolved_bodyparts": 0}


def evaluate_own_avatar(world, user_id: int, root_cframe: Any, pose_sample: dict[str, Any]) -> dict[str, Any]:
    """Compatibility one-shot wrapper; steady-state callers should reuse ReceivedAvatarRig."""
    return ReceivedAvatarRig(world, user_id).evaluate(root_cframe, pose_sample)
