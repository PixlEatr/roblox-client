//! Session-relative monotonic wire timing and ordinary DummyService time sync.
//!
//! Production clocks are anchored to `std::time::Instant`; tests can inject a
//! monotonic nanosecond source. Server callback timing is reported as observed
//! math only. It is never applied as a server offset to the wire/physics clock.

use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    fmt,
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc,
    },
    time::Instant,
};

pub const APPLICATION: u8 = 6;
pub const REQUEST_CHANNEL: u8 = 3;
pub const MAX_REQUESTS: usize = 5;
pub const INTERVAL_NANOS: u64 = 1_000_000_000;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TimingError(pub &'static str);
impl fmt::Display for TimingError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.0)
    }
}
impl std::error::Error for TimingError {}

/// Injectable monotonic nanosecond source. Implementations must never move
/// backwards; SessionWireClock detects and rejects a backwards sample.
pub trait MonotonicSource {
    fn monotonic_nanos(&self) -> u128;
}

#[derive(Debug)]
pub struct InstantSource {
    origin: Instant,
}
impl Default for InstantSource {
    fn default() -> Self {
        Self {
            origin: Instant::now(),
        }
    }
}
impl MonotonicSource for InstantSource {
    fn monotonic_nanos(&self) -> u128 {
        self.origin.elapsed().as_nanos()
    }
}

/// A lightweight deterministic clock source for unit tests and simulations.
/// Do not use it for production session time.
#[derive(Clone, Debug)]
pub struct ManualNanos(Arc<AtomicU64>);
impl ManualNanos {
    pub fn new(nanos: u64) -> Self {
        Self(Arc::new(AtomicU64::new(nanos)))
    }
    pub fn set(&self, nanos: u64) {
        self.0.store(nanos, Ordering::SeqCst);
    }
    pub fn advance(&self, nanos: u64) -> Result<(), TimingError> {
        self.0
            .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |current| {
                current.checked_add(nanos)
            })
            .map(|_| ())
            .map_err(|_| TimingError("monotonic test clock overflow"))
    }
}
impl MonotonicSource for ManualNanos {
    fn monotonic_nanos(&self) -> u128 {
        self.0.load(Ordering::SeqCst) as u128
    }
}

/// Session-local elapsed wire clock with the native fractional-millisecond
/// correction. The correction is measured from this session's replicator
/// initialization and does not estimate or invent a server offset.
pub struct SessionWireClock<C: MonotonicSource = InstantSource> {
    source: C,
    origin_ns: u128,
    replicator_elapsed_ns: Option<u128>,
    replicator_offset_seconds: Option<f64>,
}
impl SessionWireClock<InstantSource> {
    pub fn new() -> Self {
        Self::with_source(InstantSource::default())
    }
}
impl Default for SessionWireClock<InstantSource> {
    fn default() -> Self {
        Self::new()
    }
}
impl<C: MonotonicSource> SessionWireClock<C> {
    pub fn with_source(source: C) -> Self {
        let origin_ns = source.monotonic_nanos();
        Self {
            source,
            origin_ns,
            replicator_elapsed_ns: None,
            replicator_offset_seconds: None,
        }
    }

    fn elapsed_ns_u128(&self) -> Result<u128, TimingError> {
        self.source
            .monotonic_nanos()
            .checked_sub(self.origin_ns)
            .ok_or(TimingError("wire clock sample predates initialization"))
    }

    /// Elapsed nanoseconds since this clock was created, in uint64 range.
    pub fn elapsed_nanos(&self) -> Result<u64, TimingError> {
        u64::try_from(self.elapsed_ns_u128()?)
            .map_err(|_| TimingError("wire clock elapsed time exceeds uint64 nanoseconds"))
    }

    /// Native wire time, truncated to whole microseconds since this session's
    /// initialization (not host uptime or wall-clock time).
    pub fn microseconds(&self) -> Result<u64, TimingError> {
        Ok(self.elapsed_nanos()? / 1_000)
    }

    pub fn replicator_initialized(&self) -> bool {
        self.replicator_offset_seconds.is_some()
    }

    pub fn initialize_replicator(&mut self) -> Result<(), TimingError> {
        if self.replicator_offset_seconds.is_some() {
            return Err(TimingError("replicator wire clock already initialized"));
        }
        let elapsed_ns = self.elapsed_ns_u128()?;
        let seconds = elapsed_ns as f64 / 1_000_000_000.0;
        // Native 104DB2FEC retains only its fractional-millisecond correction.
        self.replicator_offset_seconds = Some((seconds * 1_000.0).trunc() / 1_000.0 - seconds);
        self.replicator_elapsed_ns = Some(elapsed_ns);
        Ok(())
    }

