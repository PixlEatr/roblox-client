//! Bounded validator for the native session's local display projection.
//!
//! This module does not infer scene properties or fetch assets. It accepts the
//! frame produced by the official Luau host, retains it unchanged, and records
//! only visual asset references that occur in that frame.

use serde_json::Value;
use std::collections::BTreeSet;

pub const MAX_FRAME_BYTES: usize = 2 * 1024 * 1024;
pub const MAX_PARTS: usize = 1800;
pub const MAX_GUI: usize = 600;
pub const MAX_PLAYERS: usize = 256;

#[derive(Clone, Debug, Eq, PartialEq, Ord, PartialOrd)]
pub struct RenderAsset {
    pub kind: &'static str,
    pub reference: String,
    pub weight: Option<u16>,
    pub style: Option<u8>,
}

#[derive(Debug)]
pub struct RenderProjection {
    frame: Value,
    sequence: u64,
    assets: BTreeSet<RenderAsset>,
}

impl RenderProjection {
    pub fn frame(&self) -> &Value {
        &self.frame
    }

    pub fn into_frame(self) -> Value {
        self.frame
    }

    pub fn sequence(&self) -> u64 {
        self.sequence
    }

    pub fn assets(&self) -> &BTreeSet<RenderAsset> {
        &self.assets
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum RenderProjectionError {
    Unavailable,
    InvalidFrame(&'static str),
    TooLarge,
}

impl std::fmt::Display for RenderProjectionError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Unavailable => f.write_str("native render projection is unavailable"),
            Self::InvalidFrame(reason) => write!(f, "invalid native render projection: {reason}"),
            Self::TooLarge => f.write_str("native render projection exceeds the display budget"),
        }
    }
}

impl std::error::Error for RenderProjectionError {}

/// Validate a frame returned by `native::Session` for `{"op":"render"}`.
/// The original `Value` is retained unchanged, including SurfaceAppearance
/// color, alpha mode, and texture-map fields.
pub fn project_frame(frame: Value) -> Result<RenderProjection, RenderProjectionError> {
    let obj = frame
        .as_object()
        .ok_or(RenderProjectionError::Unavailable)?;
    if obj.get("version").and_then(Value::as_u64) != Some(2) {
        return Err(RenderProjectionError::Unavailable);
    }
    let sequence = obj
        .get("sequence")
        .and_then(|value| {
            value.as_u64().or_else(|| {
                let number = value.as_f64()?;
                (number.is_finite()
                    && number >= 0.0
                    && number <= 9_007_199_254_740_991.0
                    && number.fract() == 0.0)
                    .then_some(number as u64)
            })
        })
        .filter(|value| *value <= 9_007_199_254_740_991)
        .ok_or(RenderProjectionError::InvalidFrame(
            "sequence is missing or not an exact nonnegative integer",
        ))?;
    let parts = bounded_array(obj.get("parts"), MAX_PARTS, "parts")?;
    let gui = bounded_array(obj.get("gui"), MAX_GUI, "gui")?;
    let players = bounded_array(obj.get("players"), MAX_PLAYERS, "players")?;
    for part in parts {
        if part.get("id").and_then(Value::as_str).is_none()
            || part.get("class").and_then(Value::as_str).is_none()
        {
            return Err(RenderProjectionError::InvalidFrame(
                "part identity is incomplete",
            ));
        }
        if let Some(decals) = part.get("decals") {
            let Some(decals) = decals.as_array() else {
                return Err(RenderProjectionError::InvalidFrame(
                    "part decals are malformed",
                ));
            };
            if decals.len() > 12 {
                return Err(RenderProjectionError::InvalidFrame(
                    "part decal budget exceeded",
                ));
            }
        }
    }
    for item in gui {
        if item.get("id").and_then(Value::as_str).is_none()
            || item.get("class").and_then(Value::as_str).is_none()
        {
            return Err(RenderProjectionError::InvalidFrame(
                "GUI identity is incomplete",
            ));
        }
    }
    for player in players {
        if player.get("name").and_then(Value::as_str).is_none()
            || player.get("local").and_then(Value::as_bool).is_none()
        {
            return Err(RenderProjectionError::InvalidFrame(
                "player entry is malformed",
            ));
        }
    }
    let encoded_size = serde_json::to_vec(&frame)
        .map_err(|_| RenderProjectionError::InvalidFrame("frame cannot be serialized"))?
        .len();
    if encoded_size > MAX_FRAME_BYTES {
        return Err(RenderProjectionError::TooLarge);
    }

    let mut assets = BTreeSet::new();
    for part in parts {
        if let Some(mesh) = part.get("mesh").and_then(Value::as_object) {
            add_asset(&mut assets, "mesh", mesh.get("id"));
            add_asset(&mut assets, "texture", mesh.get("texture"));
        }
        if let Some(appearance) = part.get("appearance").and_then(Value::as_object) {
            for field in ["colorMap", "normalMap", "roughnessMap", "metalnessMap"] {
                add_asset(&mut assets, "texture", appearance.get(field));
            }
        }
        if let Some(decals) = part.get("decals").and_then(Value::as_array) {
            for decal in decals.iter().take(12) {
                add_asset(&mut assets, "texture", decal.get("texture"));
            }
        }
        if let Some(clothing) = part.get("clothing").and_then(Value::as_object) {
            for reference in clothing.values() {
                add_asset(&mut assets, "texture", Some(reference));
            }
        }
    }
    for item in gui {
        add_asset(&mut assets, "texture", item.get("image"));
        add_asset(&mut assets, "texture", item.get("imageHover"));
        add_asset(&mut assets, "texture", item.get("imagePressed"));
        add_font(&mut assets, item.get("fontFace"));
        if let Some(runs) = item.get("richRuns").and_then(Value::as_array) {
            for run in runs {
                add_font(&mut assets, run.get("fontFace"));
            }
        }
    }

    Ok(RenderProjection {
        frame,
        sequence,
        assets,
    })
}

