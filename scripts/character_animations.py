"""Resolve received locomotion references with an incremental world index."""
from collections import defaultdict

from animation_assets import animation_asset_id, AnimationAssetError

_DESCRIPTION_FIELDS = {
    'idle': 'IdleAnimation', 'walk': 'WalkAnimation', 'run': 'RunAnimation',
    'jump': 'JumpAnimation', 'fall': 'FallAnimation', 'climb': 'ClimbAnimation',
    'swim': 'SwimAnimation',
}
_ANIMATE_ROLES = frozenset((*_DESCRIPTION_FIELDS, 'swimidle', 'sit'))
_CACHE_ATTR = '_locomotion_animation_catalog_v1'


def _text(value):
    return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value


def _class_names(world):
    return [entry.get('name') for entry in world.schema.get('classes', ())]


def _new_index(world):
    parents, children, classes, player_users = {}, defaultdict(set), {}, {}
    for key, node in world.instances.items():
        parent = node.get('parent')
        parents[key] = parent
        children[parent].add(key)
        class_id = node.get('class')
        classes[key] = class_id
        if type(class_id) is int and 0 <= class_id < len(world.schema['classes']) and world.schema['classes'][class_id].get('name') == 'Player':
            player_users[key] = node.get('properties', {}).get('UserId')
    return {'cursor': world.revision, 'parents': parents, 'children': children,
            'classes': classes, 'player_users': player_users, 'cached': {},
            'instances': world.instances, 'schema': world.schema}


def _class_name(index, key):
    class_id = index['classes'].get(key)
    return index['class_names'][class_id] if type(class_id) is int and 0 <= class_id < len(index['class_names']) else None


def _descendants(index, root):
    found, stack = set(), [root]
    while stack:
        key = stack.pop()
        if key in found:
            continue
        found.add(key)
        stack.extend(index['children'].get(key, ()))
    return found


def _advance(world, index):
    actions = world.actions
    if (index['instances'] is not world.instances or index['schema'] is not world.schema
            or (actions and index['cursor'] < actions[0].get('sequence', 0) - 1)):
        index = _new_index(world)
    else:
        relevant = set()
        pending = []
        for action in reversed(actions):
            if action.get('sequence', 0) <= index['cursor']:
                break
            pending.append(action)
        for action in reversed(pending):
            sequence = action.get('sequence', 0)
            if sequence <= index['cursor']:
                continue
            key, kind = action.get('key'), action.get('kind')
            if kind == 'create':
                node = action.get('instance', {})
                parent, class_id = node.get('parent'), node.get('class')
                index['parents'][key], index['classes'][key] = parent, class_id
                index['children'][parent].add(key)
                if _class_name(index, key) == 'Player':
                    index['player_users'][key] = node.get('properties', {}).get('UserId')
                if any(parent in entry['members'] for entry in index['cached'].values()):
                    relevant.add(key)
            elif kind == 'remove':
                members = _descendants(index, key)
                if any(members & entry['members'] for entry in index['cached'].values()):
                    relevant.update(members)
                parent = index['parents'].get(key)
                index['children'].get(parent, set()).discard(key)
                for member in members:
                    index['parents'].pop(member, None)
                    index['classes'].pop(member, None)
                    index['player_users'].pop(member, None)
                    index['children'].pop(member, None)
            elif kind == 'set':
                fields = action.get('fields', {})
                name = _class_name(index, key)
                if 'Parent' in fields:
                    old_parent = index['parents'].get(key)
                    new_parent = fields['Parent']
                    index['children'].get(old_parent, set()).discard(key)
                    index['children'][new_parent].add(key)
                    index['parents'][key] = new_parent
                    relevant.add(key)
                if name == 'Player' and 'UserId' in fields:
                    index['player_users'][key] = fields['UserId']
                relevant_fields = ({'Name', 'AnimationId', 'AnimationContent', 'Value'}
                                   | set(_DESCRIPTION_FIELDS.values()))
                field_names = set(fields)
                if ('Name' in field_names or (name == 'Animation' and {'AnimationId', 'AnimationContent'} & field_names)
                        or (name == 'NumberValue' and 'Value' in field_names)
                        or (name == 'HumanoidDescription' and set(_DESCRIPTION_FIELDS.values()) & field_names)):
                    relevant.add(key)
            index['cursor'] = sequence
        if relevant:
            for user_id, entry in tuple(index['cached'].items()):
                if relevant & entry['members'] or any(index['parents'].get(k) in entry['members'] for k in relevant):
                    del index['cached'][user_id]
        index['cursor'] = world.revision
    index['class_names'] = _class_names(world)
    return index


