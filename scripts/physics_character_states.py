"""Conservative local Humanoid state labels and transition events.

Labels are predictions from supported local contact/intent inputs. They are
not received Humanoid state and do not reproduce Roblox's full state machine.
"""
from __future__ import annotations

import math

STATE_VALUES = {
    'FallingDown': 0, 'Ragdoll': 1, 'GettingUp': 2, 'Jumping': 3,
    'Swimming': 4, 'Freefall': 5, 'Flying': 6, 'Landed': 7,
    'Running': 8, 'RunningNoPhysics': 10, 'StrafingNoPhysics': 11,
    'Climbing': 12, 'Seated': 13, 'PlatformStanding': 14, 'Dead': 15,
    'Physics': 16, 'None': 18,
}


def classify_state(*, grounded, vertical_velocity, jumping=False,
                   swimming=False, climbing=False, seated=False, dead=False, platform_standing=False):
    """Return a Roblox state label for explicit modeled conditions.

    Swimming/climbing/seated are only selected when their caller has explicit
    volume/contact or seat link/property evidence. No spatial guess is made here.
    """
    for name, value in (('grounded', grounded), ('jumping', jumping),
                        ('swimming', swimming), ('climbing', climbing),
                        ('seated', seated), ('dead', dead), ('platform_standing',platform_standing)):
        if type(value) is not bool:
            raise ValueError(f'{name} must be a boolean')
    if isinstance(vertical_velocity, bool) or not isinstance(vertical_velocity, (int, float)):
        raise ValueError('vertical_velocity must be finite')
    if not math.isfinite(vertical_velocity):
        raise ValueError('vertical_velocity must be finite')
    name = ('Dead' if dead else 'PlatformStanding' if platform_standing else 'Seated' if seated else 'Swimming' if swimming
            else 'Climbing' if climbing else 'Jumping' if jumping else
            'Running' if grounded else 'Freefall')
    return {'value': STATE_VALUES[name], 'name': name,
            'source': 'local_physics_model',
            'model': 'supported_contact_subset'}


def transition_events(previous, current):
    """Return ordered state event names for one modeled state change.

    Landed is emitted as a transient edge when airborne contact becomes
    grounded. The stable state reported after that edge is Running.
    """
    if previous == current:
        return ()
    before = previous.get('name') if isinstance(previous, dict) else previous
    after = current.get('name') if isinstance(current, dict) else current
    if after == 'Running' and before in ('Freefall', 'Jumping'):
        return ('Landed', 'Running')
    return (after,) if isinstance(after, str) else ()


def state_transition_records(previous, current):
    """Return typed state records for StateChanged edges, suppressing unknown old state."""
    before = previous.get('name') if isinstance(previous, dict) else previous
    after = current.get('name') if isinstance(current, dict) else current
    if not isinstance(before, str) or not isinstance(after, str) or before == after:
        return []
    return [{'name': name, 'value': STATE_VALUES[name]}
            for name in transition_events(before, after) if name in STATE_VALUES]


def state_signal_events(previous, current):
    """Return modeled Humanoid boolean signal edges; there is no Landed signal."""
    before = previous.get('name') if isinstance(previous, dict) else previous
    after = current.get('name') if isinstance(current, dict) else current
    if not isinstance(before, str) or not isinstance(after, str) or before == after:
        return []
    events = []
    for state, signal in (('Jumping', 'Jumping'), ('Freefall', 'FreeFalling'), ('PlatformStanding','PlatformStanding')):
        was_active, is_active = before == state, after == state
        if was_active and not is_active:
            events.append({'name': signal, 'active': False})
        if is_active and not was_active:
            events.append({'name': signal, 'active': True})
    return events


