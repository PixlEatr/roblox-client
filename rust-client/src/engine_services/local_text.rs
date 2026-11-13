//! Offline Roblox font-family lookup and CoreText shaping backend.
//!
//! The backend reads only the installed Roblox content directory. It does not
//! follow asset IDs over HTTP and never guesses a missing face or text width.
//! Widths are measured through CoreText, which uses the installed font's real
//! glyph advances and shaping. Height retains the existing TextService rule:
//! one line contributes the largest effective run size on that line.

use crate::engine_services::{
    decode_font_family, select_font_face, validate_font_bytes, FontFace, TextRequest,
    TextServiceBackend, MAX_FONT_BYTES, MAX_FONT_FAMILY_BYTES,
};
use objc2_core_foundation::{
    CFAttributedString, CFDictionary, CFDictionaryKeyCallBacks, CFDictionaryValueCallBacks,
    CFMutableDictionary, CFString, CFStringBuiltInEncodings, CFURL,
};
use objc2_core_text::{
    kCTFontAttributeName, CTFont, CTFontDescriptor, CTFontManagerCreateFontDescriptorsFromURL,
    CTLine,
};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    ffi::CString,
    fs,
    os::raw::c_void,
    path::{Path, PathBuf},
};
use unicode_segmentation::UnicodeSegmentation;

const MAX_TEXT_BYTES: usize = 4096;
const MAX_SHAPING_WORK: usize = 2_000_000;
const MAX_RUNS: usize = 128;
const MAX_VARIANTS: usize = 16;

#[derive(Clone, Debug)]
struct FontSpec {
    family: String,
    weight: u16,
    italic: bool,
    size: f64,
    path: PathBuf,
}

#[derive(Clone, Debug)]
struct Run {
    start: usize,
    end: usize,
    spec: FontSpec,
}

/// Uses files from the local Roblox app content tree (or the explicit content
/// directory override). All file paths are canonicalized and constrained to
/// that root before opening.
pub struct LocalTextBackend {
    content_root: PathBuf,
}

impl Default for LocalTextBackend {
    fn default() -> Self {
        let root = std::env::var_os("ROBLOX_CONTENT_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                PathBuf::from("/Applications/Roblox.app/Contents/Resources/content")
            });
        Self { content_root: root }
    }
}

impl LocalTextBackend {
    pub fn new(content_root: impl Into<PathBuf>) -> Self {
        Self {
            content_root: content_root.into(),
        }
    }

    fn checked_local_path(&self, suffix: &str) -> Result<PathBuf, String> {
        if suffix.len() > 512 || suffix.contains('\0') || suffix.split('/').any(|part| part == "..")
        {
            return Err("Invalid font asset reference".into());
        }
        let root = self
            .content_root
            .canonicalize()
            .map_err(|_| "Installed Roblox font assets are unavailable")?;
        let path = root
            .join(suffix)
            .canonicalize()
            .map_err(|_| "Installed Roblox font asset is unavailable")?;
        if !path.starts_with(&root) || !path.is_file() {
            return Err("Installed Roblox font asset is unavailable".into());
        }
        Ok(path)
    }

    fn checked_local_dir(&self, suffix: &str) -> Result<PathBuf, String> {
        if suffix.len() > 512 || suffix.contains('\0') || suffix.split('/').any(|part| part == "..")
        {
            return Err("Invalid font asset reference".into());
        }
        let root = self
            .content_root
            .canonicalize()
            .map_err(|_| "Installed Roblox font assets are unavailable")?;
        let path = root
            .join(suffix)
            .canonicalize()
            .map_err(|_| "Installed Roblox font asset is unavailable")?;
        if !path.starts_with(&root) || !path.is_dir() {
            return Err("Installed Roblox font asset is unavailable".into());
        }
        Ok(path)
    }

