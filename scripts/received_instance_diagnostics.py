"""Bounded, read-only inspection of one node in the current received world."""
from world_snapshot import _node_snapshot


def inspect_received(world, path, poses=None):
    if (not isinstance(path,list) or not 1<=len(path)<=16
            or any(not isinstance(p,str) or not p or len(p.encode('utf-8'))>256 for p in path)):
        raise ValueError('Expected a bounded service/child path')
    if world is None:raise ValueError('Received world is not ready')
    def cls(node):return world.schema['classes'][node['class']]['name']
    matches=[key for key in world.top_containers if cls(world.instances[key])==path[0]]
    if len(matches)!=1:raise ValueError('Received service is absent or ambiguous')
    key=matches[0]
    for name in path[1:]:
        matches=[k for k,n in world.instances.items() if n.get('parent')==key
                 and n['properties'].get('Name',cls(n).encode())==name.encode('utf-8')]
        if len(matches)!=1:raise ValueError('Received child is absent or ambiguous')
        key=matches[0]
    node=world.instances[key]
    # Incremental values are deliberately returned as raw bytes, with no
    # assumption that their format matches the full attribute-property blob.
    raw=[]
    for name,value in list(node.get('attributes',{}).items())[:64]:
        raw.append({'name_hex':name[:256].hex(),'value_hex':value[:4096].hex(),
                    'value_bytes':len(value),'truncated':len(value)>4096})
    return {'ok':True,'values':[],'node':_node_snapshot(world,key,node),
            'raw_incremental_attributes':raw,'world_revision':world.revision,
            'incremental_attribute_records_received':world.items[21],
            'authored_part_poses':poses.report() if poses is not None else None}
