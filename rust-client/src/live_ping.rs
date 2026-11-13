//! Per-connection ping observations and actual transport writes.
//!
//! Legacy RakNet fields are constructor-inactive for this explicitly negotiated
//! QUIC profile (see outputs/audit/ping-quic-statistics-evidence.md). They are
//! not estimates of queue delay. Integrity availability remains independent.
use crate::{
    ping_responder::{
        DeliveryRoute, IntegrityStatusEvidence, PingResponder, PingTelemetryProvider, PrepareError,
        ReplicationPingStats, TelemetryProviderError, MAX_FLUSH_BATCH, PING_APPLICATION,
        PING_CHANNEL,
    },
    session_integrity::SessionIntegrity,
};
use rove_client_core::{
    channels::{control_open, CONTROL_CHANNEL},
    transport::{
        session::{send_unreliable, ReliableSender},
        Connection, TransportError,
    },
};
use serde_json::{json, Value};
use std::time::Instant;

// Outgoing wire IDs are allocated by this client. 2 and 4 belong to app 6's
// clock channels; 6 is announced for app 4/channel 3 before its first datagram.
const PING_WIRE_ID: u32 = 6;

#[derive(Clone, Copy)]
pub(crate) struct PingClock(Instant);
impl PingClock {
    pub(crate) fn new(origin: Instant) -> Self {
        Self(origin)
    }
    pub(crate) fn word(&self) -> u32 {
        self.0.elapsed().as_millis() as u32
    }
}

pub(crate) struct SessionPingTelemetry {
    clock: PingClock,
    supported_profile: bool,
    extended_family_seen: bool,
    integrity: SessionIntegrity,
}
impl SessionPingTelemetry {
    pub(crate) fn new(clock: PingClock, negotiated: u64, integrity: SessionIntegrity) -> Self {
        Self {
            clock,
            supported_profile: matches!(negotiated, 0 | 0x10_0000),
            extended_family_seen: false,
            integrity,
        }
    }
    pub(crate) fn observe_packet(&mut self, application: u8, payload: &[u8]) {
        if application == 4 && payload.first() == Some(&0xe3) {
            self.extended_family_seen = true;
        }
    }
    fn missing_providers(&self) -> Vec<&'static str> {
        let mut missing = Vec::with_capacity(2);
        if !self.supported_profile || self.extended_family_seen {
            missing.push("replication_statistics");
        }
        if self.integrity.wire_evidence().is_err() {
            missing.push("complete_session_integrity_inventory");
        }
        missing
    }
    pub(crate) fn report(&self) -> Value {
        json!({
            "clock_source":"session_monotonic_milliseconds_modulo_u32",
            "receive_clock_boundary":"complete_application_frame_available_before_queue_wait",
            "enqueue_clock_boundary":"validated_ping_added_to_network_owner_queue",
            "write_clock_boundary":"after_channel_open_and_writer_lock_before_serialization",
            "legacy_statistics_available":self.supported_profile && !self.extended_family_seen,
            "legacy_statistics_provenance":"quic_without_raknet_constructor_inactive_fields",
            "extended_packet_family_seen":self.extended_family_seen,
            "integrity":self.integrity.report(),
            "wire_telemetry_ready":self.missing_providers().is_empty()
        })
    }
}
impl PingTelemetryProvider for SessionPingTelemetry {
    fn enqueue_clock_word(&mut self) -> Result<u32, TelemetryProviderError> {
        Ok(self.clock.word())
    }
    fn write_clock_word(&mut self) -> Result<u32, TelemetryProviderError> {
        Ok(self.clock.word())
    }
    fn replication_stats(&mut self) -> Result<ReplicationPingStats, TelemetryProviderError> {
        if !self.supported_profile || self.extended_family_seen {
            return Err(TelemetryProviderError::Unavailable(
                "unsupported_replication_statistics_profile",
            ));
        }
        Ok(ReplicationPingStats {
            replication_word: 0,
            tail_words: [0, 0],
        })
    }
    fn integrity_status(&mut self) -> Result<IntegrityStatusEvidence, TelemetryProviderError> {
        self.integrity.wire_evidence()
    }
}

