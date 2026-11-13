//! predict our character locally from snapshots we actually received.
//! this only changes a detached prediction. it does not edit the received world
//! or send packets, and a prediction is not proof the server accepted anything.
//! collision support is the aabb ground sweep in `rove_client_core::physics`.

use std::{collections::HashMap, fmt};

use crate::{
    physics_properties as received,
    session_integrity::{PhysicsIntegrityCheck, SessionIntegrity},
};
use rove_client_core::{
    physics::{Aabb, BodyState, PhysicsError, PhysicsStepper},
    world::WorldView,
};

const MAX_DELTA_SECONDS: f64 = 0.25;
const MAX_WALK_SPEED: f64 = 10_000.0;
const DEFAULT_WORLD_BOUNDS: [f32; 6] = [-1.0e6, -1.0e6, -1.0e6, 1.0e6, 1.0e6, 1.0e6];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EvidenceSource {
    ServerReplication,
    VerifiedConstructorDefault,
    LocalDefault,
    LocalPrediction,
    Unknown,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ReceivedFlag {
    pub value: bool,
    pub source: EvidenceSource,
}

/// Identifies one binding of the local player, Character, root part and world.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct OwnBinding {
    pub world_generation: u64,
    pub user_id: u64,
    pub character_id: u64,
    pub root_part_id: u64,
}

/// The evidence required before local prediction may advance.
///
/// The caller must build this from received state. A local default or guessed
/// owner value cannot satisfy the gate.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct OwnCharacterEvidence {
    pub binding: OwnBinding,
    pub own_player_confirmed: bool,
    pub character_model_confirmed: bool,
    pub humanoid_confirmed: bool,
    pub root_part_confirmed: bool,
    pub root_in_workspace_confirmed: bool,
    pub assembly_root_confirmed: bool,
    pub streaming_region_confirmed: bool,
    pub anchored: Option<ReceivedFlag>,
    pub network_owner_scope: Option<u64>,
    pub client_scope: Option<u64>,
    pub ownership_granted: Option<ReceivedFlag>,
    pub server_authority_mode: Option<ReceivedFlag>,
}

impl OwnCharacterEvidence {
    fn eligible(self) -> bool {
        self.own_player_confirmed
            && self.character_model_confirmed
            && self.humanoid_confirmed
            && self.root_part_confirmed
            && self.root_in_workspace_confirmed
            && self.assembly_root_confirmed
            && self.streaming_region_confirmed
            && matches!(
                self.anchored,
                Some(ReceivedFlag {
                    value: false,
                    source: EvidenceSource::ServerReplication
                        | EvidenceSource::VerifiedConstructorDefault
                })
            )
            && matches!(
                self.ownership_granted,
                Some(ReceivedFlag {
                    value: true,
                    source: EvidenceSource::ServerReplication
                })
            )
            && self.network_owner_scope.is_some()
            && self.network_owner_scope == self.client_scope
            && !matches!(
                self.server_authority_mode,
                Some(ReceivedFlag {
                    value: true,
                    source: EvidenceSource::ServerReplication
                })
            )
    }
}

/// Physics values and collision geometry detached from the received world.
#[derive(Clone, Debug, PartialEq)]
pub struct ReceivedPhysicsSnapshot {
    pub evidence: OwnCharacterEvidence,
    pub revision: u64,
    pub received_at_monotonic: f64,
    pub body: BodyState,
    pub gravity: f64,
    pub walk_speed: f64,
    pub surfaces: Vec<Aabb>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct MovementIntent {
    /// World-space requested direction; Y is ignored and X/Z are normalized.
    pub direction: [f64; 3],
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PredictionFrame {
    pub binding: OwnBinding,
    pub based_on_received_revision: u64,
    pub position: [f64; 3],
    pub velocity: [f64; 3],
    pub grounded: bool,
    pub fixed_steps: usize,
    pub simulated_seconds: f64,
    pub dropped_seconds: f64,
    pub ground_contacts: usize,
    /// Always false: only a new received snapshot can establish server state.
    pub server_acceptance_confirmed: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LivePhysicsError {
    MissingOrUntrustedAuthorityEvidence,
    Invalidated,
    InvalidSnapshot,
    StaleSnapshot,
    BindingChanged,
    InvalidTime,
    InvalidIntent,
    InvalidWalkSpeed,
    InvalidPhysics,
}

impl fmt::Display for LivePhysicsError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::MissingOrUntrustedAuthorityEvidence => {
                "received own-character authority evidence is incomplete"
            }
            Self::Invalidated => "prediction binding has been invalidated and must be reseeded",
            Self::InvalidSnapshot => "received physics snapshot contains invalid values",
            Self::StaleSnapshot => "received physics snapshot revision or time is stale",
            Self::BindingChanged => "own-character binding changed; prediction must be reseeded",
            Self::InvalidTime => "monotonic frame time or delta is invalid",
            Self::InvalidIntent => "movement intent must be finite and bounded",
            Self::InvalidWalkSpeed => "received walk speed is outside supported bounds",
            Self::InvalidPhysics => "local physics step failed",
        })
    }
}

impl std::error::Error for LivePhysicsError {}

#[derive(Clone, Debug)]
struct PhysicsJobStatistics {
    smoothing: f64,
    world_bounds: [f32; 6],
    last_completion: Option<f64>,
    completed_jobs: u64,
    samples: u64,
    last_interval: Option<f64>,
    mean_interval: Option<f64>,
    variance: f64,
}

impl Default for PhysicsJobStatistics {
    fn default() -> Self {
        Self {
            smoothing: 0.05,
            world_bounds: DEFAULT_WORLD_BOUNDS,
            last_completion: None,
            completed_jobs: 0,
            samples: 0,
            last_interval: None,
            mean_interval: None,
            variance: 0.0,
        }
    }
}

impl PhysicsJobStatistics {
    fn completed(&mut self, now: f64, integrity: Option<&SessionIntegrity>) {
        if let Some(integrity) = integrity {
            integrity.observe_physics_comparison(
                PhysicsIntegrityCheck::Smoothing,
                (0.049..=0.051).contains(&self.smoothing),
            );
            integrity.observe_physics_comparison(
                PhysicsIntegrityCheck::WorldBounds,
                native_world_bounds_match(self.world_bounds),
            );
        }
        if let Some(last) = self.last_completion {
            let interval = now - last;
            let mean = self.mean_interval.map_or(interval, |mean| {
                self.smoothing * interval + (1.0 - self.smoothing) * mean
            });
            self.variance =
                (interval - mean).powi(2) * self.smoothing + (1.0 - self.smoothing) * self.variance;
            self.mean_interval = Some(mean);
            self.last_interval = Some(interval);
            self.samples = self.samples.saturating_add(1);
        }
        self.last_completion = Some(now);
        self.completed_jobs = self.completed_jobs.saturating_add(1);
    }

    fn report(&self) -> serde_json::Value {
        serde_json::json!({
            "source":"local_physics_job_completions",
            "completed_jobs":self.completed_jobs,
            "interval_samples":self.samples,
            "smoothing_coefficient":self.smoothing,
            "world_bounds":self.world_bounds,
            "last_interval_seconds":self.last_interval,
            "mean_interval_seconds":self.mean_interval,
            "interval_variance_seconds_squared":self.variance
        })
    }
}

fn native_world_bounds_match(bounds: [f32; 6]) -> bool {
    bounds[0] <= -950_000.0
        && bounds[1] <= -960_000.0
        && bounds[2] <= -970_000.0
        && bounds[3] >= 960_000.0
        && bounds[4] >= 970_000.0
}

#[derive(Clone, Debug)]
struct PhysicsJobScheduler {
    configured_interval_enabled: bool,
    configured_interval_seconds: f64,
    deadline_us: Option<u64>,
    last_clock_us: Option<u64>,
    admitted_jobs: u64,
    skipped_intervals: u64,
}

impl Default for PhysicsJobScheduler {
    fn default() -> Self {
        Self {
            configured_interval_enabled: true,
            configured_interval_seconds: 1.0 / 60.0,
            deadline_us: None,
            last_clock_us: None,
            admitted_jobs: 0,
            skipped_intervals: 0,
        }
    }
}