def _player_character(world, index, user_id):
    players = [key for key, value in index['player_users'].items() if value == user_id and key in world.instances]
    if len(players) != 1:
        return None
    return world.instances[players[0]].get('properties', {}).get('Character')


def _resolve(world, index, character):
    empty = {'character_key': character, 'animator_key': None, 'references': {}, 'sources': {}}
    if character not in world.instances:
        return empty
    nodes = _descendants(index, character)
    humanoids = [k for k in nodes if _class_name(index, k) == 'Humanoid' and index['parents'].get(k) == character]
    animators = [k for k in nodes if _class_name(index, k) == 'Animator' and index['parents'].get(k) in humanoids]
    if len(humanoids) != 1 or len(animators) != 1:
        return {**empty, '_members': nodes}
    result = {**empty, 'animator_key': animators[0], '_members': nodes}
    animate = {k for k in nodes if _class_name(index, k) == 'LocalScript'
               and index['parents'].get(k) == character
               and _text(world.instances[k].get('properties', {}).get('Name')) == 'Animate'}
    weights = defaultdict(list)
    for key in nodes:
        node = world.instances.get(key)
        if node and _class_name(index, key) == 'NumberValue':
            props = node.get('properties', {})
            if _text(props.get('Name')) == 'Weight':
                weights[index['parents'].get(key)].append(props.get('Value'))
    candidates = defaultdict(list)
    for key in nodes:
        node = world.instances.get(key)
        if not node or _class_name(index, key) != 'Animation':
            continue
        parent = index['parents'].get(key)
        if not animate:
            continue
        role_folder = world.instances.get(parent, {})
        role = _text(role_folder.get('properties', {}).get('Name'))
        role = role.lower() if isinstance(role, str) else None
        if role not in _ANIMATE_ROLES:
            continue
        # Ensure the role folder is under the character's Animate script.
        ancestor, seen, under_animate = parent, set(), False
        while ancestor in nodes and ancestor not in seen:
            if ancestor in animate:
                under_animate = True
                break
            seen.add(ancestor)
            ancestor = index['parents'].get(ancestor)
        if not under_animate:
            continue
        props = node.get('properties', {})
        reference = _text(props.get('AnimationId') or props.get('AnimationContent'))
        try:
            reference = 'rbxassetid://' + animation_asset_id(reference)
        except AnimationAssetError:
            continue
        choices = weights.get(key, ())
        weight = choices[0] if len(choices) == 1 else 1
        if type(weight) not in (int, float) or not 0 < weight <= 1e6:
            weight = 1
        candidates[role].append((weight, reference))
    for role, choices in candidates.items():
        result['references'][role] = max(choices)[1]
        result['sources'][role] = 'received_animate_configuration'
    descriptions = [world.instances[k] for k in nodes if _class_name(index, k) == 'HumanoidDescription']
    if len(descriptions) == 1:
        for role, field in _DESCRIPTION_FIELDS.items():
            value = descriptions[0].get('properties', {}).get(field)
            if role not in result['references'] and type(value) is int and 0 < value < 2**64:
                result['references'][role] = 'rbxassetid://' + str(value)
                result['sources'][role] = 'received_humanoid_description'
    return result


def own_character_animations(world, user_id):
    """Return the received own-character animator and catalog, without world rescans."""
    empty = {'character_key': None, 'animator_key': None, 'references': {}, 'sources': {}}
    if world is None:
        return empty
    index = getattr(world, _CACHE_ATTR, None)
    if index is None:
        index = _new_index(world)
        index['class_names'] = _class_names(world)
    else:
        index = _advance(world, index)
    setattr(world, _CACHE_ATTR, index)
    character = _player_character(world, index, user_id)
    # A changed Character reference can precede its create action in this
    # receipt stream. Rebuild once for that new root, then cache it normally.
    if (character is not None and character in world.instances
            and (character not in index['classes']
                 or index['parents'].get(character) != world.instances[character].get('parent'))):
        index = _new_index(world)
        index['class_names'] = _class_names(world)
        setattr(world, _CACHE_ATTR, index)
    cached = index['cached'].get(user_id)
    if cached and cached['character'] == character and not (character is None or character not in world.instances):
        return cached['result']
    if character is None or character not in world.instances:
        result = {**empty, 'character_key': character}
        members = set()
    else:
        resolved = _resolve(world, index, character)
        members = resolved.pop('_members', set())
        result = resolved
    index['cached'][user_id] = {'character': character, 'members': members, 'result': result}
    return result


__all__ = ['own_character_animations']
