//! Small deterministic rigid-body stepping helpers for the local client model.
//!
//! These functions implement bounded kinematics against caller-supplied static
//! AABBs. They do not implement Roblox's character controller, dynamic-body
//! solver, replication, or server-confirmed movement.

use crate::world::WorldView;
use std::fmt;

pub type Vec3 = [f64; 3];

const MAX_STEP_SECONDS: f64 = 0.25;
const MAX_FRAME_SECONDS: f64 = 5.0;
const MAX_CATCH_UP_STEPS: usize = 256;
const EPSILON: f64 = 1.0e-10;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PhysicsError {
    InvalidBounds,
    InvalidRotation,
    InvalidFrameDuration,
    InvalidStepDuration,
    InvalidCatchUpLimit,
    InvalidBody,
    InvalidGravity,
    InvalidClockRate,
    InvalidInterval,
    TooManyContactListeners,
    TooManyQueryFilters,
}

impl fmt::Display for PhysicsError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::InvalidBounds => "AABB bounds must be finite and have positive extent",
            Self::InvalidRotation => "rotation must be a finite right-handed orthonormal matrix",
            Self::InvalidFrameDuration => {
                "frame duration must be finite and between 0 and 5 seconds"
            }
            Self::InvalidStepDuration => "fixed step must be finite and between 0 and 0.25 seconds",
            Self::InvalidCatchUpLimit => "catch-up limit must be between 1 and 256 steps",
            Self::InvalidBody => "body state must be finite with positive half sizes",
            Self::InvalidGravity => "gravity and terminal speed must be finite and non-negative",
            Self::InvalidClockRate => {
                "simulation clock rate must be between 1 and 1000 units per second"
            }
            Self::InvalidInterval => {
                "completed interval must be finite and between 0 and 0.25 seconds"
            }
            Self::TooManyContactListeners => "contact listener parent limit is 4096",
            Self::TooManyQueryFilters => "spatial query filter root limit is 4096",
        })
    }
}

impl std::error::Error for PhysicsError {}

fn finite_vector(value: Vec3) -> bool {
    value.into_iter().all(f64::is_finite)
}

/// Static, axis-aligned bounds in world units.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Aabb {
    pub minimum: Vec3,
    pub maximum: Vec3,
}

impl Aabb {
    pub fn new(minimum: Vec3, maximum: Vec3) -> Result<Self, PhysicsError> {
        if !finite_vector(minimum)
            || !finite_vector(maximum)
            || (0..3).any(|axis| minimum[axis] >= maximum[axis])
        {
            return Err(PhysicsError::InvalidBounds);
        }
        Ok(Self { minimum, maximum })
    }
}

pub const IDENTITY_ROTATION: [f64; 9] = [1., 0., 0., 0., 1., 0., 0., 0., 1.];

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct OrientedBox {
    pub center: Vec3,
    pub half_size: Vec3,
    pub rotation: [f64; 9],
}

impl OrientedBox {
    pub fn new(center: Vec3, half_size: Vec3, rotation: [f64; 9]) -> Result<Self, PhysicsError> {
        if !finite_vector(center)
            || !finite_vector(half_size)
            || half_size.into_iter().any(|value| value <= 0.0)
        {
            return Err(PhysicsError::InvalidBounds);
        }
        validate_rotation(rotation)?;
        Ok(Self {
            center,
            half_size,
            rotation,
        })
    }

    pub fn from_aabb(bounds: Aabb) -> Result<Self, PhysicsError> {
        Self::new(
            std::array::from_fn(|axis| (bounds.minimum[axis] + bounds.maximum[axis]) / 2.0),
            std::array::from_fn(|axis| (bounds.maximum[axis] - bounds.minimum[axis]) / 2.0),
            IDENTITY_ROTATION,
        )
    }
}

fn validate_rotation(matrix: [f64; 9]) -> Result<(), PhysicsError> {
    if !matrix.into_iter().all(f64::is_finite) {
        return Err(PhysicsError::InvalidRotation);
    }
    for first in 0..3 {
        for second in 0..3 {
            let dot = (0..3)
                .map(|row| matrix[3 * row + first] * matrix[3 * row + second])
                .sum::<f64>();
            if (dot - if first == second { 1.0 } else { 0.0 }).abs() > 1.0e-5 {
                return Err(PhysicsError::InvalidRotation);
            }
        }
    }
    let [a, b, c, d, e, f, g, h, i] = matrix;
    let determinant = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g);
    if (determinant - 1.0).abs() > 1.0e-5 {
        return Err(PhysicsError::InvalidRotation);
    }
    Ok(())
}

/// Separating-axis overlap for two validated oriented boxes, including touching.
pub fn oriented_boxes_overlap(first: OrientedBox, second: OrientedBox) -> bool {
    let axes_for = |matrix: [f64; 9]| {
        std::array::from_fn::<Vec3, 3, _>(|axis| std::array::from_fn(|row| matrix[3 * row + axis]))
    };
    let axes_a = axes_for(first.rotation);
    let axes_b = axes_for(second.rotation);
    let delta = std::array::from_fn(|axis| second.center[axis] - first.center[axis]);
    let mut axes = Vec::with_capacity(15);
    axes.extend(axes_a);
    axes.extend(axes_b);
    for a in axes_a {
        for b in axes_b {
            axes.push([
                a[1] * b[2] - a[2] * b[1],
                a[2] * b[0] - a[0] * b[2],
                a[0] * b[1] - a[1] * b[0],
            ]);
        }
    }
    for axis in axes {
        let length_squared = axis.into_iter().map(|value| value * value).sum::<f64>();
        if length_squared < 1.0e-20 {
            continue;
        }
        let projection = |point: Vec3| dot(axis, point);
        let radius = (0..3)
            .map(|index| {
                first.half_size[index] * projection(axes_a[index]).abs()
                    + second.half_size[index] * projection(axes_b[index]).abs()
            })
            .sum::<f64>();
        let tolerance = 1.0e-10 * radius.max(length_squared.sqrt());
        if projection(delta).abs() > radius + tolerance {
            return false;
        }
    }
    true
}

/// Cast a world-space ray against a validated oriented box.
pub fn raycast_oriented_box(
    origin: Vec3,
    direction: Vec3,
    bounds: OrientedBox,
    max_distance: f64,
) -> Result<Option<RaycastHit>, PhysicsError> {
    let bounds = OrientedBox::new(bounds.center, bounds.half_size, bounds.rotation)?;
    let offset = std::array::from_fn(|axis| origin[axis] - bounds.center[axis]);
    let mut local_origin = std::array::from_fn(|axis| {
        dot(
            offset,
            std::array::from_fn(|row| bounds.rotation[3 * row + axis]),
        )
    });
    let local_direction = std::array::from_fn(|axis| {
        dot(
            direction,
            std::array::from_fn(|row| bounds.rotation[3 * row + axis]),
        )
    });
    for axis in 0..3 {
        let tolerance = 1.0e-10 * bounds.half_size[axis].max(1.0);
        if (local_origin[axis] - bounds.half_size[axis]).abs() <= tolerance {
            local_origin[axis] = bounds.half_size[axis];
        } else if (local_origin[axis] + bounds.half_size[axis]).abs() <= tolerance {
            local_origin[axis] = -bounds.half_size[axis];
        }
    }
    let local_bounds = Aabb {
        minimum: bounds.half_size.map(|value| -value),
        maximum: bounds.half_size,
    };
    let Some((distance, local_normal)) =
        ray_aabb(local_origin, local_direction, local_bounds, max_distance)
    else {
        return Ok(None);
    };
    let mut normal = std::array::from_fn(|row| {
        (0..3)
            .map(|axis| bounds.rotation[3 * row + axis] * local_normal[axis])
            .sum::<f64>()
    });
    let magnitude = normal[0].hypot(normal[1]).hypot(normal[2]);
    if magnitude > 0.0 {
        normal = normal.map(|value| value / magnitude);
    }
    let direction_magnitude = direction[0].hypot(direction[1]).hypot(direction[2]);
    let unit = direction.map(|value| value / direction_magnitude);
    Ok(Some(RaycastHit {
        surface_index: 0,
        distance,
        position: std::array::from_fn(|axis| origin[axis] + unit[axis] * distance),
        normal,
    }))
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SweepHit {
    /// Fraction of the requested displacement in [0, 1].
    pub fraction: f64,
    pub normal: Vec3,
}

/// Sweep a translating AABB against a stationary oriented box.
pub fn sweep_aabb_oriented_box(
    center: Vec3,
    half_size: Vec3,
    displacement: Vec3,
    obstacle: OrientedBox,
) -> Result<Option<SweepHit>, PhysicsError> {
    if !finite_vector(center)
        || !finite_vector(half_size)
        || !finite_vector(displacement)
        || half_size.into_iter().any(|value| value <= 0.0)
    {
        return Err(PhysicsError::InvalidBody);
    }
    let obstacle = OrientedBox::new(obstacle.center, obstacle.half_size, obstacle.rotation)?;
    let basis = [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]];
    let other = std::array::from_fn::<Vec3, 3, _>(|axis| {
        std::array::from_fn(|row| obstacle.rotation[3 * row + axis])
    });
    let mut axes = Vec::with_capacity(15);
    axes.extend(basis);
    axes.extend(other);
    for first in basis {
        for second in other {
            let cross = [
                first[1] * second[2] - first[2] * second[1],
                first[2] * second[0] - first[0] * second[2],
                first[0] * second[1] - first[1] * second[0],
            ];
            let length = cross[0].hypot(cross[1]).hypot(cross[2]);
            if length > 1.0e-12 {
                axes.push(cross.map(|value| value / length));
            }
        }
    }
    let offset = std::array::from_fn(|axis| center[axis] - obstacle.center[axis]);
    let mut near = f64::NEG_INFINITY;
    let mut far = f64::INFINITY;
    let mut normal = [0.0; 3];
    let mut penetrating = true;
    for axis in axes {
        let mut position = dot(offset, axis);
        let speed = dot(displacement, axis);
        let radius = (0..3)
            .map(|index| {
                half_size[index] * axis[index].abs()
                    + obstacle.half_size[index] * dot(other[index], axis).abs()
            })
            .sum::<f64>();
        if !position.is_finite() || !speed.is_finite() || !radius.is_finite() {
            return Err(PhysicsError::InvalidBody);
        }
        let tolerance = 1.0e-10 * radius.max(1.0);
        if position.abs() >= radius - tolerance {
            penetrating = false;
        }
        if (position.abs() - radius).abs() <= tolerance {
            position = radius.copysign(position);
        }
        if speed == 0.0 {
            if position.abs() > radius {
                return Ok(None);
            }
            continue;
        }
        let mut entry = (-radius - position) / speed;
        let mut exit = (radius - position) / speed;
        let sign = if speed > 0.0 { -1.0 } else { 1.0 };
        if entry > exit {
            std::mem::swap(&mut entry, &mut exit);
        }
        if entry > near {
            near = entry;
            normal = axis.map(|value| sign * value);
        }
        far = far.min(exit);
        if near > far {
            return Ok(None);
        }
    }
    if penetrating {
        return Ok(Some(SweepHit {
            fraction: 0.0,
            normal: [0.0; 3],
        }));
    }
    if far < 0.0 || near < 0.0 || near > 1.0 {
        return Ok(None);
    }
    Ok(Some(SweepHit {
        fraction: near,
        normal,
    }))
}

