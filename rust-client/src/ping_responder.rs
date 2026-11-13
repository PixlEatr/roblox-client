//! Bounded, telemetry-backed replication ping codec and responder.
//!
//! This module performs no network I/O by itself. The live-session owner gives
//! it session-specific observations and a transport writer; absent observations
//! block a reply instead of producing fabricated status or clock words.

use rove_client_core::wire::Reader;
use std::collections::VecDeque;

pub const MAX_PING_PACKET_BYTES: usize = 4096;
pub const MAX_PING_ITEMS: usize = 64;
pub const MAX_PENDING_PINGS: usize = 128;
pub const MAX_FLUSH_BATCH: usize = 64;
pub const PING_APPLICATION: u8 = 4;
pub const PING_CHANNEL: u32 = 3;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PingKind {
    Request,
    Response,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PingFlags {
    Basic,
    ReplyWords,
    ExtraWord,
    ReplyWordsAndExtraWord,
}

impl PingFlags {
    fn from_bits(bits: u8) -> Result<Self, PingCodecError> {
        match bits {
            0 => Ok(Self::Basic),
            1 => Ok(Self::ReplyWords),
            4 => Ok(Self::ExtraWord),
            5 => Ok(Self::ReplyWordsAndExtraWord),
            _ => Err(PingCodecError::InvalidFlags(bits)),
        }
    }
    fn has_reply_words(self) -> bool {
        matches!(self, Self::ReplyWords | Self::ReplyWordsAndExtraWord)
    }
    fn has_extra_word(self) -> bool {
        matches!(self, Self::ExtraWord | Self::ReplyWordsAndExtraWord)
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PingItem {
    kind: PingKind,
    flags: PingFlags,
    timestamp: u64,
    request_word: u32,
    reply_words: Option<[u32; 6]>,
    trailer_words: [u32; 2],
    unreliable: bool,
    extra_word: Option<u32>,
}

impl PingItem {
    fn read(reader: &mut Reader<'_>, kind: u8) -> Result<Self, PingCodecError> {
        let kind = match kind {
            5 => PingKind::Request,
            6 => PingKind::Response,
            other => return Err(PingCodecError::InvalidKind(other)),
        };
        let bits = reader.u8().map_err(|_| PingCodecError::Truncated)?;
        let flags = PingFlags::from_bits(bits)?;
        let timestamp = reader.u64().map_err(|_| PingCodecError::Truncated)?;
        let request_word = reader.u32().map_err(|_| PingCodecError::Truncated)?;
        let reply_words = if flags.has_reply_words() {
            let mut words = [0u32; 6];
            for word in &mut words {
                *word = reader.u32().map_err(|_| PingCodecError::Truncated)?;
            }
            Some(words)
        } else {
            None
        };
        let trailer_words = [
            reader.u32().map_err(|_| PingCodecError::Truncated)?,
            reader.u32().map_err(|_| PingCodecError::Truncated)?,
        ];
        let route = reader.u8().map_err(|_| PingCodecError::Truncated)?;
        if route > 1 {
            return Err(PingCodecError::InvalidRoute(route));
        }
        let extra_word = if flags.has_extra_word() {
            Some(reader.u32().map_err(|_| PingCodecError::Truncated)?)
        } else {
            None
        };
        Ok(Self {
            kind,
            flags,
            timestamp,
            request_word,
            reply_words,
            trailer_words,
            unreliable: route != 0,
            extra_word,
        })
    }

    /// Convert the already-committed `SessionRecord.pings` representation.
    /// This validates all fields and their cross-field shape before use.
    pub fn from_record(value: &serde_json::Value) -> Result<Self, PingCodecError> {
        let kind = match value["kind"].as_u64() {
            Some(5) => PingKind::Request,
            Some(6) => PingKind::Response,
            Some(other) => return Err(PingCodecError::InvalidKind(other as u8)),
            None => return Err(PingCodecError::InvalidRecord),
        };
        let raw_flags = value["flags"]
            .as_u64()
            .ok_or(PingCodecError::InvalidRecord)?;
        let flags = PingFlags::from_bits(
            u8::try_from(raw_flags).map_err(|_| PingCodecError::InvalidRecord)?,
        )?;
        let timestamp = value["timestamp"]
            .as_u64()
            .ok_or(PingCodecError::InvalidRecord)?;
        let request_word = u32::try_from(
            value["first_word"]
                .as_u64()
                .ok_or(PingCodecError::InvalidRecord)?,
        )
        .map_err(|_| PingCodecError::InvalidRecord)?;
        let reply_words = if flags.has_reply_words() {
            let values = value["reply_words"]
                .as_array()
                .ok_or(PingCodecError::InvalidRecord)?;
            if values.len() != 6 {
                return Err(PingCodecError::InvalidRecord);
            }
            let mut out = [0u32; 6];
            for (dst, value) in out.iter_mut().zip(values) {
                *dst = u32::try_from(value.as_u64().ok_or(PingCodecError::InvalidRecord)?)
                    .map_err(|_| PingCodecError::InvalidRecord)?;
            }
            Some(out)
        } else {
            if !value["reply_words"]
                .as_array()
                .ok_or(PingCodecError::InvalidRecord)?
                .is_empty()
            {
                return Err(PingCodecError::InvalidRecord);
            }
            None
        };
        let trailer = value["trailer_words"]
            .as_array()
            .ok_or(PingCodecError::InvalidRecord)?;
        if trailer.len() != 2 {
            return Err(PingCodecError::InvalidRecord);
        }
        let trailer_words = [
            u32::try_from(trailer[0].as_u64().ok_or(PingCodecError::InvalidRecord)?)
                .map_err(|_| PingCodecError::InvalidRecord)?,
            u32::try_from(trailer[1].as_u64().ok_or(PingCodecError::InvalidRecord)?)
                .map_err(|_| PingCodecError::InvalidRecord)?,
        ];
        let unreliable = value["unreliable"]
            .as_bool()
            .ok_or(PingCodecError::InvalidRecord)?;
        let extra_word = if flags.has_extra_word() {
            Some(
                u32::try_from(
                    value["extra_word"]
                        .as_u64()
                        .ok_or(PingCodecError::InvalidRecord)?,
                )
                .map_err(|_| PingCodecError::InvalidRecord)?,
            )
        } else {
            if !value["extra_word"].is_null() {
                return Err(PingCodecError::InvalidRecord);
            }
            None
        };
        Ok(Self {
            kind,
            flags,
            timestamp,
            request_word,
            reply_words,
            trailer_words,
            unreliable,
            extra_word,
        })
    }

    fn is_reply(&self) -> bool {
        self.kind == PingKind::Response || self.flags.has_reply_words()
    }
    fn supported_request(&self) -> bool {
        self.kind == PingKind::Request && self.flags == PingFlags::Basic
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum PingCodecError {
    PacketTooLarge,
    InvalidHeader,
    InvalidKind(u8),
    InvalidFlags(u8),
    InvalidRoute(u8),
    TooManyItems,
    MissingTerminator,
    TrailingBytes,
    Truncated,
    InvalidRecord,
    NotARequest,
}

pub fn decode_ping_packet(payload: &[u8]) -> Result<Vec<PingItem>, PingCodecError> {
    if payload.len() > MAX_PING_PACKET_BYTES {
        return Err(PingCodecError::PacketTooLarge);
    }
    let mut reader = Reader::new(payload);
    if reader.u8().map_err(|_| PingCodecError::Truncated)? != 0x83 {
        return Err(PingCodecError::InvalidHeader);
    }
    let mut items = Vec::with_capacity(4);
    let mut terminated = false;
    while reader.remaining() > 0 {
        let kind = reader.u8().map_err(|_| PingCodecError::Truncated)?;
        if kind == 0 {
            terminated = true;
            if reader.remaining() != 0 {
                return Err(PingCodecError::TrailingBytes);
            }
            break;
        }
        if items.len() >= MAX_PING_ITEMS {
            return Err(PingCodecError::TooManyItems);
        }
        items.push(PingItem::read(&mut reader, kind)?);
    }
    if !terminated {
        return Err(PingCodecError::MissingTerminator);
    }
    Ok(items)
}

pub fn encode_ping_reply(
    item: &PingItem,
    telemetry: &PingReplyTelemetry,
) -> Result<Vec<u8>, PingCodecError> {
    if !item.supported_request() {
        return Err(PingCodecError::NotARequest);
    }
    let mut bytes = Vec::with_capacity(49);
    bytes.extend_from_slice(&[0x83, 6, 1]);
    bytes.extend_from_slice(&item.timestamp.to_le_bytes());
    bytes.extend_from_slice(&item.request_word.to_le_bytes());
    for word in telemetry.reply_words {
        bytes.extend_from_slice(&word.to_le_bytes());
    }
    bytes.extend_from_slice(&telemetry.integrity_status.to_le_bytes());
    let response_word = if item.timestamp & 0x20 != 0 {
        u32::MAX
    } else {
        0
    };
    bytes.extend_from_slice(&response_word.to_le_bytes());
    bytes.push(u8::from(item.unreliable));
    bytes.push(0); // Item terminator.
    Ok(bytes)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ReplicationPingStats {
    pub replication_word: u32,
    pub tail_words: [u32; 2],
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PingReplyTelemetry {
    pub reply_words: [u32; 6],
    pub integrity_status: u32,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct IntegrityStatusEvidence {
    pub status_word: u32,
    pub source: String,
    pub covered_producers: Vec<String>,
    pub missing_producers: Vec<String>,
}
impl IntegrityStatusEvidence {
    fn complete(&self) -> bool {
        !self.source.trim().is_empty()
            && !self.covered_producers.is_empty()
            && self.missing_producers.is_empty()
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TelemetryProviderError {
    Missing(&'static str),
    Unavailable(&'static str),
}

/// Providers must return observations from this live session. These methods
/// intentionally have no defaults and may report that an observation is absent.
pub trait PingTelemetryProvider {
    fn enqueue_clock_word(&mut self) -> Result<u32, TelemetryProviderError>;
    fn write_clock_word(&mut self) -> Result<u32, TelemetryProviderError>;
    fn replication_stats(&mut self) -> Result<ReplicationPingStats, TelemetryProviderError>;
    fn integrity_status(&mut self) -> Result<IntegrityStatusEvidence, TelemetryProviderError>;
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DeliveryRoute {
    Reliable,
    Unreliable,
}

pub trait PingTransport {
    fn send(
        &mut self,
        application: u8,
        channel: u32,
        route: DeliveryRoute,
        payload: &[u8],
    ) -> Result<(), ()>;
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct PingDiagnostics {
    pub requests_received: u64,
    pub responses_received: u64,
    pub replies_sent: u64,
    pub unsupported_requests: u64,
    pub queue_overflows: u64,
    pub telemetry_blocks: u64,
    pub telemetry_failed_requests: u64,
    pub statistics_blocks: u64,
    pub clock_blocks: u64,
    pub integrity_blocks: u64,
    pub send_failures: u64,
    pub encode_failures: u64,
}

struct PendingPing {
    id: u128,
    item: PingItem,
    received_clock_word: u32,
    enqueue_clock_word: u32,
    stats: ReplicationPingStats,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ReceiveError {
    Codec(PingCodecError),
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct ReceiveReport {
    pub enqueued: usize,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct FlushReport {
    pub sent: usize,
    pub telemetry_blocked: usize,
    pub send_failed: bool,
    pub prepare_blocked: bool,
}

#[derive(Debug, PartialEq, Eq)]
pub enum PrepareError {
    AlreadyInFlight,
    TelemetryUnavailable(&'static str),
    IntegrityEvidenceIncomplete,
    EncodeFailed,
    TokenExhausted,
}

#[derive(Debug, PartialEq, Eq)]
pub enum CommitError {
    NoInFlight,
    StaleOrWrongToken,
    PendingOrderChanged,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CommitOutcome {
    Written,
    RetryPending,
}

pub struct PreparedPing {
    token: u128,
    payload: Vec<u8>,
    route: DeliveryRoute,
}
impl PreparedPing {
    pub fn token(&self) -> u128 {
        self.token
    }
    pub fn payload(&self) -> &[u8] {
        &self.payload
    }
    pub fn route(&self) -> DeliveryRoute {
        self.route
    }
}

struct InFlight {
    token: u128,
    pending_id: u128,
}

pub struct PingResponder {
    pending: VecDeque<PendingPing>,
    max_pending: usize,
    diagnostics: PingDiagnostics,
    next_token: u128,
    next_pending_id: u128,
    in_flight: Option<InFlight>,
}

impl PingResponder {
    pub fn new(max_pending: usize) -> Result<Self, &'static str> {
        if !(1..=MAX_PENDING_PINGS).contains(&max_pending) {
            return Err("max_pending must be 1..=128");
        }
        Ok(Self {
            pending: VecDeque::with_capacity(max_pending.min(8)),
            max_pending,
            diagnostics: PingDiagnostics::default(),
            next_token: 1,
            next_pending_id: 1,
            in_flight: None,
        })
    }
    pub fn pending(&self) -> usize {
        self.pending.len()
    }
    pub fn diagnostics(&self) -> PingDiagnostics {
        self.diagnostics
    }
    pub fn front_route(&self) -> Option<DeliveryRoute> {
        self.pending.front().map(|pending| {
            if pending.item.unreliable {
                DeliveryRoute::Unreliable
            } else {
                DeliveryRoute::Reliable
            }
        })
    }

    /// Decode and validate the complete packet before mutating the queue.
    pub fn receive_packet<P: PingTelemetryProvider>(
        &mut self,
        payload: &[u8],
        received_clock_word: u32,
        providers: &mut P,
    ) -> Result<ReceiveReport, ReceiveError> {
        let items = decode_ping_packet(payload).map_err(ReceiveError::Codec)?;
        Ok(self.receive_validated_items(&items, received_clock_word, providers))
    }

    /// Use only after the enclosing replication frame has already been fully
    /// validated and committed by SessionCore.
    pub fn receive_records<P: PingTelemetryProvider>(
        &mut self,
        records: &[serde_json::Value],
        received_clock_word: u32,
        providers: &mut P,
    ) -> Result<ReceiveReport, ReceiveError> {
        if records.len() > MAX_PING_ITEMS {
            return Err(ReceiveError::Codec(PingCodecError::TooManyItems));
        }
        let items = records
            .iter()
            .map(PingItem::from_record)
            .collect::<Result<Vec<_>, _>>()
            .map_err(ReceiveError::Codec)?;
        Ok(self.receive_validated_items(&items, received_clock_word, providers))
    }

    pub(crate) fn receive_validated_items<P: PingTelemetryProvider>(
        &mut self,
        items: &[PingItem],
        received_clock_word: u32,
        providers: &mut P,
    ) -> ReceiveReport {
        let mut report = ReceiveReport::default();
        for item in items {
            if item.is_reply() {
                self.diagnostics.responses_received += 1;
                continue;
            }
            self.diagnostics.requests_received += 1;
            if !item.supported_request() {
                self.diagnostics.unsupported_requests += 1;
                continue;
            }
            if self.pending.len() >= self.max_pending {
                self.diagnostics.queue_overflows += 1;
                continue;
            }
            let stats = match providers.replication_stats() {
                Ok(stats) => stats,
                Err(_) => {
                    self.diagnostics.telemetry_blocks += 1;
                    self.diagnostics.telemetry_failed_requests += 1;
                    self.diagnostics.statistics_blocks += 1;
                    continue;
                }
            };
            let enqueue_clock_word = match providers.enqueue_clock_word() {
                Ok(word) => word,
                Err(_) => {
                    self.diagnostics.telemetry_blocks += 1;
                    self.diagnostics.telemetry_failed_requests += 1;
                    self.diagnostics.clock_blocks += 1;
                    continue;
                }
            };
            let Some(next_id) = self.next_pending_id.checked_add(1) else {
                self.diagnostics.telemetry_failed_requests += 1;
                continue;
            };
            let id = self.next_pending_id;
            self.next_pending_id = next_id;
            self.pending.push_back(PendingPing {
                id,
                item: item.clone(),
                received_clock_word,
                enqueue_clock_word,
                stats,
            });
            report.enqueued += 1;
        }
        report
    }

    /// Prepare one reply for the transport owner. Call this only immediately
    /// before the owner's actual serialization/write boundary; no clock is
    /// sampled while queueing or opening a transport stream.
    pub fn prepare<P: PingTelemetryProvider>(
        &mut self,
        providers: &mut P,
    ) -> Result<Option<PreparedPing>, PrepareError> {
        if self.in_flight.is_some() {
            return Err(PrepareError::AlreadyInFlight);
        }
        let Some(pending) = self.pending.front() else {
            return Ok(None);
        };
        let pending_id = pending.id;
        let item = pending.item.clone();
        let received_clock_word = pending.received_clock_word;
        let enqueue_clock_word = pending.enqueue_clock_word;
        let stats = pending.stats;
        let evidence = match providers.integrity_status() {
            Ok(evidence) => evidence,
            Err(_) => {
                self.diagnostics.telemetry_blocks += 1;
                self.diagnostics.telemetry_failed_requests += 1;
                self.diagnostics.integrity_blocks += 1;
                self.pending.pop_front();
                return Err(PrepareError::TelemetryUnavailable("integrity_status"));
            }
        };
        if !evidence.complete() {
            self.diagnostics.telemetry_blocks += 1;
            self.diagnostics.telemetry_failed_requests += 1;
            self.diagnostics.integrity_blocks += 1;
            self.pending.pop_front();
            return Err(PrepareError::IntegrityEvidenceIncomplete);
        }
        let write_clock_word = match providers.write_clock_word() {
            Ok(word) => word,
            Err(_) => {
                self.diagnostics.telemetry_blocks += 1;
                self.diagnostics.telemetry_failed_requests += 1;
                self.diagnostics.clock_blocks += 1;
                self.pending.pop_front();
                return Err(PrepareError::TelemetryUnavailable("write_clock"));
            }
        };
        let telemetry = PingReplyTelemetry {
            reply_words: [
                received_clock_word,
                enqueue_clock_word,
                write_clock_word,
                stats.replication_word,
                stats.tail_words[0],
                stats.tail_words[1],
            ],
            integrity_status: evidence.status_word,
        };
        let payload = match encode_ping_reply(&item, &telemetry) {
            Ok(payload) => payload,
            Err(_) => {
                self.diagnostics.encode_failures += 1;
                self.diagnostics.telemetry_failed_requests += 1;
                self.pending.pop_front();
                return Err(PrepareError::EncodeFailed);
            }
        };
        let token = self.next_token;
        let Some(next_token) = token.checked_add(1) else {
            return Err(PrepareError::TokenExhausted);
        };
        self.next_token = next_token;
        let route = if item.unreliable {
            DeliveryRoute::Unreliable
        } else {
            DeliveryRoute::Reliable
        };
        self.in_flight = Some(InFlight { token, pending_id });
        Ok(Some(PreparedPing {
            token,
            payload,
            route,
        }))
    }

    /// Complete an in-flight write. Failed writes retain the front request for
    /// a retry; only successful network writes advance the sent counter.
    pub fn commit(
        &mut self,
        token: u128,
        write_succeeded: bool,
    ) -> Result<CommitOutcome, CommitError> {
        let Some(in_flight) = self.in_flight.as_ref() else {
            return Err(CommitError::NoInFlight);
        };
        if in_flight.token != token {
            return Err(CommitError::StaleOrWrongToken);
        }
        if self.pending.front().map(|pending| pending.id) != Some(in_flight.pending_id) {
            return Err(CommitError::PendingOrderChanged);
        }
        self.in_flight = None;
        if write_succeeded {
            self.pending.pop_front();
            self.diagnostics.replies_sent += 1;
            Ok(CommitOutcome::Written)
        } else {
            self.diagnostics.send_failures += 1;
            Ok(CommitOutcome::RetryPending)
        }
    }

    pub fn flush<P: PingTelemetryProvider, T: PingTransport>(
        &mut self,
        providers: &mut P,
        transport: &mut T,
        limit: usize,
    ) -> FlushReport {
        let budget = limit.min(MAX_FLUSH_BATCH);
        let mut report = FlushReport::default();
        let mut processed = 0;
        while processed < budget {
            if self.pending.is_empty() {
                break;
            }
            processed += 1;
            let prepared = match self.prepare(providers) {
                Ok(Some(prepared)) => prepared,
                Ok(None) => break,
                Err(
                    PrepareError::TelemetryUnavailable(_)
                    | PrepareError::IntegrityEvidenceIncomplete
                    | PrepareError::EncodeFailed,
                ) => {
                    report.telemetry_blocked += 1;
                    continue;
                }
                Err(_) => {
                    report.prepare_blocked = true;
                    break;
                }
            };
            let success = transport
                .send(
                    PING_APPLICATION,
                    PING_CHANNEL,
                    prepared.route(),
                    prepared.payload(),
                )
                .is_ok();
            match self.commit(prepared.token(), success) {
                Ok(CommitOutcome::Written) => report.sent += 1,
                Ok(CommitOutcome::RetryPending) => {
                    report.send_failed = true;
                    break;
                }
                Err(_) => {
                    report.prepare_blocked = true;
                    break;
                }
            }
        }
        report
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::VecDeque;

    fn request(timestamp: u64, unreliable: bool, flags: u8) -> Vec<u8> {
        let mut bytes = vec![0x83, 5, flags];
        bytes.extend_from_slice(&timestamp.to_le_bytes());
        bytes.extend_from_slice(&0x1234_5678u32.to_le_bytes());
        if flags & 1 != 0 {
            for word in 0..6u32 {
                bytes.extend_from_slice(&word.to_le_bytes());
            }
        }
        bytes.extend_from_slice(&0x1111_1111u32.to_le_bytes());
        bytes.extend_from_slice(&0x2222_2222u32.to_le_bytes());
        bytes.push(u8::from(unreliable));
        if flags & 4 != 0 {
            bytes.extend_from_slice(&9u32.to_le_bytes());
        }
        bytes.push(0);
        bytes
    }

    #[derive(Default)]
    struct Providers {
        enqueue: VecDeque<Result<u32, TelemetryProviderError>>,
        write: VecDeque<Result<u32, TelemetryProviderError>>,
        stats: VecDeque<Result<ReplicationPingStats, TelemetryProviderError>>,
        integrity: VecDeque<Result<IntegrityStatusEvidence, TelemetryProviderError>>,
    }
    impl PingTelemetryProvider for Providers {
        fn enqueue_clock_word(&mut self) -> Result<u32, TelemetryProviderError> {
            self.enqueue
                .pop_front()
                .unwrap_or(Err(TelemetryProviderError::Missing("enqueue_clock")))
        }
        fn write_clock_word(&mut self) -> Result<u32, TelemetryProviderError> {
            self.write
                .pop_front()
                .unwrap_or(Err(TelemetryProviderError::Missing("write_clock")))
        }
        fn replication_stats(&mut self) -> Result<ReplicationPingStats, TelemetryProviderError> {
            self.stats
                .pop_front()
                .unwrap_or(Err(TelemetryProviderError::Missing("replication_stats")))
        }
        fn integrity_status(&mut self) -> Result<IntegrityStatusEvidence, TelemetryProviderError> {
            self.integrity
                .pop_front()
                .unwrap_or(Err(TelemetryProviderError::Missing("integrity_status")))
        }
    }
    #[derive(Default)]
    struct Transport {
        writes: Vec<(u8, u32, DeliveryRoute, Vec<u8>)>,
        fail: bool,
    }
    impl PingTransport for Transport {
        fn send(
            &mut self,
            app: u8,
            channel: u32,
            route: DeliveryRoute,
            payload: &[u8],
        ) -> Result<(), ()> {
            if self.fail {
                return Err(());
            }
            self.writes.push((app, channel, route, payload.to_vec()));
            Ok(())
        }
    }
    fn available() -> Providers {
        Providers {
            enqueue: [22].into_iter().map(Ok).collect(),
            write: [33].into_iter().map(Ok).collect(),
            stats: [ReplicationPingStats {
                replication_word: 44,
                tail_words: [55, 66],
            }]
            .into_iter()
            .map(Ok)
            .collect(),
            integrity: [IntegrityStatusEvidence {
                status_word: 77,
                source: "synthetic fixture".into(),
                covered_producers: vec!["fixture".into()],
                missing_producers: vec![],
            }]
            .into_iter()
            .map(Ok)
            .collect(),
        }
    }

    #[test]
    fn reply_is_byte_exact_and_echoes_opaque_request_fields() {
        let packet = request(0x0102_0304_0506_0708, true, 0);
        let mut responder = PingResponder::new(4).unwrap();
        let mut providers = available();
        assert_eq!(
            responder
                .receive_packet(&packet, 11, &mut providers)
                .unwrap()
                .enqueued,
            1
        );
        let mut transport = Transport::default();
        let report = responder.flush(&mut providers, &mut transport, 64);
        assert_eq!(report.sent, 1);
        assert_eq!(transport.writes[0], (4,3,DeliveryRoute::Unreliable, bytes_from_hex("8306010807060504030201785634120b00000016000000210000002c00000037000000420000004d000000000000000100")));
        let decoded = decode_ping_packet(&transport.writes[0].3).unwrap();
        assert_eq!(decoded[0].timestamp, 0x0102_0304_0506_0708);
        assert_eq!(decoded[0].request_word, 0x1234_5678);
    }

    #[test]
    fn response_timestamps_conditionally_complement_the_reply_word() {
        let item = decode_ping_packet(&request(0x20, false, 0))
            .unwrap()
            .remove(0);
        let payload = encode_ping_reply(
            &item,
            &PingReplyTelemetry {
                reply_words: [1, 2, 3, 4, 5, 6],
                integrity_status: 7,
            },
        )
        .unwrap();
        assert_eq!(&payload[43..47], &u32::MAX.to_le_bytes());
    }

    #[test]
    fn full_packet_validation_precedes_any_enqueue() {
        let mut malformed = request(1, true, 0);
        malformed.truncate(malformed.len() - 2);
        let mut responder = PingResponder::new(4).unwrap();
        assert!(responder
            .receive_packet(&malformed, 1, &mut Providers::default())
            .is_err());
        assert_eq!(responder.pending(), 0);
        assert_eq!(responder.diagnostics().requests_received, 0);
    }

    #[test]
    fn reply_loops_unsupported_flags_and_overflow_are_counted() {
        let response = request(1, true, 0)
            .into_iter()
            .enumerate()
            .map(|(i, b)| if i == 1 { 6 } else { b })
            .collect::<Vec<_>>();
        let mut combined = request(2, true, 0);
        combined.pop();
        combined.extend_from_slice(&request(3, true, 4)[1..]);
        combined.pop();
        combined.extend_from_slice(&request(4, false, 0)[1..]);
        let mut responder = PingResponder::new(1).unwrap();
        let mut provider = available();
        let mut records = decode_ping_packet(&response).unwrap();
        records.extend(decode_ping_packet(&combined).unwrap());
        assert_eq!(
            responder
                .receive_validated_items(&records, 9, &mut provider)
                .enqueued,
            1
        );
        let d = responder.diagnostics();
        assert_eq!(
            (
                d.responses_received,
                d.unsupported_requests,
                d.queue_overflows
            ),
            (1, 1, 1)
        );
    }

    #[test]
    fn missing_observations_block_and_send_failures_do_not_increment_sent() {
        let packet = request(1, false, 0);
        let mut responder = PingResponder::new(2).unwrap();
        let mut missing = Providers::default();
        assert_eq!(
            responder
                .receive_packet(&packet, 1, &mut missing)
                .unwrap()
                .enqueued,
            0
        );
        assert_eq!(responder.diagnostics().statistics_blocks, 1);
        let mut provider = available();
        assert_eq!(
            responder
                .receive_packet(&packet, 1, &mut provider)
                .unwrap()
                .enqueued,
            1
        );
        let mut transport = Transport {
            fail: true,
            ..Transport::default()
        };
        let failed = responder.flush(&mut provider, &mut transport, 64);
        assert!(failed.send_failed);
        assert_eq!(responder.pending(), 1);
        assert_eq!(responder.diagnostics().replies_sent, 0);
        transport.fail = false;
        provider
            .integrity
            .push_back(available().integrity.pop_front().unwrap());
        provider.write.push_back(Ok(34));
        assert_eq!(responder.flush(&mut provider, &mut transport, 1).sent, 1);
        assert_eq!(responder.diagnostics().replies_sent, 1);
        assert_eq!(transport.writes[0].2, DeliveryRoute::Reliable);
    }

    #[test]
    fn integrity_evidence_preserves_provider_coverage() {
        let incomplete = IntegrityStatusEvidence {
            status_word: 0,
            source: "fixture source".into(),
            covered_producers: vec!["producer-a".into()],
            missing_producers: vec!["producer-b".into()],
        };
        assert!(!incomplete.complete());
        let complete = IntegrityStatusEvidence {
            missing_producers: vec![],
            ..incomplete
        };
        assert!(complete.complete());
    }

    #[test]
    fn malformed_record_fields_reject_the_whole_batch_before_counting() {
        let valid = serde_json::json!({"kind":5,"flags":0,"timestamp":1,"first_word":2,
            "reply_words":[],"trailer_words":[0,0],"unreliable":false});
        let mut invalid = valid.clone();
        invalid["reply_words"] = serde_json::json!("not an empty word array");
        let mut responder = PingResponder::new(4).unwrap();
        assert!(responder
            .receive_records(&[valid, invalid], 17, &mut available())
            .is_err());
        assert_eq!(responder.pending(), 0);
        assert_eq!(responder.diagnostics().requests_received, 0);
    }

    #[test]
    fn prepare_commit_allows_one_token_and_rejects_stale_or_duplicate_completion() {
        let mut responder = PingResponder::new(2).unwrap();
        let mut providers = available();
        responder
            .receive_packet(&request(1, true, 0), 11, &mut providers)
            .unwrap();
        assert_eq!(responder.front_route(), Some(DeliveryRoute::Unreliable));
        let first = responder.prepare(&mut providers).unwrap().unwrap();
        assert_eq!(first.route(), DeliveryRoute::Unreliable);
        assert!(matches!(
            responder.prepare(&mut providers),
            Err(PrepareError::AlreadyInFlight)
        ));
        assert_eq!(
            responder.commit(first.token() + 1, true),
            Err(CommitError::StaleOrWrongToken)
        );
        assert_eq!(responder.diagnostics().replies_sent, 0);
        assert_eq!(
            responder.commit(first.token(), false),
            Ok(CommitOutcome::RetryPending)
        );
        assert_eq!(responder.pending(), 1);

        let mut retry_providers = available();
        retry_providers.enqueue.clear();
        retry_providers.stats.clear();
        retry_providers.write = [34].into_iter().map(Ok).collect();
        let second = responder.prepare(&mut retry_providers).unwrap().unwrap();
        assert_ne!(first.token(), second.token());
        assert_eq!(
            responder.commit(first.token(), true),
            Err(CommitError::StaleOrWrongToken)
        );
        assert_eq!(
            responder.commit(second.token(), true),
            Ok(CommitOutcome::Written)
        );
        assert_eq!(responder.pending(), 0);
        assert_eq!(responder.diagnostics().replies_sent, 1);
        assert_eq!(
            responder.commit(second.token(), true),
            Err(CommitError::NoInFlight)
        );
    }

    #[test]
    fn incomplete_final_telemetry_is_a_terminal_per_request_failure() {
        let mut responder = PingResponder::new(2).unwrap();
        let mut providers = available();
        responder
            .receive_packet(&request(1, false, 0), 11, &mut providers)
            .unwrap();
        providers.integrity = [Ok(IntegrityStatusEvidence {
            status_word: 0,
            source: "incomplete fixture".into(),
            covered_producers: vec!["known producer".into()],
            missing_producers: vec!["missing producer".into()],
        })]
        .into_iter()
        .collect();
        assert!(matches!(
            responder.prepare(&mut providers),
            Err(PrepareError::IntegrityEvidenceIncomplete)
        ));
        assert_eq!(responder.pending(), 0);
        assert_eq!(responder.diagnostics().telemetry_failed_requests, 1);
        assert_eq!(responder.diagnostics().replies_sent, 0);
    }

    #[test]
    fn committed_session_record_ping_shape_is_validated_as_a_batch() {
        let record = serde_json::json!({
            "kind":5, "flags":0, "timestamp":0x0102u64, "first_word":9,
            "reply_words":[], "trailer_words":[10,11], "unreliable":false
        });
        let mut responder = PingResponder::new(2).unwrap();
        let mut providers = available();
        assert_eq!(
            responder
                .receive_records(&[record], 8, &mut providers)
                .unwrap()
                .enqueued,
            1
        );
        let malformed = serde_json::json!({"kind":5, "flags":1, "timestamp":1, "first_word":2,
            "reply_words":[1], "trailer_words":[3,4], "unreliable":false});
        assert!(responder
            .receive_records(&[malformed], 8, &mut providers)
            .is_err());
        assert_eq!(responder.pending(), 1);
    }

    #[test]
    fn decoder_boundaries_and_record_shape_are_strict() {
        assert!(PingResponder::new(0).is_err());
        assert!(decode_ping_packet(&vec![0; MAX_PING_PACKET_BYTES + 1]).is_err());
        let mut many = vec![0x83];
        for n in 0..MAX_PING_ITEMS + 1 {
            many.extend_from_slice(
                &request(n as u64, false, 0)[1..request(n as u64, false, 0).len() - 1],
            );
        }
        many.push(0);
        assert_eq!(decode_ping_packet(&many), Err(PingCodecError::TooManyItems));
        let value = serde_json::json!({"kind":5,"flags":1,"timestamp":1,"first_word":2,"reply_words":[1],"trailer_words":[3,4],"unreliable":false});
        assert_eq!(
            PingItem::from_record(&value),
            Err(PingCodecError::InvalidRecord)
        );
    }

    fn bytes_from_hex(s: &str) -> Vec<u8> {
        s.as_bytes()
            .chunks_exact(2)
            .map(|pair| {
                let hex = std::str::from_utf8(pair).unwrap();
                u8::from_str_radix(hex, 16).unwrap()
            })
            .collect()
    }
}