    fn family_data(&self, reference: &str) -> Result<(String, Vec<FontFace>, PathBuf), String> {
        let suffix = reference
            .strip_prefix("rbxasset://")
            .ok_or("Font family is not installed locally")?;
        let path = self.checked_local_path(suffix)?;
        let metadata =
            fs::metadata(&path).map_err(|_| "Installed Roblox font asset is unavailable")?;
        if metadata.len() == 0 || metadata.len() as usize > MAX_FONT_FAMILY_BYTES {
            return Err("Font family exceeds size limit".into());
        }
        let raw = fs::read(&path).map_err(|_| "Installed Roblox font asset is unavailable")?;
        let value: Value = serde_json::from_slice(&raw).map_err(|_| "Invalid font family JSON")?;
        let family_name = value
            .get("name")
            .and_then(Value::as_str)
            .filter(|name| !name.is_empty() && name.len() <= 256)
            .ok_or("Font family name is unavailable")?
            .to_owned();
        let faces = decode_font_family(&raw).map_err(str::to_owned)?;
        Ok((family_name, faces, path))
    }

    fn font_path(&self, family: &str, face: &FontFace) -> Result<PathBuf, String> {
        if let Some(path) = face.asset_id.strip_prefix("rbxasset://") {
            let path = self.checked_local_path(path)?;
            self.validate_font_file(&path)?;
            return Ok(path);
        }
        // Installed Roblox builds include the same named family faces as font
        // files. Resolve only the exact family/face filename; never substitute
        // a system fallback font when the requested face is absent.
        let fonts = self.checked_local_dir("fonts")?;
        let safe_family: String = family
            .chars()
            .filter(|c| c.is_ascii_alphanumeric() || *c == '_' || *c == '-')
            .collect();
        let face_name = face
            .name
            .as_deref()
            .ok_or("Font family face name is unavailable")?;
        let safe_face: String = face_name
            .chars()
            .filter(|c| c.is_ascii_alphanumeric() || *c == '_' || *c == '-')
            .collect();
        if safe_family.is_empty() || safe_face.is_empty() {
            return Err("Installed Roblox font face is unavailable".into());
        }
        let stem = format!("{safe_family}-{safe_face}");
        for ext in ["ttf", "otf"] {
            let candidate = fonts.join(format!("{stem}.{ext}"));
            if candidate.is_file() {
                let candidate = candidate
                    .canonicalize()
                    .map_err(|_| "Installed Roblox font face is unavailable")?;
                if !candidate.starts_with(&fonts) {
                    return Err("Installed Roblox font face is unavailable".into());
                }
                self.validate_font_file(&candidate)?;
                return Ok(candidate);
            }
        }
        Err("Installed Roblox font face is unavailable".into())
    }

    fn validate_font_file(&self, path: &Path) -> Result<(), String> {
        let size = fs::metadata(path)
            .map_err(|_| "Installed Roblox font face is unavailable")?
            .len();
        if size == 0 || size as usize > MAX_FONT_BYTES {
            return Err("Invalid or oversized font file".into());
        }
        let raw = fs::read(path).map_err(|_| "Installed Roblox font face is unavailable")?;
        validate_font_bytes(&raw).map_err(str::to_owned)?;
        Ok(())
    }

