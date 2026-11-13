//! Bounded, session-scoped ordinary RemoteFunction/RemoteEvent bridge.
//!
//! The bridge accepts only received instances and received callback events.
//! It does not infer a response from a send attempt: a request is complete only
//! after a correlated replication event, or after an explicit local failure.

use crate::{
    replication_out::{guid, Guid},
    schema::{EventDesc, Schema},
    session::SessionRecord,
    wire::WireError,
    world::WorldView,
};
use serde_json::Value;
use std::collections::{HashMap, HashSet, VecDeque};

const MAX_PENDING: usize = 512;
const MAX_QUEUE: usize = 512;
const MAX_ARGS: usize = 256;
const MAX_DEPTH: usize = 16;
const MAX_VALUE_ITEMS: usize = 10_000;
const MAX_TUPLE: usize = 262_144;
const MAX_REMOTE_CALL: usize = 65_536;

#[derive(Debug, Clone, PartialEq)]
pub enum RpcError {
    Invalid(String),
    WrongSession,
    QueueFull,
    PendingLimit,
    JournalOverrun,
    Transport(String),
}

impl std::fmt::Display for RpcError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Invalid(s) | Self::Transport(s) => f.write_str(s),
            Self::WrongSession => f.write_str("RPC operation belongs to another session"),
            Self::QueueFull => f.write_str("RPC outbound queue limit exceeded"),
            Self::PendingLimit => f.write_str("RPC pending request limit exceeded"),
            Self::JournalOverrun => f.write_str("RPC event journal was overrun"),
        }
    }
}

