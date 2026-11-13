//! Rust-owned live transport, authenticated-program dispatch and received world.
//!
//! Replicated game scripts are not connected. Optional own-character physics
//! runs on the world owner and hands its newest frame to the network owner.
//! Ping delivery and the available per-session observations are connected;
//! incomplete integrity coverage blocks replies. Reports distinguish this receiver
//! from a complete playable client.
use crate::{
    allocation::IssuedJoinAllocation,
    live_physics::{LocalPrediction, PredictionFrame},
    live_ping::{self, PingClock, PingWriter, SessionPingTelemetry},
    live_runtime::{LiveRuntime, RuntimeConfig},
    physics_sender,
    ping_responder::{decode_ping_packet, PingItem, PingResponder},
    program_check::{ProgramAuth, PublicTrust},
    session_integrity::SessionIntegrity,
};
use rove_client_core::{
    channels::{control_open, parse_control_open, CONTROL_CHANNEL, MAX_FRAME},
    journal::ReaderId,
    memory::{current_process_rss_bytes, peak_process_rss_bytes},
    property_acknowledgments::{PreparedPropertyAcks, PropertyAcknowledgments},
    replication_out::{
        contact_batch, streaming_ready, MarkerQueue, MarkerReadiness, StreamingEvidence,
    },
    rpc::{
        encode_reflected_signal, ClientReply, ReflectedSignal, RemoteResult, RpcOutboundKind,
        RpcSession, RpcTransport, ServerCall, ServerEvent,
    },
    session::{IngestOutcome, SessionCore, SessionRecord},
    timing::{SessionWireClock, TimeSyncProbe},
    transport::session::{receive_unreliable, send_unreliable, ReliableReceiver, ReliableSender},
    transport::{
        close_metadata, connect_webtransport, current_close_metadata, Connection, TransportError,
    },
    wire::Reader,
};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet, HashMap},
    path::Path,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    time::{Duration, Instant},
};
use tokio::{
    sync::{mpsc, OwnedSemaphorePermit, Semaphore},
    task::JoinSet,
};

const QUEUE_BYTES: usize = 8 * 1024 * 1024;
const MAX_STREAMS: usize = 16;
const MAX_CHANNELS: usize = 64;
const BYTECODE_RANGE_CAPABILITY: u64 = 0x10_0000;

#[derive(Default)]
struct PhysicsOutput {
    latest: Option<(PredictionFrame, Instant)>,
    produced: u64,
    coalesced: u64,
}

fn live_transport_allocation(
    issued: &IssuedJoinAllocation,
) -> Result<rove_client_core::transport::IssuedAllocation, String> {
    let mut allocation = crate::connection_probe::transport_allocation(issued)?;
    // Capabilities are a local offer, not an issued credential. The allocation
    // probe defaults to zero; this owner actually sends/handles the 13..14
    // bytecode range and must offer that implemented capability explicitly.
    allocation.capabilities = BYTECODE_RANGE_CAPABILITY;
    allocation.validate().map_err(|error| error.to_string())?;
    Ok(allocation)
}

struct Incoming {
    application: u8,
    channel: u32,
    payload: Vec<u8>,
    received_clock_word: u32,
    _budget: OwnedSemaphorePermit,
}
#[derive(Debug, Eq, PartialEq)]
struct EncodedRpcPacket {
    channel: u8,
    kind: RpcOutboundKind,
    payload: Vec<u8>,
}

enum Effect {
    Pings {
        items: Vec<PingItem>,
        received_clock_word: u32,
        _budget: OwnedSemaphorePermit,
    },
    Reply {
        payload: Vec<u8>,
        program: bool,
        _budget: OwnedSemaphorePermit,
    },
    PropertyAck {
        prepared: PreparedPropertyAcks,
        _budget: OwnedSemaphorePermit,
    },
    StreamingReady {
        payload: Vec<u8>,
        _budget: OwnedSemaphorePermit,
    },
    RpcOutbound {
        payloads: Vec<EncodedRpcPacket>,
        _budget: OwnedSemaphorePermit,
    },
    Failed(&'static str),
}

#[derive(Debug)]
struct SimulationStepClock {
    last_ms: Option<u32>,
    fractional_units: f64,
    step: u32,
    completed_intervals: u64,
}
impl SimulationStepClock {
    fn new() -> Self {
        Self {
            last_ms: None,
            fractional_units: 0.0,
            step: 0,
            completed_intervals: 0,
        }
    }
    fn advance(&mut self, now_ms: u32) -> Result<(), &'static str> {
        let Some(last) = self.last_ms else {
            self.last_ms = Some(now_ms);
            return Ok(());
        };
        let delta = now_ms
            .checked_sub(last)
            .ok_or("simulation-clock-regressed")?;
        let total = self.fractional_units + f64::from(delta) * 0.240;
        let whole = total.floor();
        self.fractional_units = total - whole;
        self.step = self.step.wrapping_add(whole as u32);
        self.completed_intervals = self.completed_intervals.saturating_add(1);
        self.last_ms = Some(now_ms);
        Ok(())
    }
    fn report(&self) -> Value {
        json!({"source":"completed_local_simulation_intervals","step":self.step,
            "units_per_second":240,"completed_intervals":self.completed_intervals,
            "native_scheduler_parity_verified":false})
    }
}

#[derive(Default)]
struct PingCounts {
    requests: u64,
    responses: u64,
    unsupported: u64,
}
impl PingCounts {
    fn observe(&mut self, kind: u8, flags: u8) {
        if kind == 6 || flags & 1 != 0 {
            self.responses += 1;
        } else {
            self.requests += 1;
            if flags != 0 {
                self.unsupported += 1;
            }
        }
    }
    fn from_packet(payload: &[u8]) -> Result<Self, &'static str> {
        let parse = || -> Result<Self, rove_client_core::wire::WireError> {
            let mut reader = Reader::new(payload);
            if reader.u8()? != 131 {
                return Err(rove_client_core::wire::WireError::new("not ping data"));
            }
            let mut counts = Self::default();
            for _ in 0..=64 {
                let kind = reader.u8()?;
                if kind == 0 {
                    reader.finish()?;
                    return Ok(counts);
                }
                if !matches!(kind, 5 | 6) || counts.requests + counts.responses >= 64 {
                    return Err(rove_client_core::wire::WireError::new(
                        "invalid ping kind or count",
                    ));
                }
                let flags = reader.u8()?;
                if flags & !5 != 0 {
                    return Err(rove_client_core::wire::WireError::new("invalid ping flags"));
                }
                reader.u64()?;
                reader.u32()?;
                if flags & 1 != 0 {
                    for _ in 0..6 {
                        reader.u32()?;
                    }
                }
                reader.u32()?;
                reader.u32()?;
                if reader.u8()? > 1 {
                    return Err(rove_client_core::wire::WireError::new(
                        "invalid ping channel",
                    ));
                }
                if flags & 4 != 0 {
                    reader.u32()?;
                }
                counts.observe(kind, flags);
            }
            Err(rove_client_core::wire::WireError::new("unterminated pings"))
        };
        parse().map_err(|_| "ping-packet-rejected")
    }
}

