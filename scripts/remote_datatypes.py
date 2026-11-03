"""Engine value variants for ordinary remote calls (native 104DAE900).

The layouts match World.value's property readers. Unknown tags stay errors;
they are never reduced to strings, tables, or clean-looking null values.
"""
import math
import struct

from game_wire import encode_unsigned
from physics_rotation import (native_matrix3x3_to_quaternion, encode_native_quaternion,
                              decode_native_quaternion, native_property_selector_to_matrix)


def components(value, count):
    if (not isinstance(value, list) or len(value) != count or
            any(type(v) not in (int, float) or not math.isfinite(v) for v in value)):
        raise ValueError('Invalid remote datatype components')
    return value


def integer(value, low, high):
    if (type(value) not in (int, float) or not math.isfinite(value) or
            int(value) != value or not low <= value <= high):
        raise ValueError('Invalid remote datatype integer')
    return int(value)


def tagged(value, name):
    if not isinstance(value, dict) or set(value) != {name}:
        raise ValueError('Invalid nested remote datatype')
    return value[name]


def remote_cframe(value):
    """Expand the property rotation form used by a received CFrame variant."""
    selector = value['rotation_id']
    if selector:
        rotation = native_property_selector_to_matrix(selector)
    else:
        raw = bytes.fromhex(value['packed_rotation'])
        if len(raw) != 6:
            raise ValueError('Invalid remote CFrame quaternion')
        (x,y,z,w), _ = decode_native_quaternion(raw)
        rotation = [1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w),
                    2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w),
                    2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]
    return {'$cframe': {'position': list(value['position']), 'rotation': list(rotation)}}


def engine_variant(value):
    if not isinstance(value, dict) or len(value) != 1:
        return None
    tag, data = next(iter(value.items()))
    floats = {'$color3': (19, 3), '$vector2': (21, 2),
              '$vector3': (22, 3), '$number_range': (37, 2)}
    try:
        if tag in floats:
            kind, count = floats[tag]
            return bytes([kind]) + struct.pack('<' + 'f'*count, *components(data, count))
        if tag == '$udim':
            scale, offset = components(data, 2)
            return b'\x0d' + struct.pack('<fi', scale, integer(offset, -2**31, 2**31-1))
        if tag == '$cframe':
            if not isinstance(data, dict) or set(data) != {'position','rotation'}:
                raise ValueError('Invalid remote CFrame')
            position = components(data['position'], 3)
            rotation = components(data['rotation'], 9)
            rows = [rotation[i:i+3] for i in (0,3,6)]
            if any(abs(sum(a*b for a,b in zip(rows[i],rows[j]))-(i==j)) > 1e-4
                   for i in range(3) for j in range(3)):
                raise ValueError('Remote CFrame requires a rotation matrix')
            a,b,c,d,e,f,g,h,i = rotation
            if abs(a*(e*i-f*h)-b*(d*i-f*g)+c*(d*h-e*g)-1) > 1e-4:
                raise ValueError('Remote CFrame requires a proper rotation')
            # 104F232F4: float32 position, property selector, optional packed
            # quaternion. Select exact axis bases; all other rotations use zero.
            for selector in range(1,37):
                try: basis = native_property_selector_to_matrix(selector)
                except ValueError: continue
                if list(basis) == rotation:
                    return b'\x1b' + struct.pack('<fffB', *position, selector)
            quaternion = native_matrix3x3_to_quaternion(rotation)
            return b'\x1b' + struct.pack('<fffB', *position, 0) + encode_native_quaternion(quaternion)
        if tag in ('$udim2', '$rect'):
            if not isinstance(data, list) or len(data) != 2:
                raise ValueError('Invalid paired remote datatype')
            inner = '$udim' if tag == '$udim2' else '$vector2'
            parts = [engine_variant({inner: tagged(item, inner)})[1:] for item in data]
            return bytes([14 if tag == '$udim2' else 40]) + b''.join(parts)
        if tag == '$brickcolor':
            return b'\x12' + struct.pack('<H', integer(data, 0, 65535))
        if tag == '$datetime':
            return b'\x30' + struct.pack('<q', integer(data, -17987443200000, 253402300799999))
        if tag == '$buffer_hex':
            if (not isinstance(data, str) or len(data) > 131072 or len(data) % 2 or
                    any(c not in '0123456789abcdefABCDEF' for c in data)):
                raise ValueError('Invalid remote buffer')
            raw = bytes.fromhex(data)
            # 104DA8D24: zero compressed size selects the raw-byte branch.
            return b'\x38\x00' + encode_unsigned(len(raw)) + raw
    except (OverflowError, struct.error) as error:
        raise ValueError('Remote datatype component outside wire range') from error
    return None