impl std::error::Error for RpcError {}
impl From<WireError> for RpcError {
    fn from(value: WireError) -> Self {
        Self::Invalid(value.to_string())
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct RemoteResult {
    pub node: String,
    pub request_id: u64,
    pub ok: bool,
    pub values: Option<Vec<Value>>,
    pub error: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ServerCall {
    pub node: String,
    pub request_id: u64,
    pub arguments: Vec<Value>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ClientReply {
    pub node: String,
    pub request_id: i32,
    pub ok: bool,
    pub values: Vec<Value>,
    pub error: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ServerEvent {
    pub node: String,
    pub arguments: Vec<Value>,
    pub unreliable: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ReflectedSignal {
    pub node: String,
    pub name: String,
    pub argument_types: Vec<u8>,
    pub arguments: Vec<Value>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RpcOutboundKind {
    ServerCall,
    ClientReply,
    ServerEvent,
    TouchContact,
}

/// Transport acceptance is only local queue/socket acceptance, never server
/// receipt. A failing send leaves the correlation entry available for retry or
/// caller teardown as appropriate.
pub trait RpcTransport {
    fn send(&mut self, channel: u8, payload: &[u8]) -> Result<(), String>;
    fn send_with_kind(
        &mut self,
        channel: u8,
        payload: &[u8],
        _kind: RpcOutboundKind,
    ) -> Result<(), String> {
        self.send(channel, payload)
    }
}

/// Encode an ordinary received RemoteEvent call. Unreliable events use channel
/// 3 and are dropped if their encoded application body exceeds 1000 bytes.
pub fn encode_server_event(
    schema: &Schema,
    view: &WorldView,
    event: &ServerEvent,
) -> Result<(u8, Vec<u8>), RpcError> {
    if !view.contains(&event.node) {
        return Err(RpcError::Invalid(
            "RemoteEvent receiver is not present in this session".into(),
        ));
    }
    let class = view.class_name(&event.node).unwrap_or_default();
    if class != "RemoteEvent" && class != "UnreliableRemoteEvent" {
        return Err(RpcError::Invalid(
            "RemoteEvent receiver is not present in this session".into(),
        ));
    }
    let unreliable = class == "UnreliableRemoteEvent";
    if event.unreliable != unreliable {
        return Err(RpcError::Invalid(
            "RemoteEvent reliability does not match its received class".into(),
        ));
    }
    if event.arguments.len() > MAX_ARGS {
        return Err(RpcError::Invalid("Invalid remote event tuple".into()));
    }
    let descriptor = find_event(schema, class, "OnRemoteServerEvent", &[29])?;
    let mut body = varuint(event.arguments.len() as u64);
    let mut budget = MAX_VALUE_ITEMS;
    for value in &event.arguments {
        body.extend(variant(value, view, 0, &mut budget)?);
    }
    if body.len() > MAX_REMOTE_CALL {
        return Err(RpcError::Invalid("Remote event tuple exceeds limit".into()));
    }
    if unreliable && body.len() > 1000 {
        return Err(RpcError::Invalid(
            "UnreliableRemoteEvent payload exceeds 1000 bytes; dropped".into(),
        ));
    }
    let mut payload = vec![0x83, 0x07];
    payload.extend(guid(Some(parse_guid(&event.node)?))?);
    payload.extend((descriptor as u16).to_le_bytes());
    payload.extend(body);
    payload.push(0);
    Ok((if unreliable { 3 } else { 1 }, payload))
}

/// Encode an ordinary reflected signal using only a matching descriptor in
/// the received schema/class ancestry. `class_parents` is the current engine
/// class-parent table supplied by the runtime.
pub fn encode_reflected_signal(
    schema: &Schema,
    view: &WorldView,
    signal: &ReflectedSignal,
    class_parents: &HashMap<String, String>,
) -> Result<Vec<u8>, RpcError> {
    if !view.contains(&signal.node) {
        return Err(RpcError::Invalid(
            "Signal receiver is not present in this session".into(),
        ));
    }
    if signal.name.is_empty()
        || signal.arguments.len() != signal.argument_types.len()
        || signal.arguments.len() > MAX_ARGS
    {
        return Err(RpcError::Invalid(
            "Invalid reflected signal arguments".into(),
        ));
    }
    let target_class = view.class_name(&signal.node).ok_or_else(|| {
        RpcError::Invalid("Signal receiver is not present in this session".into())
    })?;
    let mut ancestry = HashSet::new();
    let mut c = target_class;
    loop {
        if !ancestry.insert(c.to_owned()) {
            return Err(RpcError::Invalid(
                "Received class ancestry contains a cycle".into(),
            ));
        }
        match class_parents.get(c) {
            Some(p) => c = p,
            None => break,
        }
    }
    let matches: Vec<_> = schema
        .events
        .iter()
        .filter(|e| {
            e.name == signal.name
                && schema
                    .classes
                    .get(e.class)
                    .is_some_and(|owner| owner.events.contains(&e.id))
                && ancestry.contains(
                    schema
                        .classes
                        .get(e.class)
                        .map(|c| c.name.as_str())
                        .unwrap_or(""),
                )
                && e.arguments
                    .iter()
                    .map(|a| a.type_id)
                    .eq(signal.argument_types.iter().copied())
        })
        .collect();
    if matches.len() != 1 || matches[0].id > u16::MAX as usize {
        return Err(RpcError::Invalid(
            "Missing or ambiguous received reflected signal signature".into(),
        ));
    }
    let mut body = Vec::new();
    for (ty, value) in signal.argument_types.iter().zip(&signal.arguments) {
        body.extend(typed_signal_value(*ty, value, view)?);
        if body.len() > MAX_REMOTE_CALL {
            return Err(RpcError::Invalid(
                "Reflected signal exceeds wire limit".into(),
            ));
        }
    }
    let mut payload = vec![0x83, 0x07];
    payload.extend(guid(Some(parse_guid(&signal.node)?))?);
    payload.extend((matches[0].id as u16).to_le_bytes());
    payload.extend(body);
    payload.push(0);
    Ok(payload)
}

fn typed_signal_value(type_id: u8, value: &Value, view: &WorldView) -> Result<Vec<u8>, RpcError> {
    match type_id {
        0 | 3 if value.is_null() => Ok(Vec::new()),
        0 | 3 => Err(RpcError::Invalid("Signal argument must be nil".into())),
        2 | 8 | 33 => {
            let s = value
                .as_str()
                .ok_or_else(|| RpcError::Invalid("Expected remote string".into()))?;
            string_bytes(s.as_bytes())
        }
        7 => {
            let n = value
                .get("$enum")
                .and_then(|v| v.get("value"))
                .unwrap_or(value)
                .as_u64()
                .filter(|n| *n <= u32::MAX as u64)
                .ok_or_else(|| {
                    RpcError::Invalid("Signal enum must be an unsigned integer".into())
                })?;
            Ok(varuint(n))
        }
        9 => value
            .as_bool()
            .map(|b| vec![u8::from(b)])
            .ok_or_else(|| RpcError::Invalid("Signal argument must be a boolean".into())),
        10 => {
            let n = exact_integer(value, i32::MIN as i64, i32::MAX as i64)?;
            signed(n)
        }
        11 | 12 => {
            let n = value.as_f64().filter(|n| n.is_finite()).ok_or_else(|| {
                RpcError::Invalid("Signal argument must be a finite number".into())
            })?;
            if type_id == 11 {
                let f = n as f32;
                if !f.is_finite() {
                    return Err(RpcError::Invalid("Signal number exceeds wire range".into()));
                }
                Ok(f.to_le_bytes().to_vec())
            } else {
                Ok(n.to_le_bytes().to_vec())
            }
        }
        _ => {
            let mut budget = MAX_VALUE_ITEMS;
            let encoded = variant(value, view, 0, &mut budget)?;
            let compatible = match type_id {
                29 => 30,
                32 => 31,
                69 => 28,
                x => x,
            };
            if encoded.first() != Some(&compatible) {
                return Err(RpcError::Invalid(
                    "Signal argument does not match received signature".into(),
                ));
            }
            Ok(encoded[1..].to_vec())
        }
    }
}

#[derive(Debug, Clone)]
struct OutboundPending {
    node: String,
    token: u64,
    success_event: u16,
    error_event: u16,
    after_sequence: u64,
}

/// Small proof retained from this session's validated globals, before later
/// instance refreshes replace transport-only metadata on the service node.
#[derive(Debug, Clone)]
pub struct AnnouncedPlayers {
    scope: u64,
    id: String,
}
impl AnnouncedPlayers {
    pub fn from_globals(scope: u64, view: &WorldView) -> Result<Option<Self>, RpcError> {
        if scope == 0 {
            return Err(RpcError::WrongSession);
        }
        let mut found = None;
        let mut ambiguous = false;
        view.for_each_node(|node| {
            if node.class_name() != "Players" {
                return;
            }
            let announced = node
                .record_field("top_container_flags")
                .is_some_and(|value| {
                    value.as_array().is_some_and(|flags| {
                        flags.len() == 2 && flags.iter().all(Value::is_boolean)
                    })
                });
            if announced {
                ambiguous |= found.is_some();
                found = Some(Self {
                    scope,
                    id: node.id().to_owned(),
                });
            }
        });
        if ambiguous {
            return Err(RpcError::Invalid(
                "Ambiguous announced Players service".into(),
            ));
        }
        Ok(found)
    }
}

/// State is bound to one application session scope and never migrated across
/// WorldViews. `local_player` must have been resolved by the current session
/// owner from received replication.
pub struct RpcSession {
    session_scope: u64,
    user_id: u64,
    local_player: Guid,
    announced_players: Option<AnnouncedPlayers>,
    capabilities: u32,
    counters: HashMap<String, u32>,
    pending: HashMap<(String, i32), OutboundPending>,
    server_calls: VecDeque<ServerCall>,
    server_events: VecDeque<ServerEvent>,
    client_pending: HashSet<(String, i32)>,
    client_replies: VecDeque<ClientReply>,
    client_reply_queued: HashSet<(String, i32)>,
    event_cursor: u64,
    drain_cursor: usize,
}

impl RpcSession {
    pub fn new(
        session_scope: u64,
        user_id: u64,
        local_player: Guid,
        capabilities: u32,
    ) -> Result<Self, RpcError> {
        if session_scope == 0 || user_id == 0 || capabilities != 0x100000 || local_player.0 == 0 {
            return Err(RpcError::Invalid(
                "Unsupported remote invocation session".into(),
            ));
        }
        Ok(Self {
            session_scope,
            user_id,
            local_player,
            announced_players: None,
            capabilities,
            counters: HashMap::new(),
            pending: HashMap::new(),
            server_calls: VecDeque::new(),
            server_events: VecDeque::new(),
            client_pending: HashSet::new(),
            client_replies: VecDeque::new(),
            client_reply_queued: HashSet::new(),
            event_cursor: 0,
            drain_cursor: 0,
        })
    }

    pub fn retain_announced_players(&mut self, proof: AnnouncedPlayers) -> Result<(), RpcError> {
        self.check_scope(proof.scope)?;
        if self
            .announced_players
            .as_ref()
            .is_some_and(|old| old.id != proof.id)
        {
            return Err(RpcError::Invalid(
                "Announced Players service changed".into(),
            ));
        }
        self.announced_players = Some(proof);
        Ok(())
    }

    fn check_scope(&self, scope: u64) -> Result<(), RpcError> {
        if scope != self.session_scope {
            return Err(RpcError::WrongSession);
        }
        Ok(())
    }

    fn bind(&mut self, scope: u64, view: &WorldView) -> Result<(), RpcError> {
        self.check_scope(scope)?;
        if !view.contains(&guid_text(self.local_player))
            || view.class_name(&guid_text(self.local_player)) != Some("Player")
        {
            return Err(RpcError::Invalid(
                "Issued Player is not present in this session".into(),
            ));
        }
        let player_id = guid_text(self.local_player);
        if view.get_property(&player_id, "UserId").and_then(|value| {
            value.as_u64().or_else(|| {
                let id = value.as_f64()?;
                (id.is_finite() && id >= 0.0 && id.fract() == 0.0 && id <= u64::MAX as f64)
                    .then_some(id as u64)
            })
        }) != Some(self.user_id)
        {
            return Err(RpcError::Invalid(
                "Issued Player UserId does not match this session".into(),
            ));
        }
        let parent = view
            .parent(&player_id)
            .ok_or_else(|| RpcError::Invalid("Issued Player is not parented to Players".into()))?;
        if view.class_name(parent) != Some("Players") {
            return Err(RpcError::Invalid(
                "Issued Player is not parented to Players".into(),
            ));
        }
        // Global services may subsequently receive a parent reference to a
        // DataModel that is not itself in the replicated instance set. Retain
        // the validated globals provenance instead of treating that reference
        // as proof this is an arbitrary nested Players instance.
        let announced_service = view
            .read_only_node(parent)
            .and_then(|node| node.record_field("top_container_flags"))
            .is_some_and(|value| {
                value
                    .as_array()
                    .is_some_and(|flags| flags.len() == 2 && flags.iter().all(Value::is_boolean))
            });
        let retained_service = self
            .announced_players
            .as_ref()
            .is_some_and(|proof| proof.scope == scope && proof.id == parent);
        if view.parent(parent).is_some() && !announced_service && !retained_service {
            return Err(RpcError::Invalid(
                "Players is not a top-level received container".into(),
            ));
        }
        Ok(())
    }

    pub fn queue_server_call(&mut self, scope: u64, call: ServerCall) -> Result<(), RpcError> {
        self.check_scope(scope)?;
        if call.request_id == 0
            || call.request_id >= (1u64 << 63)
            || call.arguments.len() > MAX_ARGS
        {
            return Err(RpcError::Invalid(
                "Invalid remote invocation tuple or token".into(),
            ));
        }
        if self.server_calls.len() >= MAX_QUEUE {
            return Err(RpcError::QueueFull);
        }
        if self
            .server_calls
            .iter()
            .any(|c| c.request_id == call.request_id)
            || self.pending.values().any(|p| p.token == call.request_id)
        {
            return Err(RpcError::Invalid(
                "Duplicate runtime invocation token".into(),
            ));
        }
        self.server_calls.push_back(call);
        Ok(())
    }

    pub fn queue_server_event(&mut self, scope: u64, event: ServerEvent) -> Result<(), RpcError> {
        self.check_scope(scope)?;
        if event.arguments.len() > MAX_ARGS {
            return Err(RpcError::Invalid("Invalid remote event tuple".into()));
        }
        if self.server_events.len() >= MAX_QUEUE {
            return Err(RpcError::QueueFull);
        }
        self.server_events.push_back(event);
        Ok(())
    }

    /// Process ordered replication events and emit only server-correlated
    /// completions. The caller supplies the active VM request tokens so jobs
    /// cancelled locally are discarded without publishing stale results.
    pub fn observe_record(
        &mut self,
        scope: u64,
        schema: &Schema,
        view: &WorldView,
        record: &SessionRecord,
        active_tokens: &HashSet<u64>,
    ) -> Result<Vec<RemoteResult>, RpcError> {
        self.bind(scope, view)?;
        let mut results = Vec::new();
        // Match the VM owner’s cancellation/removal decisions before walking
        // this receipt so a response racing a cancelled job is never emitted.
        let preflight: Vec<_> = self
            .pending
            .iter()
            .map(|(key, p)| (key.clone(), p.clone()))
            .collect();
        for (key, pending) in preflight {
            if !active_tokens.contains(&pending.token) {
                self.pending.remove(&key);
            } else if !view.contains(&pending.node) {
                results.push(failed(&pending, "RemoteFunction receiver was removed"));
                self.pending.remove(&key);
            }
        }
        for event in &record.events {
            let seq = event
                .get("sequence")
                .and_then(Value::as_u64)
                .unwrap_or(record.world_sequence);
            if seq <= self.event_cursor {
                continue;
            }
            let target = event
                .get("key")
                .or_else(|| event.get("id"))
                .and_then(Value::as_str);
            let event_id = event
                .get("event")
                .and_then(Value::as_u64)
                .and_then(|v| u16::try_from(v).ok());
            let args = event.get("arguments").and_then(Value::as_array);
            let (Some(target), Some(event_id), Some(args)) = (target, event_id, args) else {
                continue;
            };
            if args.len() != 2 {
                continue;
            }
            let Some(wire_id) = args[0].as_i64().and_then(|v| i32::try_from(v).ok()) else {
                continue;
            };
            let key = (target.to_owned(), wire_id);
            let Some(pending) = self.pending.get(&key).cloned() else {
                // A recognized callback request is accepted into a bounded
                // reply queue only after validation against schema and world.
                if event_name(schema, event_id as usize) == Some("RemoteOnInvokeClient") {
                    self.observe_client_request(schema, view, target, event_id, args)?;
                }
                continue;
            };
            if seq <= pending.after_sequence {
                continue;
            }
            if event_id == pending.success_event {
                if !view.contains(target) || view.class_name(target) != Some("RemoteFunction") {
                    results.push(failed(&pending, "RemoteFunction receiver was removed"));
                } else {
                    let tuple = args[1].as_array().ok_or_else(|| {
                        RpcError::Invalid("Invalid RemoteFunction success tuple".into())
                    })?;
                    if tuple.len() > MAX_ARGS {
                        return Err(RpcError::Invalid(
                            "Invalid RemoteFunction success tuple".into(),
                        ));
                    }
                    let values = tuple
                        .iter()
                        .map(|value| {
                            crate::runtime_delta::normalize_remote_variant(value, 0)
                                .map_err(RpcError::Invalid)
                        })
                        .collect::<Result<Vec<_>, _>>()?;
                    results.push(RemoteResult {
                        node: pending.node.clone(),
                        request_id: pending.token,
                        ok: true,
                        values: Some(values),
                        error: None,
                    });
                }
                self.pending.remove(&key);
            } else if event_id == pending.error_event {
                let message = args[1].as_str().map(str::to_owned).unwrap_or_else(|| {
                    args[1]
                        .get("$bytes_hex")
                        .and_then(Value::as_str)
                        .and_then(|s| hex_decode(s).ok())
                        .map(|b| String::from_utf8_lossy(&b).into_owned())
                        .unwrap_or_default()
                });
                results.push(failed(
                    &pending,
                    &message.chars().take(4096).collect::<String>(),
                ));
                self.pending.remove(&key);
            }
        }
        if record.world_sequence > self.event_cursor {
            self.event_cursor = record.world_sequence;
        }
        if results.len() > MAX_QUEUE {
            return Err(RpcError::QueueFull);
        }
        Ok(results)
    }

    fn observe_client_request(
        &mut self,
        schema: &Schema,
        view: &WorldView,
        target: &str,
        event_id: u16,
        args: &[Value],
    ) -> Result<(), RpcError> {
        if !view.contains(target) || view.class_name(target) != Some("RemoteFunction") {
            return Err(RpcError::Invalid("Invalid remote callback receiver".into()));
        }
        validate_event(
            schema,
            view,
            target,
            event_id,
            "RemoteOnInvokeClient",
            &[10, 29],
        )?;
        let request = args[0]
            .as_i64()
            .and_then(|v| i32::try_from(v).ok())
            .ok_or_else(|| RpcError::Invalid("Malformed remote callback request".into()))?;
        if args[1].as_array().is_none_or(|a| a.len() > MAX_ARGS) {
            return Err(RpcError::Invalid(
                "Malformed remote callback request".into(),
            ));
        }
        let key = (target.to_owned(), request);
        if self.client_pending.contains(&key) || self.client_pending.len() >= MAX_PENDING {
            return Err(RpcError::PendingLimit);
        }
        self.client_pending.insert(key);
        Ok(())
    }

    pub fn queue_client_reply(&mut self, scope: u64, reply: ClientReply) -> Result<(), RpcError> {
        self.check_scope(scope)?;
        let key = (reply.node.clone(), reply.request_id);
        if !self.client_pending.contains(&key) {
            return Err(RpcError::Invalid(
                "Remote reply has no outstanding server request".into(),
            ));
        }
        if self.client_replies.len() >= MAX_QUEUE {
            return Err(RpcError::QueueFull);
        }
        if !self.client_reply_queued.insert(key) {
            return Err(RpcError::Invalid(
                "Remote callback reply is already queued".into(),
            ));
        }
        self.client_replies.push_back(reply);
        Ok(())
    }

    /// Flush up to `limit` requests and replies. Successful transport enqueue
    /// increments sent counts returned by the caller, but does not imply the
    /// peer accepted or executed the operation.
    pub fn drain_outbound<T: RpcTransport>(
        &mut self,
        scope: u64,
        schema: &Schema,
        view: &WorldView,
        record_sequence: u64,
        transport: &mut T,
        limit: usize,
    ) -> Result<usize, RpcError> {
        self.bind(scope, view)?;
        let mut sent = 0;
        for _ in 0..limit.min(16) {
            let available = [
                !self.server_calls.is_empty(),
                !self.client_replies.is_empty(),
                !self.server_events.is_empty(),
            ];
            let Some(kind) = (0..3)
                .map(|offset| (self.drain_cursor + offset) % 3)
                .find(|k| available[*k])
            else {
                break;
            };
            self.drain_cursor = (kind + 1) % 3;
            match kind {
                0 => {
                    if let Some(call) = self.server_calls.pop_front() {
                        match self.encode_server_call(schema, view, record_sequence, &call) {
                            Ok((payload, key, pending)) => {
                                // Keep pending and reserve counter before send so an
                                // ambiguous transport result cannot reuse a wire id.
                                self.pending.insert(key.clone(), pending);
                                match transport.send_with_kind(
                                    1,
                                    &payload,
                                    RpcOutboundKind::ServerCall,
                                ) {
                                    Ok(()) => sent += 1,
                                    Err(e) => return Err(RpcError::Transport(e)),
                                }
                            }
                            Err(err) => return Err(err),
                        }
                    }
                }
                1 => {
                    if let Some(reply) = self.client_replies.front().cloned() {
                        let payload = encode_client_reply(schema, view, &reply)?;
                        transport
                            .send_with_kind(1, &payload, RpcOutboundKind::ClientReply)
                            .map_err(RpcError::Transport)?;
                        self.client_replies.pop_front();
                        let key = (reply.node, reply.request_id);
                        self.client_pending.remove(&key);
                        self.client_reply_queued.remove(&key);
                        sent += 1;
                    }
                }
                _ => {
                    if let Some(event) = self.server_events.front().cloned() {
                        let (channel, payload) = encode_server_event(schema, view, &event)?;
                        transport
                            .send_with_kind(channel, &payload, RpcOutboundKind::ServerEvent)
                            .map_err(RpcError::Transport)?;
                        self.server_events.pop_front();
                        sent += 1;
                    }
                }
            }
        }
        Ok(sent)
    }

    fn encode_server_call(
        &mut self,
        schema: &Schema,
        view: &WorldView,
        sequence: u64,
        call: &ServerCall,
    ) -> Result<(Vec<u8>, (String, i32), OutboundPending), RpcError> {
        let target = parse_guid(&call.node)?;
        if !view.contains(&call.node) || view.class_name(&call.node) != Some("RemoteFunction") {
            return Err(RpcError::Invalid(
                "RemoteFunction receiver is not present in this session".into(),
            ));
        }
        if self.pending.len() >= MAX_PENDING {
            return Err(RpcError::PendingLimit);
        }
        let descriptor = find_event(
            schema,
            view.class_name(&call.node).unwrap(),
            "RemoteOnInvokeServer",
            &[10, 28, 29],
        )?;
        let success = find_event(schema, "RemoteFunction", "RemoteOnInvokeSuccess", &[10, 29])?;
        let error = find_event(schema, "RemoteFunction", "RemoteOnInvokeError", &[10, 2])?;
        let next = self
            .counters
            .get(&call.node)
            .copied()
            .unwrap_or(0)
            .checked_add(1)
            .filter(|v| *v < (1u32 << 31))
            .ok_or_else(|| RpcError::Invalid("Remote invocation wire counter exhausted".into()))?;
        let mut body = varuint(call.arguments.len() as u64);
        let mut budget = MAX_VALUE_ITEMS;
        for value in &call.arguments {
            body.extend(variant(value, view, 0, &mut budget)?);
        }
        if body.len() > MAX_REMOTE_CALL {
            return Err(RpcError::Invalid(
                "Remote invocation tuple exceeds limit".into(),
            ));
        }
        let mut payload = vec![0x83, 0x07];
        payload.extend(guid(Some(target))?);
        payload.extend((descriptor as u16).to_le_bytes());
        payload.extend(signed(i64::from(next))?);
        payload.extend(guid(Some(self.local_player))?);
        payload.extend(body);
        payload.push(0);
        self.counters.insert(call.node.clone(), next);
        let wire = i32::try_from(next)
            .map_err(|_| RpcError::Invalid("Remote invocation counter outside int32".into()))?;
        let pending = OutboundPending {
            node: call.node.clone(),
            token: call.request_id,
            success_event: success as u16,
            error_event: error as u16,
            after_sequence: sequence,
        };
        Ok((payload, (call.node.clone(), wire), pending))
    }

    pub fn pending_len(&self) -> usize {
        self.pending.len() + self.client_pending.len()
    }
    pub fn capabilities(&self) -> u32 {
        self.capabilities
    }
}

fn failed(p: &OutboundPending, message: &str) -> RemoteResult {
    RemoteResult {
        node: p.node.clone(),
        request_id: p.token,
        ok: false,
        values: None,
        error: Some(message.to_owned()),
    }
}

fn event_name(schema: &Schema, id: usize) -> Option<&str> {
    schema.events.get(id).map(|e| e.name.as_str())
}

fn find_event(
    schema: &Schema,
    class_name: &str,
    name: &str,
    sig: &[u8],
) -> Result<usize, RpcError> {
    let class = schema
        .classes
        .iter()
        .find(|c| c.name == class_name)
        .ok_or_else(|| RpcError::Invalid("Received instance class missing from schema".into()))?;
    let matches: Vec<&EventDesc> = class
        .events
        .iter()
        .filter_map(|id| schema.events.get(*id))
        .filter(|e| e.name == name)
        .collect();
    if matches.len() != 1
        || matches[0]
            .arguments
            .iter()
            .map(|a| a.type_id)
            .collect::<Vec<_>>()
            != sig
    {
        return Err(RpcError::Invalid(
            "Missing, ambiguous, or unsupported remote callback event signature".into(),
        ));
    }
    if matches[0].id > u16::MAX as usize {
        return Err(RpcError::Invalid(
            "Remote callback event ID exceeds wire range".into(),
        ));
    }
    Ok(matches[0].id)
}

fn validate_event(
    schema: &Schema,
    view: &WorldView,
    target: &str,
    id: u16,
    name: &str,
    sig: &[u8],
) -> Result<(), RpcError> {
    let class = view
        .class_name(target)
        .ok_or_else(|| RpcError::Invalid("Remote callback receiver is missing".into()))?;
    if find_event(schema, class, name, sig)? != usize::from(id) {
        return Err(RpcError::Invalid("Remote callback event mismatch".into()));
    }
    Ok(())
}

fn encode_client_reply(
    schema: &Schema,
    view: &WorldView,
    reply: &ClientReply,
) -> Result<Vec<u8>, RpcError> {
    if !view.contains(&reply.node) || view.class_name(&reply.node) != Some("RemoteFunction") {
        return Err(RpcError::Invalid(
            "Remote callback receiver is no longer present".into(),
        ));
    }
    let success_body = if reply.ok {
        (|| -> Result<Vec<u8>, RpcError> {
            if reply.values.len() > MAX_ARGS {
                return Err(RpcError::Invalid("Invalid remote result tuple".into()));
            }
            let mut body = varuint(reply.values.len() as u64);
            let mut budget = MAX_VALUE_ITEMS;
            for value in &reply.values {
                body.extend(variant(value, view, 0, &mut budget)?);
            }
            if body.len() > MAX_TUPLE {
                return Err(RpcError::Invalid(
                    "Remote result tuple exceeds limit".into(),
                ));
            }
            Ok(body)
        })()
    } else {
        Err(RpcError::Invalid(reply.error.clone().unwrap_or_else(
            || "Remote callback error must be a string".into(),
        )))
    };
    let (name, sig, body) = match success_body {
        Ok(body) => ("RemoteOnInvokeSuccess", &[10, 29][..], body),
        Err(err) => {
            let message = if reply.ok {
                err.to_string()
            } else {
                reply.error.clone().unwrap_or_else(|| err.to_string())
            };
            let bounded = message.chars().take(4096).collect::<String>();
            let mut body = varuint(bounded.len() as u64);
            body.extend(bounded.as_bytes());
            ("RemoteOnInvokeError", &[10, 2][..], body)
        }
    };
    let event = find_event(schema, "RemoteFunction", name, sig)?;
    let mut payload = vec![0x83, 0x07];
    payload.extend(guid(Some(parse_guid(&reply.node)?))?);
    payload.extend((event as u16).to_le_bytes());
    payload.extend(signed(i64::from(reply.request_id))?);
    payload.extend(body);
    payload.push(0);
    Ok(payload)
}

fn variant(
    value: &Value,
    view: &WorldView,
    depth: usize,
    budget: &mut usize,
) -> Result<Vec<u8>, RpcError> {
    if depth > MAX_DEPTH || *budget == 0 {
        return Err(RpcError::Invalid(
            "Remote value nesting or item limit exceeded".into(),
        ));
    }
    *budget -= 1;
    if value.is_null() {
        return Ok(vec![0]);
    }
    if let Some(b) = value.as_bool() {
        return Ok(vec![9, u8::from(b)]);
    }
    if let Some(n) = value.as_f64() {
        if !n.is_finite() {
            return Err(RpcError::Invalid(
                "Nonfinite remote number unsupported".into(),
            ));
        }
        let mut out = vec![12];
        out.extend(n.to_le_bytes());
        return Ok(out);
    }
    if let Some(s) = value.as_str() {
        let mut out = vec![2];
        out.extend(string_bytes(s.as_bytes())?);
        return Ok(out);
    }
    let obj = value
        .as_object()
        .ok_or_else(|| RpcError::Invalid("Remote result datatype is not yet supported".into()))?;
    if obj.len() != 1 {
        return Err(RpcError::Invalid(
            "Remote result datatype is not yet supported".into(),
        ));
    }
    if let Some(hex) = obj.get("$bytes_hex").and_then(Value::as_str) {
        let bytes = hex_decode(hex)?;
        let mut out = vec![2];
        out.extend(string_bytes(&bytes)?);
        return Ok(out);
    }
    if let Some(id) = obj.get("$ref") {
        if id.is_null() {
            return Ok(vec![28, 0]);
        }
        let text = id.as_str().ok_or_else(|| {
            RpcError::Invalid("Remote reference must be a received instance ID".into())
        })?;
        if !view.contains(text) {
            return Err(RpcError::Invalid(
                "Remote result reference was not received in this session".into(),
            ));
        }
        let mut out = vec![28];
        out.extend(guid(Some(parse_guid(text)?))?);
        return Ok(out);
    }
    if let Some(values) = obj.get("$remote_array").and_then(Value::as_array) {
        return encode_array_tag(30, values, view, depth, budget);
    }
    if let Some(entries) = obj.get("$remote_map").and_then(Value::as_array) {
        if entries.len() > *budget {
            return Err(RpcError::Invalid("Invalid remote dictionary".into()));
        }
        let mut out = vec![31];
        out.extend(varuint(entries.len() as u64));
        let mut keys = HashSet::new();
        for pair in entries {
            let p = pair
                .as_array()
                .filter(|p| p.len() == 2)
                .ok_or_else(|| RpcError::Invalid("Invalid remote dictionary entry".into()))?;
            let key = p[0]
                .as_str()
                .ok_or_else(|| RpcError::Invalid("Remote dictionary key must be a string".into()))?
                .as_bytes();
            if !keys.insert(key.to_vec()) {
                return Err(RpcError::Invalid("Duplicate remote dictionary key".into()));
            }
            out.extend(string_bytes(key)?);
            out.extend(variant(&p[1], view, depth + 1, budget)?);
        }
        return Ok(out);
    }
    engine_variant(obj)
}

fn encode_array(
    values: &[Value],
    view: &WorldView,
    depth: usize,
    budget: &mut usize,
) -> Result<Vec<u8>, RpcError> {
    let mut out = varuint(values.len() as u64);
    for v in values {
        out.extend(variant(v, view, depth + 1, budget)?);
    }
    Ok(out)
}
fn encode_array_tag(
    tag: u8,
    values: &[Value],
    view: &WorldView,
    depth: usize,
    budget: &mut usize,
) -> Result<Vec<u8>, RpcError> {
    let mut out = vec![tag];
    out.extend(encode_array(values, view, depth, budget)?);
    Ok(out)
}

fn engine_variant(obj: &serde_json::Map<String, Value>) -> Result<Vec<u8>, RpcError> {
    let (tag, data) = obj
        .iter()
        .next()
        .ok_or_else(|| RpcError::Invalid("Remote result datatype is not yet supported".into()))?;
    if tag == "$brickcolor" {
        let n = exact_integer(data, 0, 65535)? as u16;
        let mut b = vec![18];
        b.extend(n.to_le_bytes());
        return Ok(b);
    }
    if tag == "$datetime" {
        let n = exact_integer(data, -17_987_443_200_000, 253_402_300_799_999)? as i64;
        let mut b = vec![48];
        b.extend(n.to_le_bytes());
        return Ok(b);
    }
    if tag == "$buffer_hex" {
        let bytes = hex_decode(
            data.as_str()
                .ok_or_else(|| RpcError::Invalid("Invalid remote buffer".into()))?,
        )?;
        let mut b = vec![56, 0];
        b.extend(varuint(bytes.len() as u64));
        b.extend(bytes);
        return Ok(b);
    }
    if tag == "$udim" {
        let a = data
            .as_array()
            .filter(|a| a.len() == 2)
            .ok_or_else(|| RpcError::Invalid("Invalid remote datatype components".into()))?;
        let scale = a[0]
            .as_f64()
            .filter(|x| x.is_finite())
            .ok_or_else(|| RpcError::Invalid("Invalid remote datatype components".into()))?
            as f32;
        let offset = exact_integer(&a[1], i32::MIN as i64, i32::MAX as i64)? as i32;
        let mut b = vec![13];
        b.extend(scale.to_le_bytes());
        b.extend(offset.to_le_bytes());
        return Ok(b);
    }
    if tag == "$udim2" || tag == "$rect" {
        let a = data
            .as_array()
            .filter(|a| a.len() == 2)
            .ok_or_else(|| RpcError::Invalid("Invalid paired remote datatype".into()))?;
        let inner = if tag == "$udim2" { "$udim" } else { "$vector2" };
        let mut b = vec![if tag == "$udim2" { 14 } else { 40 }];
        for v in a {
            let o = v
                .as_object()
                .filter(|o| o.len() == 1 && o.contains_key(inner))
                .ok_or_else(|| RpcError::Invalid("Invalid nested remote datatype".into()))?;
            let encoded = engine_variant(o)?;
            b.extend_from_slice(&encoded[1..]);
        }
        return Ok(b);
    }
    if tag == "$cframe" {
        return encode_cframe(data);
    }
    let (kind, count) = match tag.as_str() {
        "$color3" => (19, 3),
        "$vector2" => (21, 2),
        "$vector3" => (22, 3),
        "$number_range" => (37, 2),
        _ => {
            return Err(RpcError::Invalid(
                "Remote result datatype is not yet supported".into(),
            ))
        }
    };
    let values = data
        .as_array()
        .filter(|a| a.len() == count)
        .ok_or_else(|| RpcError::Invalid("Invalid remote datatype components".into()))?;
    let mut out = vec![kind];
    for v in values {
        let f = v
            .as_f64()
            .filter(|f| f.is_finite())
            .ok_or_else(|| RpcError::Invalid("Invalid remote datatype components".into()))?
            as f32;
        if !f.is_finite() {
            return Err(RpcError::Invalid(
                "Remote datatype component outside wire range".into(),
            ));
        }
        out.extend(f.to_le_bytes());
    }
    Ok(out)
}

fn encode_cframe(value: &Value) -> Result<Vec<u8>, RpcError> {
    let object = value
        .as_object()
        .filter(|o| o.len() == 2 && o.contains_key("position") && o.contains_key("rotation"))
        .ok_or_else(|| RpcError::Invalid("Invalid remote CFrame".into()))?;
    let position = finite_components(object.get("position").unwrap(), 3)?;
    let rotation = finite_components(object.get("rotation").unwrap(), 9)?;
    let rows = [&rotation[0..3], &rotation[3..6], &rotation[6..9]];
    for i in 0..3 {
        for j in 0..3 {
            let dot: f64 = rows[i].iter().zip(rows[j]).map(|(a, b)| a * b).sum();
            let expected = if i == j { 1.0 } else { 0.0 };
            if (dot - expected).abs() > 1e-4 {
                return Err(RpcError::Invalid(
                    "Remote CFrame requires a rotation matrix".into(),
                ));
            }
        }
    }
    let [a, b, c, d, e, f, g, h, i]: [f64; 9] = rotation.clone().try_into().unwrap();
    let determinant = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g);
    if (determinant - 1.0).abs() > 1e-4 {
        return Err(RpcError::Invalid(
            "Remote CFrame requires a proper rotation".into(),
        ));
    }
    let mut selector = 0u8;
    for candidate in 1..=36 {
        if property_selector_matrix(candidate).as_deref() == Some(rotation.as_slice()) {
            selector = candidate as u8;
            break;
        }
    }
    let mut out = vec![27];
    for p in &position {
        let f = *p as f32;
        if !f.is_finite() {
            return Err(RpcError::Invalid(
                "Remote datatype component outside wire range".into(),
            ));
        }
        out.extend(f.to_le_bytes());
    }
    out.push(selector);
    if selector == 0 {
        let matrix: Vec<f32> = rotation.iter().map(|n| *n as f32).collect();
        let q = matrix_quaternion(&matrix)?;
        out.extend(pack_quaternion(q)?);
    }
    Ok(out)
}

pub(crate) fn normalize_received_cframe(value: &Value) -> Result<Value, RpcError> {
    let object = value
        .as_object()
        .ok_or_else(|| RpcError::Invalid("Invalid remote CFrame".into()))?;
    let position = finite_components(
        object
            .get("position")
            .ok_or_else(|| RpcError::Invalid("Invalid remote CFrame position".into()))?,
        3,
    )?;
    let selector = object
        .get("rotation_id")
        .and_then(Value::as_u64)
        .and_then(|selector| usize::try_from(selector).ok())
        .ok_or_else(|| RpcError::Invalid("Invalid remote CFrame selector".into()))?;
    let rotation = if selector == 0 {
        let packed = object
            .get("packed_rotation")
            .and_then(Value::as_str)
            .ok_or_else(|| RpcError::Invalid("Missing remote CFrame quaternion".into()))?;
        let bytes = hex_decode(packed)?;
        if bytes.len() != 6 {
            return Err(RpcError::Invalid("Invalid remote CFrame quaternion".into()));
        }
        let [x, y, z, w] = unpack_quaternion(&bytes)?;
        vec![
            1.0 - 2.0 * (y * y + z * z),
            2.0 * (x * y - z * w),
            2.0 * (x * z + y * w),
            2.0 * (x * y + z * w),
            1.0 - 2.0 * (x * x + z * z),
            2.0 * (y * z - x * w),
            2.0 * (x * z - y * w),
            2.0 * (y * z + x * w),
            1.0 - 2.0 * (x * x + y * y),
        ]
    } else {
        property_selector_matrix(selector)
            .ok_or_else(|| RpcError::Invalid("Invalid remote CFrame selector".into()))?
            .into_iter()
            .map(|component| component as f32)
            .collect()
    };
    Ok(serde_json::json!({"$cframe":{"position":position,"rotation":rotation}}))
}

fn unpack_quaternion(bytes: &[u8]) -> Result<[f32; 4], RpcError> {
    let first = u16::from_le_bytes([bytes[0], bytes[1]]) as u32;
    let second = u32::from_le_bytes(bytes[2..6].try_into().unwrap());
    let omitted = (second >> 30) as usize;
    let packed = [first & 0x7fff, (second >> 15) & 0x7fff, second & 0x7fff];
    let components = [[1usize, 2, 3], [0, 2, 3], [0, 1, 3], [0, 1, 2]][omitted];
    let mut values = [0.0f32; 4];
    for (index, packed) in components.into_iter().zip(packed) {
        let signed = if packed & 0x4000 != 0 {
            (packed as i32) - 0x8000
        } else {
            packed as i32
        };
        values[index] = (signed as f32 / 16383.0) * 0.7071067690849304;
    }
    values[omitted] = (1.0 - values.iter().map(|value| value * value).sum::<f32>())
        .max(0.0)
        .sqrt();
    let norm = values.iter().map(|value| value * value).sum::<f32>().sqrt();
    if !norm.is_finite() || norm <= 0.0 {
        return Err(RpcError::Invalid("Invalid remote CFrame quaternion".into()));
    }
    for value in &mut values {
        *value /= norm;
    }
    Ok(values)
}

fn finite_components(value: &Value, count: usize) -> Result<Vec<f64>, RpcError> {
    let list = value
        .as_array()
        .filter(|a| a.len() == count)
        .ok_or_else(|| RpcError::Invalid("Invalid remote datatype components".into()))?;
    list.iter()
        .map(|v| {
            v.as_f64()
                .filter(|n| n.is_finite())
                .ok_or_else(|| RpcError::Invalid("Invalid remote datatype components".into()))
        })
        .collect()
}

fn property_selector_matrix(selector: usize) -> Option<Vec<f64>> {
    if !(1..=36).contains(&selector) {
        return None;
    }
    let code = selector - 1;
    let axes = [
        [1., 0., 0.],
        [0., 1., 0.],
        [0., 0., 1.],
        [-1., 0., 0.],
        [0., -1., 0.],
        [0., 0., -1.],
    ];
    let first = axes[code / 6];
    let second = axes[code % 6];
    let dot: f64 = (0..3).map(|i| first[i] * second[i]).sum();
    if dot != 0.0 {
        return None;
    }
    let cross = [
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    ];
    Some(
        (0..3)
            .flat_map(|r| [first[r], second[r], cross[r]])
            .collect(),
    )
}

fn matrix_quaternion(m: &[f32]) -> Result<[f32; 4], RpcError> {
    let [m00, m01, m02, m10, m11, m12, m20, m21, m22]: [f32; 9] = m.try_into().unwrap();
    let trace = (m00 + m11) + m22;
    let mut q = [0.0; 4];
    if trace > 0.0 {
        let root = (trace + 1.0).sqrt();
        let factor = 0.5 / root;
        q[3] = root * 0.5;
        q[0] = factor * (m21 - m12);
        q[1] = factor * (m02 - m20);
        q[2] = factor * (m10 - m01);
        return Ok(q);
    }
    if m00 > m11 && m00 > m22 {
        let root = ((1.0 + m00) - m11 - m22).sqrt();
        let factor = -0.5 / root;
        q[0] = root * -0.5;
        q[1] = factor * (m01 + m10);
        q[2] = factor * (m02 + m20);
        q[3] = factor * (m21 - m12);
    } else if m11 > m22 {
        let root = ((1.0 + m11) - m00 - m22).sqrt();
        let factor = -0.5 / root;
        q[1] = root * -0.5;
        q[0] = factor * (m01 + m10);
        q[2] = factor * (m12 + m21);
        q[3] = factor * (m02 - m20);
    } else {
        let root = ((1.0 + m22) - m00 - m11).sqrt();
        let factor = -0.5 / root;
        q[2] = root * -0.5;
        q[0] = factor * (m02 + m20);
        q[1] = factor * (m12 + m21);
        q[3] = factor * (m10 - m01);
    }
    if q.iter().all(|n| n.is_finite()) {
        Ok(q)
    } else {
        Err(RpcError::Invalid("Invalid remote CFrame quaternion".into()))
    }
}

fn pack_quaternion(mut q: [f32; 4]) -> Result<[u8; 6], RpcError> {
    let n = (((q[0] * q[0] + q[1] * q[1]) + q[2] * q[2]) + q[3] * q[3]).sqrt();
    if !n.is_finite() || n <= 0.0 {
        return Err(RpcError::Invalid("Invalid remote CFrame quaternion".into()));
    }
    for x in &mut q {
        *x /= n;
    }
    let n = (((q[0] * q[0] + q[1] * q[1]) + q[2] * q[2]) + q[3] * q[3]).sqrt();
    for x in &mut q {
        *x /= n;
    }
    let mut omitted = 0;
    for i in 1..4 {
        if q[i].abs() > q[omitted].abs() {
            omitted = i;
        }
    }
    if q[omitted] < 0.0 {
        for x in &mut q {
            *x = -*x;
        }
    }
    let components = [[1usize, 2, 3], [0, 2, 3], [0, 1, 3], [0, 1, 2]][omitted];
    let mut packed = [0u32; 3];
    for j in 0..3 {
        let ratio = q[components[j]] / 0.7071067690849304f32;
        let scaled = ratio * 16383.0f32;
        let rounded = (scaled + if scaled >= 0.0 { 0.5 } else { -0.5 }).trunc() as i32;
        packed[j] = (rounded as u32) & 0x7fff;
    }
    let second = ((omitted as u32) << 30) | (packed[1] << 15) | packed[2];
    let mut out = [0u8; 6];
    out[..2].copy_from_slice(&(packed[0] as u16).to_le_bytes());
    out[2..].copy_from_slice(&second.to_le_bytes());
    Ok(out)
}

fn exact_integer(value: &Value, low: i64, high: i64) -> Result<i64, RpcError> {
    let n = value
        .as_f64()
        .filter(|n| n.is_finite() && n.fract() == 0.0 && *n >= low as f64 && *n <= high as f64)
        .ok_or_else(|| RpcError::Invalid("Invalid remote datatype integer".into()))?;
    Ok(n as i64)
}

fn parse_guid(text: &str) -> Result<Guid, RpcError> {
    let mut parts = text.split(':');
    let scope = parts.next().and_then(|s| s.parse::<u32>().ok());
    let index = parts.next().and_then(|s| s.parse::<u32>().ok());
    if parts.next().is_some() || scope.is_none() || index.is_none() || scope == Some(0) {
        return Err(RpcError::Invalid(
            "Remote receiver must be a received instance ID".into(),
        ));
    }
    Ok((scope.unwrap(), index.unwrap()))
}
fn guid_text(value: Guid) -> String {
    format!("{}:{}", value.0, value.1)
}
fn varuint(mut v: u64) -> Vec<u8> {
    let mut out = Vec::new();
    while v >= 128 {
        out.push((v as u8) | 128);
        v >>= 7;
    }
    out.push(v as u8);
    out
}
fn signed(v: i64) -> Result<Vec<u8>, RpcError> {
    let z = ((v as u64) << 1) ^ ((v >> 63) as u64);
    Ok(varuint(z))
}
fn string_bytes(bytes: &[u8]) -> Result<Vec<u8>, RpcError> {
    if bytes.len() > 65_536 {
        return Err(RpcError::Invalid("Remote string exceeds limit".into()));
    }
    let mut out = varuint(bytes.len() as u64);
    out.extend(bytes);
    Ok(out)
}
fn hex_decode(s: &str) -> Result<Vec<u8>, RpcError> {
    if s.len() % 2 != 0 || s.len() > 131072 {
        return Err(RpcError::Invalid("Invalid remote byte string".into()));
    }
    let mut out = Vec::with_capacity(s.len() / 2);
    for i in (0..s.len()).step_by(2) {
        out.push(
            u8::from_str_radix(&s[i..i + 2], 16)
                .map_err(|_| RpcError::Invalid("Invalid remote byte string".into()))?,
        );
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::schema::{ClassDesc, EnumDesc, EventArgument, PropertyDesc};
    use serde_json::json;

    fn test_schema() -> Schema {
        Schema {
            version: 3,
            flags: 0,
            enums: vec![EnumDesc {
                id: 0,
                name: "None".into(),
                bits: 32,
            }],
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
                    events: vec![0, 1, 2, 3],
                    isr_properties: vec![],
                    unknown_u16: 0,
                },
                ClassDesc {
                    id: 3,
                    name: "RemoteEvent".into(),
                    properties: vec![],
                    events: vec![4],
                    isr_properties: vec![],
                    unknown_u16: 0,
                },
                ClassDesc {
                    id: 4,
                    name: "UnreliableRemoteEvent".into(),
                    properties: vec![],
                    events: vec![5],
                    isr_properties: vec![],
                    unknown_u16: 0,
                },
            ],
            properties: vec![PropertyDesc {
                id: 0,
                class: 1,
                name: "UserId".into(),
                type_id: 10,
                enum_id: None,
                lr_type: None,
            }],
            events: vec![
                EventDesc {
                    id: 0,
                    class: 2,
                    name: "RemoteOnInvokeClient".into(),
                    arguments: vec![
                        EventArgument {
                            type_id: 10,
                            enum_id: 0,
                        },
                        EventArgument {
                            type_id: 29,
                            enum_id: 0,
                        },
                    ],
                },
                EventDesc {
                    id: 1,
                    class: 2,
                    name: "RemoteOnInvokeSuccess".into(),
                    arguments: vec![
                        EventArgument {
                            type_id: 10,
                            enum_id: 0,
                        },
                        EventArgument {
                            type_id: 29,
                            enum_id: 0,
                        },
                    ],
                },
                EventDesc {
                    id: 2,
                    class: 2,
                    name: "RemoteOnInvokeError".into(),
                    arguments: vec![
                        EventArgument {
                            type_id: 10,
                            enum_id: 0,
                        },
                        EventArgument {
                            type_id: 2,
                            enum_id: 0,
                        },
                    ],
                },
                EventDesc {
                    id: 3,
                    class: 2,
                    name: "RemoteOnInvokeServer".into(),
                    arguments: vec![
                        EventArgument {
                            type_id: 10,
                            enum_id: 0,
                        },
                        EventArgument {
                            type_id: 28,
                            enum_id: 0,
                        },
                        EventArgument {
                            type_id: 29,
                            enum_id: 0,
                        },
                    ],
                },
                EventDesc {
                    id: 4,
                    class: 3,
                    name: "OnRemoteServerEvent".into(),
                    arguments: vec![EventArgument {
                        type_id: 29,
                        enum_id: 0,
                    }],
                },
                EventDesc {
                    id: 5,
                    class: 4,
                    name: "OnRemoteServerEvent".into(),
                    arguments: vec![EventArgument {
                        type_id: 29,
                        enum_id: 0,
                    }],
                },
            ],
            strings: vec![],
            names: vec![],
            lr_types: vec![],
            parent_property_id: 1,
            decoded_bytes: 0,
        }
    }

    fn test_world() -> WorldView {
        crate::world::WorldStore::from_snapshot(&json!({"version":1,"sequence":1,"nodes":[
            {"id":"1:1","class_name":"Players","name":"Players","parent":null,"properties":{}},
            {"id":"1:2","class_name":"Player","name":"Local","parent":"1:1","properties":{"UserId":7}},
            {"id":"1:3","class_name":"RemoteFunction","name":"RF","parent":null,"properties":{}}
        ]})).unwrap().view()
    }

    #[test]
    fn received_global_players_parent_does_not_break_session_binding() {
        let make = |flags: Value| {
            crate::world::WorldStore::from_snapshot(&json!({"version":1,"nodes":[
            {"id":"1:1","class_name":"Players","parent":"1:99","top_container_flags":flags,"properties":{}},
            {"id":"1:2","class_name":"Player","parent":"1:1","properties":{"UserId":7}}
        ]})).unwrap().view()
        };
        let mut rpc = RpcSession::new(1, 7, (1, 2), 0x100000).unwrap();
        assert!(rpc.bind(1, &make(json!([true, false]))).is_ok());
        assert!(rpc.bind(1, &make(Value::Null)).is_err());
        assert!(rpc.bind(1, &make(json!([1, false]))).is_err());
        assert!(rpc.bind(2, &make(json!([true, false]))).is_err());
        let mut wrong_user = RpcSession::new(1, 8, (1, 2), 0x100000).unwrap();
        assert!(wrong_user.bind(1, &make(json!([true, false]))).is_err());
    }

    #[test]
    fn globals_identity_survives_service_refresh_but_not_another_session() {
        let make = |flags: Value| {
            crate::world::WorldStore::from_snapshot(&json!({"version":1,"nodes":[
            {"id":"1:1","class_name":"Players","parent":"1:99","top_container_flags":flags,"properties":{}},
            {"id":"1:2","class_name":"Player","parent":"1:1","properties":{"UserId":7}}
        ]})).unwrap().view()
        };
        let proof = AnnouncedPlayers::from_globals(9, &make(json!([true, false])))
            .unwrap()
            .unwrap();
        let refreshed = make(Value::Null);
        let mut session = RpcSession::new(9, 7, (1, 2), 0x100000).unwrap();
        assert!(session.bind(9, &refreshed).is_err());
        session.retain_announced_players(proof.clone()).unwrap();
        assert!(session.bind(9, &refreshed).is_ok());
        let mut other = RpcSession::new(10, 7, (1, 2), 0x100000).unwrap();
        assert!(other.retain_announced_players(proof).is_err());
        assert!(AnnouncedPlayers::from_globals(9, &refreshed)
            .unwrap()
            .is_none());
    }

    fn remote_event_world(class: &str) -> WorldView {
        crate::world::WorldStore::from_snapshot(&json!({"version":1,"sequence":1,"nodes":[
            {"id":"1:3","class_name":class,"name":"Event","parent":null,"properties":{}}
        ]}))
        .unwrap()
        .view()
    }

    fn record(seq: u64, events: Vec<Value>) -> SessionRecord {
        SessionRecord {
            packet_kind: 131,
            world_sequence: seq,
            world_delta: json!({}).into(),
            events,
            pings: vec![],
            tags: vec![],
            regions: vec![],
            metadata: vec![],
            property_changes: vec![],
        }
    }

    #[derive(Default)]
    struct MockTransport(Vec<(u8, Vec<u8>)>);
    impl RpcTransport for MockTransport {
        fn send(&mut self, c: u8, p: &[u8]) -> Result<(), String> {
            self.0.push((c, p.to_vec()));
            Ok(())
        }
    }
    struct FailingTransport;
    impl RpcTransport for FailingTransport {
        fn send(&mut self, _: u8, _: &[u8]) -> Result<(), String> {
            Err("transport closed".into())
        }
    }

    #[test]
    fn python_variant_vectors_match() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../tests/fixtures/rpc/python_variant_vectors.json"
        ))
        .unwrap();
        let view =
            crate::world::WorldStore::from_snapshot(&json!({"version":1,"sequence":0,"nodes":[]}))
                .unwrap()
                .view();
        for vector in fixture["vectors"].as_array().unwrap() {
            let mut budget = MAX_VALUE_ITEMS;
            let actual = variant(&vector["value"], &view, 0, &mut budget).unwrap();
            assert_eq!(hex_encode(&actual), vector["hex"].as_str().unwrap());
        }
    }

    #[test]
    fn received_cframes_decode_selectors_and_packed_quaternions() {
        let identity = normalize_received_cframe(&json!({
            "position":[1,2,3],"rotation_id":2,"packed_rotation":null
        }))
        .unwrap();
        assert_eq!(
            identity["$cframe"]["rotation"],
            json!([1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0])
        );
        let selected = normalize_received_cframe(&json!({
            "position":[0,0,0],"rotation_id":17,"packed_rotation":null
        }))
        .unwrap();
        assert_eq!(
            selected["$cframe"]["rotation"],
            json!(property_selector_matrix(17).unwrap())
        );

        let angle = 0.37f32;
        let matrix = [
            angle.cos(),
            0.0,
            angle.sin(),
            0.0,
            1.0,
            0.0,
            -angle.sin(),
            0.0,
            angle.cos(),
        ];
        let packed = pack_quaternion(matrix_quaternion(&matrix).unwrap()).unwrap();
        let decoded = normalize_received_cframe(&json!({
            "position":[4,5,6],"rotation_id":0,"packed_rotation":hex_encode(&packed)
        }))
        .unwrap();
        let actual = decoded["$cframe"]["rotation"].as_array().unwrap();
        for (actual, expected) in actual.iter().zip(matrix) {
            assert!((actual.as_f64().unwrap() - expected as f64).abs() < 0.0002);
        }
        assert!(normalize_received_cframe(&json!({
            "position":[0,0,0],"rotation_id":0,"packed_rotation":"00"
        }))
        .is_err());
    }

    #[test]
    fn rejects_duplicate_map_keys_and_untagged_arrays() {
        let view =
            crate::world::WorldStore::from_snapshot(&json!({"version":1,"sequence":0,"nodes":[]}))
                .unwrap()
                .view();
        let mut budget = MAX_VALUE_ITEMS;
        assert!(variant(
            &json!({"$remote_map":[["x",1],["x",2]]}),
            &view,
            0,
            &mut budget
        )
        .is_err());
        let mut budget = MAX_VALUE_ITEMS;
        assert!(variant(&json!([1, 2]), &view, 0, &mut budget).is_err());
    }

    #[test]
    fn server_call_is_correlated_only_to_later_received_success() {
        let schema = test_schema();
        let view = test_world();
        let mut rpc = RpcSession::new(9, 7, (1, 2), 0x100000).unwrap();
        rpc.queue_server_call(
            9,
            ServerCall {
                node: "1:3".into(),
                request_id: 88,
                arguments: vec![json!("x")],
            },
        )
        .unwrap();
        let mut tx = MockTransport::default();
        assert_eq!(
            rpc.drain_outbound(9, &schema, &view, 1, &mut tx, 16)
                .unwrap(),
            1
        );
        assert_eq!(tx.0[0].0, 1);
        assert_eq!(&tx.0[0].1[..2], &[0x83, 0x07]);
        let active = HashSet::from([88]);
        let out = rpc
            .observe_record(
                9,
                &schema,
                &view,
                &record(2, vec![json!({"key":"1:3","event":1,"arguments":[1,[42]]})]),
                &active,
            )
            .unwrap();
        assert_eq!(
            out,
            vec![RemoteResult {
                node: "1:3".into(),
                request_id: 88,
                ok: true,
                values: Some(vec![json!(42)]),
                error: None
            }]
        );
        assert_eq!(rpc.pending_len(), 0);
    }

    #[test]
    fn replicated_float_user_id_binds_without_accepting_a_different_identity() {
        let view =
            crate::world::WorldStore::from_snapshot(&json!({"version":1,"sequence":1,"nodes":[
                {"id":"1:1","class_name":"Players","parent":null,"properties":{}},
                {"id":"1:2","class_name":"Player","parent":"1:1","properties":{"UserId":7.0}}
            ]}))
            .unwrap()
            .view();
        let record = record(1, vec![]);
        let mut rpc = RpcSession::new(9, 7, (1, 2), 0x100000).unwrap();
        rpc.observe_record(9, &test_schema(), &view, &record, &HashSet::new())
            .unwrap();
        let mut wrong = RpcSession::new(9, 8, (1, 2), 0x100000).unwrap();
        assert!(wrong
            .observe_record(9, &test_schema(), &view, &record, &HashSet::new())
            .is_err());
    }

    #[test]
    fn observed_server_callback_requires_and_consumes_matching_reply() {
        let schema = test_schema();
        let view = test_world();
        let mut rpc = RpcSession::new(9, 7, (1, 2), 0x100000).unwrap();
        let active = HashSet::new();
        rpc.observe_record(
            9,
            &schema,
            &view,
            &record(
                2,
                vec![json!({"key":"1:3","event":0,"arguments":[-3,["arg"]]})],
            ),
            &active,
        )
        .unwrap();
        assert_eq!(rpc.pending_len(), 1);
        rpc.queue_client_reply(
            9,
            ClientReply {
                node: "1:3".into(),
                request_id: -3,
                ok: true,
                values: vec![json!(true)],
                error: None,
            },
        )
        .unwrap();
        let mut tx = MockTransport::default();
        assert_eq!(
            rpc.drain_outbound(9, &schema, &view, 2, &mut tx, 16)
                .unwrap(),
            1
        );
        assert_eq!(&tx.0[0].1[..2], &[0x83, 0x07]);
        assert_eq!(rpc.pending_len(), 0);
    }

    #[test]
    fn cancellation_wins_over_a_response_in_the_same_receipt() {
        let schema = test_schema();
        let view = test_world();
        let mut rpc = RpcSession::new(9, 7, (1, 2), 0x100000).unwrap();
        rpc.queue_server_call(
            9,
            ServerCall {
                node: "1:3".into(),
                request_id: 91,
                arguments: vec![],
            },
        )
        .unwrap();
        let mut tx = MockTransport::default();
        rpc.drain_outbound(9, &schema, &view, 1, &mut tx, 1)
            .unwrap();
        let out = rpc
            .observe_record(
                9,
                &schema,
                &view,
                &record(2, vec![json!({"key":"1:3","event":1,"arguments":[1,[]]})]),
                &HashSet::new(),
            )
            .unwrap();
        assert!(out.is_empty());
        assert_eq!(rpc.pending_len(), 0);
    }

    #[test]
    fn bounded_queue_and_transport_failure_are_explicit() {
        let schema = test_schema();
        let view = test_world();
        let mut rpc = RpcSession::new(9, 7, (1, 2), 0x100000).unwrap();
        for token in 1..=MAX_QUEUE as u64 {
            rpc.queue_server_call(
                9,
                ServerCall {
                    node: "1:3".into(),
                    request_id: token,
                    arguments: vec![],
                },
            )
            .unwrap();
        }
        assert_eq!(
            rpc.queue_server_call(
                9,
                ServerCall {
                    node: "1:3".into(),
                    request_id: 999,
                    arguments: vec![]
                }
            ),
            Err(RpcError::QueueFull)
        );
        let mut failing = FailingTransport;
        assert_eq!(
            rpc.drain_outbound(9, &schema, &view, 1, &mut failing, 1),
            Err(RpcError::Transport("transport closed".into()))
        );
        assert_eq!(rpc.pending_len(), 1);
    }

    #[test]
    fn remote_events_select_reliability_from_received_class() {
        let schema = test_schema();
        let reliable = encode_server_event(
            &schema,
            &remote_event_world("RemoteEvent"),
            &ServerEvent {
                node: "1:3".into(),
                arguments: vec![json!("x")],
                unreliable: false,
            },
        )
        .unwrap();
        assert_eq!(reliable.0, 1);
        assert_eq!(
            reliable.1,
            vec![0x83, 0x07, 1, 3, 0, 0, 0, 4, 0, 1, 2, 1, b'x', 0]
        );
        let unreliable = encode_server_event(
            &schema,
            &remote_event_world("UnreliableRemoteEvent"),
            &ServerEvent {
                node: "1:3".into(),
                arguments: vec![],
                unreliable: true,
            },
        )
        .unwrap();
        assert_eq!(unreliable.0, 3);
        assert!(encode_server_event(
            &schema,
            &remote_event_world("UnreliableRemoteEvent"),
            &ServerEvent {
                node: "1:3".into(),
                arguments: vec![json!({"$buffer_hex":"00".repeat(1100)})],
                unreliable: true
            }
        )
        .is_err());
    }
}

#[cfg(test)]
fn hex_encode(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        use std::fmt::Write;
        let _ = write!(out, "{b:02x}");
    }
    out
}