impl PhysicsJobScheduler {
    fn admit(
        &mut self,
        now: f64,
        integrity: Option<&SessionIntegrity>,
    ) -> Result<bool, LivePhysicsError> {
        if !now.is_finite() || !(0.0..2.0f64.powi(44)).contains(&now) {
            return Err(LivePhysicsError::InvalidTime);
        }
        let now_us = (now * 1_000_000.0) as u64;
        if self.last_clock_us.is_some_and(|last| now_us < last) {
            return Err(LivePhysicsError::InvalidTime);
        }
        self.last_clock_us = Some(now_us);
        let deadline = *self.deadline_us.get_or_insert(now_us);
        if now_us < deadline {
            return Ok(false);
        }
        let interval = if self.configured_interval_enabled {
            self.configured_interval_seconds
        } else {
            1.0 / 240.0
        };
        let interval_us = interval * 1_000_000.0;
        let missed = ((now_us - deadline) as f64 / interval_us) as u64;
        self.deadline_us = Some(deadline + (interval_us * (missed + 1) as f64) as u64);
        self.skipped_intervals = self.skipped_intervals.saturating_add(missed);
        self.admitted_jobs = self.admitted_jobs.saturating_add(1);
        if let Some(integrity) = integrity {
            integrity.observe_physics_comparison(
                PhysicsIntegrityCheck::Scheduler,
                self.configured_interval_enabled,
            );
        }
        Ok(true)
    }

    fn report(&self) -> serde_json::Value {
        serde_json::json!({
            "source":"local_realtime_physics_job_scheduler",
            "configured_interval_enabled":self.configured_interval_enabled,
            "configured_interval_seconds":self.configured_interval_seconds,
            "effective_interval_seconds":if self.configured_interval_enabled {self.configured_interval_seconds} else {1.0/240.0},
            "admitted_jobs":self.admitted_jobs,
            "skipped_intervals":self.skipped_intervals
        })
    }
}

/// Predicts the received local character in memory; no transport is attached.
#[derive(Clone, Debug)]
pub struct LivePhysicsSimulator {
    binding: OwnBinding,
    received_revision: u64,
    received_at_monotonic: f64,
    state: BodyState,
    gravity: f64,
    walk_speed: f64,
    surfaces: Vec<Aabb>,
    stepper: PhysicsStepper,
    integrity: Option<SessionIntegrity>,
    job_statistics: PhysicsJobStatistics,
    job_scheduler: PhysicsJobScheduler,
    last_call_at: Option<f64>,
    last_frame_at: Option<f64>,
    invalidated: bool,
}

impl LivePhysicsSimulator {
    /// Construct from an actual received own-character snapshot.
    pub fn from_received(snapshot: ReceivedPhysicsSnapshot) -> Result<Self, LivePhysicsError> {
        validate_snapshot(&snapshot)?;
        if !snapshot.evidence.eligible() {
            return Err(LivePhysicsError::MissingOrUntrustedAuthorityEvidence);
        }
        let stepper = PhysicsStepper::new(snapshot.gravity, 512.0, 1.0 / 240.0, 64)
            .map_err(map_physics_error)?;
        Ok(Self {
            binding: snapshot.evidence.binding,
            received_revision: snapshot.revision,
            received_at_monotonic: snapshot.received_at_monotonic,
            state: snapshot.body,
            gravity: snapshot.gravity,
            walk_speed: snapshot.walk_speed,
            surfaces: snapshot.surfaces,
            stepper,
            integrity: None,
            job_statistics: PhysicsJobStatistics::default(),
            job_scheduler: PhysicsJobScheduler::default(),
            last_call_at: None,
            last_frame_at: None,
            invalidated: false,
        })
    }

    pub fn set_integrity(&mut self, observer: SessionIntegrity) {
        self.integrity = Some(observer);
    }

    /// Reconcile only a newer received pose with the same own-character binding.
    /// Callers must recreate the simulator after a player/Character/root/world change.
    pub fn reconcile_received(
        &mut self,
        snapshot: ReceivedPhysicsSnapshot,
    ) -> Result<(), LivePhysicsError> {
        validate_snapshot(&snapshot)?;
        if !snapshot.evidence.eligible() {
            self.invalidated = true;
            return Err(LivePhysicsError::MissingOrUntrustedAuthorityEvidence);
        }
        if snapshot.evidence.binding != self.binding {
            self.invalidated = true;
            return Err(LivePhysicsError::BindingChanged);
        }
        if snapshot.revision <= self.received_revision
            || snapshot.received_at_monotonic < self.received_at_monotonic
        {
            return Err(LivePhysicsError::StaleSnapshot);
        }
        self.received_revision = snapshot.revision;
        self.received_at_monotonic = snapshot.received_at_monotonic;
        self.state = snapshot.body;
        self.gravity = snapshot.gravity;
        self.walk_speed = snapshot.walk_speed;
        self.surfaces = snapshot.surfaces;
        self.stepper = PhysicsStepper::new(snapshot.gravity, 512.0, 1.0 / 240.0, 64)
            .map_err(map_physics_error)?;
        self.last_call_at = None;
        self.last_frame_at = None;
        Ok(())
    }

    /// Advance a bounded local prediction using elapsed monotonic time.
    pub fn advance(
        &mut self,
        now_monotonic: f64,
        intent: MovementIntent,
    ) -> Result<PredictionFrame, LivePhysicsError> {
        self.advance_with_policy(now_monotonic, intent, false)
    }

    fn advance_with_policy(
        &mut self,
        now_monotonic: f64,
        intent: MovementIntent,
        cap_late_frame: bool,
    ) -> Result<PredictionFrame, LivePhysicsError> {
        if self.invalidated {
            return Err(LivePhysicsError::Invalidated);
        }
        if !now_monotonic.is_finite()
            || now_monotonic < self.received_at_monotonic
            || self.last_call_at.is_some_and(|last| now_monotonic <= last)
        {
            return Err(LivePhysicsError::InvalidTime);
        }
        let delta = self.last_frame_at.map_or(0.0, |last| now_monotonic - last);
        if delta > MAX_DELTA_SECONDS && !cap_late_frame {
            return Err(LivePhysicsError::InvalidTime);
        }
        let capped_delta = delta.min(MAX_DELTA_SECONDS);
        if !intent
            .direction
            .iter()
            .all(|v| v.is_finite() && v.abs() < 1.0e9)
        {
            return Err(LivePhysicsError::InvalidIntent);
        }
        let admitted = self
            .job_scheduler
            .admit(now_monotonic, self.integrity.as_ref())?;
        if !admitted {
            self.last_call_at = Some(now_monotonic);
            return Ok(self.current_frame());
        }
        if let Some(integrity) = self.integrity.as_ref() {
            integrity.observe_physics_comparison(
                PhysicsIntegrityCheck::Gravity,
                self.stepper.gravity == self.gravity,
            );
        }
        let magnitude = intent.direction[0].hypot(intent.direction[2]);
        let mut next_state = self.state;
        if magnitude > 0.0 {
            next_state.velocity[0] = intent.direction[0] / magnitude * self.walk_speed;
            next_state.velocity[2] = intent.direction[2] / magnitude * self.walk_speed;
        } else {
            next_state.velocity[0] = 0.0;
            next_state.velocity[2] = 0.0;
        }
        let report = self
            .stepper
            .step(&mut next_state, capped_delta, &self.surfaces)
            .map_err(map_physics_error)?;
        self.state = next_state;
        self.job_statistics
            .completed(now_monotonic, self.integrity.as_ref());
        self.last_call_at = Some(now_monotonic);
        self.last_frame_at = Some(now_monotonic);
        let mut frame = self.current_frame();
        frame.fixed_steps = report.fixed_steps;
        frame.simulated_seconds = report.simulated_seconds;
        frame.dropped_seconds = report.dropped_seconds + delta - capped_delta;
        frame.ground_contacts = report.contacts;
        Ok(frame)
    }

    pub fn apply_jump(&mut self, speed: f64) -> Result<bool, LivePhysicsError> {
        if self.invalidated {
            return Err(LivePhysicsError::Invalidated);
        }
        if !speed.is_finite() || speed <= 0.0 || speed > 100_000.0 {
            return Err(LivePhysicsError::InvalidIntent);
        }
        if !self.state.grounded {
            return Ok(false);
        }
        self.state.velocity[1] = speed;
        self.state.grounded = false;
        Ok(true)
    }

    fn current_frame(&self) -> PredictionFrame {
        PredictionFrame {
            binding: self.binding,
            based_on_received_revision: self.received_revision,
            position: self.state.center,
            velocity: self.state.velocity,
            grounded: self.state.grounded,
            fixed_steps: 0,
            simulated_seconds: 0.0,
            dropped_seconds: 0.0,
            ground_contacts: 0,
            server_acceptance_confirmed: false,
        }
    }

    pub fn job_report(&self) -> serde_json::Value {
        serde_json::json!({
            "job_statistics":self.job_statistics.report(),
            "job_scheduler":self.job_scheduler.report()
        })
    }

    pub fn received_revision(&self) -> u64 {
        self.received_revision
    }

