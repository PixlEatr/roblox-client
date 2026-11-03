#!/usr/bin/env python3
"""Independently inspect a USSI .rbxlx/.rbxmx or .rbxl/.rbxm export.

This reads the serialized artifact; it does not execute exported scripts.
Expectations are an optional JSON object containing an ``instances`` array.
Each item may specify ``path``, ``class``, and a ``properties`` mapping. Paths
are slash-separated instance names from the serialized root.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import ctypes.util
import json
import os
from pathlib import Path
import struct
import sys
import xml.etree.ElementTree as ET


MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_CHUNK_BYTES = 256 * 1024 * 1024
MAX_TOTAL_PLAIN_BYTES = 1024 * 1024 * 1024
MAX_INSTANCES = 1_000_000
MAX_CLASSES = 65_536
MAX_STRING_BYTES = 64 * 1024 * 1024
XML_EXTENSIONS = {".rbxlx", ".rbxmx", ".xml"}
BINARY_MAGIC = b"<roblox!\x89\xff\r\n\x1a\n\x00\x00"


class ExportError(ValueError):
    pass


def _u32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise ExportError("truncated 32-bit field")
    return struct.unpack_from("<I", data, offset)[0]


def _read_string(data: bytes, offset: int) -> tuple[str, int]:
    size = _u32(data, offset)
    start = offset + 4
    if size > MAX_STRING_BYTES or start + size > len(data):
        raise ExportError("invalid or truncated length-prefixed string")
    try:
        value = data[start:start + size].decode("utf-8")
    except UnicodeDecodeError as error:
        raise ExportError("length-prefixed string is not UTF-8") from error
    return value, start + size


def _find_codec(name: str) -> ctypes.CDLL:
    candidates = [ctypes.util.find_library(name)]
    # Homebrew's common macOS prefixes are not always on the process loader
    # search path, even when the corresponding command-line tools are present.
    for prefix in ("/opt/homebrew/lib", "/usr/local/lib",
                   f"/opt/homebrew/opt/{name}/lib", f"/usr/local/opt/{name}/lib"):
        candidates.append(str(Path(prefix) / f"lib{name}.dylib"))
    if name == "zstd":
        candidates += ["libzstd.dylib", "libzstd.so.1", "libzstd.so"]
    else:
        candidates += ["liblz4.dylib", "liblz4.so.1", "liblz4.so"]
    for candidate in candidates:
        if candidate:
            try:
                return ctypes.CDLL(candidate)
            except OSError:
                pass
    raise ExportError(f"{name} library is required to inspect compressed chunks")


def _decompress_zstd(payload: bytes, expected: int) -> bytes:
    lib = _find_codec("zstd")
    lib.ZSTD_decompress.argtypes = [ctypes.c_void_p, ctypes.c_size_t,
                                   ctypes.c_void_p, ctypes.c_size_t]
    lib.ZSTD_decompress.restype = ctypes.c_size_t
    lib.ZSTD_isError.argtypes = [ctypes.c_size_t]
    lib.ZSTD_isError.restype = ctypes.c_uint
    out = ctypes.create_string_buffer(expected)
    src = ctypes.create_string_buffer(payload)
    result = lib.ZSTD_decompress(out, expected, src, len(payload))
    if lib.ZSTD_isError(result) or result != expected:
        raise ExportError("invalid Zstandard chunk")
    return out.raw[:result]


def _decompress_lz4(payload: bytes, expected: int) -> bytes:
    lib = _find_codec("lz4")
    lib.LZ4_decompress_safe.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_int, ctypes.c_int]
    lib.LZ4_decompress_safe.restype = ctypes.c_int
    if len(payload) > 0x7fffffff or expected > 0x7fffffff:
        raise ExportError("LZ4 chunk exceeds decoder bounds")
    out = ctypes.create_string_buffer(expected)
    src = ctypes.create_string_buffer(payload)
    result = lib.LZ4_decompress_safe(src, out, len(payload), expected)
    if result != expected:
        raise ExportError("invalid LZ4 chunk")
    return out.raw[:result]


def _decode_ref_plane(data: bytes, count: int, offset: int) -> tuple[list[int], int]:
    size = count * 4
    if count > MAX_INSTANCES or offset + size > len(data):
        raise ExportError("truncated referent plane")
    result: list[int] = []
    previous = 0
    for i in range(count):
        encoded = int.from_bytes(bytes(data[offset + i + lane * count]
                                       for lane in range(4)), "big")
        delta = (encoded >> 1) ^ -(encoded & 1)
        value = delta if i == 0 else previous + delta
        if value < -1 or value > 0x7fffffff:
            raise ExportError("referent is out of range")
        result.append(value)
        previous = value
    return result, offset + size


def _read_u32_plane(data: bytes, count: int, offset: int) -> tuple[list[int], int]:
    size = count * 4
    if count > MAX_INSTANCES or offset + size > len(data):
        raise ExportError("truncated value plane")
    result = [int.from_bytes(bytes(data[offset + i + lane * count]
                                  for lane in range(4)), "big")
              for i in range(count)]
    return result, offset + size


def _unrot_float(value: int) -> float:
    bits = (value >> 1) | ((value & 1) << 31)
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def _parse_binary(data: bytes) -> dict:
    if len(data) < 32 or not data.startswith(BINARY_MAGIC):
        raise ExportError("unrecognized Roblox binary header")
    type_count, instance_count = _u32(data, 16), _u32(data, 20)
    if type_count > MAX_CLASSES or instance_count > MAX_INSTANCES:
        raise ExportError("binary header count exceeds validation limits")

    chunks: list[tuple[str, bytes]] = []
    cursor = 32
    total_plain = 0
    saw_end = False
    while cursor < len(data):
        if cursor + 16 > len(data):
            raise ExportError("truncated binary chunk header")
        name = data[cursor:cursor + 4].decode("ascii", "replace").rstrip("\x00")
        compressed, plain, _reserved = struct.unpack_from("<III", data, cursor + 4)
        stored = compressed or plain
        if plain > MAX_CHUNK_BYTES or stored > MAX_CHUNK_BYTES:
            raise ExportError(f"{name} chunk exceeds validation size limit")
        payload_start = cursor + 16
        payload_end = payload_start + stored
        if payload_end > len(data):
            raise ExportError(f"truncated {name} chunk")
        total_plain += plain
        if total_plain > MAX_TOTAL_PLAIN_BYTES:
            raise ExportError("expanded chunks exceed validation limit")
        payload = data[payload_start:payload_end]
        if compressed:
            # USSI defaults to Zstandard. Recognize LZ4 frames as well for
            # callers selecting that option. Executor lz4compress functions
            # often return raw LZ4 blocks, which LZ4_decompress_safe handles.
            if payload.startswith(b"\x28\xb5\x2f\xfd"):
                payload = _decompress_zstd(payload, plain)
            else:
                payload = _decompress_lz4(payload, plain)
        elif len(payload) != plain:
            raise ExportError(f"invalid uncompressed {name} chunk length")
        chunks.append((name, payload))
        cursor = payload_end
        if name == "END":
            if payload != b"</roblox>" or cursor != len(data):
                raise ExportError("malformed or non-final END chunk")
            saw_end = True
            break
    if not saw_end:
        raise ExportError("binary file is missing END chunk")

    classes: dict[int, dict] = {}
    ref_to_instance: dict[int, dict] = {}
    instances: list[dict] = []
    parents: dict[int, int] = {}
    saw_prnt = False
    for name, payload in chunks:
        if name == "INST":
            if len(payload) < 9:
                raise ExportError("short INST chunk")
            class_id = _u32(payload, 0)
            class_name, offset = _read_string(payload, 4)
            if offset + 5 > len(payload):
                raise ExportError("short INST metadata")
            is_service = payload[offset]
            count = _u32(payload, offset + 1)
            refs, end = _decode_ref_plane(payload, count, offset + 5)
            if is_service not in (0, 1):
                raise ExportError("invalid INST service flag")
            if is_service and end + count != len(payload):
                raise ExportError("invalid service INST payload length")
            if not is_service and end != len(payload):
                raise ExportError("unexpected INST payload bytes")
            if class_id in classes or class_name == "":
                raise ExportError("duplicate or empty binary class entry")
            nodes = [{"ref": ref, "class": class_name, "name": class_name,
                      "parent_ref": -1, "properties": {}} for ref in refs]
            if len(set(refs)) != len(refs) or any(ref < 0 for ref in refs):
                raise ExportError("duplicate or negative instance referent")
            record = {"id": class_id, "name": class_name, "refs": refs,
                      "instances": nodes}
            classes[class_id] = record
            instances.extend(nodes)
            for node in nodes:
                ref_to_instance[node["ref"]] = node
        elif name == "PROP":
            if len(payload) < 9:
                raise ExportError("short PROP chunk")
            class_id = _u32(payload, 0)
            if class_id not in classes:
                raise ExportError("PROP references an unknown class id")
            property_name, offset = _read_string(payload, 4)
            if offset >= len(payload):
                raise ExportError("PROP is missing type id")
            type_id = payload[offset]
            offset += 1
            refs = classes[class_id]["refs"]
            if any(property_name in node["properties"]
                   for node in classes[class_id]["instances"]):
                raise ExportError("duplicate binary property chunk")
            values: list[object]
            if type_id == 1:
                values = []
                for _ in refs:
                    value, offset = _read_string(payload, offset)
                    values.append(value)
            elif type_id == 2:
                if offset + len(refs) != len(payload):
                    raise ExportError("invalid bool PROP length")
                values = [bool(value) for value in payload[offset:offset + len(refs)]]
                offset += len(refs)
            elif type_id == 3:
                plane, offset = _read_u32_plane(payload, len(refs), offset)
                values = [((word >> 1) ^ -(word & 1)) for word in plane]
            elif type_id == 4:
                plane, offset = _read_u32_plane(payload, len(refs), offset)
                values = [_unrot_float(word) for word in plane]
            elif type_id == 5:
                if offset + 8 * len(refs) != len(payload):
                    raise ExportError("invalid double PROP length")
                values = list(struct.unpack_from("<" + "d" * len(refs), payload, offset))
                offset += 8 * len(refs)
            elif type_id == 14:
                planes = []
                for _ in range(3):
                    plane, offset = _read_u32_plane(payload, len(refs), offset)
                    planes.append([_unrot_float(word) for word in plane])
                values = [[planes[0][i], planes[1][i], planes[2][i]]
                          for i in range(len(refs))]
            elif type_id == 16:
                rotations = []
                for _ in refs:
                    if offset >= len(payload):
                        raise ExportError("truncated CFrame rotation id")
                    rotation_id = payload[offset]
                    offset += 1
                    if rotation_id == 0:
                        if offset + 36 > len(payload):
                            raise ExportError("truncated custom CFrame rotation")
                        rotation = list(struct.unpack_from("<9f", payload, offset))
                        offset += 36
                    else:
                        rotation = {"rotation_id": rotation_id}
                    rotations.append(rotation)
                planes = []
                for _ in range(3):
                    plane, offset = _read_u32_plane(payload, len(refs), offset)
                    planes.append([_unrot_float(word) for word in plane])
                values = [{"position": [planes[0][i], planes[1][i], planes[2][i]],
                           "rotation": rotations[i]} for i in range(len(refs))]
            elif type_id == 19:
                refs_values, offset = _decode_ref_plane(payload, len(refs), offset)
                values = [{"ref": value} if value != -1 else None for value in refs_values]
            elif type_id == 26:
                if offset + 3 * len(refs) != len(payload):
                    raise ExportError("invalid Color3uint8 PROP length")
                # Roblox XML stores Color3uint8 as an opaque ARGB integer.
                # Normalize binary's RGB bytes to the same representation.
                values = [0xff000000 | (payload[offset + 3 * i] << 16)
                          | (payload[offset + 3 * i + 1] << 8)
                          | payload[offset + 3 * i + 2]
                          for i in range(len(refs))]
                offset += 3 * len(refs)
            else:
                # The class and property remain visible in the report only
                # through the surrounding instance list. Other Roblox types
                # are skipped because their payload can be variable width.
                continue
            if offset != len(payload):
                raise ExportError(f"unexpected bytes after {property_name} PROP values")
            for node, value in zip(classes[class_id]["instances"], values):
                node["properties"][property_name] = value
                if property_name == "Name":
                    node["name"] = value
        elif name == "PRNT":
            if saw_prnt:
                raise ExportError("duplicate PRNT chunk")
            saw_prnt = True
            if len(payload) < 5 or payload[0] != 0:
                raise ExportError("invalid PRNT chunk header")
            count = _u32(payload, 1)
            object_refs, offset = _decode_ref_plane(payload, count, 5)
            parent_refs, offset = _decode_ref_plane(payload, count, offset)
            if offset != len(payload):
                raise ExportError("unexpected bytes after PRNT planes")
            if len(set(object_refs)) != len(object_refs):
                raise ExportError("duplicate PRNT object referent")
            parents.update(zip(object_refs, parent_refs))
    if len(instances) != instance_count or len(classes) != type_count:
        raise ExportError("binary header counts do not match INST chunks")
    if set(classes) != set(range(type_count)):
        raise ExportError("binary class ids are not contiguous")
    if len(parents) != instance_count or set(parents) != set(ref_to_instance):
        raise ExportError("PRNT graph does not cover the serialized instances")
    for ref, parent_ref in parents.items():
        if parent_ref != -1 and parent_ref not in ref_to_instance:
            raise ExportError("PRNT contains a dangling parent referent")
        ref_to_instance[ref]["parent_ref"] = parent_ref
    return {"format": "binary", "header_instance_count": instance_count,
            "header_class_count": type_count, "instances": instances,
            "chunks": [name for name, _ in chunks], "binary_classes": classes,
            "ref_to_instance": ref_to_instance}


def _parse_xml(data: bytes) -> dict:
    upper = data[:1_000_000].upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ExportError("XML DTD/entity declarations are not accepted")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise ExportError(f"invalid Roblox XML: {error}") from error
    if root.tag != "roblox" or root.attrib.get("version") not in ("4", "5"):
        raise ExportError("expected a Roblox XML document with version 4 or 5")
    refs: dict[str, dict] = {}
    instances: list[dict] = []

    def visit(parent_element: ET.Element, parent_path: str | None,
              parent_ref: str | None, root_ordinal: int) -> None:
        for ordinal, item in enumerate(parent_element.findall("Item")):
            class_name = item.attrib.get("class", "")
            referent = item.attrib.get("referent", "")
            if not class_name or not referent or referent in refs:
                raise ExportError("XML Item has empty/duplicate class or referent")
            prop_container = item.find("Properties")
            props: dict[str, object] = {}
            if prop_container is not None:
                for prop in prop_container:
                    prop_name = prop.attrib.get("name")
                    if not prop_name:
                        continue
                    value = prop.text or ""
                    if prop.tag == "Ref":
                        value = {"ref": value.strip()}
                    elif prop.tag == "bool":
                        if value.strip() not in ("true", "false", "1", "0"):
                            raise ExportError("invalid XML bool property")
                        value = value.strip() in ("true", "1")
                    elif prop.tag in ("int", "token", "int64", "Color3uint8"):
                        try:
                            value = int(value.strip())
                        except ValueError as error:
                            raise ExportError("invalid XML integer property") from error
                    elif prop.tag in ("float", "double"):
                        try:
                            value = float(value.strip())
                        except ValueError as error:
                            raise ExportError("invalid XML numeric property") from error
                    elif prop.tag in ("Vector3", "Vector2", "Vector3int16", "Vector2int16"):
                        names = ("X", "Y", "Z") if prop.tag in ("Vector3", "Vector3int16") else ("X", "Y")
                        try:
                            value = [float(prop.findtext(name, default="nan")) for name in names]
                        except ValueError as error:
                            raise ExportError("invalid XML vector property") from error
                        if any(not (component == component and abs(component) != float("inf")) for component in value):
                            raise ExportError("non-finite XML vector property")
                    elif prop.tag in ("CFrame", "CoordinateFrame"):
                        try:
                            position = [float(prop.findtext(axis, default="nan")) for axis in ("X", "Y", "Z")]
                            rotation_names = ("R00", "R01", "R02", "R10", "R11", "R12", "R20", "R21", "R22")
                            rotation = [float(prop.findtext(axis, default="nan")) for axis in rotation_names]
                            value = {"position": position, "rotation": rotation}
                        except ValueError as error:
                            raise ExportError("invalid XML CFrame property") from error
                        if any(not (component == component and abs(component) != float("inf"))
                               for component in position + rotation):
                            raise ExportError("non-finite XML CFrame property")
                    elif prop.tag in ("string", "ProtectedString", "Content"):
                        pass
                    elif prop.tag == "SharedString":
                        value = {"shared_id": value.strip()}
                    props[prop_name] = value
            instance_name = str(props.get("Name", class_name))
            path = f"{parent_path}/{instance_name}" if parent_path else instance_name
            node = {"class": class_name, "name": instance_name, "path": path,
                    "referent": referent, "parent_ref": parent_ref,
                    "properties": props}
            refs[referent] = node
            instances.append(node)
            visit(item, path, referent, ordinal)

    visit(root, None, None, 0)
    shared: dict[str, str] = {}
    shared_root = root.find("SharedStrings")
    if shared_root is not None:
        for entry in shared_root.findall("SharedString"):
            key = entry.attrib.get("md5", "")
            try:
                raw = base64.b64decode(entry.text or "", validate=True)
            except ValueError as error:
                raise ExportError("invalid base64 XML SharedString") from error
            if not key or key in shared:
                raise ExportError("empty or duplicate XML SharedString key")
            shared[key] = raw.hex()
    for node in instances:
        for value in node["properties"].values():
            if isinstance(value, dict) and "ref" in value:
                ref = value["ref"]
                if ref not in ("null", "") and ref not in refs:
                    raise ExportError("XML property refers to a missing instance")
            if isinstance(value, dict) and "shared_id" in value:
                if value["shared_id"] not in shared:
                    raise ExportError("XML property refers to a missing SharedString")
    return {"format": "xml", "header_instance_count": len(instances),
            "header_class_count": len({node["class"] for node in instances}),
            "instances": instances, "shared_strings": len(shared)}


def parse_export(path: Path) -> dict:
    if not path.is_file():
        raise ExportError(f"export file does not exist: {path}")
    size = path.stat().st_size
    if size == 0 or size > MAX_FILE_BYTES:
        raise ExportError(f"export file has invalid size ({size} bytes)")
    data = path.read_bytes()
    if data.startswith(BINARY_MAGIC):
        return _parse_binary(data)
    if path.suffix.lower() in XML_EXTENSIONS or data.lstrip().startswith(b"<"):
        return _parse_xml(data)
    raise ExportError("format is neither supported Roblox XML nor binary")


def _all_paths(parsed: dict) -> dict[str, list[dict]]:
    counts: dict[str, int] = {}
    by_parent: dict[int | None, list[dict]] = {}
    instances = parsed["instances"]
    by_ref = {node["ref"]: node for node in instances if "ref" in node}
    if parsed["format"] == "xml":
        for node in instances:
            counts.setdefault(node["path"], []).append(node)
        return counts
    for node in instances:
        parent = node["parent_ref"]
        by_parent.setdefault(None if parent == -1 else parent, []).append(node)
    result: dict[str, list[dict]] = {}

    stack = [(root, "", frozenset()) for root in reversed(by_parent.get(None, []))]
    while stack:
        node, parent_path, seen = stack.pop()
        ref = node["ref"]
        if ref in seen:
            raise ExportError("cycle in binary PRNT graph")
        path = f"{parent_path}/{node['name']}" if parent_path else node["name"]
        node["path"] = path
        result.setdefault(path, []).append(node)
        next_seen = seen | {ref}
        stack.extend((child, path, next_seen) for child in reversed(by_parent.get(ref, [])))
    if len({id(node) for values in result.values() for node in values}) != len(instances):
        raise ExportError("binary PRNT graph contains an unreachable/cyclic instance")
    return result


def validate(parsed: dict, expectations: dict | None) -> dict:
    paths = _all_paths(parsed)
    by_ref = {}
    for node in parsed["instances"]:
        key = node.get("referent", node.get("ref"))
        if key is not None:
            by_ref[key] = node

    def matches(actual: object, expected: object) -> bool:
        if isinstance(expected, dict):
            if "$color3uint8" in expected:
                if type(actual) is not int or not isinstance(expected["$color3uint8"], list):
                    return False
                channels = [(actual >> 16) & 0xff, (actual >> 8) & 0xff, actual & 0xff]
                rgb = expected["$color3uint8"]
                return (len(rgb) == 3 and all(
                    isinstance(value, (int, float)) and not isinstance(value, bool)
                    and abs(channel - value * 255) <= 1
                    for channel, value in zip(channels, rgb)))
            if "ref_path" in expected:
                reference = actual.get("ref") if isinstance(actual, dict) else None
                if reference in (None, "", "null", -1):
                    return expected["ref_path"] in (None, "")
                target = by_ref.get(reference)
                return target is not None and target.get("path") == expected["ref_path"]
            if not isinstance(actual, dict):
                return False
            return all(key in actual and matches(actual[key], value)
                       for key, value in expected.items())
        if isinstance(expected, (list, tuple)):
            return isinstance(actual, (list, tuple)) and len(actual) == len(expected) and all(
                matches(a, e) for a, e in zip(actual, expected))
        if isinstance(expected, (int, float)) and not isinstance(expected, bool):
            if isinstance(actual, bool) or not isinstance(actual, (int, float)):
                return False
            return abs(actual - expected) <= max(1e-5, abs(expected) * 1e-6)
        return type(actual) is type(expected) and actual == expected

    if expectations is not None:
        expected = expectations.get("instances")
        if not isinstance(expected, list) or not expected:
            raise ExportError("expectations must contain a non-empty instances array")
        for spec in expected:
            if not isinstance(spec, dict) or not isinstance(spec.get("path"), str):
                raise ExportError("each expected instance needs a path string")
            actual = paths.get(spec["path"], [])
            if len(actual) != 1:
                raise ExportError(f"expected one instance at {spec['path']!r}, found {len(actual)}")
            node = actual[0]
            if "class" in spec and node["class"] != spec["class"]:
                raise ExportError(f"wrong class at {spec['path']!r}: {node['class']!r}")
            expected_props = spec.get("properties", {})
            if not isinstance(expected_props, dict):
                raise ExportError("expected properties must be an object")
            actual_props = node["properties"]
            for key, value in expected_props.items():
                if key not in actual_props:
                    raise ExportError(f"missing {key!r} property on {spec['path']!r}")
                actual_value = actual_props[key]
                # XML decodes CDATA/escapes; binary retains exact source bytes.
                if not matches(actual_value, value):
                    raise ExportError(f"{spec['path']!r}.{key} did not match expected value")
    return {"format": parsed["format"], "instance_count": parsed["header_instance_count"],
            "class_count": parsed["header_class_count"],
            "script_count": sum(node["class"] in ("Script", "LocalScript", "ModuleScript")
                                for node in parsed["instances"]),
            "expected_count": len(expectations.get("instances", [])) if expectations else 0}


def self_test() -> None:
    xml = b'''<roblox version="4"><Item class="Folder" referent="RBX0"><Properties><string name="Name">Probe</string></Properties><Item class="Script" referent="RBX1"><Properties><string name="Name">ExporterCheck</string><ProtectedString name="Source"><![CDATA[return 17]]></ProtectedString></Properties></Item></Item></roblox>'''
    parsed = _parse_xml(xml)
    result = validate(parsed, {"instances": [
        {"path": "Probe", "class": "Folder"},
        {"path": "Probe/ExporterCheck", "class": "Script",
         "properties": {"Source": "return 17"}},
    ]})
    assert result["instance_count"] == 2 and result["script_count"] == 1
    try:
        validate(parsed, {"instances": [{"path": "Probe/ExporterCheck", "class": "ModuleScript"}]})
    except ExportError:
        pass
    else:
        raise AssertionError("self-test did not detect an incorrect class")
    def ref_plane(values: list[int]) -> bytes:
        encoded = []
        previous = 0
        for i, value in enumerate(values):
            delta = value if i == 0 else value - previous
            previous = value
            encoded.append((delta << 1) if delta >= 0 else ((-delta << 1) - 1))
        return bytes((word >> shift) & 0xff for shift in (24, 16, 8, 0)
                     for word in encoded)

    def chunk(name: bytes, payload: bytes) -> bytes:
        return name + struct.pack("<III", 0, len(payload), 0) + payload

    def string(value: str) -> bytes:
        encoded = value.encode("utf-8")
        return struct.pack("<I", len(encoded)) + encoded

    folder_inst = struct.pack("<I", 0) + string("Folder") + b"\x00" + struct.pack("<I", 1) + ref_plane([0])
    script_inst = struct.pack("<I", 1) + string("Script") + b"\x00" + struct.pack("<I", 1) + ref_plane([1])
    value_inst = struct.pack("<I", 2) + string("ObjectValue") + b"\x00" + struct.pack("<I", 1) + ref_plane([2])
    folder_name = struct.pack("<I", 0) + string("Name") + b"\x01" + string("Probe")
    script_name = struct.pack("<I", 1) + string("Name") + b"\x01" + string("ExporterCheck")
    script_source = struct.pack("<I", 1) + string("Source") + b"\x01" + string("return 17")
    value_name = struct.pack("<I", 2) + string("Name") + b"\x01" + string("Reference")
    value_target = struct.pack("<I", 2) + string("Value") + b"\x13" + ref_plane([1])
    prnt = b"\x00" + struct.pack("<I", 3) + ref_plane([0, 1, 2]) + ref_plane([-1, 0, 0])
    binary = (BINARY_MAGIC + struct.pack("<II", 3, 3) + b"\x00" * 8
              + chunk(b"INST", folder_inst) + chunk(b"INST", script_inst)
              + chunk(b"INST", value_inst)
              + chunk(b"PROP", folder_name) + chunk(b"PROP", script_name)
              + chunk(b"PROP", script_source) + chunk(b"PROP", value_name)
              + chunk(b"PROP", value_target) + chunk(b"PRNT", prnt)
              + chunk(b"END\x00", b"</roblox>"))
    try:
        binary_parsed = _parse_binary(binary)
        binary_result = validate(binary_parsed, {"instances": [
            {"path": "Probe", "class": "Folder"},
            {"path": "Probe/ExporterCheck", "class": "Script",
             "properties": {"Source": "return 17"}},
            {"path": "Probe/Reference", "class": "ObjectValue",
             "properties": {"Value": {"ref_path": "Probe/ExporterCheck"}}},
        ]})
        assert binary_result["instance_count"] == 3 and binary_result["script_count"] == 1
    except ExportError as error:
        raise AssertionError(f"self-test binary fixture did not parse: {error}") from error

    malformed = BINARY_MAGIC + b"\x00" * 16 + b"INST"
    try:
        _parse_binary(malformed)
    except ExportError:
        pass
    else:
        raise AssertionError("self-test did not detect a truncated binary chunk")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", nargs="?", type=Path)
    parser.add_argument("--expect", type=Path, help="JSON file with expected instance paths/properties")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
        print(json.dumps({"ok": True, "self_test": True}))
        return 0
    if args.export is None:
        parser.error("export path required unless --self-test is used")
    try:
        expected = json.loads(args.expect.read_text()) if args.expect else None
        parsed = parse_export(args.export)
        report = validate(parsed, expected)
    except (OSError, json.JSONDecodeError, ExportError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 1
    print(json.dumps({"ok": True, **report}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
