"""Bounded decoding of received AttributesReplicate property blobs.

The count/name/value layout matches captured property bytes and rbx-dom's
rbx_types/src/attributes/{reader,type_id}.rs. Native packet-21 writer
104d79204 calls 1048ef598 for one typed value, then length-prefixes that
serialized value. Its supported scalar/datatype cases match _value below.
"""
import math

from wire_reader import Reader, WireError


def _string(reader, limit=65536):
    size=reader.u32()
    if size>limit:raise WireError('Attribute string exceeds limit')
    return reader.take(size)


def _number(reader, width):
    value=reader.f32() if width==4 else reader.f64()
    if not math.isfinite(value):raise WireError('Nonfinite attribute datatype unsupported')
    return value


def _vector(reader, count):
    return [_number(reader,4) for _ in range(count)]


def _value(reader):
    kind=reader.u8()
    if kind==2:
        value=_string(reader)
        try:return value.decode('utf-8')
        except UnicodeDecodeError:return {'$bytes_hex':value.hex()}
    if kind==3:
        value=reader.u8()
        if value>1:raise WireError('Invalid attribute boolean')
        return bool(value)
    if kind==4:return reader.number('i')
    if kind in (5,6):return _number(reader,4 if kind==5 else 8)
    if kind==9:return {'$udim':[_number(reader,4),reader.number('i')]}
    if kind==10:return {'$udim2':[{'$udim':[_number(reader,4),reader.number('i')]} for _ in range(2)]}
    if kind==14:return {'$brickcolor':reader.u32()}
    if kind in (15,16,17):
        tag,count={15:('$color3',3),16:('$vector2',2),17:('$vector3',3)}[kind]
        return {tag:_vector(reader,count)}
    if kind in (23,25):
        count=reader.u32()
        if not 2<=count<=20:raise WireError('Invalid attribute sequence count')
        points=[]
        for _ in range(count):
            envelope,time=_number(reader,4),_number(reader,4)
            if kind==23:points.append({'$number_keypoint':[time,_number(reader,4),envelope]})
            else:
                if envelope!=0:raise WireError('Unsupported color sequence envelope')
                points.append({'$color_keypoint':[time,{'$color3':_vector(reader,3)}]})
        return {'$number_sequence' if kind==23 else '$color_sequence':points}
    if kind==27:return {'$number_range':_vector(reader,2)}
    if kind==28:return {'$rect':[{'$vector2':_vector(reader,2)} for _ in range(2)]}
    raise WireError('Unsupported received attribute type %d'%kind)


def decode_attributes(data):
    if not isinstance(data,bytes) or len(data)>1024*1024:
        raise WireError('Invalid attribute blob')
    if not data:return {}
    reader=Reader(data);count=reader.u32()
    if count>1024:raise WireError('Attribute count exceeds limit')
    result={}
    for _ in range(count):
        name=_string(reader,100).decode('utf-8')
        if not name or '\0' in name or name in result:raise WireError('Invalid attribute name')
        result[name]=_value(reader)
    reader.finish()
    return result


def attribute_update(name, data):
    name=name.decode('utf-8')
    if not name or len(name.encode('utf-8'))>100 or '\0' in name:
        raise WireError('Invalid received attribute name')
    try:
        if not isinstance(data,bytes) or len(data)>1024*1024:
            raise WireError('Invalid attribute value blob')
        reader=Reader(data)
        # 101609d08's null reflection type has ID 0; 1048ed5d0 maps it to
        # serialization tag 1, and 1048ef598 writes no payload for that tag.
        if data[:1]==b'\x01':reader.u8();value=None
        else:value=_value(reader)
        reader.finish()
        return {'name':name,'known':True,'value':value}
    except (ValueError,TypeError):
        # Unsupported tags are unknown. They must invalidate an earlier
        # value, never replay it.
        return {'name':name,'known':False}


def attribute_snapshot(properties, updates=None, creation=None):
    result={}
    if 'AttributesReplicate' in properties:
        try:
            result={'attributes':decode_attributes(properties['AttributesReplicate']),
                    'attributes_known':True}
        except (ValueError,TypeError):
            # Preserve unknownness; never convert a malformed/unsupported blob
            # to a known empty map or retain stale values from the previous blob.
            result={'attributes':{},'attributes_known':False}
    elif creation and all(creation.get(flag) for flag in ('fresh','complete')):
        # Both cached (one list) and ordinary (two lists) new-instance records
        # construct the class and apply supplied properties. A complete fresh
        # record without AttributesReplicate therefore starts with no assigned
        # attributes; item-21 values update that initial state. Incomplete or
        # reused nodes, and undecodable explicit blobs, remain unknown.
        result={'attributes':{},'attributes_known':True,
                'attributes_origin':'constructor_default'}
    for name,data in (updates or {}).items():
        update=attribute_update(name,data)
        result.setdefault('attributes',{});result.setdefault('attributes_known',False)
        if update['known']:
            if update['value'] is None and result['attributes_known']:
                result['attributes'].pop(update['name'],None)
            else:result['attributes'][update['name']]=update['value']
        else:
            result['attributes'].pop(update['name'],None);result['attributes_known']=False
    if updates and 'attributes_origin' in result:
        result['attributes_origin']='constructor_plus_received_updates'
    return result
