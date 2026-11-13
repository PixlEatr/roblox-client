#!/usr/bin/env python3
"""Generate a byte-offset constructor registry index for the C++ host.

This is a configure/build helper only. The runtime keeps the exact source blob
in read-only storage and parses one class object on demand.
"""
from __future__ import annotations

import json
import pathlib
import sys


def ws(data: bytes, pos: int) -> int:
    while pos < len(data) and data[pos] in b" \t\r\n":
        pos += 1
    return pos


def string_token(data: bytes, pos: int) -> tuple[str, int]:
    if data[pos] != ord('"'):
        raise ValueError(f"expected JSON string at byte {pos}")
    end = pos + 1
    while end < len(data):
        if data[end] == ord('\\'):
            end += 2
        elif data[end] == ord('"'):
            end += 1
            return json.loads(data[pos:end].decode("utf-8")), end
        else:
            end += 1
    raise ValueError("unterminated JSON string")


def skip_value(data: bytes, pos: int) -> int:
    pos = ws(data, pos)
    if pos >= len(data):
        raise ValueError("missing JSON value")
    first = data[pos]
    if first == ord('"'):
        return string_token(data, pos)[1]
    if first == ord('{'):
        _, end = object_members(data, pos)
        return end
    if first == ord('['):
        pos = ws(data, pos + 1)
        if pos < len(data) and data[pos] == ord(']'):
            return pos + 1
        while True:
            pos = skip_value(data, pos)
            pos = ws(data, pos)
            if data[pos] == ord(']'):
                return pos + 1
            if data[pos] != ord(','):
                raise ValueError(f"expected array comma at byte {pos}")
            pos = ws(data, pos + 1)
    end = pos
    while end < len(data) and data[end] not in b",]} \t\r\n":
        end += 1
    if end == pos:
        raise ValueError(f"invalid JSON value at byte {pos}")
    return end


def object_members(data: bytes, pos: int) -> tuple[list[tuple[str, int, int]], int]:
    if data[pos] != ord('{'):
        raise ValueError(f"expected JSON object at byte {pos}")
    pos = ws(data, pos + 1)
    members: list[tuple[str, int, int]] = []
    if data[pos] == ord('}'):
        return members, pos + 1
    while True:
        name, after_name = string_token(data, pos)
        pos = ws(data, after_name)
        if data[pos] != ord(':'):
            raise ValueError(f"expected colon at byte {pos}")
        value_start = ws(data, pos + 1)
        value_end = skip_value(data, value_start)
        members.append((name, value_start, value_end))
        pos = ws(data, value_end)
        if data[pos] == ord('}'):
            return members, pos + 1
        if data[pos] != ord(','):
            raise ValueError(f"expected object comma at byte {pos}")
        pos = ws(data, pos + 1)


def cpp_literal(data: bytes) -> str:
    return '"' + ''.join(f"\\x{byte:02x}" for byte in data) + '"'


def byte_initializer(data: bytes, per_line: int = 16) -> str:
    return "\n".join(
        "    " + ", ".join(f"0x{byte:02x}" for byte in data[start:start + per_line]) + ","
        for start in range(0, len(data), per_line)
    )


def registry_indexes(raw: bytes):
    document = json.loads(raw.decode("utf-8"))
    root_items, _ = object_members(raw, ws(raw, 0))
    classes_span = next(((start, end) for name, start, end in root_items if name == "classes"), None)
    if classes_span is None:
        raise ValueError("constructor registry has no classes object")

    classes = document.get("classes")
    if not isinstance(classes, dict):
        raise ValueError("constructor registry classes is not an object")
    class_items, _ = object_members(raw, classes_span[0])
    if len(class_items) != len(classes):
        raise ValueError("class source index differs from parsed registry")
    entries = []
    parents = []
    descriptors = 0
    for name, start, end in class_items:
        value = classes[name]
        if not isinstance(value, dict):
            raise ValueError(f"class {name!r} is not an object")
        properties = value.get("properties", {})
        if isinstance(properties, dict):
            descriptors += len(properties)
        entries.append((name, start, end - start))
        if "parent" in value:
            parent = value["parent"]
            if parent is not None and not isinstance(parent, str):
                raise ValueError(f"class {name!r} has a non-string, non-null parent")
            parents.append((name, parent))
    entries.sort(key=lambda entry: entry[0])
    parents.sort(key=lambda entry: entry[0])

    return entries, parents, descriptors


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: scripts_generate_class_registry.py INPUT.json OUTPUT.hpp")
    source_path = pathlib.Path(sys.argv[1])
    output_path = pathlib.Path(sys.argv[2])
    raw = source_path.read_bytes()
    entries, parents, descriptors = registry_indexes(raw)

    lines = [
        "// Generated from class-constructors-745.json by scripts_generate_class_registry.py.",
        "// The contiguous unsigned-byte array avoids oversized compiler string literals.",
        "// Indexes are UTF-8 byte offsets into the exact source bytes.",
        "#include <cstddef>",
        "struct ConstructorClassIndexEntry { const char* name; std::size_t offset; std::size_t length; };",
        "struct ConstructorParentIndexEntry { const char* name; const char* parent; bool is_null; };",
        "static constexpr unsigned char kConstructorRegistrySource[] = {",
        byte_initializer(raw),
        "};",
        "static constexpr ConstructorClassIndexEntry kConstructorClassIndex[] = {",
    ]
    for name, offset, length in entries:
        lines.append(f"    {{{cpp_literal(name.encode('utf-8')).replace(chr(10), ' ')}, {offset}, {length}}},")
    lines.extend([
        "};",
        "static constexpr ConstructorParentIndexEntry kConstructorParentIndex[] = {",
    ])
    for name, parent in parents:
        if parent is None:
            lines.append(f"    {{{cpp_literal(name.encode('utf-8')).replace(chr(10), ' ')}, nullptr, true}},")
        else:
            lines.append(f"    {{{cpp_literal(name.encode('utf-8')).replace(chr(10), ' ')}, {cpp_literal(parent.encode('utf-8')).replace(chr(10), ' ')}, false}},")
    lines.extend([
        "};",
        f"static constexpr std::size_t kConstructorRegistryClassCount = {len(entries)};",
        f"static constexpr std::size_t kConstructorRegistryDescriptorCount = {descriptors};",
        f"static constexpr std::size_t kConstructorRegistryParentCount = {len(parents)};",
        f"static constexpr std::size_t kConstructorRegistrySourceBytes = {len(raw)};",
        "",
    ])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    generated = "\n".join(lines)
    if not output_path.exists() or output_path.read_text(encoding="utf-8") != generated:
        output_path.write_text(generated, encoding="utf-8")


if __name__ == "__main__":
    main()