/// The sole owner of this session's received world. An optional VM consumer
/// receives one initial globals snapshot followed by bounded ordered deltas.
struct ReceivedWorld {
    core: SessionCore,
    reader: ReaderId,
    acknowledged: u64,
    initial_tag: bool,
    markers: MarkerQueue,
    pings: PingCounts,
    validated_pings: Vec<PingItem>,
    player_ids: BTreeSet<String>,
    user_id: u64,
    events: u64,
    regions: u64,
    region_records: Vec<Value>,
    property_changes: u64,
    metadata: u64,
    property_acks: PropertyAcknowledgments,
    simulation_clock: SimulationStepClock,
    ready_property_ack: Option<PreparedPropertyAcks>,
    streaming_ready_sent: bool,
    ready_streaming_resume: Option<Vec<u8>>,
    rpc: Option<RpcSession>,
    announced_players: Option<rove_client_core::rpc::AnnouncedPlayers>,
    rpc_class_parents: HashMap<String, String>,
    ready_rpc_outbound: Vec<EncodedRpcPacket>,
    remote_server_results_received: u64,
    remote_server_errors_received: u64,
    remote_receive_error: Option<String>,
    prediction: LocalPrediction,
    prediction_started: Instant,
    physics_enabled: bool,
    physics_error: Option<&'static str>,
    engine: Option<LiveRuntime>,
    engine_error: Option<String>,
}
impl ReceivedWorld {
    fn new(user_id: u64) -> Result<Self, &'static str> {
        let core =
            SessionCore::with_limits(8, 32 * 1024 * 1024).map_err(|_| "world-init-failed")?;
        Self::with_core(core, user_id)
    }
    fn with_property_acks(
        user_id: u64,
        property_acks: PropertyAcknowledgments,
    ) -> Result<Self, &'static str> {
        let mut world = Self::new(user_id)?;
        world.property_acks = property_acks;
        Ok(world)
    }
    fn with_core(mut core: SessionCore, user_id: u64) -> Result<Self, &'static str> {
        let reader = core.retain(0).map_err(|_| "world-reader-init-failed")?;
        Ok(Self {
            core,
            reader,
            acknowledged: 0,
            initial_tag: false,
            markers: MarkerQueue::new(),
            pings: PingCounts::default(),
            validated_pings: Vec::new(),
            player_ids: BTreeSet::new(),
            user_id,
            events: 0,
            regions: 0,
            region_records: Vec::new(),
            property_changes: 0,
            metadata: 0,
            property_acks: PropertyAcknowledgments::new(),
            simulation_clock: SimulationStepClock::new(),
            ready_property_ack: None,
            streaming_ready_sent: false,
            ready_streaming_resume: None,
            rpc: None,
            announced_players: None,
            rpc_class_parents: HashMap::new(),
            ready_rpc_outbound: Vec::new(),
            remote_server_results_received: 0,
            remote_server_errors_received: 0,
            remote_receive_error: None,
            prediction: LocalPrediction::default(),
            prediction_started: Instant::now(),
            physics_enabled: false,
            physics_error: None,
            engine: None,
            engine_error: None,
        })
    }
    fn ingest(&mut self, payload: &[u8]) -> Result<Vec<Vec<u8>>, &'static str> {
        let now = self.simulation_clock.last_ms.unwrap_or(0);
        self.ingest_at(payload, now)
    }
    fn ingest_at(
        &mut self,
        payload: &[u8],
        received_clock_word: u32,
    ) -> Result<Vec<Vec<u8>>, &'static str> {
        self.ready_property_ack = None;
        self.ready_streaming_resume = None;
        self.ready_rpc_outbound.clear();
        let previous = self.engine.as_ref().and_then(|_| self.core.world_view());
        let accepted = self
            .core
            .ingest(payload)
            .map_err(|_| "world-packet-rejected")?;
        let sequence = match accepted {
            IngestOutcome::SchemaInstalled { .. } => return Ok(vec![]),
            IngestOutcome::GlobalsCommitted {
                packet_sequence, ..
            } => {
                let view = self.core.world_view().ok_or("world-view-unavailable")?;
                let scope = self.core.scope().ok_or("remote-session-scope-missing")?;
                self.announced_players =
                    rove_client_core::rpc::AnnouncedPlayers::from_globals(scope, &view)
                        .map_err(|_| "global-players-service-invalid")?;
                packet_sequence
            }
            IngestOutcome::ReplicationCommitted {
                packet_sequence, ..
            } => packet_sequence,
        };
        let entries = self
            .core
            .read_after(self.reader, self.acknowledged, 1)
            .map_err(|_| "world-receipt-unavailable")?;
        let entry = entries
            .first()
            .filter(|entry| entry.sequence == sequence)
            .ok_or("world-receipt-order-invalid")?;
        self.observe(&entry.value)?;
        self.simulation_clock.advance(received_clock_word)?;
        let schema = self.core.schema().ok_or("world-schema-unavailable")?;
        let view = self.core.world_view().ok_or("world-view-unavailable")?;
        self.property_acks
            .observe(
                schema,
                &view,
                &entry.value.property_changes,
                self.simulation_clock.step,
            )
            .map_err(|_| "property-acknowledgement-rejected")?;
        self.ready_property_ack = self
            .property_acks
            .prepare(&view)
            .map_err(|_| "property-acknowledgement-prepare-failed")?;
        self.ready_streaming_resume = self.prepare_streaming_resume()?;
        if self.engine.is_some() {
            let local_player = self.local_player()?;
            if let Err(error) = self.engine.as_mut().unwrap().observe(
                &self.core,
                previous,
                &entry.value,
                self.initial_tag,
                local_player,
            ) {
                self.engine_error = Some(error);
                return Err("engine-update-rejected");
            }
        }
        self.process_remote(&entry.value)?;
        // Acknowledgement releases the canonical receipt only after the
        // optional VM mailbox owns its normalized ordered update.
        self.core
            .acknowledge(self.reader, sequence)
            .map_err(|_| "world-receipt-ack-failed")?;
        self.acknowledged = sequence;
        // Session consumers and VM delivery have completed successfully.
        drop(entries);
        Ok(self.flush_markers())
    }
    fn process_remote(&mut self, record: &SessionRecord) -> Result<(), &'static str> {
        if self.rpc.is_none() {
            let Some(player) = self.local_player()? else {
                return Ok(());
            };
            let scope = self.core.scope().ok_or("remote-session-scope-missing")?;
            self.core.set_remote_success_variants_preserved(true);
            let mut rpc =
                RpcSession::new(scope, self.user_id, parse_instance_guid(&player)?, 0x100000)
                    .map_err(|_| "remote-session-init-failed")?;
            if let Some(proof) = self.announced_players.clone() {
                rpc.retain_announced_players(proof)
                    .map_err(|_| "global-players-service-invalid")?;
            }
            self.rpc = Some(rpc);
        }
        let active = match self.engine.as_mut() {
            Some(engine) => engine
                .active_remote_server_requests()
                .map_err(|_| "remote-state-unavailable")?,
            None => std::collections::HashSet::new(),
        };
        let scope = self.core.scope().ok_or("remote-session-scope-missing")?;
        let schema = self.core.schema().ok_or("world-schema-unavailable")?;
        let view = self.core.world_view().ok_or("world-view-unavailable")?;
        let rpc = self.rpc.as_mut().ok_or("remote-session-missing")?;
        let results = rpc
            .observe_record(scope, schema, &view, record, &active)
            .map_err(|error| {
                self.remote_receive_error = Some(error.to_string());
                "remote-request-rejected"
            })?;
        self.remote_server_results_received += results.len() as u64;
        self.remote_server_errors_received +=
            results.iter().filter(|result| !result.ok).count() as u64;
        if let Some(engine) = self.engine.as_mut() {
            engine
                .queue_remote_server_results(
                    results.into_iter().map(remote_result_to_value).collect(),
                )
                .map_err(|_| "remote-result-queue-rejected")?;
        }
        self.flush_rpc_outbound()
    }

    fn advance_prediction(&mut self) -> Result<Option<PredictionFrame>, &'static str> {
        let intents = match self.engine.as_mut() {
            Some(engine) => engine
                .take_movement_intents()
                .map_err(|_| "movement-state-unavailable")?,
            None => Vec::new(),
        };
        let Some(player) = self.local_player()? else {
            return Ok(None);
        };
        let scope = self.core.scope().ok_or("remote-session-scope-missing")?;
        if self.rpc_class_parents.is_empty() {
            let parents = crate::runtime_sources::load_class_parents(
                Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap(),
            )
            .map_err(|_| "physics-class-metadata-unavailable")?;
            self.rpc_class_parents =
                serde_json::from_value(parents).map_err(|_| "remote-class-metadata-invalid")?;
        }
        let view = self.core.world_view().ok_or("world-view-unavailable")?;
        // Character/ownership checks below use cached own bindings. Avoid a
        // whole-world Humanoid search on every physics tick.
        let region_received = view
            .get_property(&player, "Character")
            .and_then(|v| v["$ref"].as_str().map(str::to_owned))
            .and_then(|id| view.get_property(&id, "PrimaryPart"))
            .and_then(|v| v["$ref"].as_str().map(str::to_owned))
            .and_then(|root| root_position(&view, &root))
            .is_some_and(|position| region_contains(position, &self.region_records));
        let frame = self
            .prediction
            .advance(
                &view,
                &self.rpc_class_parents,
                &player,
                self.user_id,
                scope,
                region_received,
                &intents,
                self.prediction_started.elapsed().as_secs_f64(),
            )
            .map_err(|_| "local-prediction-failed")?;
        if let (Some(engine), Some(feedback)) = (self.engine.as_mut(), self.prediction.feedback()) {
            engine
                .queue_physics_feedback(view.sequence(), feedback)
                .map_err(|_| "physics-feedback-queue-failed")?;
        }
        Ok(frame)
    }

    fn flush_rpc_outbound(&mut self) -> Result<(), &'static str> {
        if self.rpc.is_none() {
            return Ok(());
        }
        let scope = self.core.scope().ok_or("remote-session-scope-missing")?;
        let schema = self.core.schema().ok_or("world-schema-unavailable")?;
        let view = self.core.world_view().ok_or("world-view-unavailable")?;
        let rpc = self.rpc.as_mut().ok_or("remote-session-missing")?;
        if let Some(engine) = self.engine.as_mut() {
            for reply in engine.take_remote_client_replies() {
                rpc.queue_client_reply(scope, client_reply_from_value(&reply)?)
                    .map_err(|_| "remote-reply-rejected")?;
            }
            let mut contacts = Vec::new();
            for event in engine.take_remote_server_events() {
                if (event.get("event").is_some() || event.get("touch").is_some())
                    && self.rpc_class_parents.is_empty()
                {
                    self.rpc_class_parents = serde_json::from_value(engine.class_parents().clone())
                        .map_err(|_| "remote-class-metadata-invalid")?;
                }
                if let Some(touch) = event.get("touch") {
                    contacts.push(touch_request(touch, &view, scope, &self.rpc_class_parents)?);
                } else if event.get("event").is_some() {
                    let signal = reflected_signal_from_value(&event)?;
                    let payload =
                        encode_reflected_signal(schema, &view, &signal, &self.rpc_class_parents)
                            .map_err(|_| "remote-signal-encode-failed")?;
                    self.ready_rpc_outbound.push(EncodedRpcPacket {
                        channel: 1,
                        kind: RpcOutboundKind::ServerEvent,
                        payload,
                    });
                } else {
                    rpc.queue_server_event(scope, server_event_from_value(&event, &view)?)
                        .map_err(|_| "remote-event-rejected")?;
                }
            }
            if !contacts.is_empty() {
                let payload =
                    contact_batch(&contacts).map_err(|_| "touch-request-encode-failed")?;
                self.ready_rpc_outbound.push(EncodedRpcPacket {
                    channel: 7,
                    kind: RpcOutboundKind::TouchContact,
                    payload,
                });
            }
            let active = engine
                .active_remote_server_requests()
                .map_err(|_| "remote-state-unavailable")?;
            for call in engine
                .take_remote_server_calls()
                .map_err(|_| "remote-state-unavailable")?
            {
                let request_id = call["request_id"].as_u64();
                if request_id.is_some_and(|id| active.contains(&id)) {
                    rpc.queue_server_call(scope, server_call_from_value(&call)?)
                        .map_err(|_| "remote-call-rejected")?;
                }
            }
        }
        let mut transport = EncodedRpcOutbound::default();
        rpc.drain_outbound(
            scope,
            schema,
            &view,
            self.core.world_sequence().unwrap_or(0),
            &mut transport,
            16,
        )
        .map_err(|_| "remote-reply-encode-failed")?;
        self.ready_rpc_outbound.append(&mut transport.payloads);
        Ok(())
    }

    fn observe(&mut self, record: &SessionRecord) -> Result<(), &'static str> {
        let pings = record
            .pings
            .iter()
            .map(PingItem::from_record)
            .collect::<Result<Vec<_>, _>>()
            .map_err(|_| "invalid-committed-ping")?;
        if pings.len() > crate::ping_responder::MAX_PING_ITEMS {
            return Err("committed-ping-batch-limit");
        }
        self.initial_tag |= record.tags.contains(&13);
        for ping in &record.pings {
            self.pings.observe(
                ping["kind"].as_u64().ok_or("invalid-committed-ping")? as u8,
                ping["flags"].as_u64().ok_or("invalid-committed-ping")? as u8,
            );
        }
        if let Some(actions) = record.world_delta.lifecycle_actions() {
            for action in actions {
                if let Some(id) = action["id"].as_str() {
                    match action["kind"].as_str() {
                        Some("create") if action["node"]["class_name"] == "Player" => {
                            if self.player_ids.len() >= 1024 && !self.player_ids.contains(id) {
                                return Err("player-index-limit");
                            }
                            self.player_ids.insert(id.to_owned());
                        }
                        Some("remove") => {
                            self.player_ids.remove(id);
                        }
                        _ => {}
                    }
                }
            }
        }
        if self.region_records.len() + record.regions.len() > 100_000 {
            return Err("streaming-region-history-limit");
        }
        self.region_records.extend(record.regions.clone());
        self.events += record.events.len() as u64;
        self.regions += record.regions.len() as u64;
        self.property_changes += record.property_changes.len() as u64;
        self.metadata += record.metadata.len() as u64;
        self.validated_pings.extend(pings);
        Ok(())
    }
    fn marker(&mut self, payload: &[u8]) -> Result<Vec<Vec<u8>>, &'static str> {
        if self.core.scope().is_none() {
            return Err("marker-before-globals");
        }
        self.markers.add(payload).map_err(|_| "marker-rejected")?;
        Ok(self.flush_markers())
    }
    fn flush_markers(&mut self) -> Vec<Vec<u8>> {
        self.markers.drain(MarkerReadiness {
            client_scope_received: self.core.scope().is_some(),
            initial_world_tag_received: self.initial_tag,
        })
    }
    fn spawn_evidence(&self) -> Result<SpawnEvidence, &'static str> {
        let player_id = match self.local_player() {
            Ok(Some(player_id)) => player_id,
            Ok(None) | Err("ambiguous-session-player") => return Ok(SpawnEvidence::default()),
            Err(error) => return Err(error),
        };
        let schema = self.core.schema().ok_or("world-schema-unavailable")?;
        let view = self.core.world_view().ok_or("world-view-unavailable")?;
        Ok(evaluate_spawn_evidence(
            schema,
            &view,
            player_id,
            &self.region_records,
        ))
    }

    fn prepare_streaming_resume(&mut self) -> Result<Option<Vec<u8>>, &'static str> {
        if self.streaming_ready_sent {
            return Ok(None);
        }
        let evidence = self.spawn_evidence()?;
        if !evidence.player_spawned || !evidence.spawn_region_received {
            return Ok(None);
        }
        let player = evidence.player_id.ok_or("spawn-player-missing")?;
        let property = evidence
            .pause_property_id
            .ok_or("gameplay-pause-property-unavailable")?;
        let payload = streaming_ready(
            parse_instance_guid(&player)?,
            property,
            9,
            StreamingEvidence {
                player_spawned: true,
                spawn_region_received: true,
            },
        )
        .map_err(|_| "streaming-resume-encode-failed")?;
        self.streaming_ready_sent = true;
        Ok(Some(payload))
    }

    fn summary(&self) -> Value {
        let view = self.core.world_view();
        let own = self.local_player().ok().flatten();
        let own_in_players = view
            .as_ref()
            .zip(own.as_deref())
            .is_some_and(|(world, id)| {
                world.parent(id).and_then(|parent| world.class_name(parent)) == Some("Players")
            });
        let character_received = view
            .as_ref()
            .zip(own.as_deref())
            .is_some_and(|(world, id)| {
                world
                    .get_property(id, "Character")
                    .and_then(|v| v["$ref"].as_str().map(str::to_owned))
                    .is_some_and(|character| world.class_name(&character) == Some("Model"))
            });
        let evidence = self.spawn_evidence().unwrap_or_default();
        let gameplay_paused = view
            .as_ref()
            .zip(own.as_deref())
            .and_then(|(world, id)| world.get_property(id, "GameplayPaused"));
        json!({"schema_received":self.core.schema_installed(), "globals_received":self.core.scope().is_some(),
            "initial_world_tag_received":self.initial_tag, "world_packets_committed":self.core.committed_packets(),
            "world_packets_rejected":self.core.rejected_packets(), "world_nodes":view.map_or(0,|v|v.node_count()),
            "property_storage":rove_client_core::property_storage::statistics(),
            "journal_records_retained":self.core.journal_stats().records,
            "own_player_parented_to_players":own_in_players,"own_character_model_received":character_received,
            "spawn_evidence_complete":evidence.player_spawned && evidence.spawn_region_received,
            "player_spawned":evidence.player_spawned,
            "spawn_region_received":evidence.spawn_region_received,
            "streaming_ready_sent":self.streaming_ready_sent,
            "server_gameplay_paused":gameplay_paused,
            "application_ping_requests_received":self.pings.requests,
            "application_ping_responses_received":self.pings.responses,
            "application_ping_requests_unsupported":self.pings.unsupported,
            "application_ping_replies_sent":0,
            "application_ping_missing_providers":["rust_session_integrity"],
            "replication_markers_pending":self.markers.len(),
            "property_acknowledgments":self.property_acks.report(),
            "simulation_clock":self.simulation_clock.report(),
            "received_property_changes":self.property_changes,
            "local_prediction":self.prediction.report(),
            "physics_enabled":self.physics_enabled,
            "physics_error":self.physics_error,
            "remote_server_results_received":self.remote_server_results_received,
            "remote_server_errors_received":self.remote_server_errors_received,
            "remote_receive_error":self.remote_receive_error,
            "announced_players_service":self.announced_players.is_some(),
            "remote_rpc_pending":self.rpc.as_ref().map_or(0,RpcSession::pending_len),
            "unconsumed_receipts":{"remote_events":self.events,"streaming_regions":self.regions,
                "property_acknowledgements":self.property_acks.report()["pending"],"metadata":self.metadata}})
    }
    fn local_player(&self) -> Result<Option<String>, &'static str> {
        let Some(world) = self.core.world_view() else {
            return Ok(None);
        };
        let mut matches = self.player_ids.iter().filter(|id| {
            world.class_name(id) == Some("Player")
                && world.get_property(id, "UserId").and_then(|v| v.as_f64())
                    == Some(self.user_id as f64)
        });
        let result = matches.next().cloned();
        if matches.next().is_some() {
            return Err("ambiguous-session-player");
        }
        Ok(result)
    }
    fn finish_engine(&mut self) -> Option<Value> {
        self.engine.take().map(|engine| {
            let mut report = engine.finish();
            if let Some(error) = self.engine_error.take() {
                report["ok"] = json!(false);
                report["ingest_error"] = json!(error);
            }
            report
        })
    }
}

