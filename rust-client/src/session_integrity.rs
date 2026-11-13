//! Per-join observations of integrity checks that actually ran.
//!
//! The wire word contains sticky failures from this session's active Rust
//! producers. Missing or malformed reports block replies instead of being
//! interpreted as a clean status.
use crate::ping_responder::{IntegrityStatusEvidence, TelemetryProviderError};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    sync::{Arc, Mutex},
};

const THREAD_FAILURE: u32 = 0x10;
const CONTAINER_FAILURE: u32 = 0x40;
const PHYSICS_FAILURE: u32 = 0x80;
const PROTECTED_FAILURE: u32 = 0x200;
const SCHEDULER_FAILURE: u32 = 0x20_0000;
const COVERED_PRODUCERS: [&str; 7] = [
    "rsb_container_checksum",
    "script_load_thread_ownership",
    "protected_string_integrity",
    "physics_interval_smoothing",
    "physics_gravity_consistency",
    "physics_scheduler_mode",
    "physics_world_bounds",
];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PhysicsIntegrityCheck {
    Smoothing,
    Gravity,
    Scheduler,
    WorldBounds,
}

impl PhysicsIntegrityCheck {
    fn producer(self) -> &'static str {
        match self {
            Self::Smoothing => "physics_interval_smoothing",
            Self::Gravity => "physics_gravity_consistency",
            Self::Scheduler => "physics_scheduler_mode",
            Self::WorldBounds => "physics_world_bounds",
        }
    }

    fn failure_flag(self) -> u32 {
        match self {
            Self::Scheduler => SCHEDULER_FAILURE,
            _ => PHYSICS_FAILURE,
        }
    }
}

const PHYSICS_CHECKS: [PhysicsIntegrityCheck; 4] = [
    PhysicsIntegrityCheck::Smoothing,
    PhysicsIntegrityCheck::Gravity,
    PhysicsIntegrityCheck::Scheduler,
    PhysicsIntegrityCheck::WorldBounds,
];

#[derive(Clone, Debug, Default)]
pub struct SessionIntegrityObserver(Arc<Mutex<State>>);
pub type SessionIntegrity = SessionIntegrityObserver;

#[derive(Debug, Default)]
struct State {
    flags: u32,
    available: bool,
    missing: u64,
    malformed: u64,
    poisoned: bool,
    checksum_comparisons: u64,
    checksum_failures: u64,
    physics: BTreeMap<&'static str, ComparisonCounters>,
    oneshot: Counters,
    oneshot_invocations: u64,
    oneshot_missing_reports: u64,
    sources: BTreeMap<String, Counters>,
}

#[derive(Clone, Copy, Debug, Default)]
struct ComparisonCounters {
    checks: u64,
    failures: u64,
}

#[derive(Clone, Copy, Debug, Default)]
struct Counters {
    thread_checks: u64,
    thread_failures: u64,
    constructions: u64,
    copies: u64,
    protected_failures: u64,
}