/// Permit only escape or tangent motion from shallow oriented-box penetration.
pub fn shallow_overlap_allows_motion(
    center: Vec3,
    half_size: Vec3,
    displacement: Vec3,
    obstacle: OrientedBox,
    max_depth: f64,
) -> Result<bool, PhysicsError> {
    if !finite_vector(center)
        || !finite_vector(half_size)
        || !finite_vector(displacement)
        || half_size.into_iter().any(|value| value <= 0.0)
        || !max_depth.is_finite()
        || max_depth < 0.0
    {
        return Err(PhysicsError::InvalidBody);
    }
    let obstacle = OrientedBox::new(obstacle.center, obstacle.half_size, obstacle.rotation)?;
    if displacement.into_iter().all(|value| value == 0.0) {
        return Ok(false);
    }
    let basis = [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]];
    let other = std::array::from_fn::<Vec3, 3, _>(|axis| {
        std::array::from_fn(|row| obstacle.rotation[3 * row + axis])
    });
    let mut axes = Vec::with_capacity(15);
    axes.extend(basis);
    axes.extend(other);
    for first in basis {
        for second in other {
            let cross = [
                first[1] * second[2] - first[2] * second[1],
                first[2] * second[0] - first[0] * second[2],
                first[0] * second[1] - first[1] * second[0],
            ];
            let length = cross[0].hypot(cross[1]).hypot(cross[2]);
            if length > 1.0e-12 {
                axes.push(cross.map(|value| value / length));
            }
        }
    }
    let offset = std::array::from_fn(|axis| center[axis] - obstacle.center[axis]);
    let mut planes = Vec::with_capacity(axes.len());
    for axis in axes {
        let position = dot(offset, axis);
        let radius = (0..3)
            .map(|index| {
                half_size[index] * axis[index].abs()
                    + obstacle.half_size[index] * dot(other[index], axis).abs()
            })
            .sum::<f64>();
        let depth = radius - position.abs();
        if !depth.is_finite() || depth <= 1.0e-10 * radius.max(1.0) {
            return Ok(false);
        }
        planes.push((depth, position, dot(displacement, axis)));
    }
    let nearest = planes
        .iter()
        .map(|plane| plane.0)
        .min_by(f64::total_cmp)
        .unwrap_or(0.0);
    if nearest > max_depth {
        return Ok(false);
    }
    Ok(planes
        .into_iter()
        .filter(|plane| plane.0 <= nearest + 1.0e-9)
        .all(|(_, position, speed)| {
            position.abs() > 1.0e-10 && speed * position.signum() >= -1.0e-10
        }))
}

#[derive(Clone, Debug, PartialEq)]
pub struct OrientedSweepHit {
    pub node_id: String,
    pub surface_index: usize,
    pub hit: SweepHit,
}

/// Return the earliest oriented surface hit, excluding the moving body itself.
pub fn sweep_aabb_oriented_records(
    center: Vec3,
    half_size: Vec3,
    displacement: Vec3,
    surfaces: &[OrientedSurface],
    self_id: &str,
) -> Result<Option<OrientedSweepHit>, PhysicsError> {
    if !finite_vector(center)
        || !finite_vector(half_size)
        || !finite_vector(displacement)
        || half_size.into_iter().any(|value| value <= 0.0)
    {
        return Err(PhysicsError::InvalidBody);
    }
    let mut best: Option<OrientedSweepHit> = None;
    for (surface_index, surface) in surfaces.iter().enumerate() {
        if surface.node_id == self_id {
            continue;
        }
        let Some(hit) = sweep_aabb_oriented_box(center, half_size, displacement, surface.bounds)?
        else {
            continue;
        };
        if best
            .as_ref()
            .is_none_or(|current| hit.fraction < current.hit.fraction)
        {
            best = Some(OrientedSweepHit {
                node_id: surface.node_id.clone(),
                surface_index,
                hit,
            });
        }
    }
    Ok(best)
}

#[derive(Clone, Debug, PartialEq)]
pub struct MotionReport {
    pub center: Vec3,
    /// Motion left unapplied after penetration or the four-contact cap.
    pub remaining: Vec3,
    pub contacts: Vec<OrientedSweepHit>,
}

/// Advance an AABB with bounded collision sliding and explicit leftover motion.
pub fn move_aabb_oriented_records(
    center: Vec3,
    half_size: Vec3,
    displacement: Vec3,
    surfaces: &[OrientedSurface],
    self_id: &str,
) -> Result<MotionReport, PhysicsError> {
    if !finite_vector(center)
        || !finite_vector(half_size)
        || !finite_vector(displacement)
        || half_size.into_iter().any(|value| value <= 0.0)
    {
        return Err(PhysicsError::InvalidBody);
    }
    let mut center = center;
    let mut remaining = displacement;
    let mut contacts = Vec::new();
    for _ in 0..4 {
        if remaining.into_iter().all(|value| value == 0.0) {
            break;
        }
        let Some(contact) =
            sweep_aabb_oriented_records(center, half_size, remaining, surfaces, self_id)?
        else {
            center = std::array::from_fn(|axis| center[axis] + remaining[axis]);
            remaining = [0.0; 3];
            break;
        };
        if contact.hit.normal == [0.0; 3] {
            contacts.push(contact);
            break;
        }
        center = std::array::from_fn(|axis| center[axis] + remaining[axis] * contact.hit.fraction);
        remaining = std::array::from_fn(|axis| remaining[axis] * (1.0 - contact.hit.fraction));
        let inward = dot(remaining, contact.hit.normal);
        remaining = std::array::from_fn(|axis| remaining[axis] - contact.hit.normal[axis] * inward);
        contacts.push(contact);
        if !finite_vector(center) || !finite_vector(remaining) {
            return Err(PhysicsError::InvalidBody);
        }
    }
    if !finite_vector(center) {
        return Err(PhysicsError::InvalidBody);
    }
    Ok(MotionReport {
        center,
        remaining,
        contacts,
    })
}

/// Sweep against received anchored, collidable BaseParts in canonical order.
pub fn sweep_aabb_static_base_parts(
    view: &WorldView,
    parents: &std::collections::HashMap<String, String>,
    center: Vec3,
    half_size: Vec3,
    displacement: Vec3,
    self_id: &str,
) -> Result<Option<OrientedSweepHit>, PhysicsError> {
    let surfaces = static_base_part_oriented_records(view, parents)?;
    sweep_aabb_oriented_records(center, half_size, displacement, &surfaces, self_id)
}

/// Move with collision sliding against received anchored, collidable BaseParts.
pub fn move_aabb_static_base_parts(
    view: &WorldView,
    parents: &std::collections::HashMap<String, String>,
    center: Vec3,
    half_size: Vec3,
    displacement: Vec3,
    self_id: &str,
) -> Result<MotionReport, PhysicsError> {
    let surfaces = static_base_part_oriented_records(view, parents)?;
    move_aabb_oriented_records(center, half_size, displacement, &surfaces, self_id)
}

fn dot(first: Vec3, second: Vec3) -> f64 {
    (0..3).map(|axis| first[axis] * second[axis]).sum()
}

fn received_vec3(value: Option<serde_json::Value>) -> Option<Vec3> {
    let value = value?;
    let values = value.get("$vector3").unwrap_or(&value).as_array()?;
    if values.len() != 3 {
        return None;
    }
    let mut result = [0.0; 3];
    for (output, value) in result.iter_mut().zip(values) {
        *output = value.as_f64().filter(|value| value.is_finite())?;
    }
    Some(result)
}

fn identity_frame(frame: &serde_json::Value) -> bool {
    if let Some(rotation) = frame.get("rotation").and_then(serde_json::Value::as_array) {
        return rotation.len() == 9
            && rotation.iter().enumerate().all(|(index, value)| {
                value.as_f64() == Some(if index % 4 == 0 { 1.0 } else { 0.0 })
            });
    }
    frame.get("rotation_id").and_then(serde_json::Value::as_u64) == Some(2)
}

fn class_is_a(
    class: &str,
    target: &str,
    parents: &std::collections::HashMap<String, String>,
) -> bool {
    let mut current = class;
    for _ in 0..64 {
        if current == target {
            return true;
        }
        let Some(parent) = parents.get(current) else {
            return false;
        };
        current = parent;
    }
    false
}

#[derive(Clone, Debug, PartialEq)]
pub struct StaticSurface {
    pub node_id: String,
    pub bounds: Aabb,
}

/// Build supported static AABB surfaces from received, unrotated Part records.
pub fn static_part_surfaces(view: &WorldView) -> Result<Vec<Aabb>, PhysicsError> {
    surface_records(
        view,
        |node| node.class_name() == "Part",
        Some(true),
        "CanCollide",
    )
    .map(|records| records.into_iter().map(|surface| surface.bounds).collect())
}

/// Build static surfaces for BasePart subclasses using received class ancestry.
pub fn static_base_part_surfaces(
    view: &WorldView,
    parents: &std::collections::HashMap<String, String>,
) -> Result<Vec<Aabb>, PhysicsError> {
    static_base_part_surface_records(view, parents)
        .map(|records| records.into_iter().map(|surface| surface.bounds).collect())
}

pub fn static_base_part_surface_records(
    view: &WorldView,
    parents: &std::collections::HashMap<String, String>,
) -> Result<Vec<StaticSurface>, PhysicsError> {
    surface_records(
        view,
        |node| class_is_a(node.class_name(), "BasePart", parents),
        Some(true),
        "CanCollide",
    )
}

/// Build unrotated BasePart query surfaces using CanQuery, not collision flags.
pub fn base_part_query_surface_records(
    view: &WorldView,
    parents: &std::collections::HashMap<String, String>,
) -> Result<Vec<StaticSurface>, PhysicsError> {
    surface_records(
        view,
        |node| class_is_a(node.class_name(), "BasePart", parents),
        None,
        "CanQuery",
    )
}

/// Build anchored, unrotated Part surfaces with received TouchTransmitter children.
pub fn touch_listener_surface_records(
    view: &WorldView,
) -> Result<Vec<StaticSurface>, PhysicsError> {
    let listeners = touch_listener_parents(view)?;
    surface_records(
        view,
        |node| node.class_name() == "Part" && listeners.contains(node.id()),
        Some(true),
        "CanTouch",
    )
}

fn touch_listener_parents(
    view: &WorldView,
) -> Result<std::collections::HashSet<String>, PhysicsError> {
    let mut listeners = std::collections::HashSet::new();
    view.for_each_node(|node| {
        if node.class_name() == "TouchTransmitter" {
            if let crate::world::ReadOnlyField::Value(parent) = node.parent() {
                listeners.insert(parent.to_owned());
            }
        }
    });
    if listeners.len() > 4096 {
        return Err(PhysicsError::TooManyContactListeners);
    }
    Ok(listeners)
}

#[derive(Clone, Debug, PartialEq)]
pub struct OrientedSurface {
    pub node_id: String,
    pub bounds: OrientedBox,
}

/// Decode anchored, collidable BaseParts as oriented boxes.
pub fn static_base_part_oriented_records(
    view: &WorldView,
    parents: &std::collections::HashMap<String, String>,
) -> Result<Vec<OrientedSurface>, PhysicsError> {
    oriented_surface_records(
        view,
        |node| class_is_a(node.class_name(), "BasePart", parents),
        Some(true),
        "CanCollide",
    )
}

/// Decode CanQuery BaseParts as oriented boxes, including received rotations.
pub fn base_part_query_oriented_records(
    view: &WorldView,
    parents: &std::collections::HashMap<String, String>,
) -> Result<Vec<OrientedSurface>, PhysicsError> {
    oriented_surface_records(
        view,
        |node| class_is_a(node.class_name(), "BasePart", parents),
        None,
        "CanQuery",
    )
}

/// Decode anchored Part touch listeners as oriented boxes using CanTouch.
pub fn touch_listener_oriented_records(
    view: &WorldView,
) -> Result<Vec<OrientedSurface>, PhysicsError> {
    let listeners = touch_listener_parents(view)?;
    oriented_surface_records(
        view,
        |node| node.class_name() == "Part" && listeners.contains(node.id()),
        Some(true),
        "CanTouch",
    )
}