def explicit_seat_evidence(humanoid_properties, nodes, *, humanoid_id=None,
                           humanoid_origins=None, character_part_ids=()):
    """Resolve seat state from received links, Occupant, joints, or Sit.

    No seat is selected by proximity. A bare local Sit overlay is not evidence;
    received Sit can label the character seated, but without a seat link it
    cannot provide a pose for physical seat following.
    """
    if not isinstance(humanoid_properties, dict) or not isinstance(nodes, dict):
        return None
    sit = humanoid_properties.get('Sit')
    ref = humanoid_properties.get('SeatPart')
    seat_id = ref.get('$ref') if isinstance(ref, dict) else None
    seat = nodes.get(seat_id)
    seat_part_received = (isinstance(humanoid_origins, dict) and
                          humanoid_origins.get('SeatPart') in ('received', 'derived_received'))
    if sit is True and seat_part_received and seat and seat.get('class_name') in ('Seat', 'VehicleSeat'):
        return {'id': seat_id, 'source': 'received_seat_link', 'position': None}
    part_ids = set(character_part_ids)
    if humanoid_id:
        for candidate in nodes.values():
            if candidate.get('class_name') not in ('Seat', 'VehicleSeat'):
                continue
            occupant = candidate.get('properties', {}).get('Occupant', {})
            occupant_origins = candidate.get('origins', {})
            if (isinstance(occupant, dict) and occupant.get('$ref') == humanoid_id
                    and isinstance(occupant_origins, dict)
                    and occupant_origins.get('Occupant') in ('received', 'derived_received')):
                return {'id': candidate.get('id'), 'source': 'received_seat_link', 'position': None}
        for joint in nodes.values():
            if joint.get('class_name') not in ('Weld', 'WeldConstraint', 'ManualWeld', 'Motor6D', 'Snap'):
                continue
            props, origins = joint.get('properties', {}), joint.get('origins', {})
            if not isinstance(props, dict) or not isinstance(origins, dict):
                continue
            if props.get('Enabled') is False:
                continue
            p0, p1 = props.get('Part0', {}), props.get('Part1', {})
            a = p0.get('$ref') if isinstance(p0, dict) else None
            b = p1.get('$ref') if isinstance(p1, dict) else None
            if (origins.get('Part0') not in ('received', 'derived_received') or
                    origins.get('Part1') not in ('received', 'derived_received')):
                continue
            for seat_key, body_key in ((a, b), (b, a)):
                linked_seat = nodes.get(seat_key)
                if (linked_seat and linked_seat.get('class_name') in ('Seat', 'VehicleSeat')
                        and body_key in part_ids):
                    return {'id': seat_key, 'source': 'received_seat_link', 'position': None}
    if sit is True and isinstance(humanoid_origins, dict) and humanoid_origins.get('Sit') in ('received', 'derived_received'):
        return {'id': None, 'source': 'received_sit_property', 'position': None}
    return None


def water_regions_from_voxel_materials(grid, material_ids, water_material_id, *, source_id=None):
    """Turn explicit decoded voxel material cells into water AABB regions.

    `grid` must expose origin, cell_size, and dimensions (as VoxelGrid does).
    `material_ids` is the parallel x-major/y/z material array from an actual
    Terrain material decoder. No water is inferred from occupancy alone.
    """
    try:
        if isinstance(grid, dict):
            origin_value, cell_value = grid.get('origin'), grid.get('cell_size')
            dimensions = grid.get('dimensions')
        else:
            origin_value, cell_value = grid.origin, grid.cell_size
            dimensions = grid.dimensions
        origin = tuple(float(v) for v in origin_value)
        cell = tuple(float(v) for v in cell_value)
        nx, ny, nz = dimensions
    except (AttributeError, TypeError, ValueError):
        raise ValueError('grid must provide voxel origin, cell size, and dimensions')
    if (len(origin) != 3 or len(cell) != 3 or any(not math.isfinite(v) for v in origin + cell)
            or any(v <= 0 for v in cell) or any(type(v) is not int or v <= 0 for v in (nx, ny, nz))):
        raise ValueError('invalid voxel grid geometry')
    if not isinstance(material_ids, (list, tuple)) or len(material_ids) != nx * ny * nz:
        raise ValueError('material_ids must match the voxel grid dimensions')
    regions = []
    for index, material in enumerate(material_ids):
        if material != water_material_id:
            continue
        ix, rem = divmod(index, ny * nz)
        iy, iz = divmod(rem, nz)
        lo = [origin[0] + ix * cell[0], origin[1] + iy * cell[1], origin[2] + iz * cell[2]]
        hi = [lo[i] + cell[i] for i in range(3)]
        regions.append({'id': source_id, 'source': 'received_geometry',
                        'min': lo, 'max': hi, 'medium': 'water'})
    return regions


