//! Bounded, data-only normalization of the pinned Luau bytecode formats.
//! This preserves every non-opcode byte, including opaque extensions/trailers.
//! Structural checks are not producer authentication or a full VM verifier.
use serde_json::{json, Value};
use std::{fmt, sync::OnceLock};

const MAX_BYTES: usize = 128 * 1024;
const MAX_ITEMS: usize = 1_000_000;
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct FormatError(&'static str);
impl fmt::Display for FormatError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.0)
    }
}
impl std::error::Error for FormatError {}
type Result<T> = std::result::Result<T, FormatError>;
fn require(ok: bool, error: &'static str) -> Result<()> {
    if ok {
        Ok(())
    } else {
        Err(FormatError(error))
    }
}

struct Reader<'a> {
    data: &'a [u8],
    at: usize,
    end: usize,
}
impl<'a> Reader<'a> {
    fn take(&mut self, size: usize) -> Result<&'a [u8]> {
        require(size <= self.end - self.at, "truncated-bytecode-field")?;
        let result = &self.data[self.at..self.at + size];
        self.at += size;
        Ok(result)
    }
    fn byte(&mut self) -> Result<u8> {
        Ok(self.take(1)?[0])
    }
    fn word(&mut self) -> Result<u32> {
        Ok(u32::from_le_bytes(self.take(4)?.try_into().unwrap()))
    }
    fn var(&mut self, bits: u32) -> Result<u64> {
        let mut value = 0u64;
        for shift in (0..bits).step_by(7) {
            let b = self.byte()?;
            require(
                u64::from(b & 127) <= u64::MAX >> shift,
                "bytecode-varint-overflow",
            )?;
            value |= u64::from(b & 127) << shift;
            require(
                bits == 64 || value < (1u64 << bits),
                "bytecode-varint-overflow",
            )?;
            if b & 128 == 0 {
                return Ok(value);
            }
        }
        Err(FormatError("unterminated-bytecode-varint"))
    }
    fn index(&mut self) -> Result<usize> {
        Ok(self.var(32)? as usize)
    }
    fn count(&mut self, minimum: usize) -> Result<usize> {
        let count = self.index()?;
        require(
            count <= MAX_ITEMS && count <= (self.end - self.at) / minimum,
            "unreasonable-bytecode-count",
        )?;
        Ok(count)
    }
    fn flag(&mut self) -> Result<bool> {
        let v = self.byte()?;
        require(v <= 1, "invalid-bytecode-boolean")?;
        Ok(v != 0)
    }
    fn string_ref(&mut self, count: usize, optional: bool) -> Result<usize> {
        let index = self.index()?;
        require(
            index <= count && (optional || index != 0),
            "invalid-bytecode-string-reference",
        )?;
        Ok(index)
    }
}
struct Constant {
    tag: u8,
    child: usize,
}
struct Proto {
    stack: u8,
    upvalues: u8,
    code_offset: usize,
    code: Vec<u32>,
    constants: Vec<Constant>,
    children: Vec<usize>,
}
struct Module {
    version: u8,
    types: u8,
    prototypes: Vec<Proto>,
    parsed: usize,
    extensions: usize,
}