fn oriented_surface_records(
    view: &WorldView,
    included: impl Fn(&crate::world::ReadOnlyNode) -> bool,
    anchored: Option<bool>,
    required_property: &str,
) -> Result<Vec<OrientedSurface>, PhysicsError> {
    let mut surfaces = Vec::new();
    let mut failure = None;
    view.for_each_node(|node| {
        if failure.is_some()
            || !included(&node)
            || anchored.is_some_and(|required| {
                node.property("Anchored").and_then(|value| value.as_bool()) != Some(required)
            })
            || node
                .property(required_property)
                .and_then(|value| value.as_bool())
                != Some(true)
        {
            return;
        }
        let geometry = (|| {
            let frame = node.property("CFrame")?;
            let frame = frame.get("$cframe").unwrap_or(&frame);
            let position = received_vec3(frame.get("position").cloned())?;
            let rotation = received_rotation(frame)?;
            let size = received_vec3(node.property("Size").or_else(|| node.property("size")))?;
            Some(OrientedBox::new(
                position,
                size.map(|value| value / 2.0),
                rotation,
            ))
        })();
        match geometry {
            Some(Ok(bounds)) if surfaces.len() < 65_536 => surfaces.push(OrientedSurface {
                node_id: node.id().to_owned(),
                bounds,
            }),
            Some(Ok(_)) => failure = Some(PhysicsError::InvalidBounds),
            Some(Err(error)) => failure = Some(error),
            None => {}
        }
    });
    if let Some(error) = failure {
        Err(error)
    } else {
        Ok(surfaces)
    }
}

fn received_rotation(frame: &serde_json::Value) -> Option<[f64; 9]> {
    if let Some(rotation) = frame.get("rotation") {
        return rotation_array(rotation);
    }
    let normalized = crate::rpc::normalize_received_cframe(frame).ok()?;
    rotation_array(normalized.get("$cframe")?.get("rotation")?)
}

fn rotation_array(value: &serde_json::Value) -> Option<[f64; 9]> {
    let values = value.as_array()?;
    if values.len() != 9 {
        return None;
    }
    let mut rotation = [0.0; 9];
    for (output, value) in rotation.iter_mut().zip(values) {
        *output = value.as_f64().filter(|value| value.is_finite())?;
    }
    Some(rotation)
}

fn surface_records(
    view: &WorldView,
    included: impl Fn(&crate::world::ReadOnlyNode) -> bool,
    anchored: Option<bool>,
    required_property: &str,
) -> Result<Vec<StaticSurface>, PhysicsError> {
    let mut surfaces = Vec::new();
    let mut failure = None;
    view.for_each_node(|node| {
        if failure.is_some()
            || !included(&node)
            || anchored.is_some_and(|required| {
                node.property("Anchored").and_then(|value| value.as_bool()) != Some(required)
            })
            || node
                .property(required_property)
                .and_then(|value| value.as_bool())
                != Some(true)
        {
            return;
        }
        let geometry = (|| {
            let frame = node.property("CFrame")?;
            let frame = frame.get("$cframe").unwrap_or(&frame);
            if !identity_frame(frame) {
                return None;
            }
            let position = received_vec3(frame.get("position").cloned())?;
            let size = received_vec3(node.property("Size").or_else(|| node.property("size")))?;
            Some((position, size.map(|value| value / 2.0)))
        })();
        let result = match geometry {
            Some((position, half)) => Aabb::new(
                std::array::from_fn(|axis| position[axis] - half[axis]),
                std::array::from_fn(|axis| position[axis] + half[axis]),
            )
            .map(Some),
            None => Ok(None),
        };
        match result {
            Ok(Some(bounds)) if surfaces.len() < 65_536 => surfaces.push(StaticSurface {
                node_id: node.id().to_owned(),
                bounds,
            }),
            Ok(Some(_)) => failure = Some(PhysicsError::InvalidBounds),
            Ok(None) => {}
            Err(error) => failure = Some(error),
        }
    });
    if let Some(error) = failure {
        Err(error)
    } else {
        Ok(surfaces)
    }
}

/// Point ray against one AABB, returning world distance and the outward face normal.
///
/// Direction is normalized internally. A start strictly inside the box reports a
/// zero-distance hit with a zero normal so the caller can treat penetration as
/// explicit state instead of choosing an arbitrary exit side.
pub fn ray_aabb(
    origin: Vec3,
    direction: Vec3,
    bounds: Aabb,
    max_distance: f64,
) -> Option<(f64, Vec3)> {
    if !finite_vector(origin)
        || !finite_vector(direction)
        || !max_distance.is_finite()
        || max_distance < 0.0
    {
        return None;
    }
    let magnitude = direction[0].hypot(direction[1]).hypot(direction[2]);
    if magnitude == 0.0 {
        return None;
    }
    if (0..3).all(|i| bounds.minimum[i] < origin[i] && origin[i] < bounds.maximum[i]) {
        return Some((0.0, [0.0; 3]));
    }
    let unit = direction.map(|value| value / magnitude);
    let mut near = f64::NEG_INFINITY;
    let mut far = f64::INFINITY;
    let mut normal = [0.0; 3];
    for axis in 0..3 {
        if unit[axis] == 0.0 {
            if origin[axis] < bounds.minimum[axis] || origin[axis] > bounds.maximum[axis] {
                return None;
            }
            continue;
        }
        let first = (bounds.minimum[axis] - origin[axis]) / unit[axis];
        let second = (bounds.maximum[axis] - origin[axis]) / unit[axis];
        let (entry, exit, sign) = if first <= second {
            (first, second, -1.0)
        } else {
            (second, first, 1.0)
        };
        if entry > near {
            near = entry;
            normal = [0.0; 3];
            normal[axis] = sign;
        }
        far = far.min(exit);
        if near > far {
            return None;
        }
    }
    if far < 0.0 || near < 0.0 || near > max_distance {
        return None;
    }
    Some((near, normal))
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct RaycastHit {
    pub surface_index: usize,
    pub distance: f64,
    pub position: Vec3,
    pub normal: Vec3,
}

/// Return the nearest supported hit across a static surface set.
pub fn raycast_surfaces(
    origin: Vec3,
    direction: Vec3,
    max_distance: f64,
    surfaces: &[Aabb],
) -> Option<RaycastHit> {
    let magnitude = direction[0].hypot(direction[1]).hypot(direction[2]);
    if !magnitude.is_finite() || magnitude == 0.0 {
        return None;
    }
    let unit = direction.map(|value| value / magnitude);
    let mut best: Option<RaycastHit> = None;
    for (surface_index, surface) in surfaces.iter().enumerate() {
        let Some((distance, normal)) = ray_aabb(origin, direction, *surface, max_distance) else {
            continue;
        };
        if best.is_none_or(|current| distance < current.distance) {
            best = Some(RaycastHit {
                surface_index,
                distance,
                position: std::array::from_fn(|axis| origin[axis] + unit[axis] * distance),
                normal,
            });
        }
    }
    best
}

#[derive(Clone, Debug, PartialEq)]
pub struct StaticRaycastHit {
    pub node_id: String,
    pub hit: RaycastHit,
}

pub fn raycast_oriented_records(
    origin: Vec3,
    direction: Vec3,
    max_distance: f64,
    surfaces: &[OrientedSurface],
) -> Result<Option<StaticRaycastHit>, PhysicsError> {
    raycast_oriented_records_filtered(origin, direction, max_distance, surfaces, |_| true)
}

pub fn raycast_oriented_records_filtered(
    origin: Vec3,
    direction: Vec3,
    max_distance: f64,
    surfaces: &[OrientedSurface],
    include: impl Fn(&str) -> bool,
) -> Result<Option<StaticRaycastHit>, PhysicsError> {
    let mut best: Option<StaticRaycastHit> = None;
    for (surface_index, surface) in surfaces.iter().enumerate() {
        if !include(&surface.node_id) {
            continue;
        }
        let Some(mut hit) = raycast_oriented_box(origin, direction, surface.bounds, max_distance)?
        else {
            continue;
        };
        hit.surface_index = surface_index;
        if best
            .as_ref()
            .is_none_or(|current| hit.distance < current.hit.distance)
        {
            best = Some(StaticRaycastHit {
                node_id: surface.node_id.clone(),
                hit,
            });
        }
    }
    Ok(best)
}

/// Raycast identity-bearing static records without losing the receiving node.
pub fn raycast_static_records(
    origin: Vec3,
    direction: Vec3,
    max_distance: f64,
    surfaces: &[StaticSurface],
) -> Option<StaticRaycastHit> {
    raycast_static_records_filtered(origin, direction, max_distance, surfaces, |_| true)
}

/// Raycast only records accepted by the caller's node predicate.
pub fn raycast_static_records_filtered(
    origin: Vec3,
    direction: Vec3,
    max_distance: f64,
    surfaces: &[StaticSurface],
    include: impl Fn(&str) -> bool,
) -> Option<StaticRaycastHit> {
    let magnitude = direction[0].hypot(direction[1]).hypot(direction[2]);
    if !magnitude.is_finite() || magnitude == 0.0 {
        return None;
    }
    let unit = direction.map(|value| value / magnitude);
    let mut best: Option<StaticRaycastHit> = None;
    for (surface_index, surface) in surfaces.iter().enumerate() {
        if !include(&surface.node_id) {
            continue;
        }
        let Some((distance, normal)) = ray_aabb(origin, direction, surface.bounds, max_distance)
        else {
            continue;
        };
        if best
            .as_ref()
            .is_none_or(|current| distance < current.hit.distance)
        {
            best = Some(StaticRaycastHit {
                node_id: surface.node_id.clone(),
                hit: RaycastHit {
                    surface_index,
                    distance,
                    position: std::array::from_fn(|axis| origin[axis] + unit[axis] * distance),
                    normal,
                },
            });
        }
    }
    best
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum QueryFilterMode {
    Exclude,
    Include,
}

/// Raycast received BasePart query geometry with bounded descendant exclusion.
pub fn raycast_base_part_query(
    view: &WorldView,
    parents: &std::collections::HashMap<String, String>,
    origin: Vec3,
    direction: Vec3,
    max_distance: f64,
    excluded_roots: &[&str],
) -> Result<Option<StaticRaycastHit>, PhysicsError> {
    raycast_base_part_query_filtered(
        view,
        parents,
        origin,
        direction,
        max_distance,
        excluded_roots,
        QueryFilterMode::Exclude,
    )
}

pub fn raycast_base_part_query_filtered(
    view: &WorldView,
    parents: &std::collections::HashMap<String, String>,
    origin: Vec3,
    direction: Vec3,
    max_distance: f64,
    roots: &[&str],
    mode: QueryFilterMode,
) -> Result<Option<StaticRaycastHit>, PhysicsError> {
    if roots.len() > 4096 {
        return Err(PhysicsError::TooManyQueryFilters);
    }
    let surfaces = base_part_query_surface_records(view, parents)?;
    Ok(raycast_static_records_filtered(
        origin,
        direction,
        max_distance,
        &surfaces,
        |id| query_filter_accepts(view, id, roots, mode),
    ))
}

pub fn raycast_base_part_oriented_query(
    view: &WorldView,
    parents: &std::collections::HashMap<String, String>,
    origin: Vec3,
    direction: Vec3,
    max_distance: f64,
    roots: &[&str],
    mode: QueryFilterMode,
) -> Result<Option<StaticRaycastHit>, PhysicsError> {
    if roots.len() > 4096 {
        return Err(PhysicsError::TooManyQueryFilters);
    }
    let surfaces = base_part_query_oriented_records(view, parents)?;
    raycast_oriented_records_filtered(origin, direction, max_distance, &surfaces, |id| {
        query_filter_accepts(view, id, roots, mode)
    })
}

fn query_filter_accepts(view: &WorldView, id: &str, roots: &[&str], mode: QueryFilterMode) -> bool {
    let matched = roots
        .iter()
        .any(|root| node_is_or_descendant(view, id, root));
    matched == (mode == QueryFilterMode::Include)
}

fn node_is_or_descendant(view: &WorldView, id: &str, root: &str) -> bool {
    let mut current = id;
    for _ in 0..64 {
        if current == root {
            return true;
        }
        let Some(parent) = view.parent(current) else {
            return false;
        };
        current = parent;
    }
    false
}

#[derive(Clone, Debug, PartialEq)]
pub struct GroundProbe {
    pub node_id: String,
    pub clearance: f64,
    pub position: Vec3,
    pub normal: Vec3,
}

/// Measure actual support clearance below a body, excluding its own node.
pub fn probe_ground(
    center: Vec3,
    half_size: Vec3,
    max_clearance: f64,
    surfaces: &[StaticSurface],
    self_id: &str,
) -> Option<GroundProbe> {
    if !finite_vector(center)
        || !finite_vector(half_size)
        || half_size.into_iter().any(|value| value <= 0.0)
        || !max_clearance.is_finite()
        || max_clearance < 0.0
    {
        return None;
    }
    let origin = [center[0], center[1] - half_size[1], center[2]];
    let hit =
        raycast_static_records_filtered(origin, [0.0, -1.0, 0.0], max_clearance, surfaces, |id| {
            id != self_id
        })?;
    Some(GroundProbe {
        node_id: hit.node_id,
        clearance: hit.hit.distance,
        position: hit.hit.position,
        normal: hit.hit.normal,
    })
}

/// Return borrowed static records overlapping a query box in canonical order.
pub fn overlapping_static_records<'a>(
    bounds: Aabb,
    surfaces: &'a [StaticSurface],
    exclude: Option<&str>,
) -> Vec<&'a StaticSurface> {
    surfaces
        .iter()
        .filter(|surface| exclude != Some(surface.node_id.as_str()))
        .filter(|surface| {
            (0..3).all(|axis| {
                bounds.minimum[axis] < surface.bounds.maximum[axis]
                    && bounds.maximum[axis] > surface.bounds.minimum[axis]
            })
        })
        .collect()
}

