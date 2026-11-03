"""User-authored Luau execution with current replicated data and local state."""
import copy

from local_runtime import MAX_SOURCE_BYTES, run_source
from world_snapshot import make_world_snapshot


class ClientScriptRuntime:
    def __init__(self, world_provider, user_id, job_id=None):
        self.world_provider = world_provider
        self.user_id = user_id
        self.job_id = job_id
        self.effects = []
        self.executions = 0

    @staticmethod
    def apply_effects(snapshot, effects):
        nodes = {node['id']: node for node in snapshot['nodes']}
        for effect in effects:
            kind = effect.get('kind')
            if kind == 'create':
                node = copy.deepcopy(effect['node'])
                if node['id'] not in nodes: nodes[node['id']] = node
            elif kind == 'set' and effect.get('id') in nodes:
                node = nodes[effect['id']]
                name, value = effect['property'], copy.deepcopy(effect.get('value'))
                if name == 'Parent':
                    node['parent'] = value.get('$ref') if isinstance(value, dict) else None
                elif name == 'Name':
                    node['name'] = value
                    node['properties']['Name'] = value
                else:
                    node['properties'][name] = value
                node.setdefault('origins', {})[name] = 'local'
            elif kind == 'attribute' and effect.get('id') in nodes:
                node = nodes[effect['id']]
                node.setdefault('attributes_known', False)
                attributes = node.setdefault('attributes', {})
                if effect.get('value') is None and node.get('attributes_known', False):
                    attributes.pop(effect['name'], None)
                else:
                    attributes[effect['name']] = copy.deepcopy(effect.get('value'))
            elif kind == 'destroy':
                # Preserve tombstones so explicit references still resolve and
                # Parent remains locked across commands. The exact target list
                # was captured at destruction, not inferred from later server
                # hierarchy updates.
                for key in effect.get('ids', []):
                    if key in nodes:
                        nodes[key]['parent'] = None
                        nodes[key]['destroyed'] = True
                        nodes[key].setdefault('origins', {})['Parent'] = 'local'
        snapshot['nodes'] = list(nodes.values())
        return snapshot

    def execute(self, source, *, timeout_ms=1000):
        if not isinstance(source, str) or len(source.encode('utf-8')) > MAX_SOURCE_BYTES:
            raise ValueError('Source must be UTF-8 Luau text up to 100 MiB')
        world = self.world_provider()
        if world is None: raise ValueError('Client world is not ready')
        snapshot = make_world_snapshot(world, self.user_id, job_id=self.job_id)
        self.apply_effects(snapshot, self.effects)
        result = run_source(source, timeout_ms=timeout_ms, memory_mb=512, world_snapshot=snapshot)
        effects = result.get('mutations', [])
        if not isinstance(effects, list) or len(effects) + len(self.effects) > 10000:
            raise ValueError('Local mutation budget exceeded')
        self.effects.extend(copy.deepcopy(effects))
        self.executions += 1
        return {**result, 'execution': self.executions,
                'world_snapshot': True, 'mutations_replicated_to_server': False}
