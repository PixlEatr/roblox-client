//! Small, data-only subset of client-originated replication messages.
//!
//! Marker acknowledgements are queued until the caller supplies explicit
//! initial-world readiness. Streaming resume requires concrete character and
//! region evidence from the current session; this module never manufactures it.

use crate::{physics::ContactTransition, wire::WireError};
use std::collections::VecDeque;

const MARKER_REQUEST: u8 = 132;
const MARKER_REPLY: [u8; 2] = [131, 4];
const STREAMING_RESUME: [u8; 2] = [131, 3];
const MAX_PENDING_MARKERS: usize = 64;
const MAX_CONTACT_TRANSITIONS: usize = 64;

/// An instance reference represented by its network scope and little-endian ID.
pub type Guid = (u32, u32);

/// Encode one native marker-queue acknowledgement.
///
/// The server marker is copied verbatim, followed by the native zero trailer.
pub fn marker_reply(payload: &[u8]) -> Result<Vec<u8>, WireError> {
    if payload.len() != 5 || payload[0] != MARKER_REQUEST {
        return Err(WireError::new("Invalid replication marker request"));
    }
    let mut reply = Vec::with_capacity(7);
    reply.extend_from_slice(&MARKER_REPLY);
    reply.extend_from_slice(&payload[1..]);
    reply.push(0);
    Ok(reply)
}

/// Encode the network BitStream unsigned LEB128 scope and little-endian ID.
pub fn guid(key: Option<Guid>) -> Result<Vec<u8>, WireError> {
    let Some((scope, index)) = key else {
        return Ok(vec![0]);
    };
    if scope == 0 {
        return Err(WireError::new("Invalid instance reference scope"));
    }
    let mut encoded = Vec::with_capacity(9);
    let mut remaining = u64::from(scope);
    while remaining > 0x7f {
        encoded.push(((remaining as u8) & 0x7f) | 0x80);
        remaining >>= 7;
    }
    encoded.push(remaining as u8);
    encoded.extend_from_slice(&index.to_le_bytes());
    Ok(encoded)
}

/// Encode a bounded batch of explicit touch transitions on reliable channel 7.
pub fn contact_batch(records: &[(Guid, Guid, bool)]) -> Result<Vec<u8>, WireError> {
    if records.is_empty() || records.len() > MAX_CONTACT_TRANSITIONS {
        return Err(WireError::new(
            "Contact batch must contain 1..64 transitions",
        ));
    }
    let mut payload = vec![134];
    for &(first, second, began) in records {
        if first == second {
            return Err(WireError::new("Contact participants must be distinct"));
        }
        payload.extend(guid(Some(first))?);
        payload.extend(guid(Some(second))?);
        payload.push(if began { 0 } else { 3 });
    }
    payload.extend(guid(None)?);
    Ok(payload)
}

/// Encode one explicit script-requested touch transition on reliable channel 7.
pub fn contact_transition(first: Guid, second: Guid, began: bool) -> Result<Vec<u8>, WireError> {
    contact_batch(&[(first, second, began)])
}

/// Convert tracked static contacts into ordered reliable channel 7 batches.
pub fn physics_contact_batches(
    local: Guid,
    transitions: &[ContactTransition],
) -> Result<Vec<Vec<u8>>, WireError> {
    transitions
        .chunks(MAX_CONTACT_TRANSITIONS)
        .map(|chunk| {
            let records = chunk
                .iter()
                .map(|transition| {
                    let mut parts = transition.node_id.split(':');
                    let scope = parts.next().and_then(|part| part.parse::<u32>().ok());
                    let index = parts.next().and_then(|part| part.parse::<u32>().ok());
                    if parts.next().is_some() || scope.is_none() || index.is_none() {
                        return Err(WireError::new(
                            "Contact node ID must be a received instance ID",
                        ));
                    }
                    Ok((local, (scope.unwrap(), index.unwrap()), transition.began))
                })
                .collect::<Result<Vec<_>, WireError>>()?;
            contact_batch(&records)
        })
        .collect()
}

