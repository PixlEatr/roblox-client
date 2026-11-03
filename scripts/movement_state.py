"""Read movement readiness from decoded replication without confusing local state with server evidence."""
import math


def _class_name(world, instance):
    if not isinstance(instance, dict):
        return None
    index = instance.get('class')
    classes = world.schema.get('classes', [])
    if type(index) is not int or not 0 <= index < len(classes):
        return None
    name = classes[index].get('name')
    return name if isinstance(name, str) else None


def _valid_vector(value):
    return (isinstance(value, (list, tuple)) and len(value) == 3
            and all(type(v) in (int, float) and math.isfinite(v) for v in value))


def inspect_movement_state(world, user_id):
    """Return sanitized readiness/evidence separately from private world handles.

    `world` is a decoded scripts.world_replication.World. No local Luau override
    or intended movement is accepted as a received pose or server ownership.
    """
    avatar = world.avatar(user_id)
    evidence = avatar['evidence']
    player = avatar['player']
    character = world.instances.get(avatar['character_key']) if avatar['character_key'] is not None else None
    root = avatar['root']
    humanoid = avatar['humanoid']
    root_properties = root.get('properties', {}) if root else {}
    received_anchored = root_properties.get('Anchored')
    anchored_explicit = type(received_anchored) is bool

    pose_name = None
    pose = None
    if root:
        versions = world.property_versions.get(avatar['root_key'], {})
        candidates = [(name, root_properties.get(name)) for name in ('CFrame', 'ReplicationPV')]
        candidates = [(name, value) for name, value in candidates
                      if isinstance(value, dict) and _valid_vector(value.get('position'))]
        if candidates:
            # Receipt revisions resolve the case where both replication fields
            # exist; use the same ordering as the source snapshot exporter.
            pose_name, selected = max(candidates, key=lambda item: versions.get(item[0], 0))
            pose = tuple(float(component) for component in selected['position'])

    reasons = []
    if not evidence.get('own_player_parented_to_players'):
        reasons.append('own_player_not_confirmed_under_players')
    if not evidence.get('character_model_resolved'):
        reasons.append('character_model_unresolved')
    if not evidence.get('humanoid_present'):
        reasons.append('humanoid_missing')
    if not evidence.get('root_part_resolved'):
        reasons.append('root_part_unresolved')
    if not evidence.get('character_in_workspace'):
        reasons.append('character_not_in_workspace')
    if not anchored_explicit:
        reasons.append('anchored_not_explicitly_received')
    elif received_anchored:
        reasons.append('root_is_anchored')
    if pose is None:
        reasons.append('received_pose_unavailable')

    locally_ready = not any(reason in reasons for reason in (
        'own_player_not_confirmed_under_players', 'character_model_unresolved',
        'humanoid_missing', 'root_part_unresolved', 'character_not_in_workspace',
        'anchored_not_explicitly_received', 'root_is_anchored', 'received_pose_unavailable'))
    server_walk_confirmed = False  # No server ownership/accepted movement evidence in World.
    public = {
        'local_controller_ready': locally_ready,
        'explicitly_unanchored': anchored_explicit and received_anchored is False,
        'anchored_origin': 'received' if anchored_explicit else 'unavailable',
        'received_pose_available': pose is not None,
        'received_pose_field': pose_name,
        'server_ownership': 'unknown',
        'server_walk_confirmed': server_walk_confirmed,
        'reasons': reasons,
    }
    private = {
        'player_key': avatar['player_key'],
        'player': player,
        'character_key': avatar['character_key'],
        'character': character,
        'root_key': avatar['root_key'],
        'root': root,
        'humanoid': humanoid,
        'received_pose': pose,
    }
    return {'public': public, 'private': private}