    pub fn physics_milliseconds(&self) -> Result<u64, TimingError> {
        let offset = self
            .replicator_offset_seconds
            .ok_or(TimingError("replicator wire clock is not initialized"))?;
        let elapsed_ns = self.elapsed_ns_u128()?;
        if elapsed_ns < self.replicator_elapsed_ns.unwrap_or_default() {
            return Err(TimingError(
                "physics clock sample predates replicator initialization",
            ));
        }
        let seconds = elapsed_ns as f64 / 1_000_000_000.0;
        let result = ((seconds + offset) * 1_000.0).trunc();
        if !result.is_finite() || result < 0.0 || result > u64::MAX as f64 {
            return Err(TimingError("physics clock value is outside uint64"));
        }
        Ok(result as u64)
    }

    pub fn report(&self) -> Result<Value, TimingError> {
        Ok(json!({
            "scope": "this_session",
            "origin": "measured_monotonic_sample_at_wire_clock_initialization",
            "elapsed_microseconds": self.microseconds()?,
            "replicator_initialized": self.replicator_initialized(),
            "replicator_quantization_offset_seconds": self.replicator_offset_seconds,
            "shared_by": ["dummy_time_sync", "outgoing_physics"],
            "server_clock_offset_applied": false,
            "workspace_adjustment_applied": false,
            "native_frame_clock_cache_modeled": false,
        }))
    }
}

/// Exact 25-byte DummyService type-3 request (timestamp plus two zero words).
pub fn request_packet(monotonic_us: u64) -> [u8; 25] {
    let mut packet = [0; 25];
    packet[0] = 3;
    packet[1..9].copy_from_slice(&monotonic_us.to_le_bytes());
    packet
}

