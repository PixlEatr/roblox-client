//! A real, bounded TLS/HTTP3/WebTransport connectivity check using this run's
//! issued allocation. No game protocol or integrity reports are sent.
use crate::allocation::IssuedJoinAllocation;
use rove_client_core::transport::{close_metadata, connect_webtransport, IssuedAllocation};
use serde_json::{json, Value};
use std::time::{Duration, Instant};

pub fn probe(issued: &IssuedJoinAllocation, seconds: u64) -> Result<Value, String> {
    if !(1..=30).contains(&seconds) {
        return Err("transport probe duration must be 1..30 seconds".into());
    }
    let allocation = transport_allocation(issued)?;
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|_| "could not start transport runtime")?;
    Ok(runtime.block_on(async {
        let started = Instant::now();
        let connection = tokio::time::timeout(Duration::from_secs(20), connect_webtransport(&allocation)).await;
        let (endpoint, connection, socket) = match connection {
            Err(_) => return json!({"accepted":false,"stage":"connect_timeout","elapsed_seconds":started.elapsed().as_secs_f64()}),
            Ok(Err(error)) => return json!({"accepted":false,"stage":"connect_failed","error":error.to_string(),"elapsed_seconds":started.elapsed().as_secs_f64()}),
            Ok(Ok(connected)) => connected,
        };
        let Some(capabilities) = connection.capability_negotiation() else {
            connection.close(0u32.into(), b"missing capability negotiation");
            endpoint.close(0u32.into(), b"missing capability negotiation");
            return json!({"accepted":false,"stage":"capability_negotiation_missing",
                "elapsed_seconds":started.elapsed().as_secs_f64()});
        };
        let accepted_at = Instant::now();
        let closed = tokio::time::timeout(Duration::from_secs(seconds), connection.closed()).await;
        let connected_seconds = accepted_at.elapsed().as_secs_f64();
        let (reason, code, ended_by_client) = match closed {
            Ok(error) => { let (reason, code) = close_metadata(&error); (reason, code, false) },
            Err(_) => ("probe_complete", None, true),
        };
        let stats = socket.stats();
        connection.close(0u32.into(), b"transport probe complete");
        endpoint.close(0u32.into(), b"transport probe complete");
        let _ = tokio::time::timeout(Duration::from_millis(500), endpoint.wait_idle()).await;
        json!({"accepted":true,"stage":"webtransport_accepted",
            "connected_seconds":connected_seconds,"elapsed_seconds":started.elapsed().as_secs_f64(),
            "requested_seconds":seconds,"ended_by_client":ended_by_client,
            "stayed_open_for_requested_duration":ended_by_client,"close_kind":reason,"close_code":code,
            "udp_datagrams_sent":stats.datagrams_sent,"udp_datagrams_received":stats.datagrams_received,
            "udp_bytes_sent":stats.bytes_sent,"udp_bytes_received":stats.bytes_received,
            "routing_token_rotations":stats.token_rotations,
            "client_capabilities":format!("{:016x}",capabilities.client_offered()),
            "server_capabilities":format!("{:016x}",capabilities.server_advertised()),
            "negotiated_capabilities":format!("{:016x}",capabilities.negotiated()),
            "game_authentication_attempted":false,"player_spawned":false})
    }))
}

pub(crate) fn transport_allocation(issued: &IssuedJoinAllocation) -> Result<IssuedAllocation, String> {
    let error = "issued allocation does not contain complete transport credentials";
    let allocation = IssuedAllocation {
        endpoint: issued.endpoint.ok_or(error)?,
        backend: issued.backend.ok_or(error)?,
        token: *issued.token().ok_or(error)?,
        certificate_sha256: issued.certificate_sha256.clone(),
        capabilities: issued.capabilities,
    };
    allocation.validate().map_err(|error| error.to_string())?;
    Ok(allocation)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn copies_only_this_allocations_issued_transport_values() {
        let issued = crate::allocation::test_issued_allocation();
        let copied = transport_allocation(&issued).unwrap();
        assert_eq!(Some(copied.endpoint), issued.endpoint);
        assert_eq!(Some(copied.backend), issued.backend);
        assert_eq!(Some(&copied.token), issued.token());
        assert_eq!(copied.certificate_sha256, issued.certificate_sha256);
    }
    #[test]
    fn incomplete_credentials_and_invalid_duration_fail_before_network() {
        let mut issued = crate::allocation::test_issued_allocation();
        assert!(probe(&issued, 0).is_err());
        assert!(probe(&issued, 31).is_err());
        issued.certificate_sha256.clear();
        assert!(probe(&issued, 1).is_err());
    }
}