fn parse(data: &[u8]) -> Result<Module> {
    require(
        !data.is_empty() && data.len() <= MAX_BYTES,
        "invalid-bytecode-size",
    )?;
    let mut r = Reader {
        data,
        at: 0,
        end: data.len(),
    };
    let version = r.byte()?;
    let types = r.byte()?;
    require(
        matches!(version, 6 | 9 | 13 | 14) && (1..=3).contains(&types),
        "unsupported-bytecode-version",
    )?;
    let strings = r.count(1)?;
    for _ in 0..strings {
        let length = r.index()?;
        r.take(length)?;
    }
    if types == 3 {
        let mut seen = [false; 256];
        loop {
            let index = r.byte()? as usize;
            if index == 0 {
                break;
            }
            require(!seen[index], "duplicate-userdata-mapping")?;
            seen[index] = true;
            r.string_ref(strings, false)?;
        }
    }
    let count = r.count(1)?;
    let mut prototypes = Vec::with_capacity(count);
    let mut extensions = 0;
    for pid in 0..count {
        let end = if version >= 12 {
            let size = r.index()?;
            require(size <= r.end - r.at, "prototype-exceeds-bytecode")?;
            r.at + size
        } else {
            r.end
        };
        let mut s = Reader {
            data,
            at: r.at,
            end,
        };
        let stack = s.byte()?;
        let parameters = s.byte()?;
        let upvalues = s.byte()?;
        s.flag()?;
        let flags = s.byte()?;
        require(
            parameters <= stack && upvalues <= 200,
            "invalid-register-counts",
        )?;
        let type_length = s.index()?;
        s.take(type_length)?;
        let words = s.count(4)?;
        require(words > 0, "empty-instruction-array")?;
        let code_offset = s.at;
        let mut code = Vec::with_capacity(words);
        for _ in 0..words {
            code.push(s.word()?);
        }
        let constant_count = s.count(1)?;
        let mut constants = Vec::with_capacity(constant_count);
        for index in 0..constant_count {
            let tag = s.byte()?;
            let mut child = 0;
            match tag {
                0 => {}
                1 => {
                    s.flag()?;
                }
                2 => {
                    s.take(8)?;
                }
                3 => {
                    s.string_ref(strings, false)?;
                }
                4 => {
                    s.take(4)?;
                }
                5 | 8 => {
                    require(tag != 8 || version >= 7, "unsupported-table-constant")?;
                    for _ in 0..s.count(if tag == 5 { 1 } else { 5 })? {
                        let key = s.index()?;
                        let value = if tag == 8 { s.word()? as i32 } else { -1 };
                        require(
                            key < index && value >= -1 && (value < 0 || (value as usize) < index),
                            "table-constant-reference-out-of-range",
                        )?;
                    }
                }
                6 => {
                    child = s.index()?;
                    require(child < pid, "closure-prototype-unavailable")?;
                }
                7 | 11 => {
                    require(tag != 11 || version >= 13, "unsupported-vector-constant")?;
                    s.take(if tag == 7 { 16 } else { 32 })?;
                }
                9 => {
                    require(version >= 8, "unsupported-integer-constant")?;
                    let negative = s.flag()?;
                    let magnitude = s.var(64)?;
                    require(
                        magnitude <= i64::MAX as u64 + u64::from(negative),
                        "integer-magnitude-out-of-range",
                    )?;
                }
                _ => return Err(FormatError("unsupported-constant-kind")),
            }
            constants.push(Constant { tag, child });
        }
        let mut children = Vec::new();
        for _ in 0..s.count(1)? {
            let child = s.index()?;
            require(child < pid, "child-prototype-unavailable")?;
            children.push(child);
        }
        s.index()?;
        s.string_ref(strings, true)?;
        if s.flag()? {
            let gap = s.byte()?;
            require(gap <= 31, "invalid-line-interval")?;
            s.take(words)?;
            s.take((((words - 1) >> gap) + 1) * 4)?;
        }
        if s.flag()? {
            for _ in 0..s.count(4)? {
                s.string_ref(strings, false)?;
                let first = s.index()?;
                let last = s.index()?;
                let register = s.byte()?;
                require(
                    first <= last && last <= words && register < stack,
                    "invalid-debug-local",
                )?;
            }
            let names = s.count(1)?;
            require(names == upvalues as usize, "upvalue-name-count-mismatch")?;
            for _ in 0..names {
                s.string_ref(strings, false)?;
            }
        }
        if version >= 11 {
            for _ in 0..s.count(2)? {
                let kind = s.byte()?;
                let value = s.index()?;
                require(
                    (kind == 0 && value < words) || (kind == 1 && value < constants.len()),
                    "invalid-feedback-entry",
                )?;
            }
        }
        if version >= 12 && flags & 8 != 0 {
            s.var(64)?;
        }
        r.at = if version >= 12 {
            extensions += s.end - s.at;
            s.end
        } else {
            s.at
        };
        prototypes.push(Proto {
            stack,
            upvalues,
            code_offset,
            code,
            constants,
            children,
        });
    }
    require(r.index()? < prototypes.len(), "invalid-main-prototype")?;
    Ok(Module {
        version,
        types,
        prototypes,
        parsed: r.at,
        extensions,
    })
}