    fn measure(&self, spec: &FontSpec, text: &str) -> Result<f64, String> {
        if text.is_empty() {
            return Ok(0.0);
        }
        let cpath = CString::new(spec.path.as_os_str().as_encoded_bytes())
            .map_err(|_| "Invalid font asset path")?;
        // SAFETY: `cpath` remains alive through the call and points to a valid
        // NUL-terminated path. CoreFoundation returns an owned URL reference.
        let url = unsafe {
            CFURL::from_file_system_representation(
                None,
                cpath.as_ptr().cast(),
                cpath.as_bytes().len() as isize,
                false,
            )
        }
        .ok_or("CoreText could not open the installed font")?;
        // SAFETY: the URL is a valid file URL retained by `url`; CoreText
        // returns a retained array of descriptors which remains alive below.
        let descriptors = unsafe { CTFontManagerCreateFontDescriptorsFromURL(&url) }
            .ok_or("CoreText could not read the installed font")?;
        if descriptors.count() == 0 {
            return Err("CoreText found no font descriptors".into());
        }
        // SAFETY: CoreText documents this array as containing CTFontDescriptor
        // references; the array retains its member for the duration of use.
        let descriptor = unsafe { &*(descriptors.value_at_index(0) as *const CTFontDescriptor) };
        // SAFETY: null matrix requests the font's standard transform; `size`
        // was validated as finite and in the supported range by bounds().
        let font = unsafe { CTFont::with_font_descriptor(descriptor, spec.size, std::ptr::null()) };
        let ctext = CString::new(text).map_err(|_| "Text contains NUL")?;
        let string = unsafe {
            CFString::with_c_string(
                None,
                ctext.as_ptr(),
                CFStringBuiltInEncodings::EncodingUTF8.0,
            )
        }
        .ok_or("CoreText could not encode text")?;
        // SAFETY: both callback tables are CoreFoundation's type callbacks;
        // key and value below are valid CF objects retained by the dictionary.
        let attributes = unsafe {
            CFMutableDictionary::new(
                None,
                1,
                &objc2_core_foundation::kCFTypeDictionaryKeyCallBacks
                    as *const CFDictionaryKeyCallBacks,
                &objc2_core_foundation::kCFTypeDictionaryValueCallBacks
                    as *const CFDictionaryValueCallBacks,
            )
        }
        .ok_or("CoreText could not create font attributes")?;
        // SAFETY: CoreText's key is a CFStringRef and `font` is a CTFontRef,
        // both valid CFTypeRefs. The dictionary retains each reference.
        unsafe {
            CFMutableDictionary::set_value(
                Some(&attributes),
                kCTFontAttributeName as *const _ as *const c_void,
                font.as_ref() as *const CTFont as *const c_void,
            );
        }
        let attributes = CFDictionary::new_copy(None, Some(&attributes))
            .ok_or("CoreText could not freeze font attributes")?;
        // SAFETY: `string` and `attributes` have the exact CF types required
        // by CoreFoundation; both outlive the returned retained object.
        let attributed = unsafe { CFAttributedString::new(None, Some(&string), Some(&attributes)) }
            .ok_or("CoreText could not shape text")?;
        // SAFETY: `attributed` remains alive through line creation; CTLine
        // returns an owned immutable line reference.
        let line = unsafe { CTLine::with_attributed_string(&attributed) };
        // SAFETY: the output pointers are valid for this call and line is live.
        let width = unsafe {
            line.typographic_bounds(
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                std::ptr::null_mut(),
            )
        };
        if !width.is_finite() || width < 0.0 {
            return Err("CoreText returned invalid text metrics".into());
        }
        Ok(width)
    }