/// Return CanQuery BaseParts overlapping a box, with bounded descendant exclusion.
pub fn overlapping_base_part_query(
    view: &WorldView,
    parents: &std::collections::HashMap<String, String>,
    bounds: Aabb,
    max_parts: usize,
    excluded_roots: &[&str],
) -> Result<Vec<StaticSurface>, PhysicsError> {
    overlapping_base_part_query_filtered(
        view,
        parents,
        bounds,
        max_parts,
        excluded_roots,
        QueryFilterMode::Exclude,
    )
}

pub fn overlapping_base_part_query_filtered(
    view: &WorldView,
    parents: &std::collections::HashMap<String, String>,
    bounds: Aabb,
    max_parts: usize,
    roots: &[&str],
    mode: QueryFilterMode,
) -> Result<Vec<StaticSurface>, PhysicsError> {
    if roots.len() > 4096 {
        return Err(PhysicsError::TooManyQueryFilters);
    }
    let bounds = Aabb::new(bounds.minimum, bounds.maximum)?;
    let surfaces = base_part_query_surface_records(view, parents)?;
    let limit = if max_parts == 0 {
        usize::MAX
    } else {
        max_parts
    };
    Ok(overlapping_static_records(bounds, &surfaces, None)
        .into_iter()
        .filter(|surface| query_filter_accepts(view, &surface.node_id, roots, mode))
        .take(limit)
        .cloned()
        .collect())
}

/// Return borrowed records whose bounds intersect a sphere in canonical order.
pub fn overlapping_static_records_in_radius<'a>(
    center: Vec3,
    radius: f64,
    surfaces: &'a [StaticSurface],
    exclude: Option<&str>,
) -> Result<Vec<&'a StaticSurface>, PhysicsError> {
    if !finite_vector(center) || !radius.is_finite() || radius < 0.0 {
        return Err(PhysicsError::InvalidBounds);
    }
    Ok(surfaces
        .iter()
        .filter(|surface| exclude != Some(surface.node_id.as_str()))
        .filter(|surface| {
            let distance = (0..3)
                .map(|axis| {
                    if center[axis] < surface.bounds.minimum[axis] {
                        surface.bounds.minimum[axis] - center[axis]
                    } else if center[axis] > surface.bounds.maximum[axis] {
                        center[axis] - surface.bounds.maximum[axis]
                    } else {
                        0.0
                    }
                })
                .reduce(|first, second| first.hypot(second))
                .unwrap_or(0.0);
            distance <= radius
        })
        .collect())
}

/// Return CanQuery BaseParts whose bounds intersect a query sphere.
pub fn overlapping_base_part_radius_query(
    view: &WorldView,
    parents: &std::collections::HashMap<String, String>,
    center: Vec3,
    radius: f64,
    max_parts: usize,
    excluded_roots: &[&str],
) -> Result<Vec<StaticSurface>, PhysicsError> {
    overlapping_base_part_radius_query_filtered(
        view,
        parents,
        center,
        radius,
        max_parts,
        excluded_roots,
        QueryFilterMode::Exclude,
    )
}

pub fn overlapping_base_part_radius_query_filtered(
    view: &WorldView,
    parents: &std::collections::HashMap<String, String>,
    center: Vec3,
    radius: f64,
    max_parts: usize,
    roots: &[&str],
    mode: QueryFilterMode,
) -> Result<Vec<StaticSurface>, PhysicsError> {
    if roots.len() > 4096 {
        return Err(PhysicsError::TooManyQueryFilters);
    }
    let surfaces = base_part_query_surface_records(view, parents)?;
    let limit = if max_parts == 0 {
        usize::MAX
    } else {
        max_parts
    };
    Ok(
        overlapping_static_records_in_radius(center, radius, &surfaces, None)?
            .into_iter()
            .filter(|surface| query_filter_accepts(view, &surface.node_id, roots, mode))
            .take(limit)
            .cloned()
            .collect(),
    )
}

/// Return oriented records overlapping a validated query box in canonical order.
pub fn overlapping_oriented_records<'a>(
    query: OrientedBox,
    surfaces: &'a [OrientedSurface],
    exclude: Option<&str>,
) -> Result<Vec<&'a OrientedSurface>, PhysicsError> {
    let query = OrientedBox::new(query.center, query.half_size, query.rotation)?;
    Ok(surfaces
        .iter()
        .filter(|surface| exclude != Some(surface.node_id.as_str()))
        .filter(|surface| oriented_boxes_overlap(query, surface.bounds))
        .collect())
}