struct Opcode {
    name: &'static str,
    words: usize,
}
fn catalogue() -> &'static [Opcode] {
    static OPS: OnceLock<Vec<Opcode>> = OnceLock::new();
    OPS.get_or_init(|| {
        let header = include_str!("../../vendor/luau/Common/include/Luau/Bytecode.h");
        let utils = include_str!("../../vendor/luau/Common/include/Luau/BytecodeUtils.h");
        let enumeration = header
            .split_once("enum LuauOpcode")
            .expect("pinned enum")
            .1
            .split_once("LOP__COUNT")
            .expect("pinned count")
            .0;
        let lengths = utils
            .split_once("inline int getOpLength")
            .expect("pinned lengths")
            .1
            .split_once("default:")
            .expect("pinned default")
            .0;
        enumeration
            .lines()
            .filter_map(|line| {
                let name = line.trim().strip_prefix("LOP_")?.split_once(',')?.0;
                Some(Opcode {
                    name,
                    words: if lengths.contains(&format!("case LOP_{name}:")) {
                        2
                    } else {
                        1
                    },
                })
            })
            .collect()
    })
}
fn minimum_version(name: &str) -> u8 {
    match name {
        "IDIV" | "IDIVK" => 4,
        "SUBRK" | "DIVRK" => 5,
        "FASTCALL3" => 6,
        "GETUDATAKS" | "SETUDATAKS" | "NAMECALLUDATA" => 9,
        "NEWCLASSMEMBER" => 10,
        "CALLFB" | "CMPPROTO" => 11,
        "FASTPCALL" => 14,
        _ => 3,
    }
}
fn fastcall(name: &str) -> bool {
    matches!(
        name,
        "FASTCALL"
            | "FASTCALL1"
            | "FASTCALL2"
            | "FASTCALL2K"
            | "FASTCALL3"
            | "FASTPCALL"
            | "FINCONSTRUCT"
    )
}
struct Instruction {
    pc: usize,
    op: usize,
    a: u8,
    b: u8,
    c: u8,
    d: i16,
    aux: u32,
    target: Option<i64>,
}