/// Readiness gates supplied by the session owner when draining marker acks.
/// All fields default false so there is no implicit ready state.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct MarkerReadiness {
    pub client_scope_received: bool,
    pub initial_world_tag_received: bool,
}

/// Queue server markers in receive order and release them after the world gate.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct MarkerQueue {
    pending: VecDeque<Vec<u8>>,
}

impl MarkerQueue {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn len(&self) -> usize {
        self.pending.len()
    }

    pub fn is_empty(&self) -> bool {
        self.pending.is_empty()
    }

    pub fn add(&mut self, payload: &[u8]) -> Result<(), WireError> {
        let reply = marker_reply(payload)?;
        if self.pending.len() >= MAX_PENDING_MARKERS {
            return Err(WireError::new("Too many pending replication markers"));
        }
        self.pending.push_back(reply);
        Ok(())
    }

    /// Drain in original order only after both readiness facts are present.
    pub fn drain(&mut self, readiness: MarkerReadiness) -> Vec<Vec<u8>> {
        if !readiness.client_scope_received || !readiness.initial_world_tag_received {
            return Vec::new();
        }
        self.pending.drain(..).collect()
    }
}

/// The exact evidence required before resuming the server's GameplayPaused flag.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct StreamingEvidence {
    pub player_spawned: bool,
    pub spawn_region_received: bool,
}