    fn bounds(&self, request: &Value) -> Result<Value, String> {
        let source = request
            .get("text")
            .and_then(Value::as_str)
            .ok_or("Invalid text measurement parameters")?;
        if source.len() > MAX_TEXT_BYTES || source.contains('\0') {
            return Err("Invalid text measurement parameters".into());
        }
        let size = finite_number(request.get("size"))
            .filter(|n| (1.0..=1000.0).contains(n))
            .ok_or("Invalid text measurement parameters")?;
        let width = finite_number(request.get("width"))
            .filter(|n| (0.0..=32768.0).contains(n))
            .ok_or("Invalid text measurement parameters")?;
        let line_spacing = match request.get("line_height") {
            None => 1.0,
            value => finite_number(value)
                .filter(|n| (1.0..=3.0).contains(n))
                .ok_or("Invalid text line height")?,
        };
        if request.get("rich_text").and_then(Value::as_bool) == Some(true) {
            return Err("Rich text must be parsed by the native worker".into());
        }
        let base = request
            .get("font")
            .and_then(Value::as_object)
            .ok_or("Invalid displayed font descriptor")?;
        let family = base
            .get("family")
            .and_then(Value::as_str)
            .ok_or("Invalid displayed font descriptor")?;
        let weight = base
            .get("weight")
            .and_then(Value::as_u64)
            .filter(|w| (100..=900).contains(w) && w % 100 == 0)
            .ok_or("Invalid displayed font weight or style")? as u16;
        let style = base
            .get("style")
            .and_then(Value::as_u64)
            .filter(|s| *s <= 1)
            .ok_or("Invalid displayed font weight or style")?
            == 1;
        if base.len() != 3
            || !base.contains_key("family")
            || !base.contains_key("weight")
            || !base.contains_key("style")
        {
            return Err("Invalid displayed font descriptor".into());
        }
        let (mut text, runs) =
            self.make_runs(source, size, family, weight, style, request.get("runs"))?;
        if text.is_empty() {
            return Ok(json!([0, 0]));
        }
        let variants: std::collections::HashSet<(String, u16, bool, u64)> = runs
            .iter()
            .map(|r| {
                (
                    r.spec.family.clone(),
                    r.spec.weight,
                    r.spec.italic,
                    r.spec.size.to_bits(),
                )
            })
            .collect();
        if variants.len() > MAX_VARIANTS {
            return Err("Text measurement font variant limit exceeded".into());
        }
        let mut measured: HashMap<(usize, usize, usize), f64> = HashMap::new();
        let mut work = 0usize;
        let advance = |start: usize,
                       end: usize,
                       measured: &mut HashMap<(usize, usize, usize), f64>,
                       work: &mut usize|
         -> Result<f64, String> {
            let mut total = 0.0;
            for (idx, run) in runs.iter().enumerate() {
                let a = start.max(run.start);
                let b = end.min(run.end);
                if a >= b {
                    continue;
                }
                let key = (idx, a, b);
                if let Some(value) = measured.get(&key) {
                    total += value;
                    continue;
                }
                let value_text = &text[a..b];
                *work += value_text.chars().count();
                if *work > MAX_SHAPING_WORK {
                    return Err("Text shaping work limit exceeded".into());
                }
                let value = self.measure(&run.spec, value_text)?;
                measured.insert(key, value);
                total += value;
            }
            Ok(total)
        };
        let mut widest: f64 = 0.0;
        let mut total_height: f64 = 0.0;
        let mut last_height: f64 = 0.0;
        let mut paragraph_start = 0usize;
        loop {
            let paragraph_end = text[paragraph_start..]
                .find('\n')
                .map(|i| paragraph_start + i)
                .unwrap_or(text.len());
            if width == 0.0
                || advance(paragraph_start, paragraph_end, &mut measured, &mut work)? <= width
            {
                widest = widest.max(advance(
                    paragraph_start,
                    paragraph_end,
                    &mut measured,
                    &mut work,
                )?);
                last_height = line_height(&runs, paragraph_start, paragraph_end, size);
                total_height += last_height * line_spacing;
            } else {
                let paragraph = &text[paragraph_start..paragraph_end];
                let graphemes: Vec<(usize, &str)> =
                    UnicodeSegmentation::grapheme_indices(paragraph, true).collect();
                let mut positions = Vec::with_capacity(graphemes.len() + 1);
                positions.push(paragraph_start);
                for (offset, cluster) in &graphemes {
                    positions.push(paragraph_start + offset + cluster.len());
                }
                let mut start = 0usize;
                while start < graphemes.len() {
                    let remaining = graphemes.len() - start;
                    let mut low = 0usize;
                    let mut high = 1usize;
                    while high <= remaining
                        && advance(
                            positions[start],
                            positions[start + high],
                            &mut measured,
                            &mut work,
                        )? <= width
                    {
                        low = high;
                        high *= 2;
                    }
                    high = high.min(remaining);
                    while low < high {
                        let middle = (low + high + 1) / 2;
                        if advance(
                            positions[start],
                            positions[start + middle],
                            &mut measured,
                            &mut work,
                        )? <= width
                        {
                            low = middle;
                        } else {
                            high = middle - 1;
                        }
                    }
                    let mut end = start + low.max(1);
                    if end < graphemes.len() && !graphemes[end].1.chars().all(char::is_whitespace) {
                        if let Some(space) = (start..end)
                            .rev()
                            .find(|i| graphemes[*i].1.chars().all(char::is_whitespace))
                        {
                            if space > start {
                                end = space;
                            }
                        }
                    }
                    let line_start = positions[start];
                    let raw_end = positions[end];
                    let line_end = text[line_start..raw_end]
                        .trim_end_matches(char::is_whitespace)
                        .len()
                        + line_start;
                    let line_width = advance(line_start, line_end, &mut measured, &mut work)?;
                    widest = widest.max(line_width);
                    last_height = line_height(&runs, line_start, line_end, size);
                    total_height += last_height * line_spacing;
                    start = end;
                    while start < graphemes.len()
                        && graphemes[start].1.chars().all(char::is_whitespace)
                    {
                        start += 1;
                    }
                }
            }
            if paragraph_end == text.len() {
                break;
            }
            paragraph_start = paragraph_end + 1;
            if paragraph_start == text.len() {
                last_height = size;
                total_height += last_height * line_spacing;
                break;
            }
        }
        text.clear();
        // LineHeight adds spacing between lines, not after the final line.
        total_height -= last_height * (line_spacing - 1.0);
        Ok(json!([widest.ceil() as u64, total_height.ceil() as u64]))
    }