#[derive(Default)]
struct EncodedRpcOutbound {
    payloads: Vec<EncodedRpcPacket>,
}
impl RpcTransport for EncodedRpcOutbound {
    fn send(&mut self, channel: u8, payload: &[u8]) -> Result<(), String> {
        self.send_with_kind(channel, payload, RpcOutboundKind::ClientReply)
    }

    fn send_with_kind(
        &mut self,
        channel: u8,
        payload: &[u8],
        kind: RpcOutboundKind,
    ) -> Result<(), String> {
        self.payloads.push(EncodedRpcPacket {
            channel,
            kind,
            payload: payload.to_vec(),
        });
        Ok(())
    }
}

fn server_call_from_value(value: &Value) -> Result<ServerCall, &'static str> {
    let node = value["node"]
        .as_str()
        .ok_or("remote-call-node-invalid")?
        .to_owned();
    let request_id = value["request_id"]
        .as_u64()
        .filter(|id| *id > 0 && *id < (1u64 << 63))
        .ok_or("remote-call-id-invalid")?;
    let arguments = value["arguments"]
        .as_array()
        .cloned()
        .ok_or("remote-call-arguments-invalid")?;
    Ok(ServerCall {
        node,
        request_id,
        arguments,
    })
}

fn remote_result_to_value(result: RemoteResult) -> Value {
    let mut value = json!({"node":result.node,"request_id":result.request_id,"ok":result.ok});
    if result.ok {
        value["values"] = json!(result.values.unwrap_or_default());
    } else {
        value["error"] = json!(result
            .error
            .unwrap_or_else(|| "RemoteFunction failed".into()));
    }
    value
}

fn class_is_a(
    view: &rove_client_core::world::WorldView,
    node: &str,
    target: &str,
    parents: &HashMap<String, String>,
) -> bool {
    let mut class = view.class_name(node);
    for _ in 0..64 {
        let Some(current) = class else {
            return false;
        };
        if current == target {
            return true;
        }
        class = parents.get(current).map(String::as_str);
    }
    false
}

fn touch_request(
    value: &Value,
    view: &rove_client_core::world::WorldView,
    scope: u64,
    parents: &HashMap<String, String>,
) -> Result<((u32, u32), (u32, u32), bool), &'static str> {
    let object = value
        .as_object()
        .filter(|object| object.len() == 3)
        .ok_or("touch-request-invalid")?;
    let first = object["first"].as_str().ok_or("touch-first-invalid")?;
    let second = object["second"].as_str().ok_or("touch-second-invalid")?;
    let began = object["began"].as_bool().ok_or("touch-state-invalid")?;
    if first == second
        || !class_is_a(view, first, "BasePart", parents)
        || !class_is_a(view, second, "BasePart", parents)
    {
        return Err("touch-requires-distinct-base-parts");
    }
    let owned = [first, second].into_iter().any(|id| {
        view.get_property(id, "NetworkOwnerV3")
            .and_then(|value| value.as_f64())
            == Some(scope as f64)
    });
    if !owned {
        return Err("touch-requires-received-ownership");
    }
    Ok((
        parse_instance_guid(first)?,
        parse_instance_guid(second)?,
        began,
    ))
}

fn reflected_signal_from_value(value: &Value) -> Result<ReflectedSignal, &'static str> {
    let node = value["node"]
        .as_str()
        .ok_or("remote-signal-node-invalid")?
        .to_owned();
    let name = value["event"]
        .as_str()
        .filter(|name| !name.is_empty())
        .ok_or("remote-signal-name-invalid")?
        .to_owned();
    let argument_types = value["argument_types"]
        .as_array()
        .ok_or("remote-signal-types-invalid")?
        .iter()
        .map(|kind| {
            kind.as_u64()
                .and_then(|kind| u8::try_from(kind).ok())
                .ok_or("remote-signal-type-invalid")
        })
        .collect::<Result<Vec<_>, _>>()?;
    let arguments = value["arguments"]
        .as_array()
        .cloned()
        .ok_or("remote-signal-arguments-invalid")?;
    if argument_types.len() != arguments.len() {
        return Err("remote-signature-length-mismatch");
    }
    Ok(ReflectedSignal {
        node,
        name,
        argument_types,
        arguments,
    })
}

fn server_event_from_value(
    value: &Value,
    view: &rove_client_core::world::WorldView,
) -> Result<ServerEvent, &'static str> {
    let node = value["node"]
        .as_str()
        .ok_or("remote-event-node-invalid")?
        .to_owned();
    let unreliable = match view.class_name(&node) {
        Some("RemoteEvent") => false,
        Some("UnreliableRemoteEvent") => true,
        _ => return Err("remote-event-class-invalid"),
    };
    let arguments = value["arguments"]
        .as_array()
        .cloned()
        .ok_or("remote-event-arguments-invalid")?;
    Ok(ServerEvent {
        node,
        arguments,
        unreliable,
    })
}

fn client_reply_from_value(value: &Value) -> Result<ClientReply, &'static str> {
    let node = value["node"]
        .as_str()
        .ok_or("remote-reply-node-invalid")?
        .to_owned();
    let request_id = value["request_id"]
        .as_i64()
        .and_then(|id| i32::try_from(id).ok())
        .ok_or("remote-reply-id-invalid")?;
    let ok = value["ok"].as_bool().ok_or("remote-reply-status-invalid")?;
    let values = value["values"].as_array().cloned().unwrap_or_default();
    let error = value["error"].as_str().map(str::to_owned);
    if ok && value["values"].as_array().is_none() {
        return Err("remote-reply-values-invalid");
    }
    Ok(ClientReply {
        node,
        request_id,
        ok,
        values,
        error,
    })
}

#[derive(Debug, Default)]
struct SpawnEvidence {
    player_spawned: bool,
    spawn_region_received: bool,
    player_id: Option<String>,
    pause_property_id: Option<u16>,
}

fn region_contains(position: [f64; 3], regions: &[Value]) -> bool {
    let coordinate = position.map(|value| (value / 64.0).floor() as i64);
    let coordinate_received = regions.iter().any(|region| {
        region["coordinate"]
            .as_array()
            .is_some_and(|values| coordinate_matches(values, coordinate))
    });
    coordinate_received
        && regions.iter().any(|region| {
            let Some(radius) = region["radius"].as_i64() else {
                return false;
            };
            if radius < 1 {
                return false;
            }
            let Some(center) = region["coordinate"].as_array() else {
                return false;
            };
            if center.len() != 3 {
                return false;
            }
            (0..3)
                .map(|axis| {
                    center[axis]
                        .as_i64()
                        .map(|value| (value - coordinate[axis]).abs())
                        .unwrap_or(i64::MAX)
                })
                .max()
                .unwrap_or(i64::MAX)
                < radius
        })
}

fn evaluate_spawn_evidence(
    schema: &rove_client_core::schema::Schema,
    view: &rove_client_core::world::WorldView,
    player_id: String,
    regions: &[Value],
) -> SpawnEvidence {
    let pause_property_id = schema
        .classes
        .iter()
        .find(|class| class.name == "Player")
        .and_then(|class| {
            schema.properties.iter().find(|property| {
                property.name == "GameplayPaused" && class.properties.contains(&property.id)
            })
        })
        .and_then(|property| u16::try_from(property.id).ok());
    let mut evidence = SpawnEvidence {
        player_id: Some(player_id.clone()),
        pause_property_id,
        ..SpawnEvidence::default()
    };
    if view.class_name(&player_id) != Some("Player") {
        return evidence;
    }
    let Some(character_id) = view
        .get_property(&player_id, "Character")
        .and_then(|value| value["$ref"].as_str().map(str::to_owned))
    else {
        return evidence;
    };
    if view.class_name(&character_id) != Some("Model")
        || view
            .parent(&character_id)
            .and_then(|parent| view.class_name(parent))
            != Some("Workspace")
    {
        return evidence;
    }
    let mut humanoid = None;
    view.for_each_node(|node| {
        if matches!(
            node.parent(),
            rove_client_core::world::ReadOnlyField::Value(parent) if parent == character_id
        ) && node.class_name() == "Humanoid"
        {
            humanoid = Some(node.id().to_owned());
        }
    });
    let Some(humanoid_id) = humanoid else {
        return evidence;
    };
    if view
        .get_property(&humanoid_id, "Health")
        .is_some_and(|health| {
            !health
                .as_f64()
                .is_some_and(|value| value.is_finite() && value > 0.0)
        })
    {
        return evidence;
    }
    let Some(root_id) = view
        .get_property(&character_id, "PrimaryPart")
        .and_then(|value| value["$ref"].as_str().map(str::to_owned))
    else {
        return evidence;
    };
    if !matches!(view.class_name(&root_id), Some("Part" | "MeshPart"))
        || view.parent(&root_id).as_deref() != Some(character_id.as_str())
        || !crate::physics_properties::name_is(view, &root_id, "HumanoidRootPart")
    {
        return evidence;
    }
    let Some(position) = root_position(view, &root_id) else {
        return evidence;
    };
    evidence.spawn_region_received = region_contains(position, regions);
    evidence.player_spawned = true;
    evidence
}

fn root_position(view: &rove_client_core::world::WorldView, root: &str) -> Option<[f64; 3]> {
    for key in ["ReplicationPV", "CFrame"] {
        let Some(value) = view.get_property(root, key) else {
            continue;
        };
        let Some(position) = value["position"].as_array() else {
            continue;
        };
        if position.len() != 3 {
            return None;
        }
        let mut out = [0.0; 3];
        for (destination, value) in out.iter_mut().zip(position) {
            *destination = value.as_f64().filter(|number| number.is_finite())?;
        }
        return Some(out);
    }
    None
}

fn coordinate_matches(values: &[Value], coordinate: [i64; 3]) -> bool {
    values.len() == 3
        && values
            .iter()
            .zip(coordinate)
            .all(|(value, expected)| value.as_i64() == Some(expected))
}

fn parse_instance_guid(id: &str) -> Result<(u32, u32), &'static str> {
    let (scope, index) = id.split_once(':').ok_or("spawn-player-guid-invalid")?;
    let scope = scope
        .parse::<u32>()
        .map_err(|_| "spawn-player-guid-invalid")?;
    let index = index
        .parse::<u32>()
        .map_err(|_| "spawn-player-guid-invalid")?;
    if scope == 0 {
        return Err("spawn-player-guid-invalid");
    }
    Ok((scope, index))
}

fn emit_rpc_outbound(
    world: &mut ReceivedWorld,
    outgoing: &mpsc::UnboundedSender<Effect>,
    reply_budget: &Arc<Semaphore>,
) -> Result<(), &'static str> {
    if world.ready_rpc_outbound.is_empty() {
        return Ok(());
    }
    let permit = reply_budget
        .clone()
        .try_acquire_owned()
        .map_err(|_| "reply-queue-limit")?;
    outgoing
        .send(Effect::RpcOutbound {
            payloads: std::mem::take(&mut world.ready_rpc_outbound),
            _budget: permit,
        })
        .map_err(|_| "session-stopped-before-send")
}

