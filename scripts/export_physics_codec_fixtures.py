#!/usr/bin/env python3
"""Export deterministic synthetic codec fixtures and verify production composition."""
import json
import math
from pathlib import Path

import hashlib
import physics_packet
from physics_packet import (
    RotationField, TransformHeader, decode_transform_component,
    encode_transform_component,
)
from physics_rotation import encode_native_quaternion
from physics_velocity import encode_native_short_velocity
from physics_wire import position as encode_position, velocity as encode_velocity


def _up_to_sign_close(a, b, tolerance=2e-5):
    return min(max(abs(x - y) for x, y in zip(a, b)),
               max(abs(x + y) for x, y in zip(a, b))) <= tolerance


def main():
    root = Path(__file__).resolve().parent.parent
    packet_module = Path(physics_packet.__file__).resolve()
    expected_packet_module = (root / 'scripts/physics_packet.py').resolve()
    if packet_module != expected_packet_module:
        raise RuntimeError(f'Unexpected physics_packet module: {packet_module}')
    provenance = json.loads((root / 'outputs/provenance.json').read_text())
    specs = (
        {
            'selector': 31,
            'position': (1.25, -2.5, 3.75),
            'quaternion_native_order': (0.2, -0.3, 0.4, 0.842614977),
            'linear_velocity': (0.001, -0.25, 10.0),
            'angular_velocity': (0.0001, 0.0, 0.0),
        },
        {
            'selector': 0,
            'position': (16384.0, -0.5, 2.0),
            'quaternion_native_order': (-0.9, 0.1, 0.2, 0.3),
            'linear_velocity': (100.0, -200.0, 300.0),
            'angular_velocity': (-0.002, 0.004, 0.0),
        },
    )
    cases = []
    for spec in specs:
        selector = spec['selector']
        header = TransformHeader(selector, False, False, False, selector)
        rotation = RotationField(selector, 'native-quaternion', spec['quaternion_native_order'])

        # Independently compose the production codec outputs in the order
        # visible in 0x104D4EF84. This intentionally does not call the packet
        # encoder for the expected bytes.
        parts = {
            'header': bytes((selector,)),
            'position': encode_position(spec['position']),
            'quaternion': encode_native_quaternion(spec['quaternion_native_order']),
            'linear_velocity': encode_velocity(spec['linear_velocity']),
            'angular_velocity': encode_velocity(spec['angular_velocity']),
        }
        expected = b''.join(parts.values())
        actual = encode_transform_component(
            header=header,
            position_value=spec['position'],
            rotation=rotation,
            linear_velocity=spec['linear_velocity'],
            angular_velocity=spec['angular_velocity'],
            velocity_mode='compact',
        )
        if actual != expected:
            raise AssertionError('Production transform bytes differ from independently assembled fields')

        decoded = decode_transform_component(actual, velocity_mode='compact')
        if decoded.consumed != len(actual):
            raise AssertionError('Production transform decoder did not consume the complete component')
        if decoded.header.raw != selector or decoded.rotation.selector != selector:
            raise AssertionError('Production transform decoder changed the selector/header')
        if not _up_to_sign_close(decoded.rotation.value, spec['quaternion_native_order']):
            # The encoder normalizes the input and quantizes to 15 bits. Compare
            # against its normalized form, up to quaternion sign.
            q = spec['quaternion_native_order']
            n = math.sqrt(sum(x*x for x in q))
            if not _up_to_sign_close(decoded.rotation.value, tuple(x/n for x in q), 0.0002):
                raise AssertionError('Production transform quaternion roundtrip exceeds quantization tolerance')

        cases.append({
            'selector': selector,
            'source': {key: list(value) if isinstance(value, tuple) else value
                       for key, value in spec.items()},
            'encoded_fields_hex': {key: value.hex() for key, value in parts.items()},
            'independently_composed_hex': expected.hex(),
            'production_transform_hex': actual.hex(),
            'byte_composition_matches': True,
            'production_decode_consumed_all_bytes': True,
        })

    short_vectors = ((0.0, 0.0, 0.0), (0.001, 0.0, 0.0), (-100.0, 25.5, 0.2))
    shorts = [{'source': list(vector), 'encoded_hex': encode_native_short_velocity(vector).hex()}
              for vector in short_vectors]
    report = {
        'synthetic_only': True,
        'binary_sha256': provenance['sha256'],
        'production_module': 'scripts/physics_packet.py',
        'production_module_sha256': hashlib.sha256(packet_module.read_bytes()).hexdigest(),
        'codec_routines': {
            'position_encoder': '0x104CBE7B4',
            'compact_5_byte_velocity_encoder': '0x104CBF25C',
            'short_3_byte_velocity_encoder': '0x104CBF4AC',
            'compact_quaternion_encoder': '0x104CBEC4C',
            'compact_quaternion_decoder': '0x104CBEE10',
            'transform_component': '0x104D4EF84',
        },
        'transform_fixtures': cases,
        'short_velocity_fixtures': shorts,
        'live_transport_or_server_authority_verified': False,
    }
    output = root / 'outputs/physics-final/native-codec-fixtures.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
