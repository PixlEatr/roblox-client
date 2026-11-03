//! Small deterministic horizontal kinematics helpers.
//!
//! This models local intent only. It does not implement Roblox character
//! control, collision, ownership, replication, or server-confirmed movement.

use std::fmt;

const MAX_COMPONENT: f64 = 1.0e9;
const MAX_SPEED: f64 = 1.0e4;
const MAX_FRAME_SECONDS: f64 = 5.0;
const MIN_STEP_SECONDS: f64 = 1.0e-4;
const MAX_STEP_SECONDS: f64 = 0.25;
const MAX_CATCH_UP_STEPS: usize = 256;
const STEP_EPSILON: f64 = 1.0e-12;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Vec3 {
    x: f64,
    y: f64,
    z: f64,
}

impl Vec3 {
    pub fn new(x: f64, y: f64, z: f64) -> Result<Self, MovementError> {
        if ![x, y, z].into_iter().all(valid_component) {
            return Err(MovementError::InvalidVector);
        }
        Ok(Self { x, y, z })
    }

    pub const fn zero() -> Self {
        Self {
            x: 0.0,
            y: 0.0,
            z: 0.0,
        }
    }

    pub const fn components(self) -> [f64; 3] {
        [self.x, self.y, self.z]
    }

    pub fn horizontal_unit(self) -> Self {
        let length = self.x.hypot(self.z);
        if length == 0.0 {
            Self::zero()
        } else {
            Self {
                x: self.x / length,
                y: 0.0,
                z: self.z / length,
            }
        }
    }

    fn add_scaled(self, other: Self, scale: f64) -> Self {
        Self {
            x: self.x + other.x * scale,
            y: self.y + other.y * scale,
            z: self.z + other.z * scale,
        }
    }
}

fn valid_component(value: f64) -> bool {
    value.is_finite() && value.abs() <= MAX_COMPONENT
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MovementError {
    InvalidVector,
    InvalidSpeed,
    InvalidTimeStep,
    InvalidCatchUpLimit,
    InvalidFrameDuration,
}

impl fmt::Display for MovementError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::InvalidVector => "vector components must be finite and bounded",
            Self::InvalidSpeed => "walk speed must be finite and between 0 and 10000",
            Self::InvalidTimeStep => "fixed step must be between 0.0001 and 0.25 seconds",
            Self::InvalidCatchUpLimit => "catch-up step limit must be between 1 and 256",
            Self::InvalidFrameDuration => {
                "frame duration must be finite and between 0 and 5 seconds"
            }
        })
    }
}

impl std::error::Error for MovementError {}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct AdvanceReport {
    pub position: Vec3,
    pub velocity: Vec3,
    pub steps: usize,
    pub simulated_seconds: f64,
    /// Whole fixed steps discarded after exceeding the catch-up limit.
    pub dropped_seconds: f64,
}

/// Integrates horizontal move intent with a fixed time step and bounded catch-up.
#[derive(Clone, Debug)]
pub struct MovementIntegrator {
    position: Vec3,
    direction: Vec3,
    walk_speed: f64,
    step_seconds: f64,
    max_catch_up_steps: usize,
    accumulator: f64,
}

impl MovementIntegrator {
    pub fn new(
        position: Vec3,
        walk_speed: f64,
        step_seconds: f64,
        max_catch_up_steps: usize,
    ) -> Result<Self, MovementError> {
        if !walk_speed.is_finite() || !(0.0..=MAX_SPEED).contains(&walk_speed) {
            return Err(MovementError::InvalidSpeed);
        }
        if !step_seconds.is_finite()
            || !(MIN_STEP_SECONDS..=MAX_STEP_SECONDS).contains(&step_seconds)
        {
            return Err(MovementError::InvalidTimeStep);
        }
        if !(1..=MAX_CATCH_UP_STEPS).contains(&max_catch_up_steps) {
            return Err(MovementError::InvalidCatchUpLimit);
        }
        Ok(Self {
            position,
            direction: Vec3::zero(),
            walk_speed,
            step_seconds,
            max_catch_up_steps,
            accumulator: 0.0,
        })
    }

    pub fn position(&self) -> Vec3 {
        self.position
    }

    pub fn velocity(&self) -> Vec3 {
        self.direction.with_scale(self.walk_speed)
    }

    pub fn set_direction(&mut self, direction: Vec3) {
        self.direction = direction.horizontal_unit();
    }

    pub fn stop(&mut self) {
        self.direction = Vec3::zero();
    }

    pub fn advance(&mut self, frame_seconds: f64) -> Result<AdvanceReport, MovementError> {
        if !frame_seconds.is_finite() || !(0.0..=MAX_FRAME_SECONDS).contains(&frame_seconds) {
            return Err(MovementError::InvalidFrameDuration);
        }

        self.accumulator += frame_seconds;
        let available = ((self.accumulator + STEP_EPSILON) / self.step_seconds).floor() as usize;
        let steps = available.min(self.max_catch_up_steps);
        let simulated_seconds = steps as f64 * self.step_seconds;
        let velocity = self.velocity();
        for _ in 0..steps {
            self.position = self.position.add_scaled(velocity, self.step_seconds);
        }
        self.accumulator = (self.accumulator - simulated_seconds).max(0.0);

        let mut dropped_seconds = 0.0;
        if self.accumulator + STEP_EPSILON >= self.step_seconds {
            let dropped_steps = ((self.accumulator + STEP_EPSILON) / self.step_seconds).floor();
            dropped_seconds = dropped_steps * self.step_seconds;
            self.accumulator = (self.accumulator - dropped_seconds).max(0.0);
        }

        Ok(AdvanceReport {
            position: self.position,
            velocity,
            steps,
            simulated_seconds,
            dropped_seconds,
        })
    }
}