fn decode(module: &Module, multiplier: u8) -> Result<Vec<(usize, u8)>> {
    let ops = catalogue();
    let mut patches = Vec::new();
    for proto in &module.prototypes {
        let mut instructions = Vec::new();
        let mut pc = 0;
        let mut starts = vec![None; proto.code.len()];
        while pc < proto.code.len() {
            let word = proto.code[pc];
            let op = (word as u8).wrapping_mul(multiplier) as usize;
            let meta = ops.get(op).ok_or(FormatError("unknown-bytecode-opcode"))?;
            require(
                module.version >= minimum_version(meta.name)
                    && !matches!(
                        meta.name,
                        "NEWCLASS"
                            | "CONSTRUCT"
                            | "FINCONSTRUCT"
                            | "GETPRIVATEFIELD"
                            | "SETPRIVATEFIELD"
                    ),
                "opcode-outside-supported-version",
            )?;
            require(
                meta.words <= proto.code.len() - pc,
                "missing-auxiliary-word",
            )?;
            let a = (word >> 8) as u8;
            let b = (word >> 16) as u8;
            let c = (word >> 24) as u8;
            let d = (word >> 16) as i16;
            let e = (word as i32) >> 8;
            let target = if matches!(
                meta.name,
                "JUMP"
                    | "JUMPBACK"
                    | "JUMPIF"
                    | "JUMPIFNOT"
                    | "JUMPIFEQ"
                    | "JUMPIFLE"
                    | "JUMPIFLT"
                    | "JUMPIFNOTEQ"
                    | "JUMPIFNOTLE"
                    | "JUMPIFNOTLT"
                    | "FORNPREP"
                    | "FORNLOOP"
                    | "FORGPREP"
                    | "FORGLOOP"
                    | "FORGPREP_INEXT"
                    | "FORGPREP_NEXT"
                    | "JUMPXEQKNIL"
                    | "JUMPXEQKB"
                    | "JUMPXEQKN"
                    | "JUMPXEQKS"
                    | "CMPPROTO"
            ) {
                Some(pc as i64 + 1 + i64::from(d))
            } else if fastcall(meta.name) {
                Some(pc as i64 + i64::from(c) + 2)
            } else if meta.name == "LOADB" && c != 0 {
                Some(pc as i64 + i64::from(c) + 1)
            } else if meta.name == "JUMPX" {
                Some(pc as i64 + 1 + i64::from(e))
            } else {
                None
            };
            starts[pc] = Some(instructions.len());
            instructions.push(Instruction {
                pc,
                op,
                a,
                b,
                c,
                d,
                target,
                aux: if meta.words == 2 {
                    proto.code[pc + 1]
                } else {
                    0
                },
            });
            patches.push((proto.code_offset + pc * 4, op as u8));
            pc += meta.words;
        }
        let at = |pc: i64| -> Option<&Instruction> {
            usize::try_from(pc)
                .ok()
                .and_then(|pc| starts.get(pc))
                .and_then(|i| *i)
                .map(|i| &instructions[i])
        };
        let mut captures = vec![false; proto.code.len()];
        for ins in &instructions {
            let name = ops[ins.op].name;
            if let Some(target) = ins.target {
                require(
                    at(target).is_some(),
                    "branch-outside-instruction-boundaries",
                )?;
            }
            let constant = match name {
                "LOADK" | "DUPTABLE" | "DUPCLOSURE" | "GETIMPORT" => Some(i64::from(ins.d)),
                "GETGLOBAL" | "SETGLOBAL" | "GETTABLEKS" | "SETTABLEKS" | "NAMECALL" | "LOADKX"
                | "FASTCALL2K" => Some(i64::from(ins.aux)),
                "GETUDATAKS" | "SETUDATAKS" | "NAMECALLUDATA" => Some(i64::from(ins.aux & 65535)),
                "JUMPXEQKN" | "JUMPXEQKS" => Some(i64::from(ins.aux & 0xffffff)),
                "ADDK" | "SUBK" | "MULK" | "DIVK" | "MODK" | "POWK" | "ANDK" | "ORK" | "IDIVK" => {
                    Some(i64::from(ins.c))
                }
                "SUBRK" | "DIVRK" => Some(i64::from(ins.b)),
                _ => None,
            };
            if let Some(index) = constant {
                require(
                    index >= 0 && (index as usize) < proto.constants.len(),
                    "constant-operand-out-of-range",
                )?;
            }
            if matches!(name, "GETUPVAL" | "SETUPVAL") {
                require(ins.b < proto.upvalues, "upvalue-operand-out-of-range")?;
            }
            let child = if name == "NEWCLOSURE" {
                require(
                    ins.d >= 0 && (ins.d as usize) < proto.children.len(),
                    "child-operand-out-of-range",
                )?;
                Some(proto.children[ins.d as usize])
            } else if name == "DUPCLOSURE" {
                let value = &proto.constants[ins.d as usize];
                require(value.tag == 6, "closure-constant-required")?;
                Some(value.child)
            } else {
                None
            };
            if let Some(child) = child {
                for index in 0..module.prototypes[child].upvalues {
                    let capture = at(ins.pc as i64 + 1 + i64::from(index))
                        .ok_or(FormatError("missing-closure-capture"))?;
                    require(
                        ops[capture.op].name == "CAPTURE" && capture.a <= 2,
                        "invalid-closure-capture",
                    )?;
                    let limit = if capture.a == 2 {
                        proto.upvalues
                    } else {
                        proto.stack
                    };
                    require(
                        capture.b < limit && !(name == "DUPCLOSURE" && capture.a == 1),
                        "invalid-capture-source",
                    )?;
                    captures[capture.pc] = true;
                }
            }
            if fastcall(name) {
                let call = at(ins.target.expect("fastcall target") - 1)
                    .ok_or(FormatError("invalid-fastcall-fallback"))?;
                require(ops[call.op].name == "CALL", "invalid-fastcall-fallback")?;
            }
        }
        for ins in &instructions {
            if let Some(target) = ins.target {
                require(!captures[target as usize], "branch-into-capture-sequence")?;
            }
            require(
                ops[ins.op].name != "CAPTURE" || captures[ins.pc],
                "orphan-closure-capture",
            )?;
        }
    }
    Ok(patches)
}