    pub fn binding(&self) -> OwnBinding {
        self.binding
    }

    pub fn position(&self) -> [f64; 3] {
        self.state.center
    }
}

fn received_reference(value: Option<serde_json::Value>) -> Option<String> {
    value?.get("$ref")?.as_str().map(str::to_owned)
}

fn received_vector(value: Option<serde_json::Value>) -> Option<[f64; 3]> {
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

fn received_instance_key(id: &str) -> Option<u64> {
    let (scope, index) = id.split_once(':')?;
    Some((scope.parse::<u64>().ok()? << 32) | index.parse::<u64>().ok()?)
}

/// Build a detached prediction snapshot only from properties in this received view.
pub fn received_snapshot(
    view: &WorldView,
    class_parents: &HashMap<String, String>,
    player_id: &str,
    user_id: u64,
    client_scope: u64,
    streaming_region_confirmed: bool,
    now_monotonic: f64,
) -> Option<ReceivedPhysicsSnapshot> {
    received_snapshot_cached(
        view,
        class_parents,
        player_id,
        user_id,
        client_scope,
        streaming_region_confirmed,
        now_monotonic,
        None,
        None,
    )
}

fn received_snapshot_cached(
    view: &WorldView,
    class_parents: &HashMap<String, String>,
    player_id: &str,
    user_id: u64,
    client_scope: u64,
    streaming_region_confirmed: bool,
    now_monotonic: f64,
    humanoid_hint: Option<&str>,
    cached_surfaces: Option<&[Aabb]>,
) -> Option<ReceivedPhysicsSnapshot> {
    if view.class_name(player_id) != Some("Player")
        || view
            .get_property(player_id, "UserId")
            .and_then(|value| value.as_f64())
            != Some(user_id as f64)
    {
        return None;
    }
    let character = received_reference(view.get_property(player_id, "Character"))?;
    if view.class_name(&character) != Some("Model") {
        return None;
    }
    let mut humanoid = humanoid_hint
        .filter(|id| {
            view.class_name(id) == Some("Humanoid") && view.parent(id) == Some(character.as_str())
        })
        .map(str::to_owned);
    if humanoid.is_none() {
        view.for_each_node(|node| {
            if node.class_name() == "Humanoid" && view.parent(node.id()) == Some(character.as_str())
            {
                humanoid = Some(node.id().to_owned());
            }
        });
    }
    let humanoid = humanoid?;
    let primary = received_reference(view.get_property(&character, "PrimaryPart"));
    let mut root =
        received_reference(view.get_property(&humanoid, "RootPart")).or_else(|| primary.clone());
    if root.is_none() {
        view.for_each_node(|node| {
            if view.parent(node.id()) == Some(character.as_str())
                && received::name_is(view, node.id(), "HumanoidRootPart")
                && matches!(node.class_name(), "Part" | "BasePart" | "MeshPart")
            {
                root = Some(node.id().to_owned());
            }
        });
    }
    let root = root.filter(|root| {
        matches!(
            view.class_name(root),
            Some("Part" | "BasePart" | "MeshPart")
        ) && view.parent(root) == Some(character.as_str())
    })?;
    let frame = received::frame(view, &root)?;
    let position = received_vector(frame.get("position").cloned())?;
    let mut half_size = received_vector(
        view.get_property(&root, "Size")
            .or_else(|| view.get_property(&root, "size"))
            .or_else(|| received::property(view, &root, "Size")),
    )?
    .map(|value| value / 2.0);
    if let Some(hip) = received::property(view, &humanoid, "HipHeight")
        .and_then(|v| v.as_f64())
        .filter(|v| v.is_finite() && *v >= 0.)
    {
        let rig = received::property(view, &humanoid, "RigType")
            .and_then(|v| v.as_u64().or_else(|| v["$enum"]["value"].as_u64()));
        let mut leg_height = None;
        if rig == Some(0) {
            view.for_each_node(|node| {
                if view.parent(node.id()) == Some(character.as_str())
                    && received::name_is(view, node.id(), "Left Leg")
                {
                    leg_height =
                        received_vector(node.property("Size").or_else(|| node.property("size")))
                            .map(|v| v[1]);
                }
            });
        }
        if rig == Some(1) || leg_height.is_some() {
            half_size[1] += hip + leg_height.unwrap_or(0.);
        }
    }
    let velocity = received_vector(view.get_property(&root, "AssemblyLinearVelocity"))
        .or_else(|| received_vector(frame.get("decoded_velocities")?.get(0).cloned()))
        .unwrap_or([0.0; 3]);
    let mut workspace = view.parent(&root);
    for _ in 0..64 {
        if workspace.is_some_and(|id| view.class_name(id) == Some("Workspace")) {
            break;
        }
        workspace = workspace.and_then(|id| view.parent(id));
    }
    let workspace = workspace.filter(|id| view.class_name(id) == Some("Workspace"))?;
    let gravity =
        received::property(view, workspace, "Gravity").and_then(|value| value.as_f64())?;
    let walk_speed =
        received::property(view, &humanoid, "WalkSpeed").and_then(|value| value.as_f64())?;
    let network_owner_scope = view
        .get_property(&root, "NetworkOwnerV3")
        .and_then(|value| value.as_f64())
        .filter(|value| value.is_finite() && value.fract() == 0.0 && *value >= 0.0)
        .map(|value| value as u64);
    let server_authority = view
        .get_property(workspace, "ServerAuthorityMode")
        .and_then(|value| value.as_str().map(str::to_owned))
        .map(|mode| ReceivedFlag {
            value: mode == "Server",
            source: EvidenceSource::ServerReplication,
        });
    let anchored = received::property(view, &root, "Anchored")
        .and_then(|value| value.as_bool())
        .map(|value| ReceivedFlag {
            value,
            source: if view.get_property(&root, "Anchored").is_some() {
                EvidenceSource::ServerReplication
            } else {
                EvidenceSource::VerifiedConstructorDefault
            },
        });
    let surfaces = cached_surfaces
        .map(<[Aabb]>::to_vec)
        .unwrap_or_else(|| received_surfaces(view, class_parents));
    let grounded = surfaces.iter().any(|bounds| {
        (position[1] - half_size[1] - bounds.maximum[1]).abs() <= 1e-6
            && [0, 2].into_iter().all(|axis| {
                position[axis] + half_size[axis] > bounds.minimum[axis]
                    && position[axis] - half_size[axis] < bounds.maximum[axis]
            })
    });
    let assembly = received_reference(view.get_property(&root, "AssemblyRootPart"));
    Some(ReceivedPhysicsSnapshot {
        evidence: OwnCharacterEvidence {
            binding: OwnBinding {
                world_generation: client_scope,
                user_id,
                character_id: received_instance_key(&character)?,
                root_part_id: received_instance_key(&root)?,
            },
            own_player_confirmed: true,
            character_model_confirmed: true,
            humanoid_confirmed: true,
            root_part_confirmed: true,
            root_in_workspace_confirmed: true,
            assembly_root_confirmed: assembly.as_deref() == Some(root.as_str())
                || (view.get_property(&root, "AssemblyRootPart").is_none()
                    && primary.as_deref() == Some(root.as_str())),
            streaming_region_confirmed,
            anchored,
            network_owner_scope,
            client_scope: Some(client_scope),
            ownership_granted: Some(ReceivedFlag {
                value: network_owner_scope == Some(client_scope),
                source: EvidenceSource::ServerReplication,
            }),
            server_authority_mode: server_authority,
        },
        revision: view.sequence(),
        received_at_monotonic: now_monotonic,
        body: BodyState {
            center: position,
            velocity,
            half_size,
            grounded,
        },
        gravity,
        walk_speed,
        surfaces,
    })
}

fn received_surfaces(view: &WorldView, parents: &HashMap<String, String>) -> Vec<Aabb> {
    let mut surfaces = Vec::new();
    view.for_each_node(|node| {
        let mut class = node.class_name();
        for _ in 0..64 {
            if class == "BasePart" {
                break;
            }
            let Some(parent) = parents.get(class) else {
                return;
            };
            class = parent;
        }
        if class != "BasePart"
            || surfaces.len() >= 65_536
            || received::property(view, node.id(), "Anchored").and_then(|v| v.as_bool())
                != Some(true)
            || received::property(view, node.id(), "CanCollide").and_then(|v| v.as_bool())
                != Some(true)
        {
            return;
        }
        let Some(frame) = received::frame(view, node.id()) else {
            return;
        };
        let identity = frame.get("rotation_id").and_then(serde_json::Value::as_u64) == Some(2)
            || frame.get("rotation").and_then(serde_json::Value::as_u64) == Some(1)
            || frame
                .get("rotation")
                .and_then(serde_json::Value::as_array)
                .is_some_and(|m| {
                    m.len() == 9
                        && m.iter()
                            .enumerate()
                            .all(|(i, v)| v.as_f64() == Some(if i % 4 == 0 { 1. } else { 0. }))
                });
        if !identity {
            return;
        }
        let Some(position) = received_vector(frame.get("position").cloned()) else {
            return;
        };
        let Some(size) = received_vector(
            node.property("Size")
                .or_else(|| node.property("size"))
                .or_else(|| received::property(view, node.id(), "Size")),
        ) else {
            return;
        };
        if let Ok(bounds) = Aabb::new(
            std::array::from_fn(|i| position[i] - size[i] / 2.),
            std::array::from_fn(|i| position[i] + size[i] / 2.),
        ) {
            surfaces.push(bounds);
        }
    });
    surfaces
}

pub fn received_humanoid_id(view: &WorldView, player_id: &str) -> Option<String> {
    let character = received_reference(view.get_property(player_id, "Character"))?;
    let mut result = None;
    view.for_each_node(|node| {
        if node.class_name() == "Humanoid" && view.parent(node.id()) == Some(character.as_str()) {
            result = Some(node.id().to_owned());
        }
    });
    result
}

fn camera_relative_direction(
    direction: [f64; 3],
    camera: Option<&serde_json::Value>,
) -> Option<[f64; 3]> {
    let rotation = camera?.get("rotation")?.as_array()?;
    if rotation.len() != 9 {
        return None;
    }
    let mut matrix = [0.0; 9];
    for (output, value) in matrix.iter_mut().zip(rotation) {
        *output = value.as_f64().filter(|value| value.is_finite())?;
    }
    for row in 0..3 {
        let values = &matrix[row * 3..row * 3 + 3];
        let length = values.iter().map(|value| value * value).sum::<f64>();
        if (length - 1.0).abs() > 0.001 {
            return None;
        }
        for other in 0..row {
            let dot = (0..3)
                .map(|column| matrix[row * 3 + column] * matrix[other * 3 + column])
                .sum::<f64>();
            if dot.abs() > 0.001 {
                return None;
            }
        }
    }
    let determinant = matrix[0] * (matrix[4] * matrix[8] - matrix[5] * matrix[7])
        - matrix[1] * (matrix[3] * matrix[8] - matrix[5] * matrix[6])
        + matrix[2] * (matrix[3] * matrix[7] - matrix[4] * matrix[6]);
    if (determinant - 1.0).abs() > 0.001 {
        return None;
    }
    Some([
        matrix[0] * direction[0] + matrix[2] * direction[2],
        0.0,
        matrix[6] * direction[0] + matrix[8] * direction[2],
    ])
}

fn jump_speed(settings: &serde_json::Value, gravity: f64) -> Option<f64> {
    let settings = settings.as_object()?;
    let boolean = |name, default| {
        settings
            .get(name)
            .map(serde_json::Value::as_bool)
            .unwrap_or(Some(default))
    };
    if boolean("PlatformStand", false)? || boolean("Sit", false)? {
        return None;
    }
    let number = |name, default| {
        settings
            .get(name)
            .map(serde_json::Value::as_f64)
            .unwrap_or(Some(default))
            .filter(|value| value.is_finite())
    };
    if number("Health", 100.0)? <= 0.0 {
        return None;
    }
    let use_power = boolean("UseJumpPower", true)?;
    let strength = if use_power {
        number("JumpPower", 50.0)?
    } else {
        number("JumpHeight", 7.2)?
    };
    if strength < 0.0 || (use_power && strength > 1000.0) {
        return None;
    }
    let speed = if use_power {
        strength
    } else {
        (2.0 * gravity * strength).sqrt()
    };
    speed.is_finite().then_some(speed)
}

#[derive(Clone, Copy, Debug)]
struct MoveToState {
    target: [f64; 3],
    started_at: f64,
    sequence: u64,
}

#[derive(Debug)]
pub struct LocalPrediction {
    simulator: Option<LivePhysicsSimulator>,
    intent: MovementIntent,
    cursor: u64,
    frame: Option<PredictionFrame>,
    rejected_intents: u64,
    camera_relative_intents: u64,
    pending_jump: Option<serde_json::Value>,
    accepted_jumps: u64,
    rejected_jumps: u64,
    move_to: Option<MoveToState>,
    completed_move_to: u64,
    expired_move_to: u64,
    humanoid: Option<String>,
    last_received_pose: Option<([f64; 3], [f64; 3])>,
    geometry: Vec<Aabb>,
    geometry_sequence: u64,
    geometry_at: Option<f64>,
    fixed_steps: u64,
    simulated_seconds: f64,
    dropped_seconds: f64,
    ground_contacts: u64,
    last_reason: &'static str,
}

impl Default for LocalPrediction {
    fn default() -> Self {
        Self {
            simulator: None,
            intent: MovementIntent {
                direction: [0.0; 3],
            },
            cursor: 0,
            frame: None,
            rejected_intents: 0,
            camera_relative_intents: 0,
            pending_jump: None,
            accepted_jumps: 0,
            rejected_jumps: 0,
            move_to: None,
            completed_move_to: 0,
            expired_move_to: 0,
            humanoid: None,
            last_received_pose: None,
            geometry: Vec::new(),
            geometry_sequence: 0,
            geometry_at: None,
            fixed_steps: 0,
            simulated_seconds: 0.,
            dropped_seconds: 0.,
            ground_contacts: 0,
            last_reason: "waiting_for_received_character",
        }
    }
}

impl LocalPrediction {
    pub fn advance(
        &mut self,
        view: &WorldView,
        class_parents: &HashMap<String, String>,
        player_id: &str,
        user_id: u64,
        client_scope: u64,
        streaming_region_confirmed: bool,
        intents: &[serde_json::Value],
        now_monotonic: f64,
    ) -> Result<Option<PredictionFrame>, LivePhysicsError> {
        if intents.len() > 256 {
            return Err(LivePhysicsError::InvalidIntent);
        }
        let character = received_reference(view.get_property(player_id, "Character"));
        let humanoid = self
            .humanoid
            .as_deref()
            .filter(|id| {
                view.class_name(id) == Some("Humanoid")
                    && character
                        .as_deref()
                        .is_some_and(|character| view.parent(id) == Some(character))
            })
            .map(str::to_owned)
            .or_else(|| received_humanoid_id(view, player_id));
        let Some(humanoid) = humanoid else {
            self.simulator = None;
            self.frame = None;
            self.last_received_pose = None;
            self.intent = MovementIntent { direction: [0.; 3] };
            self.move_to = None;
            self.pending_jump = None;
            self.humanoid = None;
            self.last_reason = "waiting_for_received_character";
            return Ok(None);
        };
        self.humanoid = Some(humanoid.clone());
        for intent in intents {
            let sequence = intent
                .get("sequence")
                .and_then(serde_json::Value::as_u64)
                .filter(|sequence| *sequence > self.cursor)
                .ok_or(LivePhysicsError::InvalidIntent)?;
            self.cursor = sequence;
            let kind = intent.get("kind").and_then(serde_json::Value::as_str);
            if kind != Some("Move") && kind != Some("Jump") && kind != Some("MoveTo") {
                continue;
            }
            let receiver = intent
                .get("instance_id")
                .and_then(serde_json::Value::as_str)
                .ok_or(LivePhysicsError::InvalidIntent)?;
            if receiver != humanoid && receiver != player_id {
                self.rejected_intents = self.rejected_intents.saturating_add(1);
                continue;
            }
            if kind == Some("Jump") {
                self.pending_jump = Some(
                    intent
                        .get("jump_settings")
                        .cloned()
                        .unwrap_or_else(|| serde_json::json!({})),
                );
                continue;
            }
            if kind == Some("MoveTo") {
                self.move_to = Some(MoveToState {
                    target: received_vector(intent.get("vector").cloned())
                        .ok_or(LivePhysicsError::InvalidIntent)?,
                    started_at: now_monotonic,
                    sequence,
                });
                self.intent = MovementIntent {
                    direction: [0.0; 3],
                };
                continue;
            }
            self.move_to = None;
            let relative_to_camera = intent
                .get("relative_to_camera")
                .and_then(serde_json::Value::as_bool)
                .ok_or(LivePhysicsError::InvalidIntent)?;
            let direction = received_vector(intent.get("vector").cloned())
                .ok_or(LivePhysicsError::InvalidIntent)?;
            let direction = if relative_to_camera {
                camera_relative_direction(direction, intent.get("camera"))
                    .ok_or(LivePhysicsError::InvalidIntent)?
            } else {
                direction
            };
            self.camera_relative_intents = self
                .camera_relative_intents
                .saturating_add(u64::from(relative_to_camera));
            self.intent = MovementIntent { direction };
        }
        let refresh_geometry = self.geometry_at.is_none()
            || (view.sequence() != self.geometry_sequence
                && now_monotonic - self.geometry_at.unwrap() >= 0.5);
        let Some(snapshot) = received_snapshot_cached(
            view,
            class_parents,
            player_id,
            user_id,
            client_scope,
            streaming_region_confirmed,
            now_monotonic,
            Some(&humanoid),
            if refresh_geometry {
                None
            } else {
                Some(&self.geometry)
            },
        ) else {
            self.simulator = None;
            self.frame = None;
            self.last_received_pose = None;
            self.intent = MovementIntent { direction: [0.; 3] };
            self.move_to = None;
            self.pending_jump = None;
            self.last_reason = "waiting_for_received_physics_properties";
            return Ok(None);
        };
        if refresh_geometry {
            self.geometry = snapshot.surfaces.clone();
            self.geometry_sequence = view.sequence();
            self.geometry_at = Some(now_monotonic);
        }
        if !snapshot.evidence.eligible() {
            self.simulator = None;
            self.frame = None;
            self.last_received_pose = None;
            self.intent = MovementIntent { direction: [0.; 3] };
            self.move_to = None;
            self.pending_jump = None;
            self.last_reason = if snapshot.evidence.anchored.is_none() {
                "anchored_unknown"
            } else if snapshot.evidence.anchored.is_some_and(|v| v.value) {
                "anchored"
            } else if snapshot.evidence.network_owner_scope != Some(client_scope) {
                "not_owned_by_this_session"
            } else if !snapshot.evidence.assembly_root_confirmed {
                "assembly_root_unknown"
            } else if !streaming_region_confirmed {
                "waiting_for_streaming_region"
            } else {
                "received_authority_incomplete"
            };
            return Ok(None);
        }
        let received_pose = (snapshot.body.center, snapshot.body.velocity);
        match self.simulator.as_mut() {
            Some(simulator) if simulator.binding() == snapshot.evidence.binding => {
                if self.last_received_pose != Some(received_pose)
                    && snapshot.revision > simulator.received_revision()
                {
                    simulator.reconcile_received(snapshot)?;
                } else {
                    // An unrelated world update is not a correction to this root.
                    simulator.gravity = snapshot.gravity;
                    simulator.stepper.gravity = snapshot.gravity;
                    simulator.walk_speed = snapshot.walk_speed;
                    simulator.surfaces = snapshot.surfaces;
                    simulator.state.half_size = snapshot.body.half_size;
                }
            }
            _ => self.simulator = Some(LivePhysicsSimulator::from_received(snapshot)?),
        }
        self.last_received_pose = Some(received_pose);
        let simulator = self.simulator.as_mut().unwrap();
        if let Some(settings) = self.pending_jump.take() {
            let accepted = match jump_speed(&settings, simulator.gravity) {
                Some(speed) if speed > 0.0 => simulator.apply_jump(speed)?,
                _ => false,
            };
            if accepted {
                self.accepted_jumps = self.accepted_jumps.saturating_add(1);
            } else {
                self.rejected_jumps = self.rejected_jumps.saturating_add(1);
            }
        }
        if let Some(target) = self.move_to {
            if now_monotonic - target.started_at >= 8.0 {
                self.move_to = None;
                self.intent = MovementIntent {
                    direction: [0.0; 3],
                };
                self.expired_move_to = self.expired_move_to.saturating_add(1);
            } else {
                let position = simulator.position();
                let direction = [
                    target.target[0] - position[0],
                    0.0,
                    target.target[2] - position[2],
                ];
                if direction[0].hypot(direction[2]) <= 1.0 {
                    self.move_to = None;
                    self.intent = MovementIntent {
                        direction: [0.0; 3],
                    };
                    self.completed_move_to = self.completed_move_to.saturating_add(1);
                } else {
                    self.intent = MovementIntent { direction };
                }
            }
        }
        let frame = simulator.advance_with_policy(now_monotonic, self.intent, true)?;
        self.fixed_steps = self.fixed_steps.saturating_add(frame.fixed_steps as u64);
        self.simulated_seconds += frame.simulated_seconds;
        self.dropped_seconds += frame.dropped_seconds;
        self.ground_contacts = self
            .ground_contacts
            .saturating_add(frame.ground_contacts as u64);
        self.last_reason = "active";
        self.frame = Some(frame);
        Ok(self.frame)
    }

    pub fn feedback(&self) -> Option<serde_json::Value> {
        let frame = self.frame?;
        let humanoid = self.humanoid.as_ref()?;
        let root = format!(
            "{}:{}",
            frame.binding.root_part_id >> 32,
            frame.binding.root_part_id as u32
        );
        Some(serde_json::json!({
            "humanoid_id":humanoid,
            "state":{"name":if frame.grounded {"Running"} else {"Freefall"},"value":if frame.grounded {8} else {5}},
            "running_speed":frame.velocity[0].hypot(frame.velocity[2]),
            "motion":{"root_id":root,"linear_velocity":frame.velocity},
            "engine_poses":{
                "character_id":format!("{}:{}",frame.binding.character_id >> 32,frame.binding.character_id as u32),
                "root_id":root,"frames":[{"id":root,"cframe":{"$cframe":{
                    "position":frame.position,"rotation":[1,0,0,0,1,0,0,0,1]}}}]
            }
        }))
    }

    pub fn report(&self) -> serde_json::Value {
        serde_json::json!({
            "active":self.simulator.is_some(),
            "status":self.last_reason,
            "fixed_steps":self.fixed_steps,
            "simulated_seconds":self.simulated_seconds,
            "dropped_seconds":self.dropped_seconds,
            "ground_contacts":self.ground_contacts,
            "ground_collider_count":self.geometry.len(),
            "collision_model":"static_axis_aligned_basepart_bounds",
            "server_acceptance_confirmed":false,
            "movement_cursor":self.cursor,
            "rejected_intents":self.rejected_intents,
            "camera_relative_intents":self.camera_relative_intents,
            "accepted_jumps":self.accepted_jumps,
            "rejected_jumps":self.rejected_jumps,
            "completed_move_to":self.completed_move_to,
            "expired_move_to":self.expired_move_to,
            "move_to":self.move_to.map(|target| serde_json::json!({
                "target":target.target,"sequence":target.sequence,"started_at":target.started_at
            })),
            "frame":self.frame.map(|frame| serde_json::json!({
                "position":frame.position,"velocity":frame.velocity,"grounded":frame.grounded,
                "based_on_received_revision":frame.based_on_received_revision,
                "server_acceptance_confirmed":frame.server_acceptance_confirmed
            }))
        })
    }
}

fn validate_snapshot(snapshot: &ReceivedPhysicsSnapshot) -> Result<(), LivePhysicsError> {
    snapshot.body.validate().map_err(map_physics_error)?;
    if !snapshot.received_at_monotonic.is_finite()
        || snapshot.received_at_monotonic < 0.0
        || !snapshot.gravity.is_finite()
        || snapshot.gravity < 0.0
        || !snapshot.walk_speed.is_finite()
        || !(0.0..=MAX_WALK_SPEED).contains(&snapshot.walk_speed)
        || snapshot.surfaces.len() > 65_536
    {
        return Err(
            if !snapshot.walk_speed.is_finite()
                || !(0.0..=MAX_WALK_SPEED).contains(&snapshot.walk_speed)
            {
                LivePhysicsError::InvalidWalkSpeed
            } else {
                LivePhysicsError::InvalidSnapshot
            },
        );
    }
    Ok(())
}

fn map_physics_error(_: PhysicsError) -> LivePhysicsError {
    LivePhysicsError::InvalidPhysics
}

#[cfg(test)]
mod tests {
    use super::*;

    fn received_world(owner: f64) -> rove_client_core::world::WorldStore {
        rove_client_core::world::WorldStore::from_snapshot(&serde_json::json!({
            "version":1,"sequence":10,"nodes":[
                {"id":"1:1","class_name":"Players","parent":null,"properties":{}},
                {"id":"1:2","class_name":"Player","parent":"1:1","properties":{
                    "UserId":42,"Character":{"$ref":"1:3"}}},
                {"id":"1:3","class_name":"Model","parent":"1:4","properties":{}},
                {"id":"1:4","class_name":"Workspace","parent":null,"properties":{"Gravity":196.2}},
                {"id":"1:5","class_name":"Humanoid","parent":"1:3","properties":{"WalkSpeed":16.0}},
                {"id":"1:6","class_name":"Part","name":"HumanoidRootPart","parent":"1:3","properties":{
                    "CFrame":{"$cframe":{"position":[0,2,0]}},"Size":{"$vector3":[2,4,2]},
                    "Anchored":false,"NetworkOwnerV3":owner,"AssemblyRootPart":{"$ref":"1:6"}}},
                {"id":"1:7","class_name":"SpawnLocation","parent":"1:4","properties":{
                    "Anchored":true,"CanCollide":true,"Size":{"$vector3":[20,1,20]},
                    "CFrame":{"position":[0,-0.5,0],"rotation_id":2}}}
            ]
        }))
        .unwrap()
    }

    fn test_parents() -> HashMap<String, String> {
        HashMap::from([
            ("Part".into(), "BasePart".into()),
            ("SpawnLocation".into(), "Part".into()),
        ])
    }

    #[test]
    fn unrelated_world_updates_do_not_reset_local_motion() {
        let world = received_world(7.0);
        let mut prediction = LocalPrediction::default();
        let intent = serde_json::json!({"sequence":1,"kind":"Move","instance_id":"1:5",
            "vector":[1,0,0],"relative_to_camera":false});
        prediction
            .advance(
                &world.view(),
                &test_parents(),
                "1:2",
                42,
                7,
                true,
                &[intent],
                1.0,
            )
            .unwrap();
        let mut source = world.to_snapshot();
        source["sequence"] = serde_json::json!(11);
        let updated = rove_client_core::world::WorldStore::from_snapshot(&source).unwrap();
        let first = prediction
            .advance(
                &updated.view(),
                &test_parents(),
                "1:2",
                42,
                7,
                true,
                &[],
                1.05,
            )
            .unwrap()
            .unwrap();
        assert!(first.position[0] > 0.6);
        source["sequence"] = serde_json::json!(12);
        let updated = rove_client_core::world::WorldStore::from_snapshot(&source).unwrap();
        let second = prediction
            .advance(
                &updated.view(),
                &test_parents(),
                "1:2",
                42,
                7,
                true,
                &[],
                1.10,
            )
            .unwrap()
            .unwrap();
        assert!(second.position[0] > first.position[0] + 0.6);
        assert!(prediction.report()["fixed_steps"].as_u64().unwrap() > 0);
        // Losing ownership must immediately stop producing frames.
        assert!(prediction
            .advance(
                &received_world(8.).view(),
                &test_parents(),
                "1:2",
                42,
                7,
                true,
                &[],
                1.12
            )
            .unwrap()
            .is_none());
    }

    #[test]
    fn streamed_raw_properties_use_verified_defaults_without_modifying_the_world() {
        let mut source = received_world(7.).to_snapshot();
        for node in source["nodes"].as_array_mut().unwrap() {
            match node["id"].as_str().unwrap() {
                "1:3" => node["properties"]["PrimaryPart"] = serde_json::json!({"$ref":"1:6"}),
                "1:4" => {
                    node["top_container_flags"] = serde_json::json!([true, false]);
                }
                "1:5" => {
                    node["received_creation_complete"] = serde_json::json!(true);
                }
                "1:6" => {
                    node["received_creation_complete"] = serde_json::json!(true);
                    node.as_object_mut().unwrap().remove("name");
                    node["properties"]["Name"] =
                        serde_json::json!({"$bytes_hex":"48756d616e6f6964526f6f7450617274"});
                    let properties = node["properties"].as_object_mut().unwrap();
                    properties.remove("Anchored");
                    properties.remove("AssemblyRootPart");
                    let size = properties.remove("Size").unwrap();
                    properties.insert("size".into(), size);
                    properties.remove("CFrame");
                    properties.insert(
                        "ReplicationPV".into(),
                        serde_json::json!({"position":[0,5,0],"rotation":1}),
                    );
                }
                _ => {}
            }
        }
        let world = rove_client_core::world::WorldStore::from_snapshot(&source).unwrap();
        let before = world.to_snapshot();
        let snapshot =
            received_snapshot(&world.view(), &test_parents(), "1:2", 42, 7, true, 1.).unwrap();
        assert!(snapshot.evidence.eligible());
        assert_eq!(
            snapshot.evidence.anchored.unwrap().source,
            EvidenceSource::VerifiedConstructorDefault
        );
        assert_eq!(snapshot.body.center, [0., 5., 0.]);
        assert!(snapshot.gravity > 190.);
        assert_eq!(world.to_snapshot(), before);
        let mut unknown = source.clone();
        for node in unknown["nodes"].as_array_mut().unwrap() {
            if node["id"] == "1:4" {
                node["properties"]
                    .as_object_mut()
                    .unwrap()
                    .remove("Gravity");
            }
        }
        let unknown = rove_client_core::world::WorldStore::from_snapshot(&unknown).unwrap();
        assert!(
            received_snapshot(&unknown.view(), &test_parents(), "1:2", 42, 7, true, 1.).is_none()
        );
    }

    #[test]
    fn live_hitch_caps_simulation_and_counts_dropped_time() {
        let world = received_world(7.);
        let mut prediction = LocalPrediction::default();
        prediction
            .advance(&world.view(), &test_parents(), "1:2", 42, 7, true, &[], 1.)
            .unwrap();
        let frame = prediction
            .advance(&world.view(), &test_parents(), "1:2", 42, 7, true, &[], 3.)
            .unwrap()
            .unwrap();
        assert!(frame.simulated_seconds <= 0.25 + 1e-9);
        assert!(frame.dropped_seconds >= 1.75);
    }

    #[test]
    fn received_character_snapshot_drives_detached_prediction() {
        let world = received_world(7.0);
        let snapshot =
            received_snapshot(&world.view(), &test_parents(), "1:2", 42, 7, true, 1.0).unwrap();
        assert!(snapshot.evidence.assembly_root_confirmed);
        assert!(snapshot.body.grounded);
        assert_eq!(snapshot.body.half_size, [1.0, 2.0, 1.0]);
        assert_eq!(
            snapshot.surfaces,
            vec![Aabb::new([-10.0, -1.0, -10.0], [10.0, 0.0, 10.0]).unwrap()]
        );
        let mut simulator = LivePhysicsSimulator::from_received(snapshot).unwrap();
        simulator
            .advance(
                1.0,
                MovementIntent {
                    direction: [0.0; 3],
                },
            )
            .unwrap();
        let frame = simulator
            .advance(
                1.02,
                MovementIntent {
                    direction: [3.0, 0.0, 0.0],
                },
            )
            .unwrap();
        assert!(frame.position[0] > 0.0);
        assert!(!frame.server_acceptance_confirmed);

        let wrong_owner = received_world(8.0);
        let snapshot = received_snapshot(
            &wrong_owner.view(),
            &test_parents(),
            "1:2",
            42,
            7,
            true,
            1.0,
        )
        .unwrap();
        assert_eq!(
            LivePhysicsSimulator::from_received(snapshot).unwrap_err(),
            LivePhysicsError::MissingOrUntrustedAuthorityEvidence
        );
    }

    #[test]
    fn ordered_move_records_drive_only_the_received_humanoid() {
        let world = received_world(7.0);
        let mut prediction = LocalPrediction::default();
        let intents = [serde_json::json!({"sequence":1,"kind":"Move",
            "instance_id":"1:5","vector":[3,0,0],"relative_to_camera":false})];
        prediction
            .advance(
                &world.view(),
                &test_parents(),
                "1:2",
                42,
                7,
                true,
                &intents,
                1.0,
            )
            .unwrap();
        let frame = prediction
            .advance(
                &world.view(),
                &test_parents(),
                "1:2",
                42,
                7,
                true,
                &[],
                1.02,
            )
            .unwrap()
            .unwrap();
        assert!(frame.position[0] > 0.0);
        assert_eq!(prediction.report()["movement_cursor"], 1);

        let player_move = [serde_json::json!({"sequence":2,"kind":"Move",
            "instance_id":"1:2","vector":[0,0,-1],"relative_to_camera":false})];
        prediction
            .advance(
                &world.view(),
                &test_parents(),
                "1:2",
                42,
                7,
                true,
                &player_move,
                1.04,
            )
            .unwrap();
        assert_eq!(prediction.report()["movement_cursor"], 2);
        assert_eq!(prediction.report()["rejected_intents"], 0);

        let rejected = [serde_json::json!({"sequence":3,"kind":"Move",
            "instance_id":"other","vector":[3,0,0],"relative_to_camera":false})];
        prediction
            .advance(
                &world.view(),
                &test_parents(),
                "1:2",
                42,
                7,
                true,
                &rejected,
                1.06,
            )
            .unwrap();
        assert_eq!(prediction.report()["rejected_intents"], 1);
        assert!(prediction
            .advance(
                &world.view(),
                &test_parents(),
                "1:2",
                42,
                7,
                true,
                &rejected,
                1.08
            )
            .is_err());
    }

    #[test]
    fn camera_relative_move_uses_captured_horizontal_orientation() {
        let world = received_world(7.0);
        let mut prediction = LocalPrediction::default();
        let intent = serde_json::json!({"sequence":1,"kind":"Move","instance_id":"1:5",
            "vector":[1,0,-1],"relative_to_camera":true,"camera":{
                "rotation":[1,0,0,0,1,0,0,0,1]}});
        prediction
            .advance(
                &world.view(),
                &test_parents(),
                "1:2",
                42,
                7,
                true,
                &[intent],
                1.0,
            )
            .unwrap();
        let frame = prediction
            .advance(
                &world.view(),
                &test_parents(),
                "1:2",
                42,
                7,
                true,
                &[],
                1.02,
            )
            .unwrap()
            .unwrap();
        assert!(frame.position[0] > 0.0 && frame.position[2] < 0.0);
        assert_eq!(prediction.report()["camera_relative_intents"], 1);

        let mut rotated = LocalPrediction::default();
        let intent = serde_json::json!({"sequence":1,"kind":"Move","instance_id":"1:5",
            "vector":[0,0,-1],"relative_to_camera":true,"camera":{
                "rotation":[0,0,1,0,1,0,-1,0,0]}});
        rotated
            .advance(
                &world.view(),
                &test_parents(),
                "1:2",
                42,
                7,
                true,
                &[intent],
                1.0,
            )
            .unwrap();
        let frame = rotated
            .advance(
                &world.view(),
                &test_parents(),
                "1:2",
                42,
                7,
                true,
                &[],
                1.02,
            )
            .unwrap()
            .unwrap();
        assert!(frame.position[0] < 0.0 && frame.position[2].abs() < 1e-9);

        let invalid = serde_json::json!({"sequence":1,"kind":"Move","instance_id":"1:5",
            "vector":[1,0,0],"relative_to_camera":true,"camera":{"rotation":vec![0;9]}});
        assert_eq!(
            LocalPrediction::default()
                .advance(
                    &world.view(),
                    &test_parents(),
                    "1:2",
                    42,
                    7,
                    true,
                    &[invalid],
                    1.0
                )
                .unwrap_err(),
            LivePhysicsError::InvalidIntent
        );
    }

    #[test]
    fn jumps_require_ground_and_use_native_strength_modes() {
        let mut snapshot = snapshot();
        snapshot.body.grounded = true;
        let mut simulator = LivePhysicsSimulator::from_received(snapshot).unwrap();
        assert_eq!(simulator.apply_jump(50.0), Ok(true));
        simulator.advance(1.0, idle()).unwrap();
        let frame = simulator.advance(1.02, idle()).unwrap();
        assert!(frame.position[1] > 1.0 && frame.velocity[1] > 0.0);
        assert_eq!(simulator.apply_jump(50.0), Ok(false));

        let height = serde_json::json!({"UseJumpPower":false,"JumpHeight":2.0});
        assert!((jump_speed(&height, 10.0).unwrap() - 40.0f64.sqrt()).abs() < 1e-12);
        assert!(jump_speed(&serde_json::json!({"Health":0}), 10.0).is_none());
        assert!(jump_speed(&serde_json::json!({"JumpPower":1001}), 10.0).is_none());

        let world = received_world(7.0);
        let jump = [
            serde_json::json!({"sequence":1,"kind":"Jump","instance_id":"1:5",
            "jump_settings":{"JumpPower":50}}),
        ];
        let mut prediction = LocalPrediction::default();
        prediction
            .advance(
                &world.view(),
                &test_parents(),
                "1:2",
                42,
                7,
                true,
                &jump,
                1.0,
            )
            .unwrap();
        assert_eq!(prediction.report()["accepted_jumps"], 1);
        assert_eq!(prediction.report()["rejected_jumps"], 0);
    }

    #[test]
    fn move_to_tracks_targets_arrival_and_move_cancellation() {
        let world = received_world(7.0);
        let target = [
            serde_json::json!({"sequence":1,"kind":"MoveTo","instance_id":"1:5",
            "vector":[2,0,0],"relative_to_camera":false}),
        ];
        let mut prediction = LocalPrediction::default();
        prediction
            .advance(
                &world.view(),
                &test_parents(),
                "1:2",
                42,
                7,
                true,
                &target,
                1.0,
            )
            .unwrap();
        let frame = prediction
            .advance(
                &world.view(),
                &test_parents(),
                "1:2",
                42,
                7,
                true,
                &[],
                1.02,
            )
            .unwrap()
            .unwrap();
        assert!(frame.position[0] > 0.0);
        assert_eq!(prediction.report()["move_to"]["sequence"], 1);

        let cancel = [
            serde_json::json!({"sequence":2,"kind":"Move","instance_id":"1:5",
            "vector":[0,0,0],"relative_to_camera":false}),
        ];
        let frame = prediction
            .advance(
                &world.view(),
                &test_parents(),
                "1:2",
                42,
                7,
                true,
                &cancel,
                1.04,
            )
            .unwrap()
            .unwrap();
        assert!(prediction.report()["move_to"].is_null());
        assert_eq!(frame.velocity[0], 0.0);

        let mut reached = LocalPrediction::default();
        let near = [
            serde_json::json!({"sequence":1,"kind":"MoveTo","instance_id":"1:5",
            "vector":[0.5,0,0],"relative_to_camera":false}),
        ];
        reached
            .advance(
                &world.view(),
                &test_parents(),
                "1:2",
                42,
                7,
                true,
                &near,
                1.0,
            )
            .unwrap();
        assert_eq!(reached.report()["completed_move_to"], 1);
        assert!(reached.report()["move_to"].is_null());
    }

    fn evidence() -> OwnCharacterEvidence {
        OwnCharacterEvidence {
            binding: OwnBinding {
                world_generation: 7,
                user_id: 42,
                character_id: 100,
                root_part_id: 101,
            },
            own_player_confirmed: true,
            character_model_confirmed: true,
            humanoid_confirmed: true,
            root_part_confirmed: true,
            root_in_workspace_confirmed: true,
            assembly_root_confirmed: true,
            streaming_region_confirmed: true,
            anchored: Some(ReceivedFlag {
                value: false,
                source: EvidenceSource::ServerReplication,
            }),
            network_owner_scope: Some(9),
            client_scope: Some(9),
            ownership_granted: Some(ReceivedFlag {
                value: true,
                source: EvidenceSource::ServerReplication,
            }),
            server_authority_mode: Some(ReceivedFlag {
                value: false,
                source: EvidenceSource::ServerReplication,
            }),
        }
    }

    fn snapshot() -> ReceivedPhysicsSnapshot {
        ReceivedPhysicsSnapshot {
            evidence: evidence(),
            revision: 10,
            received_at_monotonic: 1.0,
            body: BodyState {
                center: [0.0, 1.0, 0.0],
                velocity: [0.0; 3],
                half_size: [0.5; 3],
                grounded: false,
            },
            gravity: 196.2,
            walk_speed: 10.0,
            surfaces: vec![Aabb::new([-10.0, -1.0, -10.0], [10.0, 0.0, 10.0]).unwrap()],
        }
    }

    fn idle() -> MovementIntent {
        MovementIntent {
            direction: [0.0; 3],
        }
    }

    #[test]
    fn rejects_unconfirmed_or_non_server_ownership_and_readiness() {
        let mut no_owner = snapshot();
        no_owner.evidence.ownership_granted = None;
        assert_eq!(
            LivePhysicsSimulator::from_received(no_owner).unwrap_err(),
            LivePhysicsError::MissingOrUntrustedAuthorityEvidence
        );

        let mut guessed_owner = snapshot();
        guessed_owner.evidence.ownership_granted = Some(ReceivedFlag {
            value: true,
            source: EvidenceSource::LocalDefault,
        });
        assert_eq!(
            LivePhysicsSimulator::from_received(guessed_owner).unwrap_err(),
            LivePhysicsError::MissingOrUntrustedAuthorityEvidence
        );

        let mut unready = snapshot();
        unready.evidence.humanoid_confirmed = false;
        assert_eq!(
            LivePhysicsSimulator::from_received(unready).unwrap_err(),
            LivePhysicsError::MissingOrUntrustedAuthorityEvidence
        );

        let mut anchored = snapshot();
        anchored.evidence.anchored = Some(ReceivedFlag {
            value: true,
            source: EvidenceSource::ServerReplication,
        });
        assert_eq!(
            LivePhysicsSimulator::from_received(anchored).unwrap_err(),
            LivePhysicsError::MissingOrUntrustedAuthorityEvidence
        );
    }

    #[test]
    fn reconciles_newer_received_snapshot_for_same_character() {
        let mut simulator = LivePhysicsSimulator::from_received(snapshot()).unwrap();
        let mut received = snapshot();
        received.revision = 11;
        received.received_at_monotonic = 1.1;
        received.body.center = [3.0, 2.0, 4.0];
        received.body.velocity = [1.0, 2.0, 3.0];
        simulator.reconcile_received(received).unwrap();
        assert_eq!(simulator.received_revision(), 11);
        let frame = simulator.advance(1.1, idle()).unwrap();
        assert_eq!(frame.position, [3.0, 2.0, 4.0]);
        assert_eq!(frame.velocity[1], 2.0);
        assert_eq!(frame.based_on_received_revision, 11);
        assert!(!frame.server_acceptance_confirmed);
    }

    #[test]
    fn gravity_falls_onto_received_floor() {
        let mut simulator = LivePhysicsSimulator::from_received(snapshot()).unwrap();
        simulator.advance(1.0, idle()).unwrap();
        let frame = simulator.advance(1.1, idle()).unwrap();
        assert!(frame.fixed_steps > 0);
        assert!(frame.ground_contacts > 0);
        assert!(frame.grounded);
        assert!((frame.position[1] - 0.5).abs() < 1.0e-6);
        assert_eq!(frame.velocity[1], 0.0);
    }

    #[test]
    fn movement_intent_is_normalized_and_rejects_invalid_bounds() {
        let mut simulator = LivePhysicsSimulator::from_received(snapshot()).unwrap();
        simulator.advance(1.0, idle()).unwrap();
        let frame = simulator
            .advance(
                1.1,
                MovementIntent {
                    direction: [3.0, 900.0, 4.0],
                },
            )
            .unwrap();
        assert!((frame.velocity[0] - 6.0).abs() < 1.0e-9);
        assert!((frame.velocity[2] - 8.0).abs() < 1.0e-9);
        assert_eq!(frame.velocity[1], 0.0);

        let mut simulator = LivePhysicsSimulator::from_received(snapshot()).unwrap();
        simulator.advance(1.0, idle()).unwrap();
        assert_eq!(
            simulator.advance(
                1.1,
                MovementIntent {
                    direction: [f64::NAN, 0.0, 0.0],
                }
            ),
            Err(LivePhysicsError::InvalidIntent)
        );

        let mut simulator = LivePhysicsSimulator::from_received(snapshot()).unwrap();
        simulator.advance(1.0, idle()).unwrap();
        assert_eq!(
            simulator.advance(1.3, idle()),
            Err(LivePhysicsError::InvalidTime)
        );
    }

    #[test]
    fn respawn_or_session_change_invalidates_old_prediction() {
        let changes: [fn(&mut OwnBinding); 3] = [
            |binding: &mut OwnBinding| binding.character_id += 1,
            |binding: &mut OwnBinding| binding.root_part_id += 1,
            |binding: &mut OwnBinding| binding.world_generation += 1,
        ];
        for change in changes {
            let mut simulator = LivePhysicsSimulator::from_received(snapshot()).unwrap();
            let mut received = snapshot();
            received.revision += 1;
            received.received_at_monotonic += 0.1;
            change(&mut received.evidence.binding);
            assert_eq!(
                simulator.reconcile_received(received),
                Err(LivePhysicsError::BindingChanged)
            );
            assert_eq!(
                simulator.advance(1.1, idle()),
                Err(LivePhysicsError::Invalidated)
            );
        }

        let mut simulator = LivePhysicsSimulator::from_received(snapshot()).unwrap();
        let mut lost_ownership = snapshot();
        lost_ownership.revision += 1;
        lost_ownership.received_at_monotonic += 0.1;
        lost_ownership.evidence.ownership_granted = Some(ReceivedFlag {
            value: false,
            source: EvidenceSource::ServerReplication,
        });
        assert_eq!(
            simulator.reconcile_received(lost_ownership),
            Err(LivePhysicsError::MissingOrUntrustedAuthorityEvidence)
        );
        assert_eq!(
            simulator.advance(1.1, idle()),
            Err(LivePhysicsError::Invalidated)
        );
    }

    #[test]
    fn scheduler_skips_early_calls_and_checks_only_completed_jobs() {
        let integrity = SessionIntegrity::new();
        let mut simulator = LivePhysicsSimulator::from_received(snapshot()).unwrap();
        simulator.set_integrity(integrity.clone());
        simulator.advance(1.0, idle()).unwrap();
        let skipped = simulator.advance(1.005, idle()).unwrap();
        assert_eq!(skipped.fixed_steps, 0);
        assert_eq!(skipped.simulated_seconds, 0.0);
        assert_eq!(simulator.job_report()["job_scheduler"]["admitted_jobs"], 1);
        assert_eq!(
            simulator.job_report()["job_statistics"]["completed_jobs"],
            1
        );
        assert_eq!(
            integrity.report()["physics_comparison_checks"]["physics_gravity_consistency"],
            1
        );

        simulator.advance(1.02, idle()).unwrap();
        assert_eq!(simulator.job_report()["job_scheduler"]["admitted_jobs"], 2);
        assert_eq!(
            simulator.job_report()["job_statistics"]["completed_jobs"],
            2
        );
        assert_eq!(integrity.wire_status(), Ok(0));
        for producer in [
            "physics_interval_smoothing",
            "physics_gravity_consistency",
            "physics_scheduler_mode",
            "physics_world_bounds",
        ] {
            assert_eq!(integrity.report()["physics_comparison_checks"][producer], 2);
        }
    }

    #[test]
    fn physics_configuration_mismatches_are_sticky_and_not_repaired() {
        let integrity = SessionIntegrity::new();
        let mut gravity = LivePhysicsSimulator::from_received(snapshot()).unwrap();
        gravity.set_integrity(integrity.clone());
        gravity.stepper.gravity = 25.0;
        gravity.advance(1.0, idle()).unwrap();
        assert_eq!(gravity.stepper.gravity, 25.0);
        assert_eq!(integrity.wire_status(), Ok(0x80));
        gravity.stepper.gravity = gravity.gravity;
        gravity.advance(1.1, idle()).unwrap();
        assert_eq!(integrity.wire_status(), Ok(0x80));

        let integrity = SessionIntegrity::new();
        let mut smoothing = LivePhysicsSimulator::from_received(snapshot()).unwrap();
        smoothing.set_integrity(integrity.clone());
        smoothing.job_statistics.smoothing = 0.2;
        smoothing.advance(1.0, idle()).unwrap();
        assert_eq!(smoothing.job_statistics.smoothing, 0.2);
        assert_eq!(integrity.wire_status(), Ok(0x80));

        let integrity = SessionIntegrity::new();
        let mut bounds = LivePhysicsSimulator::from_received(snapshot()).unwrap();
        bounds.set_integrity(integrity.clone());
        bounds.job_statistics.world_bounds[0] = 0.0;
        bounds.advance(1.0, idle()).unwrap();
        assert_eq!(integrity.wire_status(), Ok(0x80));

        let integrity = SessionIntegrity::new();
        let mut scheduler = LivePhysicsSimulator::from_received(snapshot()).unwrap();
        scheduler.set_integrity(integrity.clone());
        scheduler.job_scheduler.configured_interval_enabled = false;
        scheduler.advance(1.0, idle()).unwrap();
        assert!(!scheduler.job_scheduler.configured_interval_enabled);
        assert_eq!(integrity.wire_status(), Ok(0x20_0000));
    }

    #[test]
    fn job_statistics_use_native_average_recurrence_and_first_sample_rule() {
        let mut statistics = PhysicsJobStatistics::default();
        assert_eq!(statistics.report()["interval_samples"], 0);
        for now in [1.0, 1.1, 1.3, 1.6] {
            statistics.completed(now, None);
        }
        let report = statistics.report();
        assert_eq!(report["completed_jobs"], 4);
        assert_eq!(report["interval_samples"], 3);
        assert!((report["last_interval_seconds"].as_f64().unwrap() - 0.3).abs() < 1.0e-12);
        assert!((report["mean_interval_seconds"].as_f64().unwrap() - 0.11475).abs() < 1.0e-12);
        assert!(
            (report["interval_variance_seconds_squared"]
                .as_f64()
                .unwrap()
                - 0.002144565625)
                .abs()
                < 1.0e-12
        );
    }
}