impl Vec3 {
    fn with_scale(self, scale: f64) -> Self {
        Self {
            x: self.x * scale,
            y: self.y * scale,
            z: self.z * scale,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn vec3(x: f64, y: f64, z: f64) -> Vec3 {
        Vec3::new(x, y, z).unwrap()
    }

    fn integrator(speed: f64, step: f64, cap: usize) -> MovementIntegrator {
        MovementIntegrator::new(Vec3::zero(), speed, step, cap).unwrap()
    }

    #[test]
    fn direction_is_horizontal_and_normalized() {
        let mut movement = integrator(16.0, 1.0 / 60.0, 8);
        movement.set_direction(vec3(3.0, 800.0, 4.0));
        let velocity = movement.velocity().components();
        assert!((velocity[0] - 9.6).abs() < 1e-12);
        assert_eq!(velocity[1], 0.0);
        assert!((velocity[2] - 12.8).abs() < 1e-12);
    }

    #[test]
    fn integration_uses_elapsed_fixed_steps() {
        let mut movement = integrator(10.0, 0.01, 16);
        movement.set_direction(vec3(0.0, 0.0, 1.0));
        let report = movement.advance(0.035).unwrap();
        assert_eq!(report.steps, 3);
        assert!((report.position.components()[2] - 0.3).abs() < 1e-12);
        assert!((report.simulated_seconds - 0.03).abs() < 1e-12);
    }

    #[test]
    fn bounded_catch_up_reports_discarded_whole_steps() {
        let mut movement = integrator(6.0, 0.01, 4);
        movement.set_direction(vec3(1.0, 0.0, 0.0));
        let report = movement.advance(0.105).unwrap();
        assert_eq!(report.steps, 4);
        assert!((report.simulated_seconds - 0.04).abs() < 1e-12);
        assert!((report.dropped_seconds - 0.06).abs() < 1e-12);
        assert!((report.position.components()[0] - 0.24).abs() < 1e-12);
    }

    #[test]
    fn substep_time_is_carried_to_the_next_advance() {
        let mut movement = integrator(2.0, 0.02, 8);
        movement.set_direction(vec3(0.0, 0.0, -1.0));
        assert_eq!(movement.advance(0.01).unwrap().steps, 0);
        let report = movement.advance(0.01).unwrap();
        assert_eq!(report.steps, 1);
        assert!((report.position.components()[2] + 0.04).abs() < 1e-12);
    }

    #[test]
    fn frame_partitioning_matches_when_no_catch_up_is_dropped() {
        let mut granular = integrator(7.5, 0.01, 64);
        let mut batched = integrator(7.5, 0.01, 64);
        let direction = vec3(1.0, 900.0, -2.0);
        granular.set_direction(direction);
        batched.set_direction(direction);
        for _ in 0..50 {
            granular.advance(0.01).unwrap();
        }
        batched.advance(0.5).unwrap();
        for (a, b) in granular
            .position()
            .components()
            .into_iter()
            .zip(batched.position().components())
        {
            assert!((a - b).abs() < 1e-10);
        }
    }

    #[test]
    fn stop_removes_velocity_without_resetting_position() {
        let mut movement = integrator(3.0, 0.01, 8);
        movement.set_direction(vec3(1.0, 0.0, 0.0));
        movement.advance(0.02).unwrap();
        let before = movement.position();
        movement.stop();
        assert_eq!(movement.velocity(), Vec3::zero());
        let report = movement.advance(0.02).unwrap();
        assert_eq!(report.position, before);
    }

    #[test]
    fn invalid_vectors_speeds_timestep_and_duration_are_rejected() {
        assert_eq!(
            Vec3::new(f64::NAN, 0.0, 0.0),
            Err(MovementError::InvalidVector)
        );
        assert_eq!(
            Vec3::new(MAX_COMPONENT * 2.0, 0.0, 0.0),
            Err(MovementError::InvalidVector)
        );
        assert!(matches!(
            MovementIntegrator::new(Vec3::zero(), f64::INFINITY, 0.01, 8),
            Err(MovementError::InvalidSpeed)
        ));
        assert!(matches!(
            MovementIntegrator::new(Vec3::zero(), 4.0, 0.0, 8),
            Err(MovementError::InvalidTimeStep)
        ));
        assert!(matches!(
            MovementIntegrator::new(Vec3::zero(), 4.0, 0.01, 0),
            Err(MovementError::InvalidCatchUpLimit)
        ));
        let mut movement = integrator(4.0, 0.01, 8);
        assert_eq!(
            movement.advance(f64::NAN),
            Err(MovementError::InvalidFrameDuration)
        );
        assert_eq!(
            movement.advance(MAX_FRAME_SECONDS + 1.0),
            Err(MovementError::InvalidFrameDuration)
        );
    }

    #[test]
    fn zero_horizontal_direction_stays_still_even_with_vertical_input() {
        let mut movement = integrator(10.0, 0.01, 8);
        movement.set_direction(vec3(0.0, 100.0, 0.0));
        assert_eq!(movement.velocity(), Vec3::zero());
        assert_eq!(movement.advance(0.1).unwrap().position, Vec3::zero());
    }
}