fn worker(
    mut auth: ProgramAuth,
    user_id: u64,
    mut incoming: mpsc::Receiver<Incoming>,
    outgoing: mpsc::UnboundedSender<Effect>,
    stop: Arc<AtomicBool>,
    engine: Option<LiveRuntime>,
    property_acks: PropertyAcknowledgments,
    physics_enabled: bool,
    physics_output: Arc<Mutex<PhysicsOutput>>,
) -> Value {
    // Avoid a cycle where the network waits for the input queue while this
    // worker waits for that same network to drain replies. Permits bound the
    // nonblocking output queue; saturation fails explicitly.
    let reply_budget = Arc::new(Semaphore::new(128));
    let mut world = match ReceivedWorld::with_property_acks(user_id, property_acks) {
        Ok(world) => world,
        Err(error) => {
            let _ = outgoing.send(Effect::Failed(error));
            return json!({"ok":false,"error":error});
        }
    };
    world.engine = engine;
    world.physics_enabled = physics_enabled;
    let mut next_physics = Instant::now();
    let mut unhandled = BTreeMap::<u8, u64>::new();
    let mut feature_count = None;
    let mut program_requests = 0u64;
    let mut discarded = 0u64;
    let mut failure = None;
    loop {
        if physics_enabled && !stop.load(Ordering::Acquire) && Instant::now() >= next_physics {
            next_physics = Instant::now() + Duration::from_micros(16_667);
            let frame = if world.physics_error.is_none() {
                match world.advance_prediction() {
                    Ok(frame) => frame.filter(|frame| frame.fixed_steps > 0),
                    Err(error) => {
                        world.physics_error = Some(error);
                        None
                    }
                }
            } else {
                None
            };
            if let Ok(mut output) = physics_output.lock() {
                if let Some(frame) = frame {
                    output.coalesced += u64::from(output.latest.is_some());
                    output.produced += 1;
                    output.latest = Some((frame, Instant::now()));
                } else {
                    output.latest = None;
                }
            } else {
                world.physics_error = Some("physics-output-unavailable");
            }
        }
        let message = match incoming.try_recv() {
            Ok(message) => message,
            Err(mpsc::error::TryRecvError::Empty) => {
                let result = if stop.load(Ordering::Acquire) {
                    Ok(())
                } else {
                    world
                        .flush_rpc_outbound()
                        .and_then(|_| emit_rpc_outbound(&mut world, &outgoing, &reply_budget))
                };
                if let Err(error) = result {
                    failure = Some(error);
                    let _ = outgoing.send(Effect::Failed(error));
                    break;
                }
                std::thread::sleep(Duration::from_millis(2));
                continue;
            }
            Err(mpsc::error::TryRecvError::Disconnected) => break,
        };
        if stop.load(Ordering::Acquire) {
            discarded += 1;
            continue;
        }
        let result = (|| -> Result<(), &'static str> {
            let payload = &message.payload;
            let kind = payload.first().copied().ok_or("empty-game-packet")?;
            let mut program = false;
            let replies = match (message.application, message.channel, kind) {
                (4, 1, 147) => {
                    feature_count = Some(
                        crate::login::feature_configuration(payload)
                            .map_err(|_| "feature-configuration-rejected")?
                            .len(),
                    );
                    vec![]
                }
                (4, 1, 155) => {
                    program_requests += 1;
                    program = true;
                    vec![auth
                        .handle(payload)
                        .map_err(|_| "signed-program-rejected")?]
                }
                (4, 1, 132) => world.marker(payload)?,
                (4, 3, 131) if world.core.scope().is_none() => {
                    let items = decode_ping_packet(payload).map_err(|_| "ping-packet-rejected")?;
                    let counts = PingCounts::from_packet(payload)?;
                    world.pings.requests += counts.requests;
                    world.pings.responses += counts.responses;
                    world.pings.unsupported += counts.unsupported;
                    world.validated_pings.extend(items);
                    vec![]
                }
                (4, 1, 129 | 151) | (4, 1 | 3, 131) => {
                    world.ingest_at(payload, message.received_clock_word)?
                }
                _ => {
                    *unhandled.entry(kind).or_default() += 1;
                    vec![]
                }
            };
            if !world.validated_pings.is_empty() {
                let permit = reply_budget
                    .clone()
                    .try_acquire_owned()
                    .map_err(|_| "reply-queue-limit")?;
                outgoing
                    .send(Effect::Pings {
                        items: std::mem::take(&mut world.validated_pings),
                        received_clock_word: message.received_clock_word,
                        _budget: permit,
                    })
                    .map_err(|_| "session-stopped-before-send")?;
            }
            for payload in replies {
                let permit = reply_budget
                    .clone()
                    .try_acquire_owned()
                    .map_err(|_| "reply-queue-limit")?;
                outgoing
                    .send(Effect::Reply {
                        payload,
                        program,
                        _budget: permit,
                    })
                    .map_err(|_| "session-stopped-before-send")?;
            }
            if let Some(prepared) = world.ready_property_ack.take() {
                let permit = reply_budget
                    .clone()
                    .try_acquire_owned()
                    .map_err(|_| "reply-queue-limit")?;
                outgoing
                    .send(Effect::PropertyAck {
                        prepared,
                        _budget: permit,
                    })
                    .map_err(|_| "session-stopped-before-send")?;
            }
            if let Some(payload) = world.ready_streaming_resume.take() {
                let permit = reply_budget
                    .clone()
                    .try_acquire_owned()
                    .map_err(|_| "reply-queue-limit")?;
                outgoing
                    .send(Effect::StreamingReady {
                        payload,
                        _budget: permit,
                    })
                    .map_err(|_| "session-stopped-before-send")?;
            }
            emit_rpc_outbound(&mut world, &outgoing, &reply_budget)?;
            Ok(())
        })();
        if let Err(error) = result {
            failure = Some(error);
            let _ = outgoing.send(Effect::Failed(error));
            break;
        }
    }
    let mut report = world.summary();
    let engine = world.finish_engine();
    report["ok"] = json!(failure.is_none() && engine.as_ref().is_none_or(|r| r["ok"] == true));
    report["engine_vm_connected"] =
        json!(engine.as_ref().is_some_and(|r| r["initialized"] == true));
    report["authored_scripts_submitted"] = json!(u64::from(
        engine
            .as_ref()
            .is_some_and(|r| r["source_submitted"] == true)
    ));
    report["engine_runtime"] = json!(engine);
    report["error"] = json!(failure);
    report["client_environment_challenges_received"] = json!(program_requests);
    report["client_environment_auth"] = auth.summary();
    report["server_feature_count"] = json!(feature_count);
    report["unhandled_game_packets"] = json!(unhandled);
    report["packets_discarded_after_stop"] = json!(discarded);
    report
}

async fn enqueue(
    tx: &mpsc::Sender<Incoming>,
    budget: &Arc<Semaphore>,
    application: u8,
    channel: u32,
    payload: Vec<u8>,
    clock: PingClock,
) -> Result<(), &'static str> {
    let received_clock_word = clock.word();
    if payload.len() > MAX_FRAME {
        return Err("incoming-frame-too-large");
    }
    let permit = budget
        .clone()
        .acquire_many_owned(payload.len() as u32)
        .await
        .map_err(|_| "incoming-queue-closed")?;
    tx.send(Incoming {
        application,
        channel,
        payload,
        received_clock_word,
        _budget: permit,
    })
    .await
    .map_err(|_| "world-owner-stopped")
}

fn start_receivers(
    connection: &Connection,
    tx: mpsc::Sender<Incoming>,
    budget: Arc<Semaphore>,
    failure: Arc<Mutex<Option<Value>>>,
    clock: PingClock,
) -> JoinSet<Result<(), &'static str>> {
    let mut tasks = JoinSet::new();
    let connection = connection.clone();
    tasks.spawn(async move {
        let mut readers=JoinSet::new();
        let mut total=0;
        let result=async {
            // Keep the in-progress accept/header read alive when another
            // receiver finishes; dropping that future could discard a header.
            let mut accepting=Box::pin(ReliableReceiver::accept(&connection));
            loop {
                tokio::select! {
                    accepted=&mut accepting => {
                        let mut receiver=accepted.map_err(|_| "reliable-channel-accept-failed")?;
                        accepting=Box::pin(ReliableReceiver::accept(&connection));
                        total += 1;
                        if total > MAX_STREAMS { return Err("reliable-channel-limit"); }
                        let tx=tx.clone(); let budget=budget.clone(); let failure=failure.clone();
                        readers.spawn(async move {
                            while let Some(payload)=receiver.next().await.map_err(|error| {
                                if let Ok(mut value)=failure.lock() {
                                    if value.is_none() { *value=Some(receive_failure_metadata(receiver.application(),receiver.channel(),&error)); }
                                }
                                "reliable-frame-read-failed"
                            })? {
                                enqueue(&tx,&budget,receiver.application(),receiver.channel(),payload,clock).await?;
                            }
                            Ok::<_, &'static str>(())
                        });
                    }
                    completed=readers.join_next(), if !readers.is_empty() => {
                        completed.ok_or("receiver-task-missing")?.map_err(|_| "receiver-task-failed")??;
                    }
                }
            }
        }.await;
        readers.abort_all(); while readers.join_next().await.is_some() {}
        result
    });
    tasks
}

fn receive_failure_metadata(application: u8, channel: u32, error: &TransportError) -> Value {
    let (kind, code) = match error {
        TransportError::StreamReset { code } => ("peer_stream_reset", Some(*code)),
        TransportError::StreamNotConnected => ("stream_not_connected", None),
        TransportError::StreamProtocolError => ("stream_protocol_error", None),
        TransportError::Routing(_) => ("stream_framing_rejected", None),
        _ => ("stream_read_failed", None),
    };
    json!({"application":application,"channel":channel,"kind":kind,"code":code})
}

/// Run a bounded Rust receive session using only this fresh HTTPS allocation.
/// This is deliberately separate from the older Python script console.
pub fn run(issued: &IssuedJoinAllocation, seconds: u64) -> Result<Value, String> {
    run_with_runtime(issued, seconds, None)
}

pub fn run_with_runtime(
    issued: &IssuedJoinAllocation,
    seconds: u64,
    config: Option<RuntimeConfig>,
) -> Result<Value, String> {
    run_with_options(issued, seconds, config, false)
}