impl SessionIntegrityObserver {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn wire_status(&self) -> Result<u32, &'static str> {
        let state = self
            .0
            .lock()
            .map_err(|_| "integrity observer lock unavailable")?;
        if state.poisoned {
            return Err("integrity observations incomplete");
        }
        Ok(state.flags)
    }

    pub fn wire_evidence(&self) -> Result<IntegrityStatusEvidence, TelemetryProviderError> {
        let state = self.0.lock().map_err(|_| {
            TelemetryProviderError::Unavailable("integrity observer lock unavailable")
        })?;
        if state.poisoned {
            return Err(TelemetryProviderError::Unavailable(
                "integrity observations incomplete",
            ));
        }
        Ok(IntegrityStatusEvidence {
            status_word: state.flags,
            source: "rust_session_integrity".into(),
            covered_producers: COVERED_PRODUCERS.map(str::to_owned).to_vec(),
            missing_producers: vec![],
        })
    }

    /// Record the result only at the real RSB magic+ticket-hash comparison.
    pub fn observe_container_comparison(&self, matched: bool) {
        let Ok(mut s) = self.0.lock() else { return };
        s.checksum_comparisons = s.checksum_comparisons.saturating_add(1);
        if !matched {
            s.checksum_failures = s.checksum_failures.saturating_add(1);
            s.flags |= CONTAINER_FAILURE;
        }
    }

    pub fn observe_physics_comparison(&self, kind: PhysicsIntegrityCheck, matched: bool) {
        let Ok(mut state) = self.0.lock() else { return };
        let counters = state.physics.entry(kind.producer()).or_default();
        counters.checks = counters.checks.saturating_add(1);
        if !matched {
            counters.failures = counters.failures.saturating_add(1);
            state.flags |= kind.failure_flag();
        }
    }

    /// Aggregate one fresh VM's independent counters without comparing them to
    /// another invocation. Protected-store observations are required; standalone
    /// programs legitimately omit the World-owned thread report.
    pub fn observe_oneshot_response(&self, response: &Value) {
        let Some(raw_protected) = response.get("runtime_protected_integrity") else {
            let Ok(mut state) = self.0.lock() else { return };
            state.oneshot_invocations = state.oneshot_invocations.saturating_add(1);
            state.oneshot_missing_reports = state.oneshot_missing_reports.saturating_add(1);
            state.available = false;
            state.poisoned = true;
            return;
        };
        let Some(protected) = parse_protected(Some(raw_protected)) else {
            let Ok(mut state) = self.0.lock() else { return };
            state.malformed = state.malformed.saturating_add(1);
            state.available = false;
            state.poisoned = true;
            return;
        };
        let thread = match response.get("runtime_thread_integrity") {
            Some(raw) => {
                let Some(parsed) = parse_thread(Some(raw)) else {
                    let Ok(mut state) = self.0.lock() else { return };
                    state.malformed = state.malformed.saturating_add(1);
                    state.available = false;
                    state.poisoned = true;
                    return;
                };
                Some(parsed)
            }
            None => None,
        };
        let Ok(mut state) = self.0.lock() else { return };
        state.oneshot_invocations = state.oneshot_invocations.saturating_add(1);
        state.oneshot.constructions = state.oneshot.constructions.saturating_add(protected.0);
        state.oneshot.copies = state.oneshot.copies.saturating_add(protected.1);
        state.oneshot.protected_failures =
            state.oneshot.protected_failures.saturating_add(protected.2);
        if protected.2 > 0 {
            state.flags |= PROTECTED_FAILURE;
        }
        if let Some((checks, failures)) = thread {
            state.oneshot.thread_checks = state.oneshot.thread_checks.saturating_add(checks);
            state.oneshot.thread_failures = state.oneshot.thread_failures.saturating_add(failures);
            if failures > 0 {
                state.flags |= THREAD_FAILURE;
            }
        }
        state.available = !state.poisoned;
    }

    /// Ingest one actual native VM response. `source` must identify the owning
    /// World/Actor for monotonic counter validation across successive updates.
    pub fn observe_vm_response(&self, source: &str, response: &Value) {
        let Ok(mut state) = self.0.lock() else { return };
        let Some(raw_thread) = response.get("runtime_thread_integrity") else {
            state.missing = state.missing.saturating_add(1);
            state.available = false;
            state.poisoned = true;
            return;
        };
        let Some(thread) = parse_thread(Some(raw_thread)) else {
            state.malformed = state.malformed.saturating_add(1);
            state.available = false;
            state.poisoned = true;
            return;
        };
        let Some(raw_protected) = response.get("runtime_protected_integrity") else {
            state.missing = state.missing.saturating_add(1);
            state.available = false;
            state.poisoned = true;
            return;
        };
        let Some(protected) = parse_protected(Some(raw_protected)) else {
            state.malformed = state.malformed.saturating_add(1);
            state.available = false;
            state.poisoned = true;
            return;
        };
        let now = Counters {
            thread_checks: thread.0,
            thread_failures: thread.1,
            constructions: protected.0,
            copies: protected.1,
            protected_failures: protected.2,
        };
        if state
            .sources
            .get(source)
            .is_some_and(|old| !counters_delta_valid(*old, now))
        {
            state.malformed = state.malformed.saturating_add(1);
            state.available = false;
            state.poisoned = true;
            return;
        }
        if now.thread_failures > 0 {
            state.flags |= THREAD_FAILURE;
        }
        if now.protected_failures > 0 {
            state.flags |= PROTECTED_FAILURE;
        }
        state.sources.insert(source.to_owned(), now);
        state.available = !state.poisoned;
    }

    /// Standalone authenticated programs have a protected-store observer but
    /// no World, so they must not manufacture a thread-ownership observation.
    pub fn observe_protected_report(&self, source: &str, report: Option<&Value>) {
        let Ok(mut state) = self.0.lock() else { return };
        let Some(report) = report else {
            state.missing = state.missing.saturating_add(1);
            state.available = false;
            state.poisoned = true;
            return;
        };
        let Some((constructions, copies, failures)) = parse_protected(Some(report)) else {
            state.malformed = state.malformed.saturating_add(1);
            state.available = false;
            state.poisoned = true;
            return;
        };
        let old = state.sources.get(source).copied().unwrap_or_default();
        let now = Counters {
            constructions,
            copies,
            protected_failures: failures,
            ..old
        };
        if !counters_delta_valid(old, now) {
            state.malformed = state.malformed.saturating_add(1);
            state.available = false;
            state.poisoned = true;
            return;
        }
        if failures > 0 {
            state.flags |= PROTECTED_FAILURE;
        }
        state.sources.insert(source.to_owned(), now);
        state.available = !state.poisoned;
    }

    pub fn report(&self) -> Value {
        let Ok(s) = self.0.lock() else {
            return json!({"wire_status_available":false,"full_status_available":false,"reason":"observer-lock-unavailable"});
        };
        let sources = s
            .sources
            .iter()
            .map(|(name, c)| {
                (
                    name.clone(),
                    json!({
                        "thread_checks":c.thread_checks,"thread_failures":c.thread_failures,
                        "protected_constructions":c.constructions,"protected_copies":c.copies,
                        "protected_failures":c.protected_failures
                    }),
                )
            })
            .collect::<serde_json::Map<_, _>>();
        let mut totals = s.oneshot;
        for counters in s.sources.values() {
            totals.thread_checks = totals.thread_checks.saturating_add(counters.thread_checks);
            totals.thread_failures = totals
                .thread_failures
                .saturating_add(counters.thread_failures);
            totals.constructions = totals.constructions.saturating_add(counters.constructions);
            totals.copies = totals.copies.saturating_add(counters.copies);
            totals.protected_failures = totals
                .protected_failures
                .saturating_add(counters.protected_failures);
        }
        let mut physics_checks = serde_json::Map::new();
        let mut physics_failures = serde_json::Map::new();
        for kind in PHYSICS_CHECKS {
            let counters = s.physics.get(kind.producer()).copied().unwrap_or_default();
            physics_checks.insert(kind.producer().into(), json!(counters.checks));
            physics_failures.insert(kind.producer().into(), json!(counters.failures));
        }
        json!({"wire_status_available":!s.poisoned,"full_status_available":!s.poisoned,
            "producer_scope":"active_rust_session_producers",
            "observed_subset_available":s.available,"observed_failure_flags":s.flags,
            "observed_global_flags":s.flags,
            "checksum_comparisons":s.checksum_comparisons,"checksum_failures":s.checksum_failures,
            "script_thread_checks":totals.thread_checks,
            "script_thread_failures":totals.thread_failures,
            "protected_string_constructions":totals.constructions,
            "protected_string_copies":totals.copies,
            "protected_string_failures":totals.protected_failures,
            "oneshot_worker_invocations":s.oneshot_invocations,
            "oneshot_worker_missing_reports":s.oneshot_missing_reports,
            "physics_comparison_checks":physics_checks,
            "physics_comparison_failures":physics_failures,
            "physics_coverage":"local_prediction_scheduler_gravity_interval_and_world_bounds",
            "missing_reports":s.missing,"malformed_or_regressed_reports":s.malformed,
            "covered_producers":COVERED_PRODUCERS,"unavailable_producers":[],
            "sources":sources})
    }
}