#[derive(Default)]
pub(crate) struct PingWriter {
    unreliable_announced: bool,
    unreliable_sequence: u64,
}
impl PingWriter {
    pub(crate) async fn send_unreliable(
        &mut self,
        sender: &ReliableSender,
        connection: &Connection,
        payload: &[u8],
    ) -> Result<(), &'static str> {
        if !self.unreliable_announced {
            let announcement = control_open(PING_APPLICATION, PING_CHANNEL as u8, PING_WIRE_ID)
                .map_err(|_| "ping-channel-invalid")?;
            sender
                .send(PING_APPLICATION, CONTROL_CHANNEL, &announcement)
                .await
                .map_err(|_| "ping-channel-announcement-failed")?;
            self.unreliable_announced = true;
        }
        self.unreliable_sequence = self
            .unreliable_sequence
            .checked_add(1)
            .ok_or("ping-datagram-sequence-overflow")?;
        send_unreliable(connection, PING_WIRE_ID, self.unreliable_sequence, payload)
            .map_err(|_| "unreliable-application-send-failed")
    }

    pub(crate) async fn flush<P: PingTelemetryProvider>(
        &mut self,
        responder: &mut PingResponder,
        telemetry: &mut P,
        sender: &ReliableSender,
        connection: &Connection,
    ) -> Result<(), &'static str> {
        for _ in 0..MAX_FLUSH_BATCH {
            let Some(route) = responder.front_route() else {
                break;
            };
            let mut prepared = None;
            let mut prepare_error = None;
            let mut prepare = || match responder.prepare(telemetry) {
                Ok(Some(value)) => {
                    let bytes = value.payload().to_vec();
                    prepared = Some(value);
                    Some(bytes)
                }
                Ok(None) => None,
                Err(error) => {
                    prepare_error = Some(error);
                    None
                }
            };
            let written = match route {
                DeliveryRoute::Reliable => {
                    sender
                        .send_generated(PING_APPLICATION, PING_CHANNEL, &mut prepare)
                        .await
                }
                DeliveryRoute::Unreliable => {
                    if let Some(bytes) = prepare() {
                        self.send_unreliable(sender, connection, &bytes)
                            .await
                            .map(|_| true)
                            .map_err(|_| TransportError::DatagramFailure)
                    } else {
                        Ok(false)
                    }
                }
            };
            if let Some(prepared) = prepared {
                responder
                    .commit(prepared.token(), matches!(written, Ok(true)))
                    .map_err(|_| "ping-send-accounting-invalid")?;
            }
            written.map_err(|_| "ping-reply-send-failed")?;
            match prepare_error {
                None
                | Some(
                    PrepareError::TelemetryUnavailable(_)
                    | PrepareError::IntegrityEvidenceIncomplete
                    | PrepareError::EncodeFailed,
                ) => (),
                Some(_) => return Err("ping-prepare-state-invalid"),
            }
        }
        Ok(())
    }
}