pub fn run_with_options(
    issued: &IssuedJoinAllocation,
    seconds: u64,
    config: Option<RuntimeConfig>,
    physics_enabled: bool,
) -> Result<Value, String> {
    if !(1..=3600).contains(&seconds) {
        return Err("session duration must be 1..3600 seconds".into());
    }
    let allocation = live_transport_allocation(issued)?;
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
    let trust = PublicTrust::from_files(
        &root.join("inputs/bytecode-signature-v1-public.bin"),
        &root.join("inputs/bytecode-signature-v1-public.json"),
    )
    .map_err(|e| e.to_string())?;
    let integrity = SessionIntegrity::new();
    let mut auth = ProgramAuth::for_allocation(issued, trust).map_err(|e| e.to_string())?;
    auth.set_integrity(integrity.clone());
    let engine = config
        .map(|config| {
            LiveRuntime::new_with_integrity(
                config,
                issued.game_id.clone().ok_or("issued-job-id-missing")?,
                integrity.clone(),
            )
        })
        .transpose()?;
    // Encoding and credential validation occur before the socket opens.
    let packets = issued
        .game_login_packets(Some((13, 14)))
        .map_err(|e| e.to_string())?;
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|_| "transport-runtime-unavailable")?;
    runtime.block_on(async {
        let started=Instant::now();
        let (endpoint,connection,socket)=tokio::time::timeout(Duration::from_secs(20),connect_webtransport(&allocation)).await
            .map_err(|_| "connection-timeout")?.map_err(|_| "connection-failed")?;
        let caps=connection.capability_negotiation().ok_or("capability-negotiation-missing")?;
        if caps.negotiated() & BYTECODE_RANGE_CAPABILITY == 0 {
            connection.close(0u32.into(),b"bytecode range unsupported");
            endpoint.close(0u32.into(),b"bytecode range unsupported");
            return Err("bytecode-range-negotiation-unavailable".into());
        }
        let negotiated=caps.negotiated();
        let accepted_at=Instant::now();
        let ping_clock=PingClock::new(accepted_at);
        let mut ping_telemetry=SessionPingTelemetry::new(ping_clock,negotiated,integrity.clone());
        let mut ping_responder=PingResponder::new(128).map_err(|_| "ping-queue-init-failed")?;
        let mut ping_writer=PingWriter::default();
        let sender=ReliableSender::new(&connection);
        let (network_tx,mut network_rx)=mpsc::channel(32);
        let budget=Arc::new(Semaphore::new(QUEUE_BYTES));
        let receive_failure=Arc::new(Mutex::new(None));
        let mut readers=start_receivers(&connection,network_tx.clone(),budget.clone(),receive_failure.clone(),ping_clock);
        let (world_tx,world_rx)=mpsc::channel(32);
        let (effect_tx,mut effects)=mpsc::unbounded_channel();
        let stop=Arc::new(AtomicBool::new(false));
        let worker_stop=stop.clone(); let user_id=issued.user_id;
        let property_acks=PropertyAcknowledgments::new();
        let worker_property_acks=property_acks.clone();
        let physics_output=Arc::new(Mutex::new(PhysicsOutput::default()));
        let worker_physics_output=physics_output.clone();
        let owner=tokio::task::spawn_blocking(move || worker(auth,user_id,world_rx,effect_tx,worker_stop,engine,worker_property_acks,physics_enabled,worker_physics_output));
        let mut clock=SessionWireClock::new();
        let mut sync=TimeSyncProbe::default();
        let mut channels=BTreeMap::new();
        let mut close=None; let mut application_close=None;
        let mut received=0u64; let mut sent_programs=0u64; let mut sent_markers=0u64;
        let mut streaming_ready_sent=0u64;
        let mut remote_client_replies_sent=0u64;
        let mut remote_server_events_sent=0u64;
        let mut unreliable_remote_server_events_sent=0u64;
        let mut remote_server_calls_sent=0u64;
        let mut script_contacts_sent=0u64;
        let mut login_sent=0; let mut unknown_datagrams=0u64; let mut fragmented_datagrams=0u64;
        let mut last_received=accepted_at; let mut duration_reached=false;
        let mut sync_sequence=0;
        let mut physics_sequence=0u64;
        let mut physics_announced=false;
        let mut physics_send_attempts=0u64;
        let mut physics_send_returns=0u64;
        let mut physics_stale_frames=0u64;
        let session_result=async {
            clock.initialize_replicator().map_err(|_| "wire-clock-init-failed")?;
            for app in [0,4,6] { sender.send(app,CONTROL_CHANNEL,&[0,0]).await.map_err(|_| "control-open-failed")?; }
            sender.open(6,1).await.map_err(|_| "clock-channel-open-failed")?;
            for (channel,wire) in [(2,2),(3,4)] {
                sender.send(6,CONTROL_CHANNEL,&control_open(6,channel,wire).map_err(|_| "clock-channel-invalid")?).await.map_err(|_| "clock-channel-send-failed")?;
            }
            sync.start(&clock).map_err(|_| "clock-probe-start-failed")?;
            for packet in packets { sender.send(4,1,&packet).await.map_err(|_| "login-send-failed")?; login_sent+=1; }
            let deadline=tokio::time::sleep_until((accepted_at+Duration::from_secs(seconds)).into()); tokio::pin!(deadline);
            let mut tick=tokio::time::interval(Duration::from_millis(20)); tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
            loop {
                tokio::select! {
                    biased;
                    _=&mut deadline => { duration_reached=true; return Ok::<_, &'static str>(()); }
                    error=connection.closed() => { close=Some(close_metadata(&error)); return Ok(()); }
                    _=tick.tick() => {
                        ping_writer.flush(&mut ping_responder,&mut ping_telemetry,&sender,&connection).await?;
                        let physics_frame = physics_output.lock().map_err(|_| "physics-output-unavailable")?.latest.take();
                        if let Some((frame,produced_at))=physics_frame {
                            if produced_at.elapsed()>Duration::from_millis(250) {
                                physics_stale_frames+=1;
                            } else {
                                physics_sequence=physics_sequence.checked_add(1).ok_or("physics-sequence-overflow")?;
                                let payload=physics_sender::encode_root_frame(frame.binding.root_part_id,
                                    clock.physics_milliseconds().map_err(|_| "physics-clock-unavailable")?,
                                    physics_sequence,frame.position,frame.velocity,frame.grounded)?;
                                if !physics_announced {
                                    let announcement=control_open(physics_sender::APPLICATION,physics_sender::CHANNEL,physics_sender::WIRE_ID)
                                        .map_err(|_| "physics-channel-invalid")?;
                                    sender.send(physics_sender::APPLICATION,CONTROL_CHANNEL,&announcement).await
                                        .map_err(|_| "physics-channel-announcement-failed")?;
                                    physics_announced=true;
                                }
                                physics_send_attempts+=1;
                                send_unreliable(&connection,physics_sender::WIRE_ID,physics_sequence,&payload)
                                    .map_err(|_| "physics-send-failed")?;
                                physics_send_returns+=1;
                            }
                        }
                        if let Some(packet)=sync.due(&clock).map_err(|_| "time-sync-clock-failed")? {
                            sync_sequence+=1;
                            send_unreliable(&connection,4,sync_sequence,&packet).map_err(|_| "time-sync-send-failed")?;
                        }
                    }
                    result=readers.join_next(), if !readers.is_empty() => {
                        result.ok_or("reader-task-missing")?.map_err(|_| "reader-task-failed")??;
                    }
                    effect=effects.recv() => match effect.ok_or("world-owner-stopped")? {
                        Effect::Failed(error) => return Err(error),
                        Effect::Pings {items,received_clock_word,..} => {
                            ping_responder.receive_validated_items(&items,received_clock_word,&mut ping_telemetry);
                            ping_writer.flush(&mut ping_responder,&mut ping_telemetry,&sender,&connection).await?;
                        }
                        Effect::Reply { payload,program,.. } => {
                            sender.send(4,1,&payload).await.map_err(|_| "application-reply-send-failed")?;
                            if program {sent_programs+=1;} else {sent_markers+=1;}
                        }
                        Effect::PropertyAck { prepared,.. } => {
                            let written=sender.send(4,1,prepared.payload()).await.is_ok();
                            property_acks.commit(prepared.id(),written).map_err(|_| "property-acknowledgement-accounting-invalid")?;
                            if !written {return Err("property-acknowledgement-send-failed");}
                        }
                        Effect::StreamingReady { payload,.. } => {
                            sender.send(4,1,&payload).await.map_err(|_| "streaming-resume-send-failed")?;
                            streaming_ready_sent+=1;
                        }
                        Effect::RpcOutbound { payloads,.. } => {
                            for packet in payloads {
                                match packet.channel {
                                    1 | 7 => sender.send(4,u32::from(packet.channel),&packet.payload).await.map_err(|_| "remote-rpc-send-failed")?,
                                    3 => ping_writer.send_unreliable(&sender,&connection,&packet.payload).await?,
                                    _ => return Err("remote-rpc-channel-invalid"),
                                }
                                match packet.kind {
                                    RpcOutboundKind::ClientReply => remote_client_replies_sent+=1,
                                    RpcOutboundKind::ServerEvent => {
                                        remote_server_events_sent+=1;
                                        unreliable_remote_server_events_sent+=u64::from(packet.channel==3);
                                    }
                                    RpcOutboundKind::ServerCall => remote_server_calls_sent+=1,
                                    RpcOutboundKind::TouchContact => script_contacts_sent+=1,
                                }
                            }
                        }
                    },
                    message=network_rx.recv() => {
                        let message=message.ok_or("network-receiver-stopped")?;
                        ping_telemetry.observe_packet(message.application,&message.payload);
                        received+=1;last_received=Instant::now();
                        if message.channel==CONTROL_CHANNEL {
                            if message.payload.first()==Some(&2) {
                                let (app,channel,wire)=parse_control_open(&message.payload).map_err(|_| "channel-announcement-rejected")?;
                                if channels.len()>=MAX_CHANNELS && !channels.contains_key(&wire) {return Err("unreliable-channel-limit");}
                                if channels.get(&wire).is_some_and(|existing| *existing!=(app,u32::from(channel))) {return Err("unreliable-channel-reassigned");}
                                channels.insert(wire,(app,u32::from(channel)));
                            } else if let Some(reason)=parse_application_close(&message.payload,message.application) {
                                application_close=Some(reason);return Ok(());
                            }
                        } else if message.application==6 && message.channel==3 {
                            sync.receive(&message.payload,&clock,true).map_err(|_| "time-sync-receive-failed")?;
                        } else {
                            tokio::select! {
                                result=world_tx.send(message) => {result.map_err(|_| "world-owner-stopped")?;}
                                _=&mut deadline => {duration_reached=true;return Ok(());}
                            }
                        }
                    }
                    datagram=receive_unreliable(&connection) => {
                        let datagram=datagram.map_err(|_| "datagram-read-failed")?;
                        if !datagram.complete {fragmented_datagrams+=1;continue;}
                        let Some(&(app,channel))=channels.get(&datagram.wire_id) else {unknown_datagrams+=1;continue;};
                        ping_telemetry.observe_packet(app,&datagram.payload);
                        received+=1;last_received=Instant::now();
                        if app==6 && channel==3 {sync.receive(&datagram.payload,&clock,true).map_err(|_| "time-sync-receive-failed")?;}
                        else {
                            tokio::select! {
                                result=enqueue(&world_tx,&budget,app,channel,datagram.payload,ping_clock) => {result?;}
                                _=&mut deadline => {duration_reached=true;return Ok(());}
                            }
                        }
                    }

                }
            }
        }.await;
        let connected_seconds=accepted_at.elapsed().as_secs_f64();
        // A stream read can wake just before the connection-close future.
        // Capture that actual reason before our own shutdown masks it.
        if session_result.is_err() && close.is_none() {
            close=current_close_metadata(&connection);
            if close.is_none() {
                if let Ok(error)=tokio::time::timeout(Duration::from_millis(100),connection.closed()).await {
                    close=Some(current_close_metadata(&connection).unwrap_or_else(||close_metadata(&error)));
                }
            }
        }
        // Freeze the observation before our own close wakes reliable readers.
        // Shutdown-induced NotConnected is not a peer failure.
        let observed_receive_failure=receive_failure.lock().ok().and_then(|value|value.clone());
        stop.store(true,Ordering::Release);
        connection.close(0u32.into(),b"rust session complete"); endpoint.close(0u32.into(),b"rust session complete");
        readers.abort_all(); while readers.join_next().await.is_some() {}
        drop(network_tx); drop(network_rx); drop(world_tx);
        let mut report=owner.await.map_err(|_| "world-owner-join-failed")?;
        // A worker can finish validating its last frame while shutdown starts.
        // Account for those requests without sending after the socket is closed.
        while let Ok(effect)=effects.try_recv() {
            if let Effect::Pings {items,received_clock_word,..}=effect {
                ping_responder.receive_validated_items(&items,received_clock_word,&mut ping_telemetry);
            }
        }
        let committed_ping_requests=report["application_ping_requests_received"].clone();
        live_ping::append_report(&mut report,&ping_responder,&ping_telemetry);
        report["property_acknowledgments"]=property_acks.report();
        report["application_ping_requests_committed_by_world"]=committed_ping_requests;
        report["application_ping_accounting_complete"]=json!(
            report["application_ping_accounting_complete"]==true &&
            report["application_ping_requests_received"]==report["application_ping_requests_committed_by_world"]
        );
        let _=tokio::time::timeout(Duration::from_millis(500),endpoint.wait_idle()).await;
        let stats=socket.stats();
        let completed=duration_reached && session_result.is_ok() && report["ok"]==true;
        report["ok"]=json!(completed);
        if let Err(error)=session_result {
            report["transport_error"]=json!(error);
            if report["error"].is_null() { report["error"]=json!(error); }
        }
        report["runtime"]=json!("rust_live_received_world"); report["live_game_session"]=json!(true);
        report["python_processes"]=json!(0);report["game_scripts_executed"]=json!(0);
        report["full_client_implemented"]=json!(false);
        report["game_authentication_attempted"]=json!(login_sent>0);report["login_packets_sent"]=json!(login_sent);
        report["client_environment_responses_sent"]=json!(sent_programs);report["replication_markers_acknowledged"]=json!(sent_markers);
        report["streaming_ready_sent"]=json!(streaming_ready_sent);
        report["remote_client_replies_sent"]=json!(remote_client_replies_sent);
        report["remote_server_events_sent"]=json!(remote_server_events_sent);
        report["unreliable_remote_server_events_sent"]=json!(unreliable_remote_server_events_sent);
        report["remote_server_calls_sent"]=json!(remote_server_calls_sent);
        report["script_contacts_sent"]=json!(script_contacts_sent);
        let mut physics=report["local_prediction"].clone();
        physics["enabled"]=json!(physics_enabled);
        physics["error"]=report["physics_error"].clone();
        physics["network_send_attempt_count"]=json!(physics_send_attempts);
        physics["client_send_return_count"]=json!(physics_send_returns);
        physics["stale_frames_dropped"]=json!(physics_stale_frames);
        physics["application"]=json!(physics_sender::APPLICATION);
        physics["channel"]=json!(physics_sender::CHANNEL);
        physics["rotation_mode"]=json!("identity_selector");
        if let Ok(output)=physics_output.lock() {
            physics["frames_produced"]=json!(output.produced);
            physics["frames_coalesced"]=json!(output.coalesced);
        }
        report["character_physics"]=physics;
        report["stage"]=json!(if completed {"receive_duration_complete"} else {"receive_session_ended"});
        report["stayed_open_for_requested_duration"]=json!(duration_reached);
        report["requested_seconds"]=json!(seconds); report["connected_seconds"]=json!(connected_seconds);
        report["elapsed_seconds"]=json!(started.elapsed().as_secs_f64());report["application_messages_received"]=json!(received);
        report["last_application_message_age_seconds"]=json!(last_received.elapsed().as_secs_f64());
        report["close_kind"]=json!(close.map(|c|c.0));report["close_code"]=json!(close.and_then(|c|c.1));report["application_close"]=json!(application_close);
        report["receive_failure"]=observed_receive_failure.unwrap_or(Value::Null);
        report["time_sync"]=sync.public_report(); report["negotiated_capabilities"]=json!(format!("{negotiated:016x}"));
        report["udp_datagrams_sent"]=json!(stats.datagrams_sent);report["udp_datagrams_received"]=json!(stats.datagrams_received);
        report["unknown_datagrams"]=json!(unknown_datagrams);report["fragmented_datagrams"]=json!(fragmented_datagrams);
        report["peak_kernel_resident_bytes"]=json!(peak_process_rss_bytes().ok());report["resident_end_bytes"]=json!(current_process_rss_bytes().ok());
        Ok(report)
    })
}