def extract_environment(snapshot, humanoid_id=None):
    """Extract explicit ladder parts, seat links, and decoded water materials.

    Current replication has no Terrain material decoder, so ordinary snapshots
    return no water regions. Explicit decoded water metadata can use
    :func:`water_regions_from_voxel_materials`. TrussPart AABBs require received
    Size and Position and axis aligned CFrame data; bounds are not invented.
    Seat evidence requires SeatPart, Occupant, weld, or received Sit evidence.
    """
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('nodes'), list):
        raise ValueError('snapshot must contain a nodes list')
    nodes = {node.get('id'): node for node in snapshot['nodes'] if isinstance(node, dict)}
    ladders = []
    for node in nodes.values():
        if node.get('class_name') != 'TrussPart' or node.get('destroyed') is True:
            continue
        props, origins = node.get('properties', {}), node.get('origins', {})
        if not isinstance(props, dict) or not isinstance(origins, dict):
            continue
        size_raw = props.get('Size', {}).get('$vector3') if isinstance(props.get('Size'), dict) else None
        position_raw = props.get('Position', {}).get('$vector3') if isinstance(props.get('Position'), dict) else None
        frame = props.get('CFrame', {}).get('$cframe') if isinstance(props.get('CFrame'), dict) else None
        if (origins.get('Size') not in ('received', 'derived_received') or
                origins.get('Position') not in ('received', 'derived_received') or
                not isinstance(size_raw, (list, tuple)) or len(size_raw) != 3 or
                not isinstance(position_raw, (list, tuple)) or len(position_raw) != 3):
            continue
        if isinstance(frame, dict):
            rotation = frame.get('rotation')
            if rotation not in (None, [1, 0, 0, 0, 1, 0, 0, 0, 1]):
                continue
        try:
            size = tuple(float(v) for v in size_raw)
            pos = tuple(float(v) for v in position_raw)
        except (TypeError, ValueError):
            continue
        if any(not math.isfinite(v) for v in size + pos) or any(v <= 0 for v in size):
            continue
        half = tuple(v / 2 for v in size)
        ladders.append({'id': node.get('id'), 'source': 'received_geometry',
                        'min': [pos[i] - half[i] for i in range(3)],
                        'max': [pos[i] + half[i] for i in range(3)]})
    character_id = None
    player = nodes.get(snapshot.get('local_player'))
    if player:
        char = player.get('properties', {}).get('Character', {})
        character_id = char.get('$ref') if isinstance(char, dict) else None
    if humanoid_id is not None:
        candidate = nodes.get(humanoid_id)
        humanoid = candidate if candidate and candidate.get('class_name') == 'Humanoid' else None
    else:
        humanoid = None
        for candidate in nodes.values():
            if candidate.get('class_name') != 'Humanoid':
                continue
            parent = candidate.get('parent')
            seen = set()
            while parent and parent not in seen:
                if parent == character_id:
                    humanoid = candidate
                    break
                seen.add(parent)
                ancestor = nodes.get(parent)
                parent = ancestor.get('parent') if ancestor else None
            if humanoid:
                break
    character_parts = set()
    if character_id:
        for node in nodes.values():
            parent = node.get('parent')
            seen = set()
            while parent and parent not in seen:
                if parent == character_id:
                    if node.get('class_name') in ('Part', 'MeshPart', 'UnionOperation',
                                                  'TrussPart', 'Seat', 'VehicleSeat'):
                        character_parts.add(node.get('id'))
                    break
                seen.add(parent)
                ancestor = nodes.get(parent)
                parent = ancestor.get('parent') if ancestor else None
    seat = explicit_seat_evidence(
        humanoid.get('properties', {}) if humanoid else {}, nodes,
        humanoid_id=humanoid.get('id') if humanoid else None,
        humanoid_origins=humanoid.get('origins', {}) if humanoid else {},
        character_part_ids=character_parts)
    if seat and seat.get('id'):
        seat_node = nodes.get(seat['id'], {})
        props, origins = seat_node.get('properties', {}), seat_node.get('origins', {})
        frame = props.get('CFrame', {}).get('$cframe') if isinstance(props.get('CFrame'), dict) else None
        position = None
        if (isinstance(origins, dict) and origins.get('CFrame') in ('received', 'derived_received')
                and isinstance(frame, dict) and isinstance(frame.get('position'), (list, tuple))):
            position = frame['position']
        elif (isinstance(origins, dict) and origins.get('Position') in ('received', 'derived_received')
              and isinstance(props.get('Position'), dict)):
            position = props['Position'].get('$vector3')
        if isinstance(position, (list, tuple)) and len(position) == 3:
            try:
                position = [float(v) for v in position]
                if all(math.isfinite(v) for v in position):
                    seat['position'] = position
            except (TypeError, ValueError):
                pass
    water_regions = []
    terrain = snapshot.get('decoded_terrain_materials')
    if (isinstance(terrain, dict) and terrain.get('source') == 'received_geometry'
            and terrain.get('materials_origin') in ('received', 'decoded_received')):
        try:
            water_regions = water_regions_from_voxel_materials(
                terrain, terrain.get('materials'), terrain.get('water_material_id'),
                source_id=terrain.get('id'))
        except (TypeError, ValueError):
            water_regions = []
    return {'water_regions': water_regions, 'ladder_regions': ladders, 'seat': seat,
            'water_supported': bool(water_regions),
            'water_reason': None if water_regions else 'terrain_water_occupancy_unavailable'}