fn bounded_array<'a>(
    value: Option<&'a Value>,
    max: usize,
    label: &'static str,
) -> Result<&'a [Value], RenderProjectionError> {
    let array = value
        .and_then(Value::as_array)
        .ok_or(RenderProjectionError::InvalidFrame(label))?;
    if array.len() > max {
        return Err(RenderProjectionError::InvalidFrame("item budget exceeded"));
    }
    Ok(array)
}

fn add_asset(assets: &mut BTreeSet<RenderAsset>, kind: &'static str, value: Option<&Value>) {
    let Some(value) = value.and_then(Value::as_str) else {
        return;
    };
    let Some(reference) = canonical_asset_reference(value) else {
        return;
    };
    assets.insert(RenderAsset {
        kind,
        reference,
        weight: None,
        style: None,
    });
}

fn add_font(assets: &mut BTreeSet<RenderAsset>, value: Option<&Value>) {
    let Some(font) = value.and_then(Value::as_object) else {
        return;
    };
    let Some(family) = font.get("family").and_then(Value::as_str) else {
        return;
    };
    let Some(weight) = font.get("weight").and_then(Value::as_u64) else {
        return;
    };
    let Some(style) = font.get("style").and_then(Value::as_u64) else {
        return;
    };
    let family_path = family
        .strip_prefix("rbxasset://fonts/families/")
        .and_then(|path| path.strip_suffix(".json"));
    let family_ok = family.len() <= 512
        && (family_path.is_some_and(|path| {
            !path.is_empty()
                && path
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
        }) || canonical_asset_reference(family).is_some());
    if !family_ok || !(100..=900).contains(&weight) || weight % 100 != 0 || style > 1 {
        return;
    }
    assets.insert(RenderAsset {
        kind: "font",
        reference: family.to_owned(),
        weight: Some(weight as u16),
        style: Some(style as u8),
    });
}