/// Read the echoed timestamp only; the remaining response words stay opaque.
pub fn response_echo(payload: &[u8]) -> Result<u64, TimingError> {
    if !matches!(payload.len(), 25 | 33) || payload[0] != 3 {
        return Err(TimingError("unrecognized DummyService time-sync response"));
    }
    Ok(u64::from_le_bytes(
        payload[1..9].try_into().expect("fixed echo width"),
    ))
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct NativeTimingSample {
    pub local_receive_us: u64,
    pub half_interval_ms: i64,
    pub callback_offset_ms: i64,
    pub callback_extended_offset_ms: Option<i64>,
}

/// Reproduce the observed native callback arithmetic using signed wrapping
/// 64-bit subtraction and truncation toward zero. This sample is descriptive;
/// it is not a clock correction.
pub fn response_timing(
    payload: &[u8],
    local_receive_us: u64,
    extended_timing: bool,
) -> Result<NativeTimingSample, TimingError> {
    if !matches!(payload.len(), 25 | 33) || payload[0] != 3 {
        return Err(TimingError("unrecognized DummyService time-sync response"));
    }
    let read_word = |offset: usize| -> u64 {
        u64::from_le_bytes(
            payload[offset..offset + 8]
                .try_into()
                .expect("validated response width"),
        )
    };
    let echo = read_word(1);
    let word9 = read_word(9);
    let word17 = read_word(17);
    let wrapped = local_receive_us.wrapping_sub(echo).wrapping_sub(word9) as i64;
    let half = wrapped / 2_000;
    let from_send = word17.wrapping_sub(local_receive_us) as i64;
    let offset = half.wrapping_add(from_send / 1_000);
    let extra = if extended_timing && payload.len() == 33 {
        let word25 = read_word(25);
        Some(half.wrapping_add(word25.wrapping_sub(local_receive_us) as i64 / 1_000))
    } else {
        None
    };
    Ok(NativeTimingSample {
        local_receive_us,
        half_interval_ms: half,
        callback_offset_ms: offset,
        callback_extended_offset_ms: extra,
    })
}

/// Five-request, one-second-interval echo/RTT probe, timed by the same session
/// clock used for outgoing wire timestamps.
#[derive(Debug, Default)]
pub struct TimeSyncProbe {
    next_request_at_ns: Option<u64>,
    pending: BTreeMap<u64, u64>,
    request_count: usize,
    rtt_ms: Vec<f64>,
    native_timing_samples: Vec<NativeTimingSample>,
}
impl TimeSyncProbe {
    pub fn start<C: MonotonicSource>(
        &mut self,
        clock: &SessionWireClock<C>,
    ) -> Result<(), TimingError> {
        if self.next_request_at_ns.is_some() {
            return Err(TimingError("time-sync probe already started"));
        }
        let now = clock.elapsed_nanos()?;
        self.next_request_at_ns = Some(now);
        Ok(())
    }

    /// Returns a packet only when due. The next interval starts at this actual
    /// emission sample, so delayed ticks never create bursts.
    pub fn due<C: MonotonicSource>(
        &mut self,
        clock: &SessionWireClock<C>,
    ) -> Result<Option<[u8; 25]>, TimingError> {
        let now = clock.elapsed_nanos()?;
        let Some(next_at) = self.next_request_at_ns else {
            return Ok(None);
        };
        if now < next_at || self.request_count >= MAX_REQUESTS {
            return Ok(None);
        }
        let monotonic_us = clock.microseconds()?;
        if self.pending.contains_key(&monotonic_us) {
            return Err(TimingError("duplicate request timestamp"));
        }
        let next = now
            .checked_add(INTERVAL_NANOS)
            .ok_or(TimingError("time-sync schedule exceeds uint64 nanoseconds"))?;
        let packet = request_packet(monotonic_us);
        self.pending.insert(monotonic_us, now);
        self.request_count += 1;
        self.next_request_at_ns = Some(next);
        Ok(Some(packet))
    }

    /// Malformed, unsolicited, early, or arithmetic-invalid replies return
    /// `false` and leave the pending request intact.
    pub fn receive<C: MonotonicSource>(
        &mut self,
        payload: &[u8],
        clock: &SessionWireClock<C>,
        extended_timing: bool,
    ) -> Result<bool, TimingError> {
        let Ok(echo) = response_echo(payload) else {
            return Ok(false);
        };
        let Some(&sent_at_ns) = self.pending.get(&echo) else {
            return Ok(false);
        };
        let received_at_ns = clock.elapsed_nanos()?;
        if received_at_ns < sent_at_ns {
            return Ok(false);
        }
        let local_receive_us = clock.microseconds()?;
        let Ok(sample) = response_timing(payload, local_receive_us, extended_timing) else {
            return Ok(false);
        };
        self.pending.remove(&echo);
        self.rtt_ms
            .push((received_at_ns - sent_at_ns) as f64 / 1_000_000.0);
        self.native_timing_samples.push(sample);
        Ok(true)
    }

    pub fn request_count(&self) -> usize {
        self.request_count
    }

    pub fn pending_count(&self) -> usize {
        self.pending.len()
    }

    pub fn latest_rtt_ms(&self) -> Option<f64> {
        self.rtt_ms.last().copied()
    }

    pub fn runtime_statistics(&self) -> Value {
        match self.latest_rtt_ms() {
            Some(value) => json!({"network_rtt_ms": value}),
            None => json!({}),
        }
    }

    pub fn public_report(&self) -> Value {
        let round_three = |value: f64| (value * 1_000.0).round() / 1_000.0;
        let mut result = json!({
            "time_sync_requests_sent": self.request_count,
            "time_sync_echoed_responses": self.rtt_ms.len(),
            "time_sync_measured_rtt_ms": self.rtt_ms.iter().copied().map(round_three).collect::<Vec<_>>(),
        });
        if !self.native_timing_samples.is_empty() {
            result["time_sync_native_callback_samples"] = json!(self
                .native_timing_samples
                .iter()
                .map(|sample| json!({
                    "half_interval_ms": sample.half_interval_ms,
                    "callback_offset_ms": sample.callback_offset_ms,
                    "callback_extended_offset_ms": sample.callback_extended_offset_ms,
                }))
                .collect::<Vec<_>>());
            result["time_sync_physics_clock_adjustment_applied"] = json!(false);
        }
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn manual_clock(origin_ns: u64) -> (SessionWireClock<ManualNanos>, ManualNanos) {
        let source = ManualNanos::new(origin_ns);
        (SessionWireClock::with_source(source.clone()), source)
    }

    fn response(words: [u64; 4]) -> Vec<u8> {
        let mut payload = vec![3];
        for word in words {
            payload.extend_from_slice(&word.to_le_bytes());
        }
        payload
    }

    #[test]
    fn request_vector_and_echo_lengths_match_python() {
        assert_eq!(
            request_packet(0x0102_0304_0506_0708),
            [3, 8, 7, 6, 5, 4, 3, 2, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,]
        );
        let payload = response([1234, 0, 0, 0]);
        assert_eq!(response_echo(&payload), Ok(1234));
        assert!(response_echo(&payload[..24]).is_err());
        assert!(response_echo(&[2; 25]).is_err());
    }

    #[test]
    fn callback_timing_matches_native_checked_vectors() {
        let vectors = [
            (
                [1_000_000, 80, 1_700_000_000_000_000, 80_000_000],
                1_020_080,
                1_699_999_998_989,
                78_989,
            ),
            ([1_000_000, 80, 900_000, 800_000], 1_020_080, -110, -210),
            ([1_000_000, 3, 1_021_001, 1_020_999], 1_020_004, 10, 10),
            (
                [u64::MAX - 1999, 10, 5_000_000, 4_000_000],
                1000,
                5000,
                4000,
            ),
        ];
        for (words, local, offset, extended) in vectors {
            let payload = response(words);
            let sample = response_timing(&payload, local, true).unwrap();
            assert_eq!(sample.callback_offset_ms, offset);
            assert_eq!(sample.callback_extended_offset_ms, Some(extended));
        }
        let payload = response([1_000_000, 80, 900_000, 800_000]);
        assert_eq!(
            response_timing(&payload, 1_020_080, false)
                .unwrap()
                .callback_extended_offset_ms,
            None
        );
        assert_eq!(
            response_timing(&payload[..25], 1_020_080, true)
                .unwrap()
                .callback_extended_offset_ms,
            None
        );
        assert!(response_timing(&[3; 24], 0, true).is_err());
    }

    #[test]
    fn session_clock_is_relative_independent_and_quantized() {
        let (mut first, time1) = manual_clock(38_000_000_000_000);
        let (second, time2) = manual_clock(51_000_000_000_000);
        assert_eq!(first.microseconds(), Ok(0));
        assert_eq!(second.microseconds(), Ok(0));
        time1.advance(1_250_000_000).unwrap();
        time2.advance(11_000).unwrap();
        assert_eq!(first.microseconds(), Ok(1_250_000));
        assert_eq!(second.microseconds(), Ok(11));
        first.initialize_replicator().unwrap();
        time1.advance(1_111_000_000).unwrap();
        assert_eq!(first.physics_milliseconds(), Ok(2_361));
        assert!(first.initialize_replicator().is_err());
        assert!(second.physics_milliseconds().is_err());

        let (mut fractional, source) = manual_clock(10_000_000_000);
        source.advance(123_750_000).unwrap();
        fractional.initialize_replicator().unwrap();
        let report = fractional.report().unwrap();
        assert!(
            (report["replicator_quantization_offset_seconds"]
                .as_f64()
                .unwrap()
                + 0.00075)
                .abs()
                < 1e-12
        );
        source.advance(1_111_000_000).unwrap();
        assert_eq!(fractional.physics_milliseconds(), Ok(1_234));
    }

    #[test]
    fn probe_uses_same_session_origin_and_schedules_without_bursts() {
        let (clock, source) = manual_clock(38_000_000_000_000);
        let mut probe = TimeSyncProbe::default();
        assert_eq!(probe.runtime_statistics(), json!({}));
        probe.start(&clock).unwrap();
        assert!(probe.due(&clock).unwrap().is_some());
        assert!(probe.due(&clock).unwrap().is_none());
        source.advance(INTERVAL_NANOS - 1).unwrap();
        assert!(probe.due(&clock).unwrap().is_none());
        source.advance(2 * INTERVAL_NANOS + 1).unwrap();
        let request = probe.due(&clock).unwrap().unwrap();
        assert_eq!(
            u64::from_le_bytes(request[1..9].try_into().unwrap()),
            3_000_000
        );
        source.advance(20_000_000).unwrap();
        let reply = response([3_000_000, 0, 0, 3_020_000]);
        assert!(probe.receive(&reply, &clock, false).unwrap());
        assert_eq!(probe.latest_rtt_ms(), Some(20.0));
        assert_eq!(probe.pending_count(), 1);
        assert!(!probe.receive(&reply, &clock, false).unwrap());
        assert_eq!(probe.public_report()["time_sync_echoed_responses"], 1);
        assert_eq!(probe.public_report()["time_sync_measured_rtt_ms"][0], 20.0);
    }

    #[test]
    fn probe_preserves_pending_on_invalid_response_and_caps_requests() {
        let (clock, source) = manual_clock(100);
        let mut probe = TimeSyncProbe::default();
        probe.start(&clock).unwrap();
        let first = probe.due(&clock).unwrap().unwrap();
        assert!(!probe.receive(&first[..24], &clock, false).unwrap());
        assert_eq!(probe.pending_count(), 1);
        for _ in 1..MAX_REQUESTS {
            source.advance(INTERVAL_NANOS).unwrap();
            assert!(probe.due(&clock).unwrap().is_some());
        }
        source.advance(INTERVAL_NANOS).unwrap();
        assert!(probe.due(&clock).unwrap().is_none());
        assert_eq!(probe.request_count(), MAX_REQUESTS);
        assert_eq!(probe.pending_count(), MAX_REQUESTS);
    }

    #[test]
    fn backward_test_clock_sample_is_rejected() {
        let (clock, source) = manual_clock(100);
        source.set(99);
        assert!(clock.elapsed_nanos().is_err());
        assert!(clock.microseconds().is_err());
    }
}