pub(crate) fn append_report(
    report: &mut Value,
    responder: &PingResponder,
    telemetry: &SessionPingTelemetry,
) {
    let counts = responder.diagnostics();
    let failed =
        counts.unsupported_requests + counts.queue_overflows + counts.telemetry_failed_requests;
    report["application_ping_requests_received"] = json!(counts.requests_received);
    report["application_ping_responses_received"] = json!(counts.responses_received);
    report["application_ping_replies_sent"] = json!(counts.replies_sent);
    report["application_ping_replies_pending"] = json!(responder.pending());
    report["application_ping_requests_failed"] = json!(failed);
    report["application_ping_requests_unsupported"] = json!(counts.unsupported_requests);
    report["application_ping_queue_overflows"] = json!(counts.queue_overflows);
    report["application_ping_telemetry_blocks"] = json!(counts.telemetry_blocks);
    report["application_ping_telemetry_blocks_by_provider"] = json!({
        "statistics":counts.statistics_blocks,"clock":counts.clock_blocks,"integrity":counts.integrity_blocks
    });
    report["application_ping_send_failures"] = json!(counts.send_failures);
    report["application_ping_encode_failures"] = json!(counts.encode_failures);
    report["application_ping_accounting_complete"] = json!(
        counts.requests_received == counts.replies_sent + responder.pending() as u64 + failed
    );
    report["application_ping_reply_transport_implemented"] = json!(true);
    let missing = telemetry.missing_providers();
    report["application_ping_replies_implemented"] = json!(missing.is_empty());
    report["application_ping_missing_providers"] = json!(missing);
    report["application_ping_session_telemetry"] = telemetry.report();
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::session_integrity::PhysicsIntegrityCheck;

    #[test]
    fn profile_is_session_owned_and_unknown_packet_family_invalidates_it() {
        let clock = PingClock::new(Instant::now());
        let mut first = SessionPingTelemetry::new(clock, 0x10_0000, SessionIntegrity::new());
        let mut second = SessionPingTelemetry::new(clock, 0x10_0000, SessionIntegrity::new());
        assert_eq!(first.replication_stats().unwrap().tail_words, [0, 0]);
        first.observe_packet(6, &[0xe3]);
        assert!(first.replication_stats().is_ok());
        first.observe_packet(4, &[0xe3]);
        assert!(first.replication_stats().is_err());
        assert!(second.replication_stats().is_ok());
        assert_eq!(second.integrity_status().unwrap().status_word, 0);
        assert!(SessionPingTelemetry::new(clock, 1, SessionIntegrity::new())
            .replication_stats()
            .is_err());
    }

    fn request() -> Vec<u8> {
        let mut packet = vec![0x83, 5, 0];
        packet.extend_from_slice(&[0; 20]);
        packet.extend_from_slice(&[0, 0]);
        packet
    }

    #[test]
    fn reply_encodes_clean_and_sticky_failure_status() {
        for (matched, expected) in [(true, 0), (false, 0x40)] {
            let integrity = SessionIntegrity::new();
            integrity.observe_container_comparison(matched);
            let mut telemetry =
                SessionPingTelemetry::new(PingClock::new(Instant::now()), 0x10_0000, integrity);
            let mut responder = PingResponder::new(128).unwrap();
            responder
                .receive_packet(&request(), 17, &mut telemetry)
                .unwrap();
            let prepared = responder.prepare(&mut telemetry).unwrap().unwrap();
            assert_eq!(&prepared.payload()[39..43], &expected_u32_bytes(expected));
            responder.commit(prepared.token(), true).unwrap();
            let mut report = json!({});
            append_report(&mut report, &responder, &telemetry);
            assert_eq!(report["application_ping_replies_sent"], 1);
            assert_eq!(report["application_ping_replies_implemented"], true);
            assert_eq!(report["application_ping_missing_providers"], json!([]));
        }
        for (kind, expected) in [
            (PhysicsIntegrityCheck::Gravity, 0x80),
            (PhysicsIntegrityCheck::Scheduler, 0x20_0000),
        ] {
            let integrity = SessionIntegrity::new();
            integrity.observe_physics_comparison(kind, false);
            let mut telemetry =
                SessionPingTelemetry::new(PingClock::new(Instant::now()), 0x10_0000, integrity);
            let mut responder = PingResponder::new(128).unwrap();
            responder
                .receive_packet(&request(), 17, &mut telemetry)
                .unwrap();
            let prepared = responder.prepare(&mut telemetry).unwrap().unwrap();
            assert_eq!(&prepared.payload()[39..43], &expected_u32_bytes(expected));
            responder.commit(prepared.token(), true).unwrap();
        }
    }

    #[test]
    fn invalid_integrity_has_terminal_accounting_and_never_becomes_a_clean_reply() {
        let integrity = SessionIntegrity::new();
        integrity.observe_vm_response("root", &Value::Null);
        let mut telemetry =
            SessionPingTelemetry::new(PingClock::new(Instant::now()), 0x10_0000, integrity);
        let mut responder = PingResponder::new(128).unwrap();
        responder
            .receive_packet(&request(), 17, &mut telemetry)
            .unwrap();
        assert!(matches!(
            responder.prepare(&mut telemetry),
            Err(PrepareError::TelemetryUnavailable(_))
        ));
        let mut report = json!({});
        append_report(&mut report, &responder, &telemetry);
        assert_eq!(report["application_ping_requests_received"], 1);
        assert_eq!(report["application_ping_replies_sent"], 0);
        assert_eq!(report["application_ping_requests_failed"], 1);
        assert_eq!(report["application_ping_accounting_complete"], true);
    }

    fn expected_u32_bytes(value: u32) -> [u8; 4] {
        value.to_le_bytes()
    }
}