/// Encode the narrow GameplayPaused=false update after explicit spawn evidence.
pub fn streaming_ready(
    player: Guid,
    gameplay_paused_property_id: u16,
    gameplay_paused_type_id: u8,
    evidence: StreamingEvidence,
) -> Result<Vec<u8>, WireError> {
    if !evidence.player_spawned || !evidence.spawn_region_received {
        return Err(WireError::new(
            "Character and surrounding region must be received before resuming",
        ));
    }
    if gameplay_paused_type_id != 9 {
        return Err(WireError::new("Unexpected pause property type"));
    }
    let mut packet = Vec::with_capacity(12);
    packet.extend_from_slice(&STREAMING_RESUME);
    packet.extend_from_slice(&guid(Some(player))?);
    packet.extend_from_slice(&gameplay_paused_property_id.to_le_bytes());
    packet.extend_from_slice(&[0, 0, 0]);
    Ok(packet)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;

    fn fixture() -> Value {
        serde_json::from_str(include_str!(
            "../tests/fixtures/replication_out/python_reference_vectors.json"
        ))
        .unwrap()
    }

    fn unhex(text: &str) -> Vec<u8> {
        text.as_bytes()
            .chunks_exact(2)
            .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
            .collect()
    }

    #[test]
    fn marker_ack_preserves_marker_and_native_trailer() {
        let vectors = fixture();
        let marker = &vectors["marker"];
        let request = unhex(marker["request_hex"].as_str().unwrap());
        assert_eq!(
            marker_reply(&request).unwrap(),
            unhex(marker["reply_hex"].as_str().unwrap())
        );
        assert!(marker_reply(&[132, 1]).is_err());
        assert!(marker_reply(&[133, 0, 0, 0, 0]).is_err());
    }

    #[test]
    fn marker_queue_is_bounded_ordered_and_requires_both_readiness_facts() {
        let mut queue = MarkerQueue::new();
        for marker in 0..MAX_PENDING_MARKERS {
            queue.add(&[132, marker as u8, 0, 0, 0]).unwrap();
        }
        assert!(queue.add(&[132, 1, 0, 0, 0]).is_err());
        assert!(queue
            .drain(MarkerReadiness {
                client_scope_received: true,
                initial_world_tag_received: false
            })
            .is_empty());
        assert_eq!(queue.len(), MAX_PENDING_MARKERS);
        let drained = queue.drain(MarkerReadiness {
            client_scope_received: true,
            initial_world_tag_received: true,
        });
        assert_eq!(drained.len(), MAX_PENDING_MARKERS);
        assert_eq!(drained[0], [131, 4, 0, 0, 0, 0, 0]);
        assert!(queue.is_empty());
    }

    #[test]
    fn contact_transition_uses_scoped_parts_and_native_state_bytes() {
        assert_eq!(
            contact_transition((1, 7), (1, 8), true).unwrap(),
            [134, 1, 7, 0, 0, 0, 1, 8, 0, 0, 0, 0, 0]
        );
        assert_eq!(contact_transition((1, 7), (1, 8), false).unwrap()[11], 3);
        assert!(contact_transition((1, 7), (1, 7), true).is_err());
        assert!(contact_transition((0, 7), (1, 8), true).is_err());
    }

    #[test]
    fn contact_batches_preserve_order_and_enforce_limits() {
        let records = [((1, 7), (1, 8), true), ((1, 8), (1, 7), false)];
        assert_eq!(
            contact_batch(&records).unwrap(),
            [134, 1, 7, 0, 0, 0, 1, 8, 0, 0, 0, 0, 1, 8, 0, 0, 0, 1, 7, 0, 0, 0, 3, 0]
        );
        assert!(contact_batch(&[]).is_err());
        assert!(contact_batch(&vec![((1, 7), (1, 8), true); 65]).is_err());
        assert!(contact_batch(&[((1, 7), (1, 7), true)]).is_err());
    }

    #[test]
    fn physics_contacts_resolve_ids_and_split_ordered_batches() {
        assert!(physics_contact_batches((1, 7), &[]).unwrap().is_empty());
        let transitions = [
            ContactTransition {
                node_id: "1:8".into(),
                began: true,
            },
            ContactTransition {
                node_id: "2:9".into(),
                began: false,
            },
        ];
        assert_eq!(
            physics_contact_batches((1, 7), &transitions).unwrap(),
            [contact_batch(&[((1, 7), (1, 8), true), ((1, 7), (2, 9), false)]).unwrap()]
        );
        for invalid in ["missing", "1:2:3", "0:8", "1:7"] {
            let transitions = [ContactTransition {
                node_id: invalid.into(),
                began: true,
            }];
            assert!(physics_contact_batches((1, 7), &transitions).is_err());
        }

        let transitions = (0..65)
            .map(|index| ContactTransition {
                node_id: format!("1:{}", index + 8),
                began: index % 2 == 0,
            })
            .collect::<Vec<_>>();
        let batches = physics_contact_batches((1, 7), &transitions).unwrap();
        assert_eq!(batches.len(), 2);
        assert_eq!(batches[0].len(), 706);
        assert_eq!(
            batches[1],
            contact_transition((1, 7), (1, 72), true).unwrap()
        );
    }

    #[test]
    fn guid_matches_python_network_leb128_and_le32_layout() {
        for vector in fixture()["guids"].as_array().unwrap() {
            let key = if vector["key"].is_null() {
                None
            } else {
                Some((
                    vector["key"][0].as_u64().unwrap() as u32,
                    vector["key"][1].as_u64().unwrap() as u32,
                ))
            };
            assert_eq!(
                guid(key).unwrap(),
                unhex(vector["encoded_hex"].as_str().unwrap())
            );
        }
        assert!(guid(Some((0, 0))).is_err());
    }

    #[test]
    fn streaming_resume_uses_received_evidence_and_typed_pause_property() {
        let vector = fixture()["streaming_ready"].clone();
        let player = (
            vector["player"][0].as_u64().unwrap() as u32,
            vector["player"][1].as_u64().unwrap() as u32,
        );
        let evidence = StreamingEvidence {
            player_spawned: vector["player_spawned"].as_bool().unwrap(),
            spawn_region_received: vector["spawn_region_received"].as_bool().unwrap(),
        };
        assert_eq!(
            streaming_ready(
                player,
                vector["pause_property_id"].as_u64().unwrap() as u16,
                vector["pause_property_type"].as_u64().unwrap() as u8,
                evidence
            )
            .unwrap(),
            unhex(vector["encoded_hex"].as_str().unwrap())
        );
        assert!(streaming_ready((1, 7), 0x1234, 9, StreamingEvidence::default()).is_err());
        assert!(streaming_ready((1, 7), 0x1234, 8, evidence).is_err());
    }
}
