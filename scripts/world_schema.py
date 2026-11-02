"""Decode network schema v3 (native reader 0x104d3d618)."""
from wire_reader import Reader, WireError, decompress_stream


def parse_schema(payload):
    outer = Reader(payload)
    if outer.u8() != 151:
        raise WireError("Not a network schema packet")
    r = decompress_stream(outer)
    outer.finish()
    version = r.varuint(64)
    if version != 3:
        raise WireError("Unsupported schema version")
    result = {'version': version, 'flags': r.u64(), 'enums': [], 'classes': [],
              'properties': [], 'events': [], 'strings': [''], 'names': [], 'lr_types': []}
    for i in range(r.count()):
        result['enums'].append({'id': i, 'name': r.string(), 'bits': r.u8()})
    classes, properties, events = r.count(), r.count(), r.count()
    for ci in range(classes):
        c = {'id': ci, 'name': r.string(), 'properties': [], 'events': [], 'isr_properties': []}
        for _ in range(r.count()):
            p = {'id': len(result['properties']), 'class': ci, 'name': r.string(), 'type': r.u8()}
            if p['type'] == 7:
                p['enum'] = r.u16()
                if p['enum'] != 65535 and p['enum'] >= len(result['enums']):
                    raise WireError("Invalid schema enum")
            elif p['type'] == 60:
                p['lr_type'] = r.varuint(64)
            result['properties'].append(p)
            c['properties'].append(p['id'])
        c['unknown_u16'] = r.u16()
        for _ in range(r.count()):
            e = {'id': len(result['events']), 'class': ci, 'name': r.string(), 'arguments': []}
            for _ in range(r.count(1024)):
                e['arguments'].append({'type': r.u8(), 'enum': r.u16()})
            result['events'].append(e)
            c['events'].append(e['id'])
        result['classes'].append(c)
    if len(result['properties']) != properties or len(result['events']) != events:
        raise WireError("Schema counts do not match")
    result['parent_property_id'] = properties
    if r.remaining:
        result['strings'].extend(r.string() for _ in range(r.count()))
    if r.remaining:
        result['names'] = [r.string() for _ in range(r.count())]
    if r.remaining:
        count = r.u32()
        if count > classes:
            raise WireError("Too many ISR classes")
        for _ in range(count):
            ci, count = r.u32(), r.u32()
            if ci >= classes or count > 128:
                raise WireError("Invalid ISR class/property count")
            props = [r.u32() for _ in range(count)]
            if any(p >= properties for p in props):
                raise WireError("Invalid ISR property reference")
            result['classes'][ci]['isr_properties'] = props
    if r.remaining:
        for _ in range(r.count()):
            entry = [r.varuint(64), r.varuint(64), r.varuint(64)]
            if entry[2]:
                entry.append(r.varuint(64))
            result['lr_types'].append(entry)
    r.finish()
    result['decoded_bytes'] = r.pos
    return result