fn canonical_asset_reference(raw: &str) -> Option<String> {
    if raw.len() > 512 {
        return None;
    }
    let value = raw.trim();
    if let Some(id) = value
        .strip_prefix("rbxassetid://")
        .or_else(|| value.strip_prefix("RBXASSETID://"))
    {
        if !id.is_empty() && id.bytes().all(|b| b.is_ascii_digit()) {
            let parsed = id.parse::<u64>().ok().filter(|n| *n > 0)?;
            return Some(format!("rbxassetid://{parsed}"));
        }
    }
    let rest = value.strip_prefix("rbxasset://")?;
    let mut segments = rest.split('/');
    let family = segments.next()?;
    if !matches!(family, "textures" | "avatar") {
        return None;
    }
    if rest.contains("..")
        || rest
            .bytes()
            .any(|b| !(b.is_ascii_alphanumeric() || matches!(b, b'/' | b'_' | b'-' | b'.')))
        || !matches!(rest.rsplit('.').next()?, "png" | "jpg" | "jpeg" | "mesh")
    {
        return None;
    }
    Some(value.to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn frame() -> Value {
        json!({
            "version":2,"sequence":17,
            "parts":[{
                "id":"mesh:1","class":"MeshPart","name":"AvatarPart",
                "position":{"$vector3":[0,1,2]},"size":{"$vector3":[1,2,1]},
                "mesh":{"class":"MeshPart","id":"rbxassetid://0010","texture":"rbxassetid://11"},
                "appearance":{"color":{"$color3":[0.5,0.7,1.0]},"alphaMode":1,
                    "colorMap":"rbxassetid://12","normalMap":"rbxassetid://13",
                    "roughnessMap":"rbxassetid://14","metalnessMap":"rbxassetid://15"},
                "decals":[{"texture":"rbxasset://textures/face.png"}]
            }],
            "gui":[{"id":"gui:1","class":"TextLabel","image":""}],
            "players":[{"name":"Player","local":true}],
            "lighting":{"ClockTime":17},"skippedParts":0,"skippedGui":0
        })
    }

    #[test]
    fn accepts_native_integral_float_sequence_and_rejects_fractional_or_unsafe_numbers() {
        let mut value = frame();
        value["sequence"] = serde_json::json!(7.0);
        let projection = project_frame(value.clone()).unwrap();
        assert_eq!(projection.sequence(), 7);
        assert_eq!(projection.frame(), &value);
        for bad in [
            serde_json::json!(7.5),
            serde_json::json!(-1),
            serde_json::json!(9007199254740992_u64),
        ] {
            value["sequence"] = bad;
            assert!(project_frame(value.clone()).is_err());
        }
    }

    #[test]
    fn preserves_surface_appearance_and_gates_only_observed_assets() {
        let original = frame();
        let projection = project_frame(original.clone()).unwrap();
        assert_eq!(projection.sequence(), 17);
        assert_eq!(projection.frame(), &original);
        let appearance = &projection.frame()["parts"][0]["appearance"];
        assert_eq!(appearance["alphaMode"], 1);
        assert_eq!(appearance["color"]["$color3"][1], 0.7);
        for id in 12..=15 {
            assert!(projection.assets().contains(&RenderAsset {
                kind: "texture",
                reference: format!("rbxassetid://{id}"),
                weight: None,
                style: None,
            }));
        }
        assert!(!projection
            .assets()
            .iter()
            .any(|asset| asset.reference.ends_with("99")));
    }

    #[test]
    fn rejects_missing_native_projection_without_fabricating_a_frame() {
        assert_eq!(
            project_frame(json!({"ok":true})).unwrap_err(),
            RenderProjectionError::Unavailable
        );
    }

    #[test]
    fn enforces_shape_and_serialized_size_budgets() {
        let mut malformed = frame();
        malformed["parts"] = json!([{"class":"Part"}]);
        assert!(matches!(
            project_frame(malformed),
            Err(RenderProjectionError::InvalidFrame(_))
        ));
        let mut oversized = frame();
        oversized["lighting"]["large"] = Value::String("x".repeat(MAX_FRAME_BYTES));
        assert_eq!(
            project_frame(oversized).unwrap_err(),
            RenderProjectionError::TooLarge
        );
    }

    #[test]
    fn asset_canonicalization_rejects_unreferenced_or_unsafe_locations() {
        assert_eq!(
            canonical_asset_reference(" rbxassetid://0007 ").as_deref(),
            Some("rbxassetid://7")
        );
        assert!(canonical_asset_reference("rbxasset://textures/../secret.png").is_none());
        assert!(canonical_asset_reference("file:///etc/passwd").is_none());
    }
}
