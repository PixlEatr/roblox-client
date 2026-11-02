"""Incremental, data-only world model. Never executes replicated scripts."""
import collections
import hashlib
import math

from wire_reader import Reader, WireError, decompress_stream
from replication_ping import read_ping


class World:
    def __init__(self, schema):
        self.schema = schema
        self.instances = {}
        self.shared = {hashlib.blake2b(b'', digest_size=16).digest(): b''}
        self.items = collections.Counter()
        self.context = None
        self.tags = []
        self.guid_batches = []
        self.regions = []
        self.last_region = None
        self.received_regions = set()
        # Event args can have player data, so keep them local.
        # Public reports only need the structure.
        self.events = collections.deque(maxlen=4096)
        self.pings = collections.deque(maxlen=256)
        self.client_scope = None
        self.global_flags = None
        self.top_containers = {}
        # Separate from explicit properties: enables faithful, provenance-aware
        # class defaults only after a fresh native creation record completes.
        self.creation = {}

    def globals(self, payload):
        """Globals/top containers for capability set 0x100000.

        Native 104ca5c4c and 104df2254. No optional simulation-step field is
        negotiated. Parse the full packet before committing any state.
        """
        r = Reader(payload)
        if r.u8() != 129: raise WireError('Not a globals packet')
        flags = [r.u8() for _ in range(7)]
        if any(v > 1 for v in flags): raise WireError('Invalid globals boolean')
        scope = r.varuint()
        if scope == 0: raise WireError('Invalid client scope')
        containers = {}
        for _ in range(r.count(1000)):
            key, ci, first, second = self.guid(r), r.u16(), r.u8(), r.u8()
            if key is None or key in containers or ci >= len(self.schema['classes']) or max(first, second) > 1:
                raise WireError('Invalid top container')
            containers[key] = {'class': ci, 'flags': [first, second]}
        r.finish()
        for key, item in containers.items():
            if key in self.instances and self.instances[key]['class'] != item['class']:
                raise WireError('Top container class mismatch')
        self.client_scope, self.global_flags, self.top_containers = scope, flags, containers
        for key, item in containers.items():
            self.instances.setdefault(key, {'class': item['class'], 'properties': {}, 'parent': None})

    def guid(self, r):
        scope = r.varuint()
        return (scope, r.u32()) if scope else None

    def remove_tree(self, key):
        if key is None: return
        pending = {key}
        while pending:
            children = {k for k, v in self.instances.items() if v.get('parent') in pending}
            for k in pending:
                self.instances.pop(k, None)
                self.creation.pop(k, None)
            pending = children

    def avatar(self, user_id):
        """Return evidence plus private references; callers publish evidence only."""
        def cls(instance):
            return self.schema['classes'][instance['class']]['name'] if instance else None
        player_key = next((k for k, v in self.instances.items() if cls(v) == 'Player'
                          and v['properties'].get('UserId') == user_id), None)
        player = self.instances.get(player_key)
        character_key = player['properties'].get('Character') if player else None
        character = self.instances.get(character_key)
        root_key = character['properties'].get('PrimaryPart') if character else None
        root = self.instances.get(root_key)
        humanoid = next((v for v in self.instances.values() if v.get('parent') == character_key
                         and cls(v) == 'Humanoid'), None) if character else None
        position = None
        if root:
            for name in ('ReplicationPV', 'CFrame'):
                value = root['properties'].get(name)
                if isinstance(value, dict) and 'position' in value:
                    position = value['position']
                    break
        evidence = {
            'game_account_accepted': player is not None,
            'own_player_parented_to_players': bool(player and cls(self.instances.get(player.get('parent'))) == 'Players'),
            'character_reference_received': character_key is not None,
            'character_model_resolved': cls(character) == 'Model',
            'character_in_workspace': bool(character and cls(self.instances.get(character.get('parent'))) == 'Workspace'),
            'humanoid_present': humanoid is not None,
            'root_part_resolved': bool(root and cls(root) in ('Part', 'MeshPart')
                                      and root.get('parent') == character_key
                                      and root['properties'].get('Name') == b'HumanoidRootPart'),
            'root_position_finite': bool(isinstance(position, list) and len(position) == 3
                                         and all(isinstance(v, (int, float)) and math.isfinite(v) for v in position)),
            'health_explicitly_replicated': bool(humanoid and 'Health' in humanoid['properties']),
        }
        evidence['player_spawned'] = all(evidence[k] for k in (
            'game_account_accepted', 'character_model_resolved', 'character_in_workspace',
            'humanoid_present', 'root_part_resolved', 'root_position_finite'))
        if humanoid and (not math.isfinite(humanoid['properties'].get('Health', 1))
                         or humanoid['properties'].get('Health', 1) <= 0):
            evidence['player_spawned'] = False
        covered = False
        if evidence['root_position_finite']:
            coordinate = tuple(math.floor(v / 64) for v in position)
            covered = coordinate in self.received_regions and any(
                'radius' in region and region['radius'] >= 1
                and max(abs(x-y) for x, y in zip(coordinate, region['coordinate'])) < region['radius']
                for region in self.regions)
        evidence['spawn_region_received'] = covered
        workspace = self.instances.get(character.get('parent')) if character else next(
            (v for v in self.instances.values() if cls(v) == 'Workspace'), None)
        streaming = workspace['properties'].get('StreamingEnabled') if cls(workspace) == 'Workspace' else None
        evidence['workspace_streaming_enabled'] = streaming
        evidence['initial_world_received'] = 13 in self.tags
        evidence['spawn_world_received'] = covered or (streaming is False and evidence['initial_world_received'])
        evidence['root_unanchored_by_server'] = bool(root and root['properties'].get('Anchored') is False)
        evidence['server_gameplay_paused'] = player['properties'].get('GameplayPaused') if player else None
        evidence['movement_ready'] = (evidence['player_spawned'] and evidence['spawn_world_received']
                                      and evidence['root_unanchored_by_server'])
        return {'evidence': evidence, 'root_key': root_key, 'root': root,
                'character_key': character_key, 'humanoid': humanoid, 'position': position,
                'player_key': player_key, 'player': player}

    @staticmethod
    def compact_position(r):
        def signed32(value):
            value &= 0xffffffff
            return value - (1 << 32) if value & 0x80000000 else value
        b = r.u8()
        sx, sy, sz = bool(b & 4), bool(b & 2), bool(b & 1)
        if b <= 0x27:
            n = r.u32()
            xyz = [signed32((2*n) | (sx<<31)) >> 21,
                   signed32(((n<<11)&0x7fe00000) | (sy<<31)) >> 21,
                   signed32((sz<<31) | ((n&1023)<<21)) >> 21]
            divisor = 1023.0
        elif b <= 0x57:
            xyz = [signed32((s<<31) | (r.u16()<<15)) >> 15 for s in (sx, sy, sz)]
            divisor = 65535.0
        else:
            n, m = r.u32(), r.u32()
            xyz = [signed32((((b>>2)<<32)|n)>>1) >> 10,
                   signed32((sy<<31) | ((((n&2047)<<10)|(m>>21))<<10)) >> 10,
                   signed32((sz<<31) | ((m&0x1fffff)<<10)) >> 10]
            divisor = 2097151.0
        return [math.ldexp(v/divisor, b>>3) for v in xyz]

    def shared_entry(self, r):
        key, marker = r.take(16), r.u8()
        if marker == 0:
            value = r.take(r.count(16 * 1024 * 1024))
            # Native labels retain MD5 terminology, but the current wire hash
            # is BLAKE2b-128 (also verified against every captured entry).
            if hashlib.blake2b(value, digest_size=16).digest() != key:
                raise WireError('Shared string digest mismatch')
            self.shared[key] = value
        elif marker != 1 or key not in self.shared:
            raise WireError('Unknown shared string marker/reference')
        return key

    def content_id(self, r, compressed):
        if not compressed: return r.take(r.count(16 * 1024 * 1024))
        marker = r.u8()
        index = marker >> 1
        if index >= len(self.schema['strings']): raise WireError('Invalid content prefix index')
        prefix = self.schema['strings'][index].encode()
        suffix = str(r.signed(64)).encode() if marker & 1 else r.take(r.count(16 * 1024 * 1024))
        return prefix + suffix

    def value(self, r, p, deferred, depth=0, compressed=False):
        if depth > 16: raise WireError('Value nesting exceeds limit')
        t = p['type']
        self.context = {'property': p.get('name'), 'type': t, 'offset': r.pos}
        if t in (2, 8): return r.take(r.count(16 * 1024 * 1024))
        if t == 33: return self.content_id(r, compressed)
        if t in (0, 3): return None
        if t in (29, 30):
            return [self.variant(r, deferred, depth+1, compressed) for _ in range(r.count(10000))]
        if t in (31, 32):
            return [(r.take(r.count()), self.variant(r, deferred, depth+1, compressed)) for _ in range(r.count(10000))]
        if t == 7: return r.varuint()
        if t == 18: return r.u16()
        if t == 9:
            value = r.u8()
            if value > 1: raise WireError('Invalid boolean')
            return bool(value)
        if t == 10: return r.signed(32)
        if t == 11: return r.f32()
        if t == 12: return r.f64()
        if t == 13: return [r.f32(), r.number('i')]
        if t == 14: return [r.f32(), r.number('i'), r.f32(), r.number('i')]
        if t in (21, 37): return [r.f32(), r.f32()]
        if t == 40: return [r.f32() for _ in range(4)]
        if t in (19, 22, 36): return [r.f32(), r.f32(), r.f32()]
        if t == 20: return list(r.take(3))
        if t == 27:
            position = [r.f32(), r.f32(), r.f32()]
            rotation = r.u8()
            return {'position': position, 'rotation_id': rotation,
                    'packed_rotation': r.take(6).hex() if rotation == 0 else None}
        if t == 41:
            flag = r.u8()
            return [r.f32() for _ in range(6 if flag >= 2 else 5)] if flag & 1 else None
        if t == 51:
            flag = r.u8()
            if not flag & 128: return None
            rotation = r.take(6).hex() if flag == 128 else flag & 127
            return {'rotation': rotation, 'position': [r.f32(), r.f32(), r.f32()]}
        if t == 54:
            return [r.u16(), r.u8(), r.take(r.u32()), r.take(r.u32())]
        if t == 61:
            marker = r.varuint()
            if marker == 0: return None
            if marker == 1: return self.content_id(r, compressed)
            raise WireError('Unsupported content marker %d' % marker)
        if t in (28, 69): return self.guid(r)
        if t == 34: return r.varuint()
        if t in (35, 38):
            count = r.u32()
            if count > 20: raise WireError('Sequence too long')
            return [[r.f32() for _ in range(3 if t == 35 else 5)] for _ in range(count)]
        if t == 57:
            flags = r.u8()
            pos = [0., 0., 0.] if flags & 128 else self.compact_position(r)
            rot = flags & 31
            rotation = [r.f32() for _ in range(7)] if rot == 31 else r.take(6).hex() if rot == 0 else rot
            velocities = []
            for mask in (64, 32):
                if flags & mask: velocities.append(None)
                else:
                    b = r.u8()
                    velocities.append([b, r.u32() if b else 0])
            return {'position': pos, 'rotation': rotation, 'packed_velocities': velocities}
        if t == 44: return r.signed(64)
        if t == 68: return [r.signed(64), r.varuint(), r.signed(64), r.signed(64)]
        if t in (52, 55): return r.u64()
        if t in (46, 47):
            key = r.take(16)
            if key not in self.shared: deferred.add(key)
            return {'shared': key.hex()}
        if t == 49:
            marker = r.u8()
            if marker & 128:
                index = marker & 127
                if index == 127: index += r.varuint()
                return self.schema['names'][index].encode()
            size = r.count() if marker == 127 else marker
            return r.take(size)
        raise WireError('Unimplemented property type %d' % t)

    def variant(self, r, deferred, depth, compressed=False):
        p = {'type': r.u8(), 'name': 'Event variant'}
        if p['type'] == 7: p['enum'] = r.u16()
        return self.value(r, p, deferred, depth, compressed)

    def property_list(self, r, instance, deferred, compressed=False):
        cls = self.schema['classes'][instance['class']]
        last = -1
        while True:
            index = r.u8()
            if index == 255: return
            if index <= last or index >= len(cls['properties']):
                raise WireError('Invalid property order/index')
            last = index
            p = self.schema['properties'][cls['properties'][index]]
            instance['properties'][p['name']] = self.value(r, p, deferred, compressed=compressed)

    def new_instance(self, r, lists=2, inline_shared=True, batch_deferred=None):
        key, ci = self.guid(r), r.u16()
        if key is None or ci >= len(self.schema['classes']):
            raise WireError('Invalid instance header')
        fresh = key not in self.instances
        self.creation[key] = {'fresh': fresh, 'complete': False,
                              'property_lists': lists, 'ordinary': lists == 2 and inline_shared}
        instance = self.instances.setdefault(key, {'class': ci, 'properties': {}})
        if instance['class'] != ci: raise WireError('Instance changed class')
        deferred = set() if batch_deferred is None else batch_deferred
        for index in range(lists):
            self.property_list(r, instance, deferred, compressed=(lists == 2 and index == 0))
        if inline_shared:
            while deferred:
                deferred.discard(self.shared_entry(r))
        elif deferred and batch_deferred is None:
            raise WireError('Cached instance references unknown shared string')
        instance['parent'] = self.guid(r)
        self.creation[key]['complete'] = True
        return instance

    def packet(self, payload):
        r = Reader(payload)
        if r.u8() != 131: raise WireError('Not a replication packet')
        while r.remaining:
            kind = r.u8()
            if kind == 0:
                r.finish()
                return
            self.items[kind] += 1
            if kind == 1:
                self.remove_tree(self.guid(r))
            elif kind == 2:
                self.new_instance(r)
            elif kind == 3:
                key, pi, versioned = self.guid(r), r.u16(), r.u8()
                if versioned > 1 or pi > self.schema['parent_property_id']:
                    raise WireError('Invalid property change header')
                instance = self.instances.get(key)
                deferred = set()
                if pi == self.schema['parent_property_id']:
                    value = self.guid(r)
                    if instance is not None: instance['parent'] = value
                else:
                    p = self.schema['properties'][pi]
                    value = self.value(r, p, deferred, compressed=True)
                    if instance is not None: instance['properties'][p['name']] = value
                while deferred: deferred.discard(self.shared_entry(r))
            elif kind == 19:
                key, parent = self.guid(r), self.guid(r)
                if key in self.instances: self.instances[key]['parent'] = parent
            elif kind == 20:
                wide = r.u8()
                if wide > 1: raise WireError('Invalid region coordinate width')
                coordinate = [r.number('i' if wide else 'b') for _ in range(3)]
                self.regions.append({'coordinate': coordinate, 'radius': r.number('i'), 'version': r.u32()})
            elif kind == 7:
                key, ei = self.guid(r), r.u16()
                if ei >= len(self.schema['events']): raise WireError('Invalid event ID')
                event = self.schema['events'][ei]
                deferred = set()
                arguments = [self.value(r, dict(arg, name='Event:' + event['name']), deferred, compressed=True)
                             for arg in event['arguments']]
                while deferred: deferred.discard(self.shared_entry(r))
                self.events.append({'key': key, 'event': ei, 'arguments': arguments})
            elif kind == 21:
                key = self.guid(r)
                # Only read the version byte when that capability is enabled.
                name, value = r.take(r.count()), r.take(r.count())
                if key in self.instances:
                    self.instances[key].setdefault('attributes', {})[name] = value
            elif kind in (5, 6):
                self.pings.append(read_ping(r, kind))
            elif kind == 22:
                subtype = r.u8()
                content = decompress_stream(r)
                if subtype == 1:
                    while content.remaining: self.shared_entry(content)
                elif subtype in (2, 3, 4):
                    count = r.u32()
                    if count > 100000: raise WireError('Too many cached instances')
                    offsets = {r.varuint(): r.varuint() for _ in range(r.count())}
                    for _ in range(count):
                        while content.pos in offsets: content.take(offsets[content.pos])
                        self.new_instance(content, lists=1, inline_shared=False)
                    while content.pos in offsets: content.take(offsets[content.pos])
                    content.finish()
                else:
                    raise WireError('Unsupported cached replication subtype %d' % subtype)
            elif kind in (11, 13):
                count = r.u32()
                if count > 100000: raise WireError('Too many streamed instances')
                if count:
                    content = decompress_stream(r)
                    deferred = set()
                    for _ in range(count):
                        self.new_instance(content, lists=1, inline_shared=False, batch_deferred=deferred)
                    while deferred: deferred.discard(self.shared_entry(content))
                    content.finish()
                if kind == 13:
                    for _ in range(100000):
                        flags = r.u8()
                        region = {'flags': flags}
                        if flags & ~8 == 0:
                            wide = r.u8()
                            if wide > 1: raise WireError('Invalid region coordinate width')
                            region['coordinate'] = [r.number('i' if wide else 'b') for _ in range(3)]
                        elif flags & ~8 in (1, 2, 3):
                            if self.last_region is None: raise WireError('Relative region before absolute region')
                            region['coordinate'] = list(self.last_region)
                            axis = {1: 1, 2: 2, 3: 0}[flags & ~8]
                            region['coordinate'][axis] += 1
                        elif flags & ~8 != 4:
                            raise WireError('Unsupported streamed region flag')
                        if 'coordinate' in region:
                            self.last_region = tuple(region['coordinate'])
                            self.received_regions.add(self.last_region)
                        self.regions.append(region)
                        if not flags & 8: break
                    else: raise WireError('Too many streamed regions')
            elif kind == 16:
                self.tags.append(r.number('i'))
            elif kind == 35:
                self.server_step = r.number('i')
            elif kind == 27:
                count = r.u32()
                if count > 100000: raise WireError('Too many batched instance references')
                keys = [self.guid(r) for _ in range(count)]
                self.guid_batches.append(keys)
                for key in keys: self.remove_tree(key)
            else:
                raise WireError('Unimplemented replication kind %d' % kind)
        raise WireError('Missing replication terminator')