fn exact_fields(value: &Value, fields: &[&str]) -> bool {
    value.as_object().is_some_and(|object| {
        object.len() == fields.len() && fields.iter().all(|field| object.contains_key(*field))
    })
}

fn parse_thread(v: Option<&Value>) -> Option<(u64, u64)> {
    let v = v?;
    if !exact_fields(v, &["version", "checks", "failures"]) || v["version"].as_u64() != Some(1) {
        return None;
    }
    let checks = v["checks"].as_u64()?;
    let failures = v["failures"].as_u64()?;
    (failures <= checks).then_some((checks, failures))
}
fn parse_protected(v: Option<&Value>) -> Option<(u64, u64, u64)> {
    let v = v?;
    if !exact_fields(v, &["version", "constructions", "copies", "failures"])
        || v["version"].as_u64() != Some(1)
    {
        return None;
    }
    let constructions = v["constructions"].as_u64()?;
    let copies = v["copies"].as_u64()?;
    let failures = v["failures"].as_u64()?;
    (failures <= constructions.saturating_add(copies)).then_some((constructions, copies, failures))
}

fn counters_delta_valid(old: Counters, now: Counters) -> bool {
    if now.thread_checks < old.thread_checks
        || now.thread_failures < old.thread_failures
        || now.constructions < old.constructions
        || now.copies < old.copies
        || now.protected_failures < old.protected_failures
    {
        return false;
    }
    now.thread_failures - old.thread_failures <= now.thread_checks - old.thread_checks
        && now.protected_failures - old.protected_failures
            <= (now.constructions - old.constructions).saturating_add(now.copies - old.copies)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn vm(tc: u64, tf: u64, pc: u64, pf: u64) -> Value {
        json!({
        "runtime_thread_integrity":{"version":1,"checks":tc,"failures":tf},
        "runtime_protected_integrity":{"version":1,"constructions":pc,"copies":0,"failures":pf}})
    }
    #[test]
    fn fresh_observers_are_isolated_and_expose_their_own_wire_status() {
        let a = SessionIntegrityObserver::new();
        let b = SessionIntegrityObserver::new();
        assert_eq!(b.wire_status(), Ok(0));
        let evidence = b.wire_evidence().unwrap();
        assert_eq!(evidence.status_word, 0);
        assert_eq!(evidence.covered_producers, COVERED_PRODUCERS);
        assert!(evidence.missing_producers.is_empty());

        a.observe_container_comparison(false);
        assert_eq!(a.wire_status(), Ok(CONTAINER_FAILURE));
        assert_eq!(b.wire_status(), Ok(0));
        assert_eq!(a.report()["wire_status_available"], true);
    }
    #[test]
    fn actual_failures_are_sticky_across_clean_updates() {
        let o = SessionIntegrityObserver::new();
        o.observe_vm_response("root", &vm(2, 1, 1, 1));
        o.observe_vm_response("root", &vm(3, 1, 2, 1));
        assert_eq!(o.wire_status(), Ok(THREAD_FAILURE | PROTECTED_FAILURE));
    }
    #[test]
    fn regression_or_missing_report_blocks_the_wire_status() {
        let o = SessionIntegrityObserver::new();
        o.observe_vm_response("root", &vm(4, 0, 3, 0));
        o.observe_vm_response("root", &vm(2, 0, 2, 0));
        assert!(o.wire_status().is_err());
        assert!(o.wire_evidence().is_err());
        o.observe_vm_response("root", &json!({}));
        assert!(o.report()["missing_reports"].as_u64().unwrap() > 0);
    }
    #[test]
    fn physics_failures_use_their_native_bits_and_remain_sticky() {
        for (kind, flag) in [
            (PhysicsIntegrityCheck::Smoothing, PHYSICS_FAILURE),
            (PhysicsIntegrityCheck::Gravity, PHYSICS_FAILURE),
            (PhysicsIntegrityCheck::Scheduler, SCHEDULER_FAILURE),
            (PhysicsIntegrityCheck::WorldBounds, PHYSICS_FAILURE),
        ] {
            let observer = SessionIntegrityObserver::new();
            observer.observe_physics_comparison(kind, false);
            assert_eq!(observer.wire_status(), Ok(flag));
            observer.observe_physics_comparison(kind, true);
            assert_eq!(observer.wire_status(), Ok(flag));
            let report = observer.report();
            assert_eq!(report["physics_comparison_checks"][kind.producer()], 2);
            assert_eq!(report["physics_comparison_failures"][kind.producer()], 1);
        }
    }
    #[test]
    fn oneshot_reports_aggregate_independent_vms_and_fail_stickily() {
        let observer = SessionIntegrityObserver::new();
        observer.observe_oneshot_response(&json!({
            "runtime_protected_integrity":{"version":1,"constructions":1,"copies":1,"failures":1}
        }));
        observer.observe_oneshot_response(&json!({
            "runtime_thread_integrity":{"version":1,"checks":1,"failures":1},
            "runtime_protected_integrity":{"version":1,"constructions":2,"copies":1,"failures":0}
        }));
        let report = observer.report();
        assert_eq!(report["oneshot_worker_invocations"], 2);
        assert_eq!(report["script_thread_checks"], 1);
        assert_eq!(report["script_thread_failures"], 1);
        assert_eq!(report["protected_string_constructions"], 3);
        assert_eq!(report["protected_string_copies"], 2);
        assert_eq!(report["protected_string_failures"], 1);
        assert_eq!(
            observer.wire_status(),
            Ok(THREAD_FAILURE | PROTECTED_FAILURE)
        );

        for invalid in [
            json!({"runtime_protected_integrity":{"version":1,"constructions":1,"copies":true,"failures":0}}),
            json!({"runtime_protected_integrity":{"version":1,"constructions":1,"copies":0,"failures":0,"extra":0}}),
            json!({
                "runtime_thread_integrity":{"version":1,"checks":0,"failures":1},
                "runtime_protected_integrity":{"version":1,"constructions":1,"copies":0,"failures":0}
            }),
        ] {
            observer.observe_oneshot_response(&invalid);
            assert_eq!(observer.report()["oneshot_worker_invocations"], 2);
            assert_eq!(observer.report()["protected_string_constructions"], 3);
        }
        assert!(observer.wire_status().is_err());

        let missing = SessionIntegrityObserver::new();
        missing.observe_oneshot_response(&json!({}));
        assert_eq!(missing.report()["oneshot_worker_invocations"], 1);
        assert_eq!(missing.report()["oneshot_worker_missing_reports"], 1);
        assert!(missing.wire_status().is_err());
    }
    #[test]
    fn retroactive_failures_and_extra_report_fields_are_rejected() {
        let thread = SessionIntegrityObserver::new();
        thread.observe_vm_response("root", &vm(2, 0, 2, 0));
        thread.observe_vm_response("root", &vm(2, 1, 2, 0));
        assert!(thread.wire_status().is_err());

        let protected = SessionIntegrityObserver::new();
        protected.observe_protected_report(
            "root",
            Some(&json!({"version":1,"constructions":2,"copies":2,"failures":0})),
        );
        protected.observe_protected_report(
            "root",
            Some(&json!({"version":1,"constructions":2,"copies":2,"failures":1})),
        );
        assert!(protected.wire_status().is_err());

        let extra = SessionIntegrityObserver::new();
        extra.observe_vm_response(
            "root",
            &json!({
                "runtime_thread_integrity":{"version":1,"checks":1,"failures":0,"extra":0},
                "runtime_protected_integrity":{"version":1,"constructions":1,"copies":0,"failures":0}
            }),
        );
        assert!(extra.wire_status().is_err());
    }
    #[test]
    fn malformed_counts_are_rejected_and_block_the_wire_status() {
        let o = SessionIntegrityObserver::new();
        o.observe_vm_response(
            "root",
            &json!({
            "runtime_thread_integrity":{"version":1,"checks":1,"failures":2},
            "runtime_protected_integrity":{"version":1,"constructions":0,"copies":0,"failures":0}}),
        );
        assert!(o.wire_status().is_err());
        assert_eq!(o.report()["observed_failure_flags"], 0);
        assert!(
            o.report()["malformed_or_regressed_reports"]
                .as_u64()
                .unwrap()
                > 0
        );

        let protected = SessionIntegrityObserver::new();
        protected.observe_protected_report(
            "program",
            Some(&json!({"version":1,"constructions":0,"copies":0,"failures":1})),
        );
        assert!(protected.wire_status().is_err());
    }
}