pub struct Normalized {
    pub bytes: Vec<u8>,
    pub metadata: Value,
}
/// An explicit multiplier is useful for owned format fixtures. Auto mode
/// accepts standard opcodes first, otherwise demands exactly one valid odd map.
pub fn normalize(data: &[u8], requested: Option<u8>) -> Result<Normalized> {
    let module = parse(data)?;
    let (multiplier, patches) = if let Some(multiplier) = requested {
        require(multiplier & 1 == 1, "invalid-opcode-multiplier")?;
        (multiplier, decode(&module, multiplier)?)
    } else if let Ok(patches) = decode(&module, 1) {
        (1, patches)
    } else {
        let mut candidate = None;
        for multiplier in (3..=255).step_by(2) {
            if let Ok(patches) = decode(&module, multiplier) {
                require(candidate.is_none(), "ambiguous-opcode-encoding")?;
                candidate = Some((multiplier, patches));
            }
        }
        candidate.ok_or(FormatError("unrecognized-opcode-encoding"))?
    };
    let mut bytes = data.to_vec();
    for &(offset, opcode) in &patches {
        bytes[offset] = opcode;
    }
    Ok(Normalized {
        bytes,
        metadata: json!({"bytecode_version":module.version,
        "type_version":module.types,"prototype_count":module.prototypes.len(),
        "instruction_count":patches.len(),"opcode_decode_multiplier":multiplier,
        "trailer_bytes":data.len()-module.parsed,"prototype_extension_bytes":module.extensions,
        "authentication_validated":false,"execution_safety_verified":false}),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    const TINY: &[u8] = include_bytes!("../../tests/fixtures/luau-v9/tiny.luac");
    const CALC: &[u8] = include_bytes!("../../tests/fixtures/luau-v9/calculation.luac");
    #[test]
    fn pinned_v6_bundle_remains_byte_exact() {
        use sha2::{Digest, Sha256};
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .join("vendor/roblox-player-scripts-745");
        let manifest: Value =
            serde_json::from_slice(&std::fs::read(root.join("bytecode-v6-manifest.json")).unwrap())
                .unwrap();
        let entries = manifest["files"].as_array().unwrap();
        assert_eq!(entries.len(), 44);
        for entry in entries {
            let bytes = std::fs::read(root.join(entry["bytecode_path"].as_str().unwrap())).unwrap();
            assert_eq!(
                format!("{:x}", Sha256::digest(&bytes)),
                entry["bytecode_sha256"].as_str().unwrap()
            );
            assert_eq!(&bytes[..2], &[6, 3]);
            assert_eq!(normalize(&bytes, Some(1)).unwrap().bytes, bytes);
        }
    }

    #[test]
    fn compiler_fixtures_roundtrip_and_preserve_opaque_trailer() {
        for input in [TINY, CALC] {
            assert_eq!(normalize(input, None).unwrap().bytes, input);
            let module = parse(input).unwrap();
            let mut encoded = input.to_vec();
            for (at, op) in decode(&module, 1).unwrap() {
                encoded[at] = op.wrapping_mul(227);
            }
            encoded.extend([0xa5; 24]);
            let normalized = normalize(&encoded, Some(203)).unwrap();
            assert_eq!(normalized.bytes, [input, &[0xa5; 24]].concat());
            assert_eq!(normalized.metadata["trailer_bytes"], 24);
            assert_eq!(normalized.metadata["authentication_validated"], false);
            assert_eq!(normalized.metadata["execution_safety_verified"], false);
        }
    }
    #[test]
    fn rejects_truncations_unsupported_headers_and_bad_operands() {
        for size in 0..TINY.len() {
            assert!(normalize(&TINY[..size], Some(1)).is_err(), "cut {size}");
        }
        for version in [0, 5, 7, 8, 10, 11, 12, 15] {
            let mut bad = TINY.to_vec();
            bad[0] = version;
            assert!(normalize(&bad, None).is_err());
        }
        let module = parse(TINY).unwrap();
        let first = module.prototypes[0].code_offset;
        let mut bad = TINY.to_vec();
        bad[first] = 255;
        assert!(normalize(&bad, Some(1)).is_err());
        assert!(normalize(TINY, Some(0)).is_err());
        assert!(normalize(TINY, Some(2)).is_err());
    }
    #[test]
    fn jump_into_auxiliary_word_and_orphan_capture_are_rejected() {
        let op = |name| catalogue().iter().position(|v| v.name == name).unwrap() as u32;
        let module = |code| Module {
            version: 9,
            types: 3,
            parsed: 0,
            extensions: 0,
            prototypes: vec![Proto {
                stack: 2,
                upvalues: 0,
                code_offset: 0,
                code,
                constants: vec![Constant { tag: 3, child: 0 }],
                children: vec![],
            }],
        };
        assert!(decode(
            &module(vec![
                op("JUMP") | (1 << 16),
                op("GETGLOBAL"),
                0,
                op("RETURN")
            ]),
            1
        )
        .is_err());
        assert!(decode(&module(vec![op("CAPTURE"), op("RETURN")]), 1).is_err());
        assert!(decode(&module(vec![op("GETGLOBAL")]), 1).is_err());
    }
    #[test]
    fn varint_overflow_is_rejected() {
        for (bits, data) in [(32, vec![255; 5]), (64, vec![255; 10])] {
            let mut reader = Reader {
                data: &data,
                at: 0,
                end: data.len(),
            };
            assert!(reader.var(bits).is_err());
        }
    }
}