    fn make_runs(
        &self,
        source: &str,
        base_size: f64,
        base_family: &str,
        base_weight: u16,
        base_italic: bool,
        input: Option<&Value>,
    ) -> Result<(String, Vec<Run>), String> {
        let default = [json!({"text":source,"style":{}})];
        let values = match input {
            None => &default,
            Some(Value::Array(values)) => values.as_slice(),
            _ => return Err("Invalid text runs".into()),
        };
        if values.len() > MAX_RUNS {
            return Err("Invalid text runs".into());
        }
        let mut raw = String::new();
        let mut text = String::new();
        let mut runs: Vec<Run> = Vec::new();
        let mut previous_cr = false;
        for value in values {
            let part = value
                .get("text")
                .and_then(Value::as_str)
                .ok_or("Invalid text run")?;
            let overrides = value
                .get("style")
                .and_then(Value::as_object)
                .ok_or("Invalid text run")?;
            raw.push_str(part);
            let mut content = part.to_owned();
            if previous_cr && content.starts_with('\n') {
                content.remove(0);
            }
            if !part.is_empty() {
                previous_cr = part.ends_with('\r');
            }
            content = content
                .replace("\r\n", "\n")
                .replace('\r', "\n")
                .replace('\t', " ");
            if content.is_empty() {
                continue;
            }
            let family = match overrides.get("family") {
                Some(value) => value.as_str().ok_or("Invalid displayed font descriptor")?,
                None => base_family,
            };
            let weight = match overrides.get("weight") {
                Some(v) => {
                    v.as_u64()
                        .filter(|w| (100..=900).contains(w) && w % 100 == 0)
                        .ok_or("Invalid displayed font weight or style")? as u16
                }
                None => base_weight,
            };
            let italic = match overrides.get("style") {
                Some(v) => match v.as_u64() {
                    Some(0) => false,
                    Some(1) => true,
                    _ => return Err("Invalid displayed font weight or style".into()),
                },
                None => base_italic,
            };
            if !family.starts_with("rbxasset://fonts/families/")
                || !(100..=900).contains(&weight)
                || weight % 100 != 0
            {
                return Err("Invalid displayed font descriptor".into());
            }
            let run_size = match overrides.get("size") {
                Some(v) => finite_number(Some(v)).ok_or("Invalid text run size")?,
                None => base_size,
            };
            let scale = match overrides.get("sizeScale") {
                Some(v) => finite_number(Some(v)).ok_or("Invalid text run size scale")?,
                None => 1.0,
            };
            if !(1.0..=1000.0).contains(&run_size) || !(0.5..=1.0).contains(&scale) {
                return Err("Invalid text run size".into());
            }
            let (family_name, faces, _) = self.family_data(family)?;
            let face =
                select_font_face(&faces, weight, italic).ok_or("Font face is unavailable")?;
            let path = self.font_path(&family_name, face)?;
            let effective_size = run_size * scale;
            let start = text.len();
            text.push_str(&content);
            let end = text.len();
            if let Some(last) = runs.last_mut() {
                if last.end == start
                    && last.spec.family == family
                    && last.spec.weight == weight
                    && last.spec.italic == italic
                    && last.spec.size == effective_size
                {
                    last.end = end;
                    continue;
                }
            }
            runs.push(Run {
                start,
                end,
                spec: FontSpec {
                    family: family.to_owned(),
                    weight,
                    italic,
                    size: effective_size,
                    path,
                },
            });
        }
        if raw != source {
            return Err("Text runs do not match their source".into());
        }
        Ok((text, runs))
    }
}