pub fn overlapping_base_part_oriented_query(
    view: &WorldView,
    parents: &std::collections::HashMap<String, String>,
    query: OrientedBox,
    max_parts: usize,
    roots: &[&str],
    mode: QueryFilterMode,
) -> Result<Vec<OrientedSurface>, PhysicsError> {
    if roots.len() > 4096 {
        return Err(PhysicsError::TooManyQueryFilters);
    }
    let surfaces = base_part_query_oriented_records(view, parents)?;
    let limit = if max_parts == 0 {
        usize::MAX
    } else {
        max_parts
    };
    Ok(overlapping_oriented_records(query, &surfaces, None)?
        .into_iter()
        .filter(|surface| query_filter_accepts(view, &surface.node_id, roots, mode))
        .take(limit)
        .cloned()
        .collect())
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ContactTransition {
    pub node_id: String,
    pub began: bool,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ContactTracker {
    active: Vec<String>,
}

impl ContactTracker {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn active_nodes(&self) -> &[String] {
        &self.active
    }

    pub fn synchronize_body(
        &mut self,
        body: BodyState,
        surfaces: &[StaticSurface],
        self_id: &str,
    ) -> Result<Vec<ContactTransition>, PhysicsError> {
        Ok(self.synchronize(body.bounds()?, surfaces, self_id))
    }

    pub fn synchronize_oriented_body(
        &mut self,
        body: BodyState,
        surfaces: &[OrientedSurface],
        self_id: &str,
    ) -> Result<Vec<ContactTransition>, PhysicsError> {
        body.validate()?;
        let query = OrientedBox::new(body.center, body.half_size, IDENTITY_ROTATION)?;
        let mut current = Vec::new();
        for surface in overlapping_oriented_records(query, surfaces, Some(self_id))? {
            if !current.contains(&surface.node_id) {
                current.push(surface.node_id.clone());
            }
        }
        Ok(self.replace_active(current))
    }

    pub fn synchronize(
        &mut self,
        bounds: Aabb,
        surfaces: &[StaticSurface],
        self_id: &str,
    ) -> Vec<ContactTransition> {
        let mut current = Vec::new();
        for surface in overlapping_static_records(bounds, surfaces, Some(self_id)) {
            if !current.contains(&surface.node_id) {
                current.push(surface.node_id.clone());
            }
        }
        self.replace_active(current)
    }

    fn replace_active(&mut self, current: Vec<String>) -> Vec<ContactTransition> {
        let mut transitions = self
            .active
            .iter()
            .filter(|id| !current.contains(id))
            .map(|node_id| ContactTransition {
                node_id: node_id.clone(),
                began: false,
            })
            .collect::<Vec<_>>();
        transitions.extend(
            current
                .iter()
                .filter(|id| !self.active.contains(id))
                .map(|node_id| ContactTransition {
                    node_id: node_id.clone(),
                    began: true,
                }),
        );
        self.active = current;
        transitions
    }

    pub fn clear(&mut self) -> Vec<ContactTransition> {
        self.active
            .drain(..)
            .map(|node_id| ContactTransition {
                node_id,
                began: false,
            })
            .collect()
    }
}

/// Return the first top-face contact crossed by a downward-moving box.
///
/// `start_center`/`end_center` are world-space centers and `half_size` is the
/// body's axis-aligned half extent. The sweep tests the body's bottom against
/// AABB top faces and requires overlap on X and Z. It intentionally does not
/// resolve initial penetration, ceilings, sides, or moving surfaces.
pub fn sweep_ground_aabbs(
    start_center: Vec3,
    end_center: Vec3,
    half_size: Vec3,
    surfaces: &[Aabb],
) -> Result<Option<GroundHit>, PhysicsError> {
    sweep_ground_candidates(
        start_center,
        end_center,
        half_size,
        surfaces
            .iter()
            .enumerate()
            .map(|(index, surface)| (index, *surface)),
    )
}

/// Return the first static top-face contact, preserving its received node ID.
pub fn sweep_ground_static_records(
    start_center: Vec3,
    end_center: Vec3,
    half_size: Vec3,
    surfaces: &[StaticSurface],
    self_id: &str,
) -> Result<Option<StaticGroundHit>, PhysicsError> {
    let hit = sweep_ground_candidates(
        start_center,
        end_center,
        half_size,
        surfaces
            .iter()
            .enumerate()
            .filter(|(_, surface)| surface.node_id != self_id)
            .map(|(index, surface)| (index, surface.bounds)),
    )?;
    Ok(hit.map(|hit| StaticGroundHit {
        node_id: surfaces[hit.surface_index].node_id.clone(),
        hit,
    }))
}

fn sweep_ground_candidates(
    start_center: Vec3,
    end_center: Vec3,
    half_size: Vec3,
    surfaces: impl Iterator<Item = (usize, Aabb)>,
) -> Result<Option<GroundHit>, PhysicsError> {
    if !finite_vector(start_center)
        || !finite_vector(end_center)
        || !finite_vector(half_size)
        || half_size.into_iter().any(|v| v <= 0.0)
    {
        return Err(PhysicsError::InvalidBody);
    }
    if end_center[1] >= start_center[1] {
        return Ok(None);
    }
    let start_foot = start_center[1] - half_size[1];
    let end_foot = end_center[1] - half_size[1];
    let mut best: Option<GroundHit> = None;
    for (index, surface) in surfaces {
        let top = surface.maximum[1];
        // Require a downward crossing from above; embedded starts are left to
        // the caller because their contact normal is not determined here.
        if start_foot + EPSILON < top || end_foot > top + EPSILON {
            continue;
        }
        let fraction = ((start_foot - top) / (start_foot - end_foot)).clamp(0.0, 1.0);
        let x = start_center[0] + (end_center[0] - start_center[0]) * fraction;
        let z = start_center[2] + (end_center[2] - start_center[2]) * fraction;
        let overlaps_x =
            x + half_size[0] > surface.minimum[0] && x - half_size[0] < surface.maximum[0];
        let overlaps_z =
            z + half_size[2] > surface.minimum[2] && z - half_size[2] < surface.maximum[2];
        if !overlaps_x || !overlaps_z {
            continue;
        }
        let candidate = GroundHit {
            center: [x, top + half_size[1], z],
            fraction,
            normal: [0.0, 1.0, 0.0],
            surface_index: index,
        };
        if best.is_none_or(|previous| candidate.fraction < previous.fraction) {
            best = Some(candidate);
        }
    }
    Ok(best)
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct GroundHit {
    pub center: Vec3,
    /// Fraction of the swept segment in [0, 1].
    pub fraction: f64,
    pub normal: Vec3,
    pub surface_index: usize,
}

#[derive(Clone, Debug, PartialEq)]
pub struct StaticGroundHit {
    pub node_id: String,
    pub hit: GroundHit,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct BodyState {
    pub center: Vec3,
    pub velocity: Vec3,
    pub half_size: Vec3,
    pub grounded: bool,
}

impl BodyState {
    pub fn validate(self) -> Result<(), PhysicsError> {
        if !finite_vector(self.center)
            || !finite_vector(self.velocity)
            || !finite_vector(self.half_size)
            || self.half_size.into_iter().any(|value| value <= 0.0)
        {
            return Err(PhysicsError::InvalidBody);
        }
        Ok(())
    }

    pub fn bounds(self) -> Result<Aabb, PhysicsError> {
        self.validate()?;
        let mut minimum = self.center;
        let mut maximum = self.center;
        for axis in 0..3 {
            minimum[axis] -= self.half_size[axis];
            maximum[axis] += self.half_size[axis];
        }
        Aabb::new(minimum, maximum)
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct StepReport {
    pub fixed_steps: usize,
    pub simulated_seconds: f64,
    /// Whole fixed steps omitted after the configured catch-up cap.
    pub dropped_seconds: f64,
    pub contacts: usize,
}

#[derive(Clone, Debug, PartialEq)]
pub struct StaticStepReport {
    pub report: StepReport,
    pub ground: Option<GroundProbe>,
}

#[derive(Clone, Debug)]
pub struct PhysicsStepper {
    pub gravity: f64,
    pub terminal_speed: f64,
    pub step_seconds: f64,
    pub max_catch_up_steps: usize,
    accumulator: f64,
}

impl PhysicsStepper {
    pub fn new(
        gravity: f64,
        terminal_speed: f64,
        step_seconds: f64,
        max_catch_up_steps: usize,
    ) -> Result<Self, PhysicsError> {
        if !gravity.is_finite()
            || gravity < 0.0
            || !terminal_speed.is_finite()
            || terminal_speed <= 0.0
        {
            return Err(PhysicsError::InvalidGravity);
        }
        if !step_seconds.is_finite()
            || !(f64::MIN_POSITIVE..=MAX_STEP_SECONDS).contains(&step_seconds)
        {
            return Err(PhysicsError::InvalidStepDuration);
        }
        if !(1..=MAX_CATCH_UP_STEPS).contains(&max_catch_up_steps) {
            return Err(PhysicsError::InvalidCatchUpLimit);
        }
        Ok(Self {
            gravity,
            terminal_speed,
            step_seconds,
            max_catch_up_steps,
            accumulator: 0.0,
        })
    }

    pub fn step(
        &mut self,
        state: &mut BodyState,
        frame_seconds: f64,
        surfaces: &[Aabb],
    ) -> Result<StepReport, PhysicsError> {
        state.validate()?;
        if !frame_seconds.is_finite() || !(0.0..=MAX_FRAME_SECONDS).contains(&frame_seconds) {
            return Err(PhysicsError::InvalidFrameDuration);
        }
        self.accumulator += frame_seconds;
        let available = ((self.accumulator + 1e-12) / self.step_seconds).floor() as usize;
        let fixed_steps = available.min(self.max_catch_up_steps);
        let simulated_seconds = fixed_steps as f64 * self.step_seconds;
        self.accumulator = (self.accumulator - simulated_seconds).max(0.0);
        let mut contacts = 0;
        for _ in 0..fixed_steps {
            let dt = self.step_seconds;
            let mut horizontal_advanced = false;
            let start_vy = state.velocity[1].clamp(-self.terminal_speed, self.terminal_speed);
            if state.grounded && start_vy <= 0.0 {
                // A support hit from the previous frame persists only while
                // the footprint still overlaps an actual AABB top.
                let supported = surfaces.iter().any(|surface| {
                    let foot_y = state.center[1] - state.half_size[1];
                    (foot_y - surface.maximum[1]).abs() <= 1e-6
                        && state.center[0] + state.half_size[0] > surface.minimum[0]
                        && state.center[0] - state.half_size[0] < surface.maximum[0]
                        && state.center[2] + state.half_size[2] > surface.minimum[2]
                        && state.center[2] - state.half_size[2] < surface.maximum[2]
                });
                if supported {
                    state.center[0] += state.velocity[0] * dt;
                    state.center[2] += state.velocity[2] * dt;
                    horizontal_advanced = true;
                    let support_at_end = surfaces.iter().any(|surface| {
                        let foot_y = state.center[1] - state.half_size[1];
                        (foot_y - surface.maximum[1]).abs() <= 1e-6
                            && state.center[0] + state.half_size[0] > surface.minimum[0]
                            && state.center[0] - state.half_size[0] < surface.maximum[0]
                            && state.center[2] + state.half_size[2] > surface.minimum[2]
                            && state.center[2] - state.half_size[2] < surface.maximum[2]
                    });
                    if support_at_end {
                        state.velocity[1] = 0.0;
                        continue;
                    }
                }
                state.grounded = false;
            }

            let start = state.center;
            let vy = start_vy;
            let free_fall_time = if self.gravity > 0.0 {
                ((vy + self.terminal_speed) / self.gravity).clamp(0.0, dt)
            } else {
                0.0
            };
            let next_vy = if self.gravity > 0.0 {
                (vy - self.gravity * free_fall_time).max(-self.terminal_speed)
            } else {
                vy
            };
            let fall_displacement = vy * free_fall_time
                - 0.5 * self.gravity * free_fall_time * free_fall_time
                + next_vy * (dt - free_fall_time);
            let mut end = [
                if horizontal_advanced {
                    start[0]
                } else {
                    start[0] + state.velocity[0] * dt
                },
                start[1] + fall_displacement,
                if horizontal_advanced {
                    start[2]
                } else {
                    start[2] + state.velocity[2] * dt
                },
            ];
            if let Some(hit) = sweep_ground_aabbs(start, end, state.half_size, surfaces)? {
                end[1] = hit.center[1];
                state.velocity[1] = 0.0;
                state.grounded = true;
                contacts += 1;
            } else {
                state.velocity[1] = next_vy;
                state.grounded = false;
            }
            state.center = end;
        }

        let mut dropped_seconds = 0.0;
        if self.accumulator + 1e-12 >= self.step_seconds {
            let dropped_steps = ((self.accumulator + 1e-12) / self.step_seconds).floor();
            dropped_seconds = dropped_steps * self.step_seconds;
            self.accumulator = (self.accumulator - dropped_seconds).max(0.0);
        }
        Ok(StepReport {
            fixed_steps,
            simulated_seconds,
            dropped_seconds,
            contacts,
        })
    }

    pub fn step_static_records(
        &mut self,
        state: &mut BodyState,
        frame_seconds: f64,
        surfaces: &[StaticSurface],
        self_id: &str,
    ) -> Result<StaticStepReport, PhysicsError> {
        let collision = surfaces
            .iter()
            .filter(|surface| surface.node_id != self_id)
            .map(|surface| surface.bounds)
            .collect::<Vec<_>>();
        let report = self.step(state, frame_seconds, &collision)?;
        let ground = if state.grounded {
            probe_ground(state.center, state.half_size, 1.0e-6, surfaces, self_id)
        } else {
            None
        };
        Ok(StaticStepReport { report, ground })
    }
}

/// Counts completed local simulation intervals at an explicitly chosen rate.
/// The counter wraps to u32 only when exported as `step`.
#[derive(Clone, Debug)]
pub struct SimulationStepClock {
    units_per_second: u32,
    whole_units: u64,
    fractional_units: f64,
    completed_intervals: u64,
}

impl SimulationStepClock {
    pub fn new(units_per_second: u32) -> Result<Self, PhysicsError> {
        if !(1..=1000).contains(&units_per_second) {
            return Err(PhysicsError::InvalidClockRate);
        }
        Ok(Self {
            units_per_second,
            whole_units: 0,
            fractional_units: 0.0,
            completed_intervals: 0,
        })
    }

    pub fn advance_completed_interval(&mut self, simulated_delta: f64) -> Result<(), PhysicsError> {
        if !simulated_delta.is_finite() || !(0.0..=MAX_STEP_SECONDS).contains(&simulated_delta) {
            return Err(PhysicsError::InvalidInterval);
        }
        let total = self.fractional_units + simulated_delta * f64::from(self.units_per_second);
        let whole = total.floor() as u64;
        self.fractional_units = total - whole as f64;
        self.whole_units = self.whole_units.saturating_add(whole);
        self.completed_intervals = self.completed_intervals.saturating_add(1);
        Ok(())
    }

    pub fn step(&self) -> u32 {
        self.whole_units as u32
    }
    pub fn whole_units(&self) -> u64 {
        self.whole_units
    }
    pub fn completed_intervals(&self) -> u64 {
        self.completed_intervals
    }
    pub fn fractional_units(&self) -> f64 {
        self.fractional_units
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bounds(lo: Vec3, hi: Vec3) -> Aabb {
        Aabb::new(lo, hi).unwrap()
    }

    #[test]
    fn oriented_box_overlap_uses_rotation_and_validates_rigid_matrices() {
        let angle = std::f64::consts::FRAC_PI_4;
        let (sin, cos) = angle.sin_cos();
        let rotated = OrientedBox::new(
            [0.; 3],
            [4., 0.1, 0.1],
            [cos, -sin, 0., sin, cos, 0., 0., 0., 1.],
        )
        .unwrap();
        let outside = OrientedBox::new([2., -2., 0.], [0.1; 3], IDENTITY_ROTATION).unwrap();
        let crossing = OrientedBox::new([1., 1., 0.], [0.1; 3], IDENTITY_ROTATION).unwrap();
        assert!(!oriented_boxes_overlap(rotated, outside));
        assert!(oriented_boxes_overlap(rotated, crossing));

        let first = OrientedBox::from_aabb(bounds([-1.; 3], [1.; 3])).unwrap();
        let touching = OrientedBox::new([2., 0., 0.], [1.; 3], IDENTITY_ROTATION).unwrap();
        let separated = OrientedBox::new([2.01, 0., 0.], [1.; 3], IDENTITY_ROTATION).unwrap();
        assert!(oriented_boxes_overlap(first, touching));
        assert!(!oriented_boxes_overlap(first, separated));
        assert_eq!(
            OrientedBox::new([0.; 3], [1.; 3], [2., 0., 0., 0., 1., 0., 0., 0., 1.]),
            Err(PhysicsError::InvalidRotation)
        );
        assert_eq!(
            OrientedBox::new([0.; 3], [1.; 3], [-1., 0., 0., 0., 1., 0., 0., 0., 1.]),
            Err(PhysicsError::InvalidRotation)
        );
        assert!(OrientedBox::new([0.; 3], [1.; 3], [f64::NAN; 9]).is_err());
    }

    #[test]
    fn oriented_box_raycast_returns_world_hits_and_normals() {
        let rotated = OrientedBox::new(
            [0.; 3],
            [4., 0.1, 0.1],
            [0., -1., 0., 1., 0., 0., 0., 0., 1.],
        )
        .unwrap();
        let hit = raycast_oriented_box([0., 5., 0.], [0., -1., 0.], rotated, 10.)
            .unwrap()
            .unwrap();
        assert_eq!(hit.distance, 1.);
        assert_eq!(hit.position, [0., 4., 0.]);
        assert_eq!(hit.normal, [0., 1., 0.]);
        let inside = raycast_oriented_box([0.; 3], [1., 0., 0.], rotated, 10.)
            .unwrap()
            .unwrap();
        assert_eq!(inside.distance, 0.);
        assert_eq!(inside.normal, [0.; 3]);
        assert!(
            raycast_oriented_box([0., 5., 0.], [0., -1., 0.], rotated, 0.99)
                .unwrap()
                .is_none()
        );
        assert!(raycast_oriented_box([0.; 3], [0.; 3], rotated, 10.)
            .unwrap()
            .is_none());
        assert_eq!(
            raycast_oriented_box(
                [0.; 3],
                [1., 0., 0.],
                OrientedBox {
                    center: [0.; 3],
                    half_size: [1.; 3],
                    rotation: [0.; 9],
                },
                10.,
            ),
            Err(PhysicsError::InvalidRotation)
        );
    }

    #[test]
    fn oriented_sweep_returns_first_contact_and_explicit_initial_overlap() {
        let obstacle = OrientedBox::new([0.; 3], [1.; 3], IDENTITY_ROTATION).unwrap();
        let hit = sweep_aabb_oriented_box([-3., 0., 0.], [0.5; 3], [4., 0., 0.], obstacle)
            .unwrap()
            .unwrap();
        assert_eq!(hit.fraction, 0.375);
        assert_eq!(hit.normal, [-1., 0., 0.]);
        let penetrating = sweep_aabb_oriented_box([0.; 3], [0.5; 3], [1., 0., 0.], obstacle)
            .unwrap()
            .unwrap();
        assert_eq!(penetrating.fraction, 0.);
        assert_eq!(penetrating.normal, [0.; 3]);
        assert!(
            sweep_aabb_oriented_box([-3., 0., 0.], [0.5; 3], [0.; 3], obstacle)
                .unwrap()
                .is_none()
        );

        let rotated = OrientedBox::new(
            [0.; 3],
            [2., 0.5, 0.5],
            [0., -1., 0., 1., 0., 0., 0., 0., 1.],
        )
        .unwrap();
        let hit = sweep_aabb_oriented_box([0., 4., 0.], [0.5; 3], [0., -3., 0.], rotated)
            .unwrap()
            .unwrap();
        assert_eq!(hit.fraction, 0.5);
        assert_eq!(hit.normal, [0., 1., 0.]);
    }

    #[test]
    fn shallow_overlap_only_allows_escape_or_tangent_motion() {
        let obstacle = OrientedBox::new([0.; 3], [1.; 3], IDENTITY_ROTATION).unwrap();
        assert!(shallow_overlap_allows_motion(
            [-1.4, 0., 0.],
            [0.5; 3],
            [-0.1, 0., 0.],
            obstacle,
            0.15,
        )
        .unwrap());
        assert!(shallow_overlap_allows_motion(
            [-1.4, 0., 0.],
            [0.5; 3],
            [0., 0., 1.],
            obstacle,
            0.15,
        )
        .unwrap());
        assert!(!shallow_overlap_allows_motion(
            [-1.4, 0., 0.],
            [0.5; 3],
            [0.1, 0., 0.],
            obstacle,
            0.15,
        )
        .unwrap());
        assert!(!shallow_overlap_allows_motion(
            [-0.5, 0., 0.],
            [0.5; 3],
            [-0.1, 0., 0.],
            obstacle,
            0.15,
        )
        .unwrap());
        assert!(
            !shallow_overlap_allows_motion([0.; 3], [0.5; 3], [1., 0., 0.], obstacle, 2.,).unwrap()
        );
        assert!(
            !shallow_overlap_allows_motion([-1.4, 0., 0.], [0.5; 3], [0.; 3], obstacle, 0.15,)
                .unwrap()
        );
        assert!(shallow_overlap_allows_motion(
            [-1.4, 0., 0.],
            [0.5; 3],
            [-0.1, 0., 0.],
            obstacle,
            -1.,
        )
        .is_err());
    }

    #[test]
    fn static_oriented_sweep_chooses_earliest_received_collision() {
        let part = |id, x| {
            serde_json::json!({
                "id":id,"class_name":"Part","parent":null,"properties":{
                    "Anchored":true,"CanCollide":true,"CanQuery":false,
                    "Size":{"$vector3":[2,2,2]},
                    "CFrame":{"position":[x,0,0],"rotation_id":2}
                }
            })
        };
        let world = crate::world::WorldStore::from_snapshot(&serde_json::json!({
            "version":1,"nodes":[part("far",0),part("near",-2),part("self",-5)]
        }))
        .unwrap();
        let parents = std::collections::HashMap::from([("Part".into(), "BasePart".into())]);
        let hit = sweep_aabb_static_base_parts(
            &world.view(),
            &parents,
            [-5., 0., 0.],
            [0.5; 3],
            [8., 0., 0.],
            "self",
        )
        .unwrap()
        .unwrap();
        assert_eq!(hit.node_id, "near");
        assert_eq!(hit.surface_index, 1);
        assert_eq!(hit.hit.fraction, 0.1875);
        assert_eq!(hit.hit.normal, [-1., 0., 0.]);
        let own_hit = sweep_aabb_static_base_parts(
            &world.view(),
            &parents,
            [-5., 0., 0.],
            [0.5; 3],
            [8., 0., 0.],
            "other",
        )
        .unwrap()
        .unwrap();
        assert_eq!(own_hit.node_id, "self");
        assert_eq!(own_hit.hit.fraction, 0.);
        let movement = move_aabb_static_base_parts(
            &world.view(),
            &parents,
            [-5., 0., 0.],
            [0.5; 3],
            [8., 0., 0.],
            "self",
        )
        .unwrap();
        assert_eq!(movement.center, [-3.5, 0., 0.]);
        assert_eq!(movement.remaining, [0.; 3]);
        assert_eq!(movement.contacts.len(), 1);
        assert_eq!(movement.contacts[0].node_id, "near");
    }

    #[test]
    fn oriented_motion_slides_and_preserves_unapplied_displacement() {
        let wall = OrientedSurface {
            node_id: "wall".into(),
            bounds: OrientedBox::new([0.; 3], [1., 2., 2.], IDENTITY_ROTATION).unwrap(),
        };
        let report = move_aabb_oriented_records(
            [-3., 0., 0.],
            [0.5; 3],
            [4., 0., 4.],
            std::slice::from_ref(&wall),
            "character",
        )
        .unwrap();
        assert_eq!(report.center, [-1.5, 0., 4.]);
        assert_eq!(report.remaining, [0.; 3]);
        assert_eq!(report.contacts.len(), 1);
        assert_eq!(report.contacts[0].node_id, "wall");

        let corner = OrientedSurface {
            node_id: "corner".into(),
            bounds: OrientedBox::new([0.; 3], [2., 2., 1.], IDENTITY_ROTATION).unwrap(),
        };
        let report = move_aabb_oriented_records(
            [-3., 0., -3.],
            [0.5; 3],
            [4., 0., 4.],
            &[wall.clone(), corner],
            "character",
        )
        .unwrap();
        assert_eq!(report.center, [-1.5, 0., -1.5]);
        assert_eq!(report.remaining, [0.; 3]);
        assert_eq!(report.contacts.len(), 2);

        let report =
            move_aabb_oriented_records([0.; 3], [0.5; 3], [1., 0., 0.], &[wall], "character")
                .unwrap();
        assert_eq!(report.center, [0.; 3]);
        assert_eq!(report.remaining, [1., 0., 0.]);
        assert_eq!(report.contacts[0].hit.normal, [0.; 3]);
        assert!(move_aabb_oriented_records([0.; 3], [0.; 3], [0.; 3], &[], "character").is_err());
    }

    #[test]
    fn received_rotations_produce_queryable_oriented_records() {
        let world = crate::world::WorldStore::from_snapshot(&serde_json::json!({
            "version":1,"nodes":[
                {"id":"explicit","class_name":"Part","parent":null,"properties":{
                    "Anchored":true,"CanTouch":true,"CanQuery":true,"CanCollide":false,
                    "Size":{"$vector3":[8,0.2,0.2]},
                    "CFrame":{"position":[10,0,0],"rotation":[0,-1,0,1,0,0,0,0,1]}
                }},
                {"id":"selector","class_name":"Part","parent":null,"properties":{
                    "Anchored":true,"CanTouch":true,"CanQuery":true,"CanCollide":false,
                    "Size":{"$vector3":[2,2,2]},
                    "CFrame":{"position":[20,0,0],"rotation_id":17,"packed_rotation":null}
                }},
                {"id":"touch-explicit","class_name":"TouchTransmitter","parent":"explicit","properties":{}},
                {"id":"touch-selector","class_name":"TouchTransmitter","parent":"selector","properties":{}},
                {"id":"disabled","class_name":"Part","parent":null,"properties":{
                    "CanQuery":false,"Size":{"$vector3":[2,2,2]},
                    "CFrame":{"position":[0,0,0],"rotation_id":2}
                }}
            ]
        }))
        .unwrap();
        let parents = std::collections::HashMap::from([("Part".into(), "BasePart".into())]);
        let records = base_part_query_oriented_records(&world.view(), &parents).unwrap();
        assert_eq!(
            records
                .iter()
                .map(|record| record.node_id.as_str())
                .collect::<Vec<_>>(),
            ["explicit", "selector"]
        );
        let hit = raycast_oriented_box([10., 5., 0.], [0., -1., 0.], records[0].bounds, 10.)
            .unwrap()
            .unwrap();
        assert_eq!(hit.position, [10., 4., 0.]);
        assert_eq!(hit.normal, [0., 1., 0.]);
        let record_hit = raycast_oriented_records([10., 5., 0.], [0., -1., 0.], 10., &records)
            .unwrap()
            .unwrap();
        assert_eq!(record_hit.node_id, "explicit");
        assert_eq!(record_hit.hit.surface_index, 0);
        let world_hit = raycast_base_part_oriented_query(
            &world.view(),
            &parents,
            [10., 5., 0.],
            [0., -1., 0.],
            10.,
            &[],
            QueryFilterMode::Exclude,
        )
        .unwrap()
        .unwrap();
        assert_eq!(world_hit.node_id, "explicit");
        assert!(raycast_base_part_oriented_query(
            &world.view(),
            &parents,
            [10., 5., 0.],
            [0., -1., 0.],
            10.,
            &["selector"],
            QueryFilterMode::Include,
        )
        .unwrap()
        .is_none());
        let miss = OrientedBox::new([12., -2., 0.], [0.1; 3], IDENTITY_ROTATION).unwrap();
        assert!(overlapping_oriented_records(miss, &records, None)
            .unwrap()
            .is_empty());
        let crossing = OrientedBox::new([10., 1., 0.], [0.1; 3], IDENTITY_ROTATION).unwrap();
        let overlaps = overlapping_base_part_oriented_query(
            &world.view(),
            &parents,
            crossing,
            1,
            &[],
            QueryFilterMode::Exclude,
        )
        .unwrap();
        assert_eq!(overlaps.len(), 1);
        assert_eq!(overlaps[0].node_id, "explicit");
        assert!(overlapping_base_part_oriented_query(
            &world.view(),
            &parents,
            crossing,
            0,
            &["selector"],
            QueryFilterMode::Include,
        )
        .unwrap()
        .is_empty());
        let listeners = touch_listener_oriented_records(&world.view()).unwrap();
        assert_eq!(
            listeners
                .iter()
                .map(|record| record.node_id.as_str())
                .collect::<Vec<_>>(),
            ["explicit", "selector"]
        );
        let body = BodyState {
            center: [10., 4.5, 0.],
            velocity: [0.; 3],
            half_size: [0.5; 3],
            grounded: true,
        };
        let mut contacts = ContactTracker::new();
        assert_eq!(
            contacts
                .synchronize_oriented_body(body, &listeners, "character")
                .unwrap(),
            [ContactTransition {
                node_id: "explicit".into(),
                began: true,
            }]
        );
        assert!(base_part_query_surface_records(&world.view(), &parents)
            .unwrap()
            .is_empty());
    }

    #[test]
    fn clock_carries_fractional_units_and_wraps_only_on_export() {
        let mut clock = SimulationStepClock::new(240).unwrap();
        for _ in 0..60 {
            clock.advance_completed_interval(1.0 / 60.0).unwrap();
        }
        assert_eq!(clock.step(), 240);
        assert_eq!(clock.completed_intervals(), 60);
        clock.whole_units = u64::from(u32::MAX) - 1;
        clock.advance_completed_interval(1.0 / 60.0).unwrap();
        assert_eq!(clock.whole_units(), u64::from(u32::MAX) + 3);
        assert_eq!(clock.step(), 2);
    }

    #[test]
    fn swept_aabb_contact_clamps_foot_to_top_and_chooses_earliest_hit() {
        let surfaces = [
            bounds([-10., -1., -10.], [10., 0., 10.]),
            bounds([-10., 1., -10.], [10., 2., 10.]),
        ];
        let hit = sweep_ground_aabbs([0., 5., 0.], [0., -5., 0.], [0.5, 1., 0.5], &surfaces)
            .unwrap()
            .unwrap();
        assert_eq!(hit.surface_index, 1);
        assert!((hit.fraction - 0.2).abs() < 1e-12);
        assert_eq!(hit.center, [0., 3., 0.]);
        assert_eq!(hit.normal, [0., 1., 0.]);
    }

    #[test]
    fn static_ground_sweep_preserves_identity_and_excludes_the_local_body() {
        let records = [
            StaticSurface {
                node_id: "lower".into(),
                bounds: bounds([-10., -1., -10.], [10., 0., 10.]),
            },
            StaticSurface {
                node_id: "raised".into(),
                bounds: bounds([-10., 1., -10.], [10., 2., 10.]),
            },
            StaticSurface {
                node_id: "character".into(),
                bounds: bounds([-10., 2., -10.], [10., 3., 10.]),
            },
        ];
        let hit = sweep_ground_static_records(
            [0., 5., 0.],
            [0., -5., 0.],
            [0.5, 1., 0.5],
            &records,
            "character",
        )
        .unwrap()
        .unwrap();
        assert_eq!(hit.node_id, "raised");
        assert_eq!(hit.hit.surface_index, 1);
        assert_eq!(hit.hit.center, [0., 3., 0.]);
        let unfiltered = sweep_ground_static_records(
            [0., 5., 0.],
            [0., -5., 0.],
            [0.5, 1., 0.5],
            &records,
            "other",
        )
        .unwrap()
        .unwrap();
        assert_eq!(unfiltered.node_id, "character");
        assert_eq!(unfiltered.hit.surface_index, 2);
        assert!(sweep_ground_static_records(
            [0., 5., 0.],
            [0., -5., 0.],
            [0.5, f64::NAN, 0.5],
            &records,
            "character",
        )
        .is_err());
    }

    #[test]
    fn static_step_reports_support_and_excludes_the_local_body() {
        let floor = StaticSurface {
            node_id: "floor".into(),
            bounds: bounds([-10., 1., -10.], [10., 2., 10.]),
        };
        let character = StaticSurface {
            node_id: "character".into(),
            bounds: bounds([-1., 2., -1.], [1., 3., 1.]),
        };
        let records = [floor, character.clone()];
        let mut state = BodyState {
            center: [0., 3.25, 0.],
            velocity: [0.; 3],
            half_size: [0.5, 1., 0.5],
            grounded: false,
        };
        let mut stepper = PhysicsStepper::new(10., 50., 0.01, 64).unwrap();
        let landed = stepper
            .step_static_records(&mut state, 0.25, &records, "character")
            .unwrap();
        assert_eq!(landed.report.contacts, 1);
        assert_eq!(landed.ground.unwrap().node_id, "floor");
        assert_eq!(state.center, [0., 3., 0.]);
        assert!(state.grounded);

        let supported = stepper
            .step_static_records(&mut state, 0.01, &records, "character")
            .unwrap();
        assert_eq!(supported.report.contacts, 0);
        assert_eq!(supported.ground.unwrap().node_id, "floor");

        let mut self_supported = BodyState {
            center: [0., 4., 0.],
            grounded: true,
            ..state
        };
        let falling = stepper
            .step_static_records(&mut self_supported, 0.01, &[character], "character")
            .unwrap();
        assert!(!self_supported.grounded);
        assert!(falling.ground.is_none());
    }

    #[test]
    fn static_raycast_returns_nearest_surface_and_world_hit() {
        let surfaces = [
            bounds([-1., -1., -10.], [1., 1., -9.]),
            bounds([-1., -1., -4.], [1., 1., -3.]),
        ];
        let hit = raycast_surfaces([0., 0., 0.], [0., 0., -2.], 20.0, &surfaces).unwrap();
        assert_eq!(hit.surface_index, 1);
        assert_eq!(hit.distance, 3.0);
        assert_eq!(hit.position, [0., 0., -3.]);
        assert_eq!(hit.normal, [0., 0., 1.]);
        assert!(raycast_surfaces([0.; 3], [0.; 3], 20.0, &surfaces).is_none());
        assert!(raycast_surfaces([0.; 3], [0., 0., -1.], 2.0, &surfaces).is_none());
    }

    #[test]
    fn received_static_parts_supply_filtered_collision_geometry() {
        let part = |id, position, rotation| {
            serde_json::json!({
                "id":id,"class_name":"Part","parent":null,"properties":{
                    "Anchored":true,"CanCollide":true,"Size":{"$vector3":[20,1,20]},
                    "CFrame":{"position":position,"rotation_id":rotation}
                }
            })
        };
        let mut nodes = vec![part("floor", [0, 0, 0], 2)];
        nodes.push(part("rotated", [0, 10, 0], 17));
        let mut loose = part("loose", [0, 20, 0], 2);
        loose["properties"]["Anchored"] = serde_json::json!(false);
        nodes.push(loose);
        let mut disabled = part("disabled", [0, 30, 0], 2);
        disabled["properties"]["CanCollide"] = serde_json::json!(false);
        nodes.push(disabled);
        nodes.push(
            serde_json::json!({"id":"folder","class_name":"Folder","parent":null,"properties":{}}),
        );
        let world = crate::world::WorldStore::from_snapshot(&serde_json::json!({
            "version":1,"nodes":nodes
        }))
        .unwrap();
        let surfaces = static_part_surfaces(&world.view()).unwrap();
        assert_eq!(surfaces, vec![bounds([-10., -0.5, -10.], [10., 0.5, 10.])]);

        let mut state = BodyState {
            center: [0., 3., 0.],
            velocity: [0.; 3],
            half_size: [0.5; 3],
            grounded: false,
        };
        let mut stepper = PhysicsStepper::new(10., 50., 0.01, 64).unwrap();
        for _ in 0..100 {
            stepper.step(&mut state, 0.01, &surfaces).unwrap();
        }
        assert!(state.grounded);
        assert_eq!(state.center[1], 1.0);
    }

    #[test]
    fn base_part_surfaces_use_bounded_received_ancestry() {
        let node = |id, class| {
            serde_json::json!({
                "id":id,"class_name":class,"parent":null,"properties":{
                    "Anchored":true,"CanCollide":true,"Size":{"$vector3":[4,1,4]},
                    "CFrame":{"position":[0,0,0],"rotation_id":2}
                }
            })
        };
        let world = crate::world::WorldStore::from_snapshot(&serde_json::json!({
            "version":1,"nodes":[node("spawn","SpawnLocation"),node("cycle","CycleA")]
        }))
        .unwrap();
        let parents = std::collections::HashMap::from([
            ("SpawnLocation".into(), "Part".into()),
            ("Part".into(), "BasePart".into()),
            ("CycleA".into(), "CycleB".into()),
            ("CycleB".into(), "CycleA".into()),
        ]);
        let surfaces = static_base_part_surfaces(&world.view(), &parents).unwrap();
        assert_eq!(surfaces, vec![bounds([-2., -0.5, -2.], [2., 0.5, 2.])]);
        assert!(static_part_surfaces(&world.view()).unwrap().is_empty());

        let records = static_base_part_surface_records(&world.view(), &parents).unwrap();
        assert_eq!(records[0].node_id, "spawn");
        let hit = raycast_static_records([0., 2., 0.], [0., -2., 0.], 10.0, &records).unwrap();
        assert_eq!(hit.node_id, "spawn");
        assert_eq!(hit.hit.position, [0., 0.5, 0.]);
        assert!(raycast_static_records_filtered(
            [0., 2., 0.],
            [0., -2., 0.],
            10.0,
            &records,
            |id| id != "spawn"
        )
        .is_none());
        assert!(raycast_static_records_filtered(
            [0., 2., 0.],
            [0., -2., 0.],
            10.0,
            &records,
            |id| id == "spawn"
        )
        .is_some());
        let ground = probe_ground([0., 2., 0.], [0.5; 3], 2.0, &records, "character").unwrap();
        assert_eq!(ground.node_id, "spawn");
        assert_eq!(ground.clearance, 1.0);
        assert_eq!(ground.position, [0., 0.5, 0.]);
        assert!(probe_ground([0., 2., 0.], [0.5; 3], 2.0, &records, "spawn").is_none());
        let mut overlapping_records = records.clone();
        overlapping_records.push(StaticSurface {
            node_id: "second".into(),
            bounds: records[0].bounds,
        });
        let hits = overlapping_static_records(
            bounds([-1., 0.4, -1.], [1., 0.6, 1.]),
            &overlapping_records,
            None,
        );
        assert_eq!(
            hits.iter()
                .map(|hit| hit.node_id.as_str())
                .collect::<Vec<_>>(),
            ["spawn", "second"]
        );
        let hits = overlapping_static_records(
            bounds([-1., 0.4, -1.], [1., 0.6, 1.]),
            &overlapping_records,
            Some("spawn"),
        );
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].node_id, "second");
    }

    #[test]
    fn query_surfaces_use_can_query_without_collision_or_anchor_requirements() {
        let part = |id, class, z, can_query, anchored| {
            serde_json::json!({
                "id":id,"class_name":class,"parent":null,"properties":{
                    "Anchored":anchored,"CanQuery":can_query,"CanCollide":false,
                    "Size":{"$vector3":[2,2,2]},
                    "CFrame":{"position":[0,0,z],"rotation_id":2}
                }
            })
        };
        let mut near = part("near", "Part", -4, true, false);
        near["parent"] = serde_json::json!("nested");
        let world = crate::world::WorldStore::from_snapshot(&serde_json::json!({
            "version":1,"nodes":[
                near,
                part("far","Part",-8,true,true),
                part("disabled","Part",-2,false,true),
                part("wrong-class","Folder",-1,true,true),
                {"id":"nested","class_name":"Model","parent":"model","properties":{}},
                {"id":"model","class_name":"Model","parent":null,"properties":{}}
            ]
        }))
        .unwrap();
        let parents = std::collections::HashMap::from([("Part".into(), "BasePart".into())]);
        let records = base_part_query_surface_records(&world.view(), &parents).unwrap();
        assert_eq!(
            records
                .iter()
                .map(|record| record.node_id.as_str())
                .collect::<Vec<_>>(),
            ["near", "far"]
        );
        assert!(static_base_part_surface_records(&world.view(), &parents)
            .unwrap()
            .is_empty());
        let hit = raycast_static_records([0.; 3], [0., 0., -1.], 20., &records).unwrap();
        assert_eq!(hit.node_id, "near");
        assert_eq!(hit.hit.distance, 3.);
        let filtered = raycast_base_part_query(
            &world.view(),
            &parents,
            [0.; 3],
            [0., 0., -1.],
            20.,
            &["missing", "model"],
        )
        .unwrap()
        .unwrap();
        assert_eq!(filtered.node_id, "far");
        assert_eq!(filtered.hit.distance, 7.);
        let included = raycast_base_part_query_filtered(
            &world.view(),
            &parents,
            [0.; 3],
            [0., 0., -1.],
            20.,
            &["model"],
            QueryFilterMode::Include,
        )
        .unwrap()
        .unwrap();
        assert_eq!(included.node_id, "near");
        let query_bounds = bounds([-3., -3., -20.], [3., 3., 0.]);
        let all =
            overlapping_base_part_query(&world.view(), &parents, query_bounds, 0, &[]).unwrap();
        assert_eq!(
            all.iter()
                .map(|record| record.node_id.as_str())
                .collect::<Vec<_>>(),
            ["near", "far"]
        );
        let limited =
            overlapping_base_part_query(&world.view(), &parents, query_bounds, 1, &[]).unwrap();
        assert_eq!(limited.len(), 1);
        assert_eq!(limited[0].node_id, "near");
        let remaining =
            overlapping_base_part_query(&world.view(), &parents, query_bounds, 0, &["model"])
                .unwrap();
        assert_eq!(remaining.len(), 1);
        assert_eq!(remaining[0].node_id, "far");
        let included = overlapping_base_part_query_filtered(
            &world.view(),
            &parents,
            query_bounds,
            0,
            &["model"],
            QueryFilterMode::Include,
        )
        .unwrap();
        assert_eq!(included.len(), 1);
        assert_eq!(included[0].node_id, "near");
        assert!(overlapping_base_part_query(
            &world.view(),
            &parents,
            Aabb {
                minimum: [0.; 3],
                maximum: [0.; 3],
            },
            0,
            &[],
        )
        .is_err());
        assert!(
            overlapping_base_part_radius_query(&world.view(), &parents, [0.; 3], 2.99, 0, &[],)
                .unwrap()
                .is_empty()
        );
        let spherical =
            overlapping_base_part_radius_query(&world.view(), &parents, [0.; 3], 7., 0, &[])
                .unwrap();
        assert_eq!(
            spherical
                .iter()
                .map(|record| record.node_id.as_str())
                .collect::<Vec<_>>(),
            ["near", "far"]
        );
        let spherical =
            overlapping_base_part_radius_query(&world.view(), &parents, [0.; 3], 7., 1, &["model"])
                .unwrap();
        assert_eq!(spherical.len(), 1);
        assert_eq!(spherical[0].node_id, "far");
        let spherical = overlapping_base_part_radius_query_filtered(
            &world.view(),
            &parents,
            [0.; 3],
            7.,
            0,
            &["model"],
            QueryFilterMode::Include,
        )
        .unwrap();
        assert_eq!(spherical.len(), 1);
        assert_eq!(spherical[0].node_id, "near");
        assert!(
            overlapping_base_part_radius_query(&world.view(), &parents, [0.; 3], -1., 0, &[],)
                .is_err()
        );
        assert_eq!(
            raycast_base_part_query(
                &world.view(),
                &parents,
                [0.; 3],
                [0., 0., -1.],
                20.,
                &vec!["missing"; 4097],
            ),
            Err(PhysicsError::TooManyQueryFilters)
        );
    }

    #[test]
    fn touch_listener_surfaces_require_received_transmitters_and_can_touch() {
        let part = |id, class, can_touch, anchored| {
            serde_json::json!({
                "id":id,"class_name":class,"parent":null,"properties":{
                    "Anchored":anchored,"CanTouch":can_touch,"CanCollide":false,
                    "Size":{"$vector3":[4,1,4]},
                    "CFrame":{"position":[0,0,0],"rotation_id":2}
                }
            })
        };
        let transmitter = |id: String, parent: &str| serde_json::json!({"id":id,"class_name":"TouchTransmitter","parent":parent,"properties":{}});
        let nodes = vec![
            part("enabled", "Part", true, true),
            transmitter("touch-enabled".into(), "enabled"),
            part("enabled-two", "Part", true, true),
            transmitter("touch-enabled-two-a".into(), "enabled-two"),
            transmitter("touch-enabled-two-b".into(), "enabled-two"),
            part("disabled", "Part", false, true),
            transmitter("touch-disabled".into(), "disabled"),
            part("orphan", "Part", true, true),
            part("loose", "Part", true, false),
            transmitter("touch-loose".into(), "loose"),
            part("wrong-class", "SpawnLocation", true, true),
            transmitter("touch-wrong-class".into(), "wrong-class"),
        ];
        let world = crate::world::WorldStore::from_snapshot(&serde_json::json!({
            "version":1,"nodes":nodes
        }))
        .unwrap();
        let records = touch_listener_surface_records(&world.view()).unwrap();
        assert_eq!(
            records
                .iter()
                .map(|record| record.node_id.as_str())
                .collect::<Vec<_>>(),
            ["enabled", "enabled-two"]
        );
        assert!(records
            .iter()
            .all(|record| record.bounds == bounds([-2., -0.5, -2.], [2., 0.5, 2.])));

        let nodes = (0..4097)
            .map(|index| transmitter(format!("touch-{index}"), &format!("missing-{index}")))
            .collect::<Vec<_>>();
        let world = crate::world::WorldStore::from_snapshot(&serde_json::json!({
            "version":1,"nodes":nodes
        }))
        .unwrap();
        assert_eq!(
            touch_listener_surface_records(&world.view()),
            Err(PhysicsError::TooManyContactListeners)
        );
    }

    #[test]
    fn contact_tracker_deduplicates_and_emits_ordered_begin_end_transitions() {
        let surface = |node_id: &str| StaticSurface {
            node_id: node_id.into(),
            bounds: bounds([-2., -0.5, -2.], [2., 0.5, 2.]),
        };
        let transition = |node_id: &str, began| ContactTransition {
            node_id: node_id.into(),
            began,
        };
        let query = bounds([-1., 0.4, -1.], [1., 0.6, 1.]);
        let initial = vec![surface("first"), surface("second"), surface("first")];
        let mut tracker = ContactTracker::new();
        assert_eq!(
            tracker.synchronize(query, &initial, "character"),
            [transition("first", true), transition("second", true)]
        );
        assert_eq!(tracker.active_nodes(), ["first", "second"]);
        assert!(tracker.synchronize(query, &initial, "character").is_empty());

        let replacement = vec![
            surface("second"),
            surface("third"),
            surface("third"),
            surface("character"),
        ];
        assert_eq!(
            tracker.synchronize(query, &replacement, "character"),
            [transition("first", false), transition("third", true)]
        );
        assert_eq!(tracker.active_nodes(), ["second", "third"]);
        assert_eq!(
            tracker.clear(),
            [transition("second", false), transition("third", false)]
        );
        assert!(tracker.active_nodes().is_empty());
        assert!(tracker.clear().is_empty());
    }

    #[test]
    fn body_contact_synchronization_validates_before_mutating() {
        let body = BodyState {
            center: [0., 1., 0.],
            velocity: [0.; 3],
            half_size: [1., 0.6, 1.],
            grounded: false,
        };
        let query = bounds([-1., 0.4, -1.], [1., 1.6, 1.]);
        assert_eq!(body.bounds().unwrap(), query);
        let surfaces = [StaticSurface {
            node_id: "floor".into(),
            bounds: bounds([-2., -0.5, -2.], [2., 0.5, 2.]),
        }];
        let mut tracker = ContactTracker::new();
        assert_eq!(
            tracker
                .synchronize_body(body, &surfaces, "character")
                .unwrap(),
            [ContactTransition {
                node_id: "floor".into(),
                began: true,
            }]
        );
        let invalid = BodyState {
            velocity: [f64::NAN, 0., 0.],
            ..body
        };
        assert!(tracker.synchronize_body(invalid, &[], "character").is_err());
        assert_eq!(tracker.active_nodes(), ["floor"]);
    }

    #[test]
    fn oriented_contacts_include_touching_and_validate_before_mutating() {
        let floor = OrientedSurface {
            node_id: "floor".into(),
            bounds: OrientedBox::new([0.; 3], [2., 0.5, 2.], IDENTITY_ROTATION).unwrap(),
        };
        let character = OrientedSurface {
            node_id: "character".into(),
            bounds: floor.bounds,
        };
        let surfaces = [floor.clone(), character, floor];
        let body = BodyState {
            center: [0., 1., 0.],
            velocity: [0.; 3],
            half_size: [0.5; 3],
            grounded: true,
        };
        let mut tracker = ContactTracker::new();
        assert_eq!(
            tracker
                .synchronize_oriented_body(body, &surfaces, "character")
                .unwrap(),
            [ContactTransition {
                node_id: "floor".into(),
                began: true,
            }]
        );
        assert!(tracker
            .synchronize_oriented_body(body, &surfaces, "character")
            .unwrap()
            .is_empty());
        let invalid = BodyState {
            velocity: [f64::NAN, 0., 0.],
            ..body
        };
        assert!(tracker
            .synchronize_oriented_body(invalid, &[], "character")
            .is_err());
        assert_eq!(tracker.active_nodes(), ["floor"]);
        assert_eq!(
            tracker.clear(),
            [ContactTransition {
                node_id: "floor".into(),
                began: false,
            }]
        );
    }

    #[test]
    fn fixed_step_gravity_lands_once_and_remains_grounded() {
        let floor = bounds([-20., -1., -20.], [20., 0., 20.]);
        let mut state = BodyState {
            center: [0., 3., 0.],
            velocity: [1., 0., 0.],
            half_size: [0.5, 1., 0.5],
            grounded: false,
        };
        let mut stepper = PhysicsStepper::new(10., 50., 0.01, 64).unwrap();
        let mut contacts = 0;
        for _ in 0..100 {
            contacts += stepper.step(&mut state, 0.01, &[floor]).unwrap().contacts;
        }
        assert_eq!(state.center[1], 1.0);
        assert_eq!(state.velocity[1], 0.0);
        assert!(state.grounded);
        assert_eq!(contacts, 1);
        assert!((state.center[0] - 1.0).abs() < 1e-12);
    }

    #[test]
    fn grounded_horizontal_velocity_moves_the_body_across_the_support() {
        let floor = bounds([-20., -1., -20.], [20., 0., 20.]);
        let mut state = BodyState {
            center: [0., 1., 0.],
            velocity: [2., 0., -1.],
            half_size: [0.5, 1., 0.5],
            grounded: true,
        };
        let mut stepper = PhysicsStepper::new(196.2, 512., 1.0 / 60.0, 8).unwrap();
        for _ in 0..60 {
            stepper.step(&mut state, 1.0 / 60.0, &[floor]).unwrap();
        }
        // Repeated fixed-step integration accumulates the same floating-point
        // rounding as the Python reference, so compare the bounded error.
        assert!((state.center[0] - 2.0).abs() < 1e-12);
        assert_eq!(state.center[1], 1.0);
        assert!((state.center[2] + 1.0).abs() < 1e-12);
        assert_eq!(state.velocity[1], 0.0);
        assert!(state.grounded);
    }

    #[test]
    fn bounded_catch_up_reports_discarded_time() {
        let mut state = BodyState {
            center: [0., 10., 0.],
            velocity: [0.; 3],
            half_size: [0.5; 3],
            grounded: false,
        };
        let mut stepper = PhysicsStepper::new(9.8, 50., 0.01, 4).unwrap();
        let report = stepper.step(&mut state, 0.105, &[]).unwrap();
        assert_eq!(report.fixed_steps, 4);
        assert!((report.simulated_seconds - 0.04).abs() < 1e-12);
        assert!((report.dropped_seconds - 0.06).abs() < 1e-12);
        assert!((state.velocity[1] + 0.392).abs() < 1e-12);
    }

    #[test]
    fn invalid_inputs_are_rejected_without_partial_step() {
        let mut state = BodyState {
            center: [0., 2., 0.],
            velocity: [0.; 3],
            half_size: [1.; 3],
            grounded: false,
        };
        let original = state;
        let mut stepper = PhysicsStepper::new(9.8, 50., 0.01, 8).unwrap();
        assert_eq!(
            stepper.step(&mut state, f64::NAN, &[]),
            Err(PhysicsError::InvalidFrameDuration)
        );
        assert_eq!(state, original);
        assert!(Aabb::new([0.; 3], [0., 1., 1.]).is_err());
        assert!(SimulationStepClock::new(1001).is_err());
    }
}