fn parse_application_close(payload: &[u8], application: u8) -> Option<Value> {
    if payload.len() < 10 || payload[0] != 1 || payload[1] < 8 {
        return None;
    }
    Some(
        json!({"application":application,"reason_class":u32::from_be_bytes(payload[2..6].try_into().ok()?),
        "reason_code":u32::from_be_bytes(payload[6..10].try_into().ok()?)}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    fn owned_world() -> ReceivedWorld {
        use rove_client_core::schema::{ClassDesc, PropertyDesc, Schema};
        let schema = Schema {
            version: 3,
            flags: 0,
            enums: vec![],
            classes: vec![ClassDesc {
                id: 0,
                name: "Part".into(),
                properties: vec![0],
                events: vec![],
                isr_properties: vec![],
                unknown_u16: 0,
            }],
            properties: vec![PropertyDesc {
                id: 0,
                class: 0,
                name: "Transparency".into(),
                type_id: 11,
                enum_id: None,
                lr_type: None,
            }],
            events: vec![],
            strings: vec![],
            names: vec![],
            lr_types: vec![],
            parent_property_id: 1,
            decoded_bytes: 0,
        };
        ReceivedWorld::with_core(SessionCore::with_schema(schema, 8, 65536).unwrap(), 42).unwrap()
    }
    #[test]
    fn live_owner_keeps_received_properties_and_releases_consumed_receipts() {
        let mut state = owned_world();
        state.ingest(&[129, 0, 0, 0, 0, 0, 0, 0, 1, 0]).unwrap();
        let mut create = vec![131, 2, 1];
        create.extend(7u32.to_le_bytes());
        create.extend(0u16.to_le_bytes());
        create.push(0);
        create.extend(0.25f32.to_le_bytes());
        create.extend([255, 255, 0, 0]);
        state.ingest(&create).unwrap();
        assert_eq!(
            state
                .core
                .world_view()
                .unwrap()
                .get_property("1:7", "Transparency"),
            Some(json!(0.25))
        );
        assert_eq!(state.core.journal_stats().records, 0);
        state.ingest(&[131, 1, 1, 7, 0, 0, 0, 0]).unwrap();
        assert_eq!(state.core.world_view().unwrap().node_count(), 0);
        assert_eq!(state.summary()["player_spawned"], false);
    }
    #[test]
    fn marker_replies_wait_for_valid_committed_initial_world_tag() {
        let mut state = owned_world();
        assert!(state.marker(&[132, 1, 2, 3, 4]).is_err());
        state.ingest(&[129, 0, 0, 0, 0, 0, 0, 0, 1, 0]).unwrap();
        assert!(state.marker(&[132, 1, 2, 3, 4]).unwrap().is_empty());
        let tag = [131, 16, 13, 0, 0, 0, 0];
        assert!(state.ingest(&tag[..6]).is_err());
        assert!(!state.initial_tag);
        assert_eq!(state.markers.len(), 1);
        assert_eq!(
            state.ingest(&tag).unwrap(),
            vec![vec![131, 4, 1, 2, 3, 4, 0]]
        );
        assert_eq!(state.markers.len(), 0);
    }
    #[test]
    fn ping_counts_validate_the_entire_packet_without_manufacturing_replies() {
        let mut payload = vec![131, 5, 0];
        payload.extend([0; 20]);
        payload.extend([0, 0]);
        let counts = PingCounts::from_packet(&payload).unwrap();
        assert_eq!(counts.requests, 1);
        assert_eq!(counts.responses, 0);
        for end in 0..payload.len() {
            assert!(PingCounts::from_packet(&payload[..end]).is_err());
        }
        payload.push(9);
        assert!(PingCounts::from_packet(&payload).is_err());
        let mut state = ReceivedWorld::new(42).unwrap();
        state.pings = counts;
        assert_eq!(state.summary()["application_ping_replies_sent"], 0);
        assert_eq!(
            state.summary()["application_ping_missing_providers"],
            json!(["rust_session_integrity"])
        );
    }
    #[test]
    fn close_report_contains_only_numeric_reason_fields() {
        let mut payload = vec![1, 8];
        payload.extend(12u32.to_be_bytes());
        payload.extend(34u32.to_be_bytes());
        payload.extend(b"private peer reason");
        let result = parse_application_close(&payload, 4).unwrap();
        assert_eq!(
            result,
            json!({"application":4,"reason_class":12,"reason_code":34})
        );
        for end in 0..10 {
            assert!(parse_application_close(&payload[..end], 4).is_none());
        }
    }
    #[test]
    fn receive_failure_report_retains_channel_and_numeric_reset_code() {
        let report = receive_failure_metadata(4, 3, &TransportError::StreamReset { code: 17 });
        assert_eq!(
            report,
            json!({"application":4,"channel":3,"kind":"peer_stream_reset","code":17})
        );
        assert_eq!(
            receive_failure_metadata(6, 1, &TransportError::StreamNotConnected)["kind"],
            "stream_not_connected"
        );
    }
    #[test]
    fn invalid_session_options_fail_before_credentials_or_network() {
        let issued = crate::allocation::test_issued_allocation();
        assert!(run(&issued, 0).is_err());
        assert!(run(&issued, 3601).is_err());
    }

    #[test]
    fn live_owner_offers_implemented_range_without_changing_issued_credentials() {
        let issued = crate::allocation::test_issued_allocation();
        let transport = live_transport_allocation(&issued).unwrap();
        assert_eq!(transport.capabilities, BYTECODE_RANGE_CAPABILITY);
        assert_eq!(issued.capabilities, 0);
        assert_eq!(Some(transport.endpoint), issued.endpoint);
        assert_eq!(Some(transport.backend), issued.backend);
        assert_eq!(Some(&transport.token), issued.token());
        assert_eq!(transport.certificate_sha256, issued.certificate_sha256);
    }
}
#[cfg(test)]
mod runtime_tests {
    use super::*;
    fn engine_world() -> ReceivedWorld {
        use rove_client_core::schema::{ClassDesc, PropertyDesc, Schema};
        let classes = [
            ("Workspace", vec![]),
            ("Players", vec![]),
            ("Player", vec![0]),
            ("Part", vec![1]),
        ]
        .into_iter()
        .enumerate()
        .map(|(id, (name, properties))| ClassDesc {
            id,
            name: name.into(),
            properties,
            events: vec![],
            isr_properties: vec![],
            unknown_u16: 0,
        })
        .collect();
        let properties = vec![
            PropertyDesc {
                id: 0,
                class: 2,
                name: "UserId".into(),
                type_id: 12,
                enum_id: None,
                lr_type: None,
            },
            PropertyDesc {
                id: 1,
                class: 3,
                name: "Transparency".into(),
                type_id: 11,
                enum_id: None,
                lr_type: None,
            },
        ];
        let schema = Schema {
            version: 3,
            flags: 0,
            enums: vec![],
            classes,
            properties,
            events: vec![],
            strings: vec![],
            names: vec![],
            lr_types: vec![],
            parent_property_id: 2,
            decoded_bytes: 0,
        };
        ReceivedWorld::with_core(SessionCore::with_schema(schema, 8, 65536).unwrap(), 42).unwrap()
    }
    fn spawn_schema() -> rove_client_core::schema::Schema {
        use rove_client_core::schema::{ClassDesc, PropertyDesc, Schema};
        let classes = [
            ("Players", vec![]),
            ("Player", vec![0, 1, 2]),
            ("Workspace", vec![]),
            ("Model", vec![3]),
            ("Humanoid", vec![4]),
            ("Part", vec![5]),
        ]
        .into_iter()
        .enumerate()
        .map(|(id, (name, properties))| ClassDesc {
            id,
            name: name.into(),
            properties,
            events: vec![],
            isr_properties: vec![],
            unknown_u16: 0,
        })
        .collect();
        let properties = [
            (0, 2, "UserId", 12),
            (1, 2, "Character", 12),
            (2, 2, "GameplayPaused", 9),
            (3, 3, "PrimaryPart", 12),
            (4, 4, "Health", 11),
            (5, 5, "CFrame", 27),
        ]
        .into_iter()
        .map(|(id, class, name, type_id)| PropertyDesc {
            id,
            class,
            name: name.into(),
            type_id,
            enum_id: None,
            lr_type: None,
        })
        .collect();
        Schema {
            version: 3,
            flags: 0,
            enums: vec![],
            classes,
            properties,
            events: vec![],
            strings: vec![],
            names: vec![],
            lr_types: vec![],
            parent_property_id: 6,
            decoded_bytes: 0,
        }
    }

    fn spawn_snapshot() -> Value {
        json!({"version":1,"sequence":0,"nodes":[
            {"id":"1:1","class_name":"Players","parent":null,"properties":{}},
            {"id":"1:2","class_name":"Player","parent":"1:1","properties":{"UserId":42,"Character":{"$ref":"1:3"},"GameplayPaused":true}},
            {"id":"1:3","class_name":"Model","parent":"1:4","properties":{"PrimaryPart":{"$ref":"1:6"}}},
            {"id":"1:4","class_name":"Workspace","parent":null,"properties":{}},
            {"id":"1:5","class_name":"Humanoid","parent":"1:3","properties":{"Health":100}},
            {"id":"1:6","class_name":"Part","name":"HumanoidRootPart","parent":"1:3","properties":{"CFrame":{"position":[64,128,-64]}}}
        ]})
    }

    #[test]
    fn spawn_and_region_evidence_gate_the_native_resume_packet() {
        let schema = spawn_schema();
        let world = rove_client_core::world::WorldStore::from_snapshot(&spawn_snapshot()).unwrap();
        let regions = vec![json!({"coordinate":[1,2,-1],"radius":1,"version":1})];
        let evidence = evaluate_spawn_evidence(&schema, &world.view(), "1:2".into(), &regions);
        assert!(evidence.player_spawned);
        assert!(evidence.spawn_region_received);
        assert_eq!(evidence.pause_property_id, Some(2));
        let packet = streaming_ready(
            (1, 2),
            2,
            9,
            StreamingEvidence {
                player_spawned: evidence.player_spawned,
                spawn_region_received: evidence.spawn_region_received,
            },
        )
        .unwrap();
        assert_eq!(packet, [131, 3, 1, 2, 0, 0, 0, 2, 0, 0, 0, 0]);

        let uncovered = evaluate_spawn_evidence(&schema, &world.view(), "1:2".into(), &[]);
        assert!(uncovered.player_spawned);
        assert!(!uncovered.spawn_region_received);
        assert!(streaming_ready(
            (1, 2),
            2,
            9,
            StreamingEvidence {
                player_spawned: uncovered.player_spawned,
                spawn_region_received: uncovered.spawn_region_received,
            },
        )
        .is_err());

        let mut missing_humanoid = spawn_snapshot();
        missing_humanoid["nodes"]
            .as_array_mut()
            .unwrap()
            .retain(|node| node["id"] != "1:5");
        let world = rove_client_core::world::WorldStore::from_snapshot(&missing_humanoid).unwrap();
        let evidence = evaluate_spawn_evidence(&schema, &world.view(), "1:2".into(), &regions);
        assert!(!evidence.player_spawned);
    }

    fn fixture_globals() -> Vec<u8> {
        let mut packet = vec![129, 0, 0, 0, 0, 0, 0, 0, 1, 2];
        for (id, class) in [(1u32, 0u16), (2, 1)] {
            packet.push(1);
            packet.extend(id.to_le_bytes());
            packet.extend(class.to_le_bytes());
            packet.extend([0, 0]);
        }
        packet
    }
    fn fixture_create(id: u32, class: u16, property: &[u8], parent: u32) -> Vec<u8> {
        let mut packet = vec![131, 2, 1];
        packet.extend(id.to_le_bytes());
        packet.extend(class.to_le_bytes());
        packet.push(0);
        packet.extend(property);
        packet.extend([255, 255]);
        packet.push(1);
        packet.extend(parent.to_le_bytes());
        packet.push(0);
        packet
    }
    fn ack_world() -> ReceivedWorld {
        use rove_client_core::schema::{ClassDesc, PropertyDesc, Schema};
        let schema = Schema {
            version: 3,
            flags: 0,
            enums: vec![],
            classes: vec![ClassDesc {
                id: 0,
                name: "Part".into(),
                properties: vec![0],
                events: vec![],
                isr_properties: vec![],
                unknown_u16: 0,
            }],
            properties: vec![PropertyDesc {
                id: 0,
                class: 0,
                name: "CFrame".into(),
                type_id: 27,
                enum_id: None,
                lr_type: None,
            }],
            events: vec![],
            strings: vec![],
            names: vec![],
            lr_types: vec![],
            parent_property_id: 1,
            decoded_bytes: 0,
        };
        ReceivedWorld::with_core(SessionCore::with_schema(schema, 8, 65536).unwrap(), 42).unwrap()
    }

    fn cframe_set(id: u32, reset: bool) -> Vec<u8> {
        let mut packet = vec![131, 3, 1];
        packet.extend(id.to_le_bytes());
        packet.extend(0u16.to_le_bytes());
        packet.push(u8::from(reset));
        packet.extend(1.0f32.to_le_bytes());
        packet.extend(2.0f32.to_le_bytes());
        packet.extend(3.0f32.to_le_bytes());
        packet.push(2);
        packet.push(0);
        packet
    }

    #[test]
    fn cframe_receipts_prepare_and_commit_native_acknowledgements() {
        let mut world = ack_world();
        world.ingest(&[129, 0, 0, 0, 0, 0, 0, 0, 1, 0]).unwrap();
        let mut create = vec![131, 2, 1];
        create.extend(7u32.to_le_bytes());
        create.extend(0u16.to_le_bytes());
        create.push(0);
        create.extend(1.0f32.to_le_bytes());
        create.extend(2.0f32.to_le_bytes());
        create.extend(3.0f32.to_le_bytes());
        create.push(2);
        create.extend([255, 255, 0, 0]);
        world.ingest(&create).unwrap();
        assert!(world.ready_property_ack.take().is_none());

        world.ingest_at(&cframe_set(7, false), 100).unwrap();
        let first = world.ready_property_ack.take().unwrap();
        assert_eq!(
            first.payload(),
            rove_client_core::property_acknowledgments::encode_property_ack((1, 7), 0, 1, 24)
                .unwrap()
                .as_slice()
        );
        world.property_acks.commit(first.id(), true).unwrap();

        world.ingest_at(&cframe_set(7, false), 200).unwrap();
        let second = world.ready_property_ack.take().unwrap();
        assert_eq!(
            second.payload(),
            rove_client_core::property_acknowledgments::encode_property_ack((1, 7), 0, 2, 48)
                .unwrap()
                .as_slice()
        );
        world.property_acks.commit(second.id(), true).unwrap();
        assert_eq!(world.property_acks.report()["replies_sent"], 2);
        assert_eq!(world.property_acks.report()["pending"], 0);
        assert_eq!(world.simulation_clock.step, 48);
    }

    #[test]
    fn remote_callback_reply_is_encoded_after_an_idle_poll() {
        use rove_client_core::schema::{ClassDesc, EventArgument, EventDesc, PropertyDesc, Schema};
        let argument = |type_id| EventArgument {
            type_id,
            enum_id: 0,
        };
        let schema = Schema {
            version: 3,
            flags: 0,
            enums: vec![],
            classes: vec![
                ClassDesc {
                    id: 0,
                    name: "Players".into(),
                    properties: vec![],
                    events: vec![],
                    isr_properties: vec![],
                    unknown_u16: 0,
                },
                ClassDesc {
                    id: 1,
                    name: "Player".into(),
                    properties: vec![0],
                    events: vec![],
                    isr_properties: vec![],
                    unknown_u16: 0,
                },
                ClassDesc {
                    id: 2,
                    name: "RemoteFunction".into(),
                    properties: vec![],
                    events: vec![0, 1, 2, 5],
                    isr_properties: vec![],
                    unknown_u16: 0,
                },
                ClassDesc {
                    id: 3,
                    name: "RemoteEvent".into(),
                    properties: vec![],
                    events: vec![3],
                    isr_properties: vec![],
                    unknown_u16: 0,
                },
                ClassDesc {
                    id: 4,
                    name: "UnreliableRemoteEvent".into(),
                    properties: vec![],
                    events: vec![4],
                    isr_properties: vec![],
                    unknown_u16: 0,
                },
                ClassDesc {
                    id: 5,
                    name: "ProximityPrompt".into(),
                    properties: vec![],
                    events: vec![6],
                    isr_properties: vec![],
                    unknown_u16: 0,
                },
                ClassDesc {
                    id: 6,
                    name: "Part".into(),
                    properties: vec![1],
                    events: vec![],
                    isr_properties: vec![],
                    unknown_u16: 0,
                },
            ],
            properties: vec![
                PropertyDesc {
                    id: 0,
                    class: 1,
                    name: "UserId".into(),
                    type_id: 12,
                    enum_id: None,
                    lr_type: None,
                },
                PropertyDesc {
                    id: 1,
                    class: 6,
                    name: "NetworkOwnerV3".into(),
                    type_id: 12,
                    enum_id: None,
                    lr_type: None,
                },
            ],
            events: vec![
                EventDesc {
                    id: 0,
                    class: 2,
                    name: "RemoteOnInvokeClient".into(),
                    arguments: vec![argument(10), argument(29)],
                },
                EventDesc {
                    id: 1,
                    class: 2,
                    name: "RemoteOnInvokeSuccess".into(),
                    arguments: vec![argument(10), argument(29)],
                },
                EventDesc {
                    id: 2,
                    class: 2,
                    name: "RemoteOnInvokeError".into(),
                    arguments: vec![argument(10), argument(2)],
                },
                EventDesc {
                    id: 3,
                    class: 3,
                    name: "OnRemoteServerEvent".into(),
                    arguments: vec![argument(29)],
                },
                EventDesc {
                    id: 4,
                    class: 4,
                    name: "OnRemoteServerEvent".into(),
                    arguments: vec![argument(29)],
                },
                EventDesc {
                    id: 5,
                    class: 2,
                    name: "RemoteOnInvokeServer".into(),
                    arguments: vec![argument(10), argument(28), argument(29)],
                },
                EventDesc {
                    id: 6,
                    class: 5,
                    name: "TriggeredActionReplicated".into(),
                    arguments: vec![argument(69)],
                },
            ],
            strings: vec![],
            names: vec![],
            lr_types: vec![],
            parent_property_id: 1,
            decoded_bytes: 0,
        };
        let mut world =
            ReceivedWorld::with_core(SessionCore::with_schema(schema, 8, 65536).unwrap(), 42)
                .unwrap();
        world.engine = Some(
            LiveRuntime::new(
                RuntimeConfig::owned_fixture(
                    "local p=game:GetService('Players'); local r=p:FindFirstChildWhichIsA('RemoteFunction'); task.spawn(function() shared.serverResult=table.pack(pcall(function() return r:InvokeServer(5) end)) end); r.OnClientInvoke=function(x,frame) task.wait(.005); if x==45 then local v=shared.serverError; if v then return v[1],v[2] end; return false,'pending' end; if x==44 then task.spawn(function() shared.serverError=table.pack(pcall(function() return r:InvokeServer('fail') end)) end); return 'started' end; if x==43 then return frame end; if x==42 then local v=shared.serverResult; if v then return v[1],v[2],v[3],v[4] end; return false,'pending' end; if x==0 then error('expected failure') end; return x+1 end; p:FindFirstChildWhichIsA('RemoteEvent'):FireServer('hello',2); p:FindFirstChildWhichIsA('UnreliableRemoteEvent'):FireServer(true); fireproximityprompt(p:FindFirstChildWhichIsA('ProximityPrompt')); local parts={}; for _,child in p:GetChildren() do if child:IsA('Part') then table.insert(parts,child) end end; firetouchinterest(parts[1],parts[2],true); firetouchinterest(parts[1],parts[2],false); return true",
                ),
                "remote-reply-test".into(),
            )
            .unwrap(),
        );
        world
            .ingest(&[129, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0])
            .unwrap();
        world
            .ingest(&fixture_create(2, 1, &42f64.to_le_bytes(), 1))
            .unwrap();
        world
            .ingest(&[131, 2, 1, 3, 0, 0, 0, 2, 0, 255, 255, 1, 1, 0, 0, 0, 0])
            .unwrap();
        world
            .ingest(&[131, 2, 1, 4, 0, 0, 0, 3, 0, 255, 255, 1, 1, 0, 0, 0, 0])
            .unwrap();
        world
            .ingest(&[131, 2, 1, 5, 0, 0, 0, 4, 0, 255, 255, 1, 1, 0, 0, 0, 0])
            .unwrap();
        world
            .ingest(&[131, 2, 1, 6, 0, 0, 0, 5, 0, 255, 255, 1, 1, 0, 0, 0, 0])
            .unwrap();
        world
            .ingest(&fixture_create(7, 6, &1f64.to_le_bytes(), 1))
            .unwrap();
        world
            .ingest(&fixture_create(8, 6, &1f64.to_le_bytes(), 1))
            .unwrap();
        world.ingest(&[131, 16, 13, 0, 0, 0, 0]).unwrap();

        let mut request = vec![131, 7, 1, 3, 0, 0, 0, 0, 0, 82, 1, 12];
        request.extend(2f64.to_le_bytes());
        request.push(0);
        world.ingest(&request).unwrap();
        for _ in 0..100 {
            world.flush_rpc_outbound().unwrap();
            if world
                .ready_rpc_outbound
                .iter()
                .any(|packet| packet.kind == RpcOutboundKind::ClientReply)
                && world
                    .ready_rpc_outbound
                    .iter()
                    .any(|packet| packet.kind == RpcOutboundKind::ServerCall)
            {
                break;
            }
            std::thread::sleep(Duration::from_millis(2));
        }
        let mut expected = vec![131, 7, 1, 3, 0, 0, 0, 1, 0, 82, 1, 12];
        expected.extend(3f64.to_le_bytes());
        expected.push(0);
        let replies = world
            .ready_rpc_outbound
            .iter()
            .filter(|packet| packet.kind == RpcOutboundKind::ClientReply)
            .collect::<Vec<_>>();
        assert_eq!(
            replies,
            vec![&EncodedRpcPacket {
                channel: 1,
                kind: RpcOutboundKind::ClientReply,
                payload: expected,
            }]
        );
        let reliable = world
            .ready_rpc_outbound
            .iter()
            .find(|packet| {
                packet.kind == RpcOutboundKind::ServerEvent
                    && packet.channel == 1
                    && packet.payload.get(3) == Some(&4)
            })
            .unwrap();
        let mut expected = vec![131, 7, 1, 4, 0, 0, 0, 3, 0, 2, 2, 5];
        expected.extend(b"hello");
        expected.push(12);
        expected.extend(2f64.to_le_bytes());
        expected.push(0);
        assert_eq!(reliable.payload, expected);
        let unreliable = world
            .ready_rpc_outbound
            .iter()
            .find(|packet| packet.kind == RpcOutboundKind::ServerEvent && packet.channel == 3)
            .unwrap();
        assert_eq!(
            unreliable.payload,
            vec![131, 7, 1, 5, 0, 0, 0, 4, 0, 1, 9, 1, 0]
        );
        let reflected = world
            .ready_rpc_outbound
            .iter()
            .find(|packet| {
                packet.kind == RpcOutboundKind::ServerEvent && packet.payload.get(3) == Some(&6)
            })
            .unwrap();
        assert_eq!(
            reflected.payload,
            vec![131, 7, 1, 6, 0, 0, 0, 6, 0, 1, 2, 0, 0, 0, 0]
        );
        let touch = world
            .ready_rpc_outbound
            .iter()
            .find(|packet| packet.kind == RpcOutboundKind::TouchContact)
            .unwrap();
        assert_eq!(touch.channel, 7);
        assert_eq!(
            touch.payload,
            vec![134, 1, 7, 0, 0, 0, 1, 8, 0, 0, 0, 0, 1, 7, 0, 0, 0, 1, 8, 0, 0, 0, 3, 0]
        );
        let call = world
            .ready_rpc_outbound
            .iter()
            .find(|packet| packet.kind == RpcOutboundKind::ServerCall)
            .unwrap();
        let mut expected = vec![131, 7, 1, 3, 0, 0, 0, 5, 0, 2, 1, 2, 0, 0, 0, 1, 12];
        expected.extend(5f64.to_le_bytes());
        expected.push(0);
        assert_eq!(call.payload, expected);
        assert_eq!(world.ready_rpc_outbound.len(), 6);
        assert_eq!(world.rpc.as_ref().unwrap().pending_len(), 1);
        world.flush_rpc_outbound().unwrap();
        assert_eq!(world.ready_rpc_outbound.len(), 6);

        world.ready_rpc_outbound.clear();
        let mut response = vec![131, 7, 1, 3, 0, 0, 0, 1, 0, 2, 3, 2, 6];
        response.extend(b"server");
        response.extend([30, 1, 12]);
        response.extend(7f64.to_le_bytes());
        response.extend([0, 0]);
        world.ingest(&response).unwrap();
        for _ in 0..100 {
            world.flush_rpc_outbound().unwrap();
            if world
                .engine
                .as_mut()
                .unwrap()
                .active_remote_server_requests()
                .unwrap()
                .is_empty()
            {
                break;
            }
            std::thread::sleep(Duration::from_millis(2));
        }
        assert!(world
            .engine
            .as_mut()
            .unwrap()
            .active_remote_server_requests()
            .unwrap()
            .is_empty());

        let mut query = vec![131, 7, 1, 3, 0, 0, 0, 0, 0, 86, 1, 12];
        query.extend(42f64.to_le_bytes());
        query.push(0);
        world.ingest(&query).unwrap();
        for _ in 0..100 {
            world.flush_rpc_outbound().unwrap();
            if !world.ready_rpc_outbound.is_empty() {
                break;
            }
            std::thread::sleep(Duration::from_millis(2));
        }
        let mut expected = vec![131, 7, 1, 3, 0, 0, 0, 1, 0, 86, 4, 9, 1, 2, 6];
        expected.extend(b"server");
        expected.extend([30, 1, 12]);
        expected.extend(7f64.to_le_bytes());
        expected.extend([0, 0]);
        assert_eq!(
            world.ready_rpc_outbound,
            vec![EncodedRpcPacket {
                channel: 1,
                kind: RpcOutboundKind::ClientReply,
                payload: expected,
            }]
        );
        assert_eq!(world.rpc.as_ref().unwrap().pending_len(), 0);

        world.ready_rpc_outbound.clear();
        let mut start_error = vec![131, 7, 1, 3, 0, 0, 0, 0, 0, 88, 1, 12];
        start_error.extend(44f64.to_le_bytes());
        start_error.push(0);
        world.ingest(&start_error).unwrap();
        for _ in 0..100 {
            world.flush_rpc_outbound().unwrap();
            if world
                .ready_rpc_outbound
                .iter()
                .any(|packet| packet.kind == RpcOutboundKind::ClientReply)
                && world
                    .ready_rpc_outbound
                    .iter()
                    .any(|packet| packet.kind == RpcOutboundKind::ServerCall)
            {
                break;
            }
            std::thread::sleep(Duration::from_millis(2));
        }
        let call = world
            .ready_rpc_outbound
            .iter()
            .find(|packet| packet.kind == RpcOutboundKind::ServerCall)
            .unwrap();
        assert_eq!(
            call.payload,
            vec![131, 7, 1, 3, 0, 0, 0, 5, 0, 4, 1, 2, 0, 0, 0, 1, 2, 4, b'f', b'a', b'i', b'l', 0]
        );
        let reply = world
            .ready_rpc_outbound
            .iter()
            .find(|packet| packet.kind == RpcOutboundKind::ClientReply)
            .unwrap();
        assert_eq!(
            reply.payload,
            vec![
                131, 7, 1, 3, 0, 0, 0, 1, 0, 88, 1, 2, 7, b's', b't', b'a', b'r', b't', b'e', b'd',
                0
            ]
        );

        world.ready_rpc_outbound.clear();
        world
            .ingest(&[131, 7, 1, 3, 0, 0, 0, 1, 0, 2, 0, 0])
            .unwrap();
        assert!(world.ready_rpc_outbound.is_empty());
        assert_eq!(world.summary()["remote_server_results_received"], 1);

        let mut server_error = vec![131, 7, 1, 3, 0, 0, 0, 2, 0, 4, 6];
        server_error.extend(b"denied");
        server_error.push(0);
        world.ingest(&server_error).unwrap();
        for _ in 0..100 {
            world.flush_rpc_outbound().unwrap();
            if world
                .engine
                .as_mut()
                .unwrap()
                .active_remote_server_requests()
                .unwrap()
                .is_empty()
            {
                break;
            }
            std::thread::sleep(Duration::from_millis(2));
        }
        assert!(world
            .engine
            .as_mut()
            .unwrap()
            .active_remote_server_requests()
            .unwrap()
            .is_empty());

        let mut query_error = vec![131, 7, 1, 3, 0, 0, 0, 0, 0, 90, 1, 12];
        query_error.extend(45f64.to_le_bytes());
        query_error.push(0);
        world.ingest(&query_error).unwrap();
        for _ in 0..100 {
            world.flush_rpc_outbound().unwrap();
            if !world.ready_rpc_outbound.is_empty() {
                break;
            }
            std::thread::sleep(Duration::from_millis(2));
        }
        let mut expected = vec![131, 7, 1, 3, 0, 0, 0, 1, 0, 90, 2, 9, 0, 2, 6];
        expected.extend(b"denied");
        expected.push(0);
        assert_eq!(
            world.ready_rpc_outbound,
            vec![EncodedRpcPacket {
                channel: 1,
                kind: RpcOutboundKind::ClientReply,
                payload: expected,
            }]
        );
        assert_eq!(world.summary()["remote_server_results_received"], 2);
        assert_eq!(world.summary()["remote_server_errors_received"], 1);
        assert_eq!(world.rpc.as_ref().unwrap().pending_len(), 0);

        world.ready_rpc_outbound.clear();
        let mut request = vec![131, 7, 1, 3, 0, 0, 0, 0, 0, 13, 1, 12];
        request.extend(0f64.to_le_bytes());
        request.push(0);
        world.ingest(&request).unwrap();
        for _ in 0..100 {
            world.flush_rpc_outbound().unwrap();
            if !world.ready_rpc_outbound.is_empty() {
                break;
            }
            std::thread::sleep(Duration::from_millis(2));
        }
        let mut expected = vec![131, 7, 1, 3, 0, 0, 0, 2, 0, 13, 37];
        expected.extend(b"persistent-source:1: expected failure");
        expected.push(0);
        assert_eq!(
            world.ready_rpc_outbound,
            vec![EncodedRpcPacket {
                channel: 1,
                kind: RpcOutboundKind::ClientReply,
                payload: expected,
            }]
        );
        assert_eq!(world.rpc.as_ref().unwrap().pending_len(), 0);

        world.ready_rpc_outbound.clear();
        let mut request = vec![131, 7, 1, 3, 0, 0, 0, 0, 0, 88, 2, 12];
        request.extend(43f64.to_le_bytes());
        request.push(27);
        for component in [1f32, 2.0, 3.0] {
            request.extend(component.to_le_bytes());
        }
        request.extend([17, 0]);
        world.ingest(&request).unwrap();
        for _ in 0..100 {
            world.flush_rpc_outbound().unwrap();
            if !world.ready_rpc_outbound.is_empty() {
                break;
            }
            std::thread::sleep(Duration::from_millis(2));
        }
        let mut expected = vec![131, 7, 1, 3, 0, 0, 0, 1, 0, 88, 1, 27];
        for component in [1f32, 2.0, 3.0] {
            expected.extend(component.to_le_bytes());
        }
        expected.extend([17, 0]);
        assert_eq!(
            world.ready_rpc_outbound,
            vec![EncodedRpcPacket {
                channel: 1,
                kind: RpcOutboundKind::ClientReply,
                payload: expected,
            }]
        );
        let report = world.finish_engine().unwrap();
        assert_eq!(report["ok"], true, "{report}");
    }

    #[test]
    fn live_world_drives_one_persistent_vm_with_late_local_player_and_ordered_events() {
        let mut world = engine_world();
        let source = r#"
            assert(game:IsLoaded())
            local player=game:GetService('Players').LocalPlayer
            assert(player and player.UserId==42)
            local part=workspace.ChildAdded:Wait()
            assert(part:IsA('Part') and part.Transparency==0.25)
            local localPart=Instance.new('Part'); localPart.Parent=workspace
            assert(localPart:IsDescendantOf(game)); localPart:Destroy()
            return player.UserId,part.Transparency,localPart.Parent==nil
        "#;
        world.engine = Some(
            LiveRuntime::new(
                RuntimeConfig::owned_fixture(source),
                "owned-test-session".into(),
            )
            .unwrap(),
        );
        world.ingest(&fixture_globals()).unwrap();
        world
            .ingest(&fixture_create(3, 2, &42f64.to_le_bytes(), 2))
            .unwrap();
        assert_eq!(world.local_player().unwrap().as_deref(), Some("1:3"));
        world.ingest(&[131, 16, 13, 0, 0, 0, 0]).unwrap();
        world
            .ingest(&fixture_create(4, 3, &0.25f32.to_le_bytes(), 1))
            .unwrap();
        let report = world.finish_engine().unwrap();
        assert_eq!(report["ok"], true, "{report}");
        assert_eq!(report["snapshot_initializations"], 1);
        assert_eq!(report["unapplied_queue_records"], 0);
        assert_eq!(report["source_completed_successfully"], true, "{report}");
        assert_eq!(report["script_result"]["values"], json!([42.0, 0.25, true]));
        assert_eq!(world.core.journal_stats().records, 0);
        assert_eq!(world.core.world_view().unwrap().node_count(), 4);
    }
    #[test]
    fn session_player_resolution_is_unique_and_uses_received_identity() {
        let mut world = engine_world();
        world.ingest(&fixture_globals()).unwrap();
        world
            .ingest(&fixture_create(3, 2, &41f64.to_le_bytes(), 2))
            .unwrap();
        assert_eq!(world.local_player().unwrap(), None);
        world
            .ingest(&fixture_create(4, 2, &42f64.to_le_bytes(), 2))
            .unwrap();
        assert_eq!(world.local_player().unwrap().as_deref(), Some("1:4"));
        world
            .ingest(&fixture_create(5, 2, &42f64.to_le_bytes(), 2))
            .unwrap();
        assert_eq!(
            world.local_player().unwrap_err(),
            "ambiguous-session-player"
        );
    }

    #[test]
    fn authored_script_errors_do_not_stop_the_live_world_consumer() {
        for source in ["error('owned script failure')", "local = syntax_error"] {
            let mut world = engine_world();
            world.engine = Some(
                LiveRuntime::new(RuntimeConfig::owned_fixture(source), "owned-session".into())
                    .unwrap(),
            );
            world.ingest(&fixture_globals()).unwrap();
            world
                .ingest(&fixture_create(3, 2, &42f64.to_le_bytes(), 2))
                .unwrap();
            world.ingest(&[131, 16, 13, 0, 0, 0, 0]).unwrap();
            world
                .ingest(&fixture_create(4, 3, &0.25f32.to_le_bytes(), 1))
                .unwrap();
            let report = world.finish_engine().unwrap();
            assert_eq!(report["ok"], true, "{report}");
            assert_eq!(report["script_result"]["ok"], false, "{report}");
            assert_eq!(report["source_completed_successfully"], false);
            assert_eq!(report["updates_applied"], 4);
            assert_eq!(report["last_applied_sequence"], 4);
            assert_eq!(report["unapplied_queue_records"], 0);
        }
    }
}