impl TextServiceBackend for LocalTextBackend {
    fn handle(&self, request: &TextRequest) -> Result<Value, String> {
        match request {
            TextRequest::Family { family, .. } => {
                let (name, faces, _) = self.family_data(family)?;
                if faces.iter().any(|face| face.name.is_none()) {
                    return Err("Font family face name is unavailable".into());
                }
                Ok(
                    json!({"Name":name,"Faces":faces.iter().map(|face|json!({"Name":face.name.as_deref().unwrap_or_default(),"Weight":face.weight,"Style":u8::from(face.italic)})).collect::<Vec<_>>()}),
                )
            }
            TextRequest::Bounds { payload, .. } => self.bounds(payload),
        }
    }
}

fn finite_number(value: Option<&Value>) -> Option<f64> {
    match value? {
        Value::Number(n) => n.as_f64().filter(|n| n.is_finite()),
        _ => None,
    }
}

fn line_height(runs: &[Run], start: usize, end: usize, base: f64) -> f64 {
    let height = runs
        .iter()
        .filter(|r| r.start < end && r.end > start)
        .map(|r| r.spec.size)
        .fold(0.0, f64::max);
    if height == 0.0 {
        base
    } else {
        height
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine_services::TextServiceBackend;

    fn has_local_roblox_fonts() -> bool {
        Path::new(
            "/Applications/Roblox.app/Contents/Resources/content/fonts/families/BuilderSans.json",
        )
        .exists()
            || std::env::var_os("ROBLOX_CONTENT_DIR").is_some()
    }

    #[test]
    fn family_lookup_uses_only_installed_files() {
        if !has_local_roblox_fonts() {
            return;
        }
        let backend = LocalTextBackend::default();
        let family = TextRequest::Family {
            request_id: 1,
            family: "rbxasset://fonts/families/BuilderSans.json".into(),
        };
        let result = backend.handle(&family).unwrap();
        assert_eq!(result["Name"], "Builder Sans");
        assert!(!result["Faces"].as_array().unwrap().is_empty());
        let missing = TextRequest::Family {
            request_id: 2,
            family: "rbxasset://fonts/families/../../secret.json".into(),
        };
        assert!(backend.handle(&missing).is_err());
    }

    #[test]
    fn coretext_returns_real_positive_and_wrapped_metrics() {
        if !has_local_roblox_fonts() {
            return;
        }
        let backend = LocalTextBackend::default();
        let family =
            json!({"family":"rbxasset://fonts/families/BuilderSans.json","weight":400,"style":0});
        let req = |text: &str, width: f64| TextRequest::Bounds {
            request_id: 1,
            payload: json!({"kind":"bounds","request_id":1,"font":family,"text":text,"size":20,"width":width}),
        };
        let single = backend.handle(&req("Hello", 1000.0)).unwrap();
        let wrapped = backend.handle(&req("Hello world", 35.0)).unwrap();
        assert!(single[0].as_u64().unwrap() > 0);
        assert!(wrapped[1].as_u64().unwrap() > single[1].as_u64().unwrap());
    }
}
