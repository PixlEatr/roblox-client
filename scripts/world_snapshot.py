"""Turn the current world into something the Luau worker can read."""
import base64
import json
import math
from pathlib import Path

from world_replication import World
from world_schema import parse_schema


ROOT = Path(__file__).resolve().parents[1]
MAX_NODES = 20000
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024


def instance_id(key):
    return None if key is None else f'{key[0]}:{key[1]}'


def _encode(value, type_id=None, depth=0):
    if depth > 16:
        raise ValueError('Snapshot property nesting exceeds limit')
    if type_id in (28, 69):
        return {'$ref': instance_id(value)} if value is not None else None
    if isinstance(value, bytes):
        try:
            return value.decode('utf-8')
        except UnicodeDecodeError:
            return {'$bytes_hex': value.hex()}
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value): raise ValueError('Nonfinite snapshot property')
        return value
    if type_id == 19 and isinstance(value, list) and len(value) == 3:
        return {'$vector3': value}
    if type_id in (27, 51, 57) and isinstance(value, dict):
        return {'$cframe': _encode(value, depth=depth+1)}
    if isinstance(value, tuple) and len(value) == 2 and all(type(v) is int for v in value):
        return {'$ref': instance_id(value)}
    if isinstance(value, (list, tuple)):
        return [_encode(item, depth=depth+1) for item in value]
    if isinstance(value, dict):
        return {str(k): _encode(v, depth=depth+1) for k, v in value.items()}
    raise ValueError('Unsupported snapshot property type')


def make_world_snapshot(world, user_id, *, job_id=None):
    if len(world.instances) > MAX_NODES-1:
        raise ValueError('World snapshot node limit exceeded')
    parents = json.loads((ROOT/'runtime/class-parents-745.json').read_text())['parents']
    class_parents = {name: parent for name, parent in parents.items() if isinstance(parent, str)}
    nodes = [{'id': 'game', 'class_name': 'DataModel', 'name': 'Game',
              'parent': None, 'properties': {}, 'origins': {}}]
    if job_id is not None:
        nodes[0]['properties']['JobId'] = job_id
        nodes[0]['origins']['JobId'] = 'issued_session'
    services = {}
    for key, instance in world.instances.items():
        ci = instance['class']
        cls = world.schema['classes'][ci]
        types = {world.schema['properties'][pi]['name']: world.schema['properties'][pi]['type']
                 for pi in cls.get('properties', [])}
        properties, origins = {}, {}
        for name, value in instance['properties'].items():
            # Reading received script bodies is a separate engine capability;
            # never turn arbitrary server source into this program's source.
            if name in ('Source', 'Bytecode', 'ScriptBytecode'):
                continue
            properties[name] = _encode(value, types.get(name))
            origins[name] = 'received'
        creation = world.creation.get(key, {})
        if (cls['name'] in ('Part', 'MeshPart') and 'Anchored' not in properties
                and all(creation.get(flag) for flag in ('fresh', 'complete', 'ordinary'))):
            # Native factory-before-lists proof and independent 0.745 Studio
            # defaults: only this fully decoded, fresh creation path is covered.
            properties['Anchored'] = False
            origins['Anchored'] = 'class_default'
        if 'Position' not in properties:
            frame = instance['properties'].get('CFrame') or instance['properties'].get('ReplicationPV')
            if isinstance(frame, dict) and 'position' in frame:
                properties['Position'] = {'$vector3': frame['position']}
                origins['Position'] = 'derived_received'
        parent = instance_id(instance.get('parent'))
        if key in world.top_containers:
            parent = 'game'
            services[cls['name']] = instance_id(key)
        raw_name = instance['properties'].get('Name', cls['name'])
        name = raw_name.decode('utf-8', 'replace') if isinstance(raw_name, bytes) else str(raw_name)
        nodes.append({'id': instance_id(key), 'class_name': cls['name'], 'name': name,
                      'parent': parent, 'properties': properties, 'origins': origins})
    player = world.avatar(user_id)['player_key']
    snapshot = {'version': 1, 'local_player': instance_id(player), 'services': services,
                'nodes': nodes, 'class_parents': class_parents, 'defaults': {},
                'default_origins': {}, 'sequence': sum(world.items.values())}
    if len(json.dumps(snapshot, ensure_ascii=True, allow_nan=False).encode()) > MAX_SNAPSHOT_BYTES:
        raise ValueError('World snapshot exceeds 8 MiB')
    return snapshot


def replay_world(report_path, user_id):
    """Load a local capture without invoking a server or running received code."""
    report = json.loads(Path(report_path).read_text())
    capture = (ROOT/report['private_capture']).resolve()
    if not capture.is_relative_to((ROOT/'secrets').resolve()):
        raise ValueError('Capture must remain inside secrets')
    world = None
    for row in json.loads(capture.read_text()):
        if row.get('direction') != 'received' or row.get('application') != 4 or row.get('channel') != 1:
            continue
        payload = base64.b64decode(row['data_base64'], validate=True)
        if payload[:1] == b'\x97': world = World(parse_schema(payload))
        elif payload[:1] == b'\x81':
            if world is None: raise ValueError('Globals before schema')
            world.globals(payload)
        elif payload[:1] == b'\x83':
            if world is None: raise ValueError('World packet before schema')
            world.packet(payload)
    if world is None or not world.avatar(user_id)['evidence']['player_spawned']:
        raise ValueError('Capture lacks the selected player character')
    return world
