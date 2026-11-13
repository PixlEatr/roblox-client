//! Bounded RUPP routing and Quinn socket support for the Rust transport.
//!
//! Inputs are supplied by the allocation/join layer. This module does not
//! create credentials or tokens, emulate trust decisions, or synthesize game
//! replies. HTTP/3 and WebTransport sessions are implemented by a minimally
//! patched, pinned `wtransport` client over the allocation-scoped Quinn socket.

use crate::channels::{parse_rupp, ChannelError, RuppPacket};
use std::net::{Ipv4Addr, SocketAddr};

#[cfg(feature = "transport")]
pub use wtransport::Connection;

pub const MAX_UDP_DATAGRAM: usize = 65_535;
pub const DEFAULT_MAX_SESSION_BYTES: u64 = u64::MAX;
pub const DEFAULT_MAX_SESSION_DATAGRAMS: u64 = u64::MAX;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum TransportError {
    InvalidToken,
    InvalidEndpoint,
    InvalidCertificatePins,
    InvalidCertificateTime,
    InvalidCertificatePolicy,
    InvalidCapabilities,
    UnexpectedDestination,
    DatagramTooLarge,
    TrafficLimit,
    SessionRejected,
    InvalidServerCapabilities,
    StreamFailure,
    StreamReset { code: u64 },
    StreamNotConnected,
    StreamProtocolError,
    DatagramFailure,
    Routing(ChannelError),
}

impl std::fmt::Display for TransportError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::StreamReset { code } => write!(f, "reliable stream reset (code: {code})"),
            Self::StreamNotConnected => f.write_str("reliable stream connection ended"),
            Self::StreamProtocolError => f.write_str("reliable stream protocol error"),
            Self::InvalidToken => f.write_str("expected a 16-byte issued routing token"),
            Self::InvalidEndpoint => f.write_str("invalid allocated IPv4 endpoint"),
            Self::InvalidCertificatePins => {
                f.write_str("missing or mismatched allocated certificate hashes")
            }
            Self::InvalidCertificateTime => {
                f.write_str("pinned certificate is outside its validity period")
            }
            Self::InvalidCertificatePolicy => {
                f.write_str("pinned certificate failed WebTransport certificate validation")
            }
            Self::InvalidCapabilities => {
                f.write_str("unimplemented transport capability requested")
            }
            Self::UnexpectedDestination => {
                f.write_str("refused an unallocated transport destination")
            }
            Self::DatagramTooLarge => f.write_str("UDP datagram exceeds configured bound"),
            Self::TrafficLimit => f.write_str("transport session traffic limit reached"),
            Self::SessionRejected => f.write_str("WebTransport session negotiation failed"),
            Self::InvalidServerCapabilities => {
                f.write_str("server capability response is missing or invalid")
            }
            Self::StreamFailure => f.write_str("WebTransport reliable stream failed"),
            Self::DatagramFailure => f.write_str("WebTransport datagram failed"),
            Self::Routing(error) => write!(f, "invalid routing envelope: {error}"),
        }
    }
}

impl std::error::Error for TransportError {}

impl From<ChannelError> for TransportError {
    fn from(value: ChannelError) -> Self {
        Self::Routing(value)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TokenKind {
    Initial = 1,
    Rotated = 2,
}

/// Allocation inputs already validated by the HTTPS join path.
#[derive(Clone, PartialEq, Eq)]
pub struct IssuedAllocation {
    pub endpoint: SocketAddr,
    pub backend: Ipv4Addr,
    pub token: [u8; 16],
    pub certificate_sha256: Vec<[u8; 32]>,
    /// Implemented client features offered for intersection with the server.
    /// This is local configuration, not an assertion about server features.
    pub capabilities: u64,
}

impl std::fmt::Debug for IssuedAllocation {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("IssuedAllocation")
            .field("endpoint", &self.endpoint)
            .field("backend", &self.backend)
            .field("token", &"[REDACTED]")
            .field("certificate_pin_count", &self.certificate_sha256.len())
            .field("capabilities", &format_args!("{:016x}", self.capabilities))
            .finish()
    }
}

impl IssuedAllocation {
    pub fn validate(&self) -> Result<(), TransportError> {
        let endpoint_ipv4 = match self.endpoint {
            SocketAddr::V4(address) => *address.ip(),
            SocketAddr::V6(_) => return Err(TransportError::InvalidEndpoint),
        };
        if !self.endpoint.is_ipv4()
            || self.endpoint.port() == 0
            || (endpoint_ipv4.is_private() && !endpoint_ipv4.is_loopback())
            || endpoint_ipv4.is_unspecified()
            || endpoint_ipv4.is_multicast()
            || self.backend.is_unspecified()
            || !self.backend.is_private()
            || self.capabilities != 0 && self.capabilities != 0x10_0000
        {
            return if self.capabilities != 0 && self.capabilities != 0x10_0000 {
                Err(TransportError::InvalidCapabilities)
            } else {
                Err(TransportError::InvalidEndpoint)
            };
        }
        if self.certificate_sha256.is_empty() {
            return Err(TransportError::InvalidCertificatePins);
        }
        Ok(())
    }
}

/// Transport counters. Send counters advance only after a successful UDP send;
/// receive counters advance after an accepted datagram is unwrapped.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct TransportStats {
    pub datagrams_sent: u64,
    pub datagrams_received: u64,
    pub bytes_sent: u64,
    pub bytes_received: u64,
    pub token_rotations: u64,
}

/// RUPP envelope state. The endpoint is pinned to the issued allocation even
/// when a direct peer reply arrives from a different UDP source address.
/// Keeping that mapping matches the existing QUIC input path and prevents an
/// unallocated socket send.
#[derive(Clone)]
pub struct RuppTransport {
    endpoint: SocketAddr,
    backend: Ipv4Addr,
    token: [u8; 16],
    header: Vec<u8>,
    token_kind: TokenKind,
    stats: TransportStats,
    max_datagrams: u64,
    max_bytes: u64,
}

impl std::fmt::Debug for RuppTransport {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("RuppTransport")
            .field("endpoint", &self.endpoint)
            .field("backend", &self.backend)
            .field("token", &"[REDACTED]")
            .field("header", &"[REDACTED]")
            .field("token_kind", &self.token_kind)
            .field("stats", &self.stats)
            .field("max_datagrams", &self.max_datagrams)
            .field("max_bytes", &self.max_bytes)
            .finish()
    }
}

impl RuppTransport {
    pub fn new(allocation: &IssuedAllocation) -> Result<Self, TransportError> {
        allocation.validate()?;
        let header = encode_client_header(
            &allocation.token,
            allocation.backend,
            allocation.endpoint.port(),
            TokenKind::Initial,
        )?;
        Ok(Self {
            endpoint: allocation.endpoint,
            backend: allocation.backend,
            token: allocation.token,
            header,
            token_kind: TokenKind::Initial,
            stats: TransportStats::default(),
            max_datagrams: DEFAULT_MAX_SESSION_DATAGRAMS,
            max_bytes: DEFAULT_MAX_SESSION_BYTES,
        })
    }

    /// Configure a finite observation budget. Passing `None` removes that
    /// particular limit; a caller must make that opt-in explicit.
    pub fn with_limits(mut self, datagrams: Option<u64>, bytes: Option<u64>) -> Self {
        self.max_datagrams = datagrams.unwrap_or(u64::MAX);
        self.max_bytes = bytes.unwrap_or(u64::MAX);
        self
    }

    pub fn endpoint(&self) -> SocketAddr {
        self.endpoint
    }

    pub fn stats(&self) -> TransportStats {
        self.stats
    }

    pub fn issued_header(&self) -> &[u8] {
        &self.header
    }

    pub fn token_kind(&self) -> TokenKind {
        self.token_kind
    }

    /// Add the current routing envelope before Quinn passes a packet to UDP.
    pub fn wrap_outgoing(
        &mut self,
        destination: SocketAddr,
        quic_packet: &[u8],
    ) -> Result<Vec<u8>, TransportError> {
        if destination != self.endpoint {
            return Err(TransportError::UnexpectedDestination);
        }
        if self.stats.datagrams_sent >= self.max_datagrams
            || self
                .stats
                .bytes_sent
                .saturating_add(quic_packet.len() as u64)
                > self.max_bytes
        {
            return Err(TransportError::TrafficLimit);
        }
        let size = self.header.len().saturating_add(quic_packet.len());
        if size > MAX_UDP_DATAGRAM {
            return Err(TransportError::DatagramTooLarge);
        }
        let mut datagram = Vec::with_capacity(size);
        datagram.extend_from_slice(&self.header);
        datagram.extend_from_slice(quic_packet);
        Ok(datagram)
    }

    /// Record a successful socket send after `wrap_outgoing` has succeeded.
    pub fn record_send_success(&mut self, quic_payload_bytes: usize) {
        self.stats.datagrams_sent = self.stats.datagrams_sent.saturating_add(1);
        self.stats.bytes_sent = self
            .stats
            .bytes_sent
            .saturating_add(quic_payload_bytes as u64);
    }

    /// Strip a peer routing envelope and return only QUIC bytes. Rotation is
    /// accepted solely from the observed issued token TLV (field 1, kind 2,
    /// exactly 16 token bytes), and the replacement header retains the same
    /// backend and allocated port.
    pub fn unwrap_incoming<'a>(
        &mut self,
        datagram: &'a [u8],
    ) -> Result<InboundDatagram<'a>, TransportError> {
        if datagram.len() > MAX_UDP_DATAGRAM {
            return Err(TransportError::DatagramTooLarge);
        }
        let routing = parse_rupp(datagram)?;
        let mut payload = datagram;
        let mut token_rotation = false;
        if let Some(packet) = routing {
            payload = packet.payload;
            token_rotation = self.observe_routing(&packet)?;
        }
        if self.stats.datagrams_received >= self.max_datagrams
            || self
                .stats
                .bytes_received
                .saturating_add(payload.len() as u64)
                > self.max_bytes
        {
            return Err(TransportError::TrafficLimit);
        }
        self.stats.datagrams_received += 1;
        self.stats.bytes_received = self
            .stats
            .bytes_received
            .saturating_add(payload.len() as u64);
        Ok(InboundDatagram {
            quic_packet: payload,
            token_rotated: token_rotation,
        })
    }

    fn observe_routing(&mut self, routing: &RuppPacket<'_>) -> Result<bool, TransportError> {
        let mut rotated = false;
        for field in &routing.fields {
            if field.kind == 1 && field.value.len() == 17 && field.value[0] == 2 {
                let token: [u8; 16] = field.value[1..]
                    .try_into()
                    .map_err(|_| TransportError::InvalidToken)?;
                if token != self.token {
                    self.header = encode_client_header(
                        &token,
                        self.backend,
                        self.endpoint.port(),
                        TokenKind::Rotated,
                    )?;
                    self.token = token;
                    self.token_kind = TokenKind::Rotated;
                    self.stats.token_rotations += 1;
                    rotated = true;
                }
            }
        }
        Ok(rotated)
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub struct InboundDatagram<'a> {
    pub quic_packet: &'a [u8],
    pub token_rotated: bool,
}

impl std::fmt::Debug for InboundDatagram<'_> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("InboundDatagram")
            .field("quic_packet_bytes", &self.quic_packet.len())
            .field("token_rotated", &self.token_rotated)
            .finish()
    }
}

pub fn encode_client_header(
    token: &[u8],
    backend: Ipv4Addr,
    port: u16,
    kind: TokenKind,
) -> Result<Vec<u8>, TransportError> {
    if token.len() != 16 {
        return Err(TransportError::InvalidToken);
    }
    if port == 0 || backend.is_unspecified() {
        return Err(TransportError::InvalidEndpoint);
    }
    let mut result = Vec::with_capacity(31);
    result.extend_from_slice(&[1, 0, 0, 31, 1, 17, kind as u8]);
    result.extend_from_slice(token);
    result.extend_from_slice(&[2, 6]);
    result.extend_from_slice(&backend.octets());
    result.extend_from_slice(&port.to_be_bytes());
    Ok(result)
}

/// Report the implemented WebTransport client stack used by this module.
pub const fn webtransport_client_support() -> WebTransportClientSupport {
    WebTransportClientSupport::SupportedByPinnedWtransport
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum WebTransportClientSupport {
    SupportedByPinnedWtransport,
}

#[cfg(feature = "transport")]
pub mod session;

/// Bounded, non-secret close metadata for user-facing transport diagnostics.
/// Peer-controlled reason bytes are deliberately not copied into logs.
#[cfg(feature = "transport")]
pub fn close_metadata(error: &wtransport::error::ConnectionError) -> (&'static str, Option<u64>) {
    use wtransport::error::ConnectionError;
    match error {
        ConnectionError::ConnectionClosed(_) => ("peer_transport_close", None),
        ConnectionError::ApplicationClosed(close) => {
            ("peer_application_close", Some(close.code().into_inner()))
        }
        ConnectionError::LocallyClosed => ("locally_closed", None),
        ConnectionError::LocalH3Error(_) => ("local_http3_error", None),
        ConnectionError::TimedOut => ("transport_timed_out", None),
        ConnectionError::QuicProto(_) => ("quic_protocol_error", None),
        ConnectionError::CidsExhausted => ("connection_ids_exhausted", None),
    }
}

/// Sample an already observed QUIC close before requesting local shutdown.
/// This preserves numeric peer transport codes without exposing reason text.
#[cfg(feature = "transport")]
pub fn current_close_metadata(connection: &wtransport::Connection) -> Option<(&'static str, Option<u64>)> {
    connection.quic_connection().close_reason().map(|error| match error {
        quinn::ConnectionError::ConnectionClosed(close) => ("peer_transport_close",Some(close.error_code.into())),
        quinn::ConnectionError::TransportError(error) => ("quic_protocol_error",Some(error.code.into())),
        error => close_metadata(&error.into()),
    })
}

#[cfg(feature = "transport")]
mod quinn_socket {
    use super::*;
    use quinn::{
        udp::{RecvMeta, Transmit},
        AsyncUdpSocket, UdpPoller,
    };
    use std::{
        fmt,
        io::{self, IoSliceMut},
        pin::Pin,
        sync::{Arc, Mutex},
        task::{Context, Poll},
    };
    use tokio::net::UdpSocket;

    /// A single-datagram socket adapter that inserts/removes the allocation's
    /// RUPP routing header. It binds only an ephemeral local UDP port and only
    /// sends to the fixed allocation endpoint.
    pub struct RuppUdpSocket {
        io: Arc<UdpSocket>,
        route: Mutex<RuppTransport>,
    }

    impl fmt::Debug for RuppUdpSocket {
        fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
            f.debug_struct("RuppUdpSocket")
                .field("local_addr", &self.io.local_addr())
                .finish_non_exhaustive()
        }
    }

    impl RuppUdpSocket {
        pub async fn bind(allocation: &IssuedAllocation) -> Result<Arc<Self>, TransportError> {
            let route = RuppTransport::new(allocation)?;
            let socket = std::net::UdpSocket::bind(if allocation.endpoint.is_ipv4() {
                "0.0.0.0:0"
            } else {
                "[::]:0"
            })
            .map_err(|_| TransportError::InvalidEndpoint)?;
            socket
                .set_nonblocking(true)
                .map_err(|_| TransportError::InvalidEndpoint)?;
            let io = UdpSocket::from_std(socket).map_err(|_| TransportError::InvalidEndpoint)?;
            Ok(Arc::new(Self {
                io: Arc::new(io),
                route: Mutex::new(route),
            }))
        }

        pub fn stats(&self) -> TransportStats {
            self.route.lock().expect("RUPP mutex poisoned").stats()
        }
    }

    struct SendPoller(Arc<UdpSocket>);

    impl fmt::Debug for SendPoller {
        fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
            f.write_str("RuppUdpSendPoller")
        }
    }

    impl UdpPoller for SendPoller {
        fn poll_writable(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
            self.0.poll_send_ready(cx)
        }
    }

    impl AsyncUdpSocket for RuppUdpSocket {
        fn create_io_poller(self: Arc<Self>) -> Pin<Box<dyn UdpPoller>> {
            Box::pin(SendPoller(self.io.clone()))
        }

        fn try_send(&self, transmit: &Transmit<'_>) -> io::Result<()> {
            if transmit.segment_size.is_some() {
                return Err(io::Error::new(
                    io::ErrorKind::Unsupported,
                    "GSO is unsupported",
                ));
            }
            let mut route = self
                .route
                .lock()
                .map_err(|_| io::Error::other("RUPP state poisoned"))?;
            let datagram = route
                .wrap_outgoing(transmit.destination, transmit.contents)
                .map_err(to_io_error)?;
            match self.io.try_send_to(&datagram, transmit.destination) {
                Ok(written) if written == datagram.len() => {
                    route.record_send_success(transmit.contents.len());
                    Ok(())
                }
                Ok(_) => Err(io::Error::new(io::ErrorKind::WriteZero, "short UDP send")),
                Err(error) => Err(error),
            }
        }

        fn poll_recv(
            &self,
            cx: &mut Context<'_>,
            bufs: &mut [IoSliceMut<'_>],
            meta: &mut [RecvMeta],
        ) -> Poll<io::Result<usize>> {
            let (Some(buffer), Some(metadata)) = (bufs.first_mut(), meta.first_mut()) else {
                return Poll::Ready(Ok(0));
            };
            loop {
                match self.io.poll_recv_ready(cx) {
                    Poll::Pending => return Poll::Pending,
                    Poll::Ready(Err(error)) => return Poll::Ready(Err(error)),
                    Poll::Ready(Ok(())) => {}
                }
                let mut packet = [0u8; MAX_UDP_DATAGRAM];
                let (length, source) = match self.io.try_recv_from(&mut packet) {
                    Ok(result) => result,
                    Err(error) if error.kind() == io::ErrorKind::WouldBlock => continue,
                    Err(error) => return Poll::Ready(Err(error)),
                };
                let inbound = match self
                    .route
                    .lock()
                    .map_err(|_| io::Error::other("RUPP state poisoned"))
                    .and_then(|mut route| {
                        route
                            .unwrap_incoming(&packet[..length])
                            .map_err(to_io_error)
                    }) {
                    Ok(packet) => packet,
                    // Malformed/oversized envelopes are dropped, as UDP loss,
                    // instead of turning one bad datagram into a connection error.
                    Err(_) => continue,
                };
                if inbound.quic_packet.len() > buffer.len() {
                    continue;
                }
                buffer[..inbound.quic_packet.len()].copy_from_slice(inbound.quic_packet);
                *metadata = RecvMeta {
                    // Quinn's peer tuple remains the issued allocation even if
                    // a backend replies from a different route address.
                    addr: self.route.lock().expect("RUPP mutex poisoned").endpoint(),
                    len: inbound.quic_packet.len(),
                    stride: inbound.quic_packet.len(),
                    ecn: None,
                    dst_ip: None,
                };
                let _ = source;
                return Poll::Ready(Ok(1));
            }
        }

        fn local_addr(&self) -> io::Result<SocketAddr> {
            self.io.local_addr()
        }

        fn max_transmit_segments(&self) -> usize {
            1
        }

        fn max_receive_segments(&self) -> usize {
            1
        }

        fn may_fragment(&self) -> bool {
            true
        }
    }

    fn to_io_error(error: TransportError) -> io::Error {
        let kind = match error {
            TransportError::UnexpectedDestination => io::ErrorKind::PermissionDenied,
            TransportError::TrafficLimit | TransportError::DatagramTooLarge => io::ErrorKind::Other,
            _ => io::ErrorKind::InvalidData,
        };
        io::Error::new(kind, error)
    }

    pub use RuppUdpSocket as Socket;
}

#[cfg(feature = "transport")]
pub use quinn_socket::Socket as RuppUdpSocket;

/// Create a Quinn endpoint with the supplied allocation-scoped RUPP socket and
/// certificate pins. The caller can pass this endpoint to `wtransport` to
/// negotiate a real WebTransport session.
#[cfg(feature = "transport")]
pub async fn create_quinn_endpoint(
    allocation: &IssuedAllocation,
) -> Result<(quinn::Endpoint, std::sync::Arc<RuppUdpSocket>), TransportError> {
    let (endpoint, socket, _) = build_quinn_endpoint(allocation).await?;
    Ok((endpoint, socket))
}

#[cfg(feature = "transport")]
async fn build_quinn_endpoint(
    allocation: &IssuedAllocation,
) -> Result<
    (
        quinn::Endpoint,
        std::sync::Arc<RuppUdpSocket>,
        std::sync::Arc<std::sync::atomic::AtomicU8>,
    ),
    TransportError,
> {
    allocation.validate()?;
    let socket = RuppUdpSocket::bind(allocation).await?;
    let provider = std::sync::Arc::new(rustls::crypto::ring::default_provider());
    let certificate_failure = std::sync::Arc::new(std::sync::atomic::AtomicU8::new(0));
    let verifier = std::sync::Arc::new(PinnedServerVerifier {
        pins: allocation.certificate_sha256.clone(),
        policy: wtransport::tls::client::ServerHashVerification::new(
            allocation
                .certificate_sha256
                .iter()
                .copied()
                .map(wtransport::tls::Sha256Digest::new),
        ),
        provider: provider.clone(),
        certificate_failure: certificate_failure.clone(),
    });
    let mut tls = rustls::ClientConfig::builder_with_provider(provider)
        .with_safe_default_protocol_versions()
        .map_err(|_| TransportError::InvalidCertificatePins)?
        .dangerous()
        .with_custom_certificate_verifier(verifier)
        .with_no_client_auth();
    tls.alpn_protocols = vec![b"h3".to_vec()];
    let quic_crypto = quinn::crypto::rustls::QuicClientConfig::try_from(tls)
        .map_err(|_| TransportError::InvalidCertificatePins)?;
    let mut transport = quinn::TransportConfig::default();
    transport
        .receive_window(quinn::VarInt::from_u32(16 * 1024 * 1024))
        .stream_receive_window(quinn::VarInt::from_u32(1024 * 1024))
        .max_concurrent_bidi_streams(quinn::VarInt::from_u32(32))
        .max_concurrent_uni_streams(quinn::VarInt::from_u32(256))
        .max_idle_timeout(Some(quinn::VarInt::from_u32(15_000).into()))
        .keep_alive_interval(Some(std::time::Duration::from_secs(2)));
    let mut client_config = quinn::ClientConfig::new(std::sync::Arc::new(quic_crypto));
    client_config.transport_config(std::sync::Arc::new(transport));
    let mut endpoint = quinn::Endpoint::new_with_abstract_socket(
        quinn::EndpointConfig::default(),
        None,
        socket.clone(),
        std::sync::Arc::new(quinn::TokioRuntime),
    )
    .map_err(|_| TransportError::InvalidEndpoint)?;
    endpoint.set_default_client_config(client_config);
    Ok((endpoint, socket, certificate_failure))
}

/// Create and handshake one raw QUIC connection using the allocation's
/// endpoint, routing token, backend, and certificate pins.
#[cfg(feature = "transport")]
pub async fn connect_quic(
    allocation: &IssuedAllocation,
) -> Result<
    (
        quinn::Endpoint,
        quinn::Connection,
        std::sync::Arc<RuppUdpSocket>,
    ),
    TransportError,
> {
    let (endpoint, socket, certificate_failure) = build_quinn_endpoint(allocation).await?;
    let server_name = allocation.endpoint.ip().to_string();
    let connection = endpoint
        .connect(allocation.endpoint, &server_name)
        .map_err(|_| TransportError::InvalidEndpoint)?
        .await
        .map_err(|_| {
            certificate_failure_error(
                certificate_failure.load(std::sync::atomic::Ordering::Acquire),
            )
            .unwrap_or(TransportError::SessionRejected)
        })?;
    Ok((endpoint, connection, socket))
}

/// Establish the allocation's actual HTTP/3 WebTransport session. The endpoint,
/// TLS pins and routing token come from the fresh HTTPS allocation. Capabilities
/// advertise locally implemented features; only their intersection with the
/// parsed server mask is negotiated. This only reports a session after the WT
/// library accepts the CONNECT response.
#[cfg(feature = "transport")]
pub async fn connect_webtransport(
    allocation: &IssuedAllocation,
) -> Result<
    (
        quinn::Endpoint,
        wtransport::Connection,
        std::sync::Arc<RuppUdpSocket>,
    ),
    TransportError,
> {
    let (endpoint, socket, certificate_failure) = build_quinn_endpoint(allocation).await?;
    let client = wtransport::Endpoint::from_quinn_endpoint(endpoint.clone());
    let capabilities = format!("{:016x}", allocation.capabilities);
    let url = format!(
        "https://{}:{}/wt?X-Rbx-Capabilities={}",
        allocation.endpoint.ip(),
        allocation.endpoint.port(),
        capabilities
    );
    let options = wtransport::endpoint::ConnectOptions::builder(url)
        .protocol(wtransport::endpoint::ConnectProtocol::WebTransportH3)
        .add_header("x-rbx-capabilities", capabilities.clone())
        .negotiate_capabilities(allocation.capabilities)
        .build();
    let connection = client.connect(options).await.map_err(|error| {
        if let Some(failure) = certificate_failure_error(
            certificate_failure.load(std::sync::atomic::Ordering::Acquire),
        ) {
            failure
        } else {
            match error {
                wtransport::error::ConnectingError::ServerCapabilitiesMismatch => {
                    TransportError::InvalidServerCapabilities
                }
                _ => TransportError::SessionRejected,
            }
        }
    })?;
    Ok((endpoint, connection, socket))
}

#[cfg(feature = "transport")]
fn certificate_failure_error(kind: u8) -> Option<TransportError> {
    match kind {
        1 => Some(TransportError::InvalidCertificatePins),
        3 => Some(TransportError::InvalidCertificateTime),
        4 => Some(TransportError::InvalidCertificatePolicy),
        _ => None,
    }
}

#[cfg(feature = "transport")]
#[derive(Debug)]
struct PinnedServerVerifier {
    pins: Vec<[u8; 32]>,
    policy: wtransport::tls::client::ServerHashVerification,
    provider: std::sync::Arc<rustls::crypto::CryptoProvider>,
    certificate_failure: std::sync::Arc<std::sync::atomic::AtomicU8>,
}

#[cfg(feature = "transport")]
impl rustls::client::danger::ServerCertVerifier for PinnedServerVerifier {
    fn verify_server_cert(
        &self,
        end_entity: &rustls::pki_types::CertificateDer<'_>,
        intermediates: &[rustls::pki_types::CertificateDer<'_>],
        server_name: &rustls::pki_types::ServerName<'_>,
        ocsp_response: &[u8],
        now: rustls::pki_types::UnixTime,
    ) -> Result<rustls::client::danger::ServerCertVerified, rustls::Error> {
        use sha2::Digest;
        let digest: [u8; 32] = sha2::Sha256::digest(end_entity.as_ref()).into();
        if !self.pins.iter().any(|pin| pin == &digest) {
            self.certificate_failure
                .store(1, std::sync::atomic::Ordering::Release);
            return Err(rustls::Error::General(
                "server certificate does not match the issued allocation pins".into(),
            ));
        }

        // The fresh HTTPS allocation authenticates the exact certificate hash.
        // WebTransport's serverCertificateHashes policy authenticates that
        // certificate rather than a DNS name. The vendored verifier also checks
        // current validity, a <=14-day lifetime, and an ECDSA P-256 public key.
        // TLS CertificateVerify signatures are independently checked below.
        // https://www.w3.org/TR/webtransport/#dom-webtransportoptions-servercertificatehashes
        let result = self.policy.verify_server_cert(
            end_entity,
            intermediates,
            server_name,
            ocsp_response,
            now,
        );
        if let Err(error) = &result {
            use rustls::CertificateError;
            let kind = match error {
                rustls::Error::InvalidCertificate(
                    CertificateError::Expired
                    | CertificateError::ExpiredContext { .. }
                    | CertificateError::NotValidYet
                    | CertificateError::NotValidYetContext { .. },
                ) => 3,
                _ => 4,
            };
            self.certificate_failure
                .store(kind, std::sync::atomic::Ordering::Release);
        }
        result
    }

    fn verify_tls12_signature(
        &self,
        message: &[u8],
        cert: &rustls::pki_types::CertificateDer<'_>,
        dss: &rustls::DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        rustls::crypto::verify_tls12_signature(
            message,
            cert,
            dss,
            &self.provider.signature_verification_algorithms,
        )
    }

    fn verify_tls13_signature(
        &self,
        message: &[u8],
        cert: &rustls::pki_types::CertificateDer<'_>,
        dss: &rustls::DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        rustls::crypto::verify_tls13_signature(
            message,
            cert,
            dss,
            &self.provider.signature_verification_algorithms,
        )
    }

    fn supported_verify_schemes(&self) -> Vec<rustls::SignatureScheme> {
        self.provider
            .signature_verification_algorithms
            .supported_schemes()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn allocation() -> IssuedAllocation {
        IssuedAllocation {
            endpoint: "8.8.8.8:53640".parse().unwrap(),
            backend: Ipv4Addr::new(10, 1, 2, 3),
            token: [0x12; 16],
            certificate_sha256: vec![[0x77; 32]],
            capabilities: 0x10_0000,
        }
    }

    fn server_header() -> Vec<u8> {
        let next = [0x93; 16];
        let mut bytes = vec![3, 0, 0, 23, 1, 17, 2];
        bytes.extend_from_slice(&next);
        bytes
    }

    #[test]
    fn client_header_matches_existing_python_vector() {
        assert_eq!(
            encode_client_header(
                &[0x12; 16],
                Ipv4Addr::new(10, 1, 2, 3),
                53640,
                TokenKind::Initial
            )
            .unwrap(),
            [
                &[1, 0, 0, 31, 1, 17, 1][..],
                &[0x12; 16][..],
                &[2, 6, 10, 1, 2, 3, 0xd1, 0x88][..]
            ]
            .concat()
        );
    }

    #[test]
    fn unwrapped_quic_datagram_is_borrowed_without_copy() {
        let mut transport = RuppTransport::new(&allocation()).unwrap();
        let incoming = [0x40, 1, 2, 3];
        let result = transport.unwrap_incoming(&incoming).unwrap();
        assert_eq!(result.quic_packet, incoming);
        assert_eq!(result.quic_packet.as_ptr(), incoming.as_ptr());
        assert!(!result.token_rotated);
    }

    #[test]
    fn server_issued_token_rotation_changes_only_the_token_and_kind() {
        let mut transport = RuppTransport::new(&allocation()).unwrap();
        let before = transport.issued_header().to_vec();
        let mut datagram = server_header();
        datagram.extend_from_slice(&[0xaa, 0xbb]);
        let result = transport.unwrap_incoming(&datagram).unwrap();
        assert_eq!(result.quic_packet, &[0xaa, 0xbb]);
        assert!(result.token_rotated);
        assert_eq!(transport.token_kind(), TokenKind::Rotated);
        assert_eq!(transport.stats().token_rotations, 1);
        assert_ne!(before, transport.issued_header());
        assert!(transport
            .issued_header()
            .ends_with(&[10, 1, 2, 3, 0xd1, 0x88]));
    }

    #[test]
    fn malformed_rupp_and_unallocated_send_are_rejected() {
        let mut transport = RuppTransport::new(&allocation()).unwrap();
        assert!(transport.unwrap_incoming(&[1, 0]).is_err());
        assert_eq!(
            transport.wrap_outgoing("1.1.1.1:443".parse().unwrap(), b"x"),
            Err(TransportError::UnexpectedDestination)
        );
    }

    #[test]
    fn debug_output_redacts_issued_route_tokens_and_header_bytes() {
        let allocation = allocation();
        let debug_allocation = format!("{allocation:?}");
        let route = RuppTransport::new(&allocation).unwrap();
        let debug_route = format!("{route:?}");
        let token_hex = "12".repeat(16);
        assert!(!debug_allocation.contains(&token_hex));
        assert!(!debug_route.contains(&token_hex));
        assert!(debug_allocation.contains("[REDACTED]"));
        assert!(debug_route.contains("[REDACTED]"));
    }

    #[test]
    fn capability_endpoint_and_traffic_limits_fail_closed() {
        let mut config = allocation();
        config.capabilities = 0x200000;
        assert_eq!(
            RuppTransport::new(&config).unwrap_err(),
            TransportError::InvalidCapabilities
        );
        let limited = RuppTransport::new(&allocation())
            .unwrap()
            .with_limits(Some(1), Some(2));
        let mut transport = limited;
        transport
            .wrap_outgoing(transport.endpoint(), b"ab")
            .unwrap();
        transport.record_send_success(2);
        assert_eq!(
            transport.wrap_outgoing(transport.endpoint(), b""),
            Err(TransportError::TrafficLimit)
        );
    }

    #[cfg(feature = "transport-test")]
    fn verify_test_certificate(certificate: &[u8], pin: [u8; 32]) -> Result<(), TransportError> {
        use rustls::client::danger::ServerCertVerifier;
        use std::sync::{atomic::AtomicU8, Arc};

        let failure = Arc::new(AtomicU8::new(0));
        let verifier = PinnedServerVerifier {
            pins: vec![pin],
            policy: wtransport::tls::client::ServerHashVerification::new([
                wtransport::tls::Sha256Digest::new(pin),
            ]),
            provider: Arc::new(rustls::crypto::ring::default_provider()),
            certificate_failure: failure.clone(),
        };
        verifier
            .verify_server_cert(
                &rustls::pki_types::CertificateDer::from(certificate),
                &[],
                &"127.0.0.1".try_into().unwrap(),
                &[],
                rustls::pki_types::UnixTime::since_unix_epoch(std::time::Duration::from_secs(
                    1_700_000_000,
                )),
            )
            .map(|_| ())
            .map_err(|_| {
                certificate_failure_error(failure.load(std::sync::atomic::Ordering::Acquire))
                    .expect("certificate rejection must retain a safe diagnostic")
            })
    }

    #[cfg(feature = "transport-test")]
    fn certificate_fixture(before_days: i64, after_days: i64) -> Vec<u8> {
        use wtransport::tls::self_signed::time::{Duration, OffsetDateTime};
        let now = OffsetDateTime::from_unix_timestamp(1_700_000_000).unwrap();
        wtransport::Identity::self_signed_builder()
            .subject_alt_names(["allocation-pinned.invalid"])
            .validity_period(
                now + Duration::days(before_days),
                now + Duration::days(after_days),
            )
            .build()
            .unwrap()
            .certificate_chain()
            .as_ref()[0]
            .der()
            .to_vec()
    }

    #[cfg(feature = "transport-test")]
    #[test]
    fn pinned_certificate_accepts_exact_hash_without_hostname_trust() {
        use sha2::Digest;
        let certificate = certificate_fixture(-1, 13);
        let pin = sha2::Sha256::digest(&certificate).into();
        assert_eq!(verify_test_certificate(&certificate, pin), Ok(()));
        assert_eq!(
            verify_test_certificate(&certificate, [0; 32]),
            Err(TransportError::InvalidCertificatePins)
        );
    }

    #[cfg(feature = "transport-test")]
    #[test]
    fn pinned_certificate_rejects_expired_and_future_even_with_matching_hash() {
        use sha2::Digest;
        for (before, after) in [(-2, -1), (1, 2)] {
            let certificate = certificate_fixture(before, after);
            let pin = sha2::Sha256::digest(&certificate).into();
            assert_eq!(
                verify_test_certificate(&certificate, pin),
                Err(TransportError::InvalidCertificateTime)
            );
        }
    }

    #[cfg(feature = "transport-test")]
    #[test]
    fn pinned_certificate_rejects_long_lifetime_and_malformed_der() {
        use sha2::Digest;
        for certificate in [certificate_fixture(-1, 14), vec![0x30, 0x00]] {
            let pin = sha2::Sha256::digest(&certificate).into();
            assert_eq!(
                verify_test_certificate(&certificate, pin),
                Err(TransportError::InvalidCertificatePolicy)
            );
        }
    }

    #[cfg(feature = "transport")]
    #[tokio::test]
    async fn loopback_webtransport_uses_issued_certificate_pin_and_real_session() {
        use crate::channels::{frame, reliable_header};
        use crate::transport::session::{
            receive_unreliable, send_unreliable, ReliableReceiver, ReliableSender,
        };
        use sha2::Digest;
        use std::net::Ipv4Addr;
        use tokio::{net::UdpSocket, sync::oneshot, task::JoinHandle};
        use wtransport::{endpoint::Endpoint as WebTransportEndpoint, Identity, ServerConfig};

        // A hash-authenticated endpoint does not need an IP/DNS SAN match.
        let identity = Identity::self_signed(["allocation-pinned.invalid"]).unwrap();
        let certificate = identity.certificate_chain().as_ref()[0].der();
        let cert_pin: [u8; 32] = sha2::Sha256::digest(certificate).into();
        let server = WebTransportEndpoint::server(
            ServerConfig::builder()
                .with_bind_address("127.0.0.1:0".parse().unwrap())
                .with_identity(identity)
                .build(),
        )
        .unwrap();
        let server_addr = server.local_addr().unwrap();

        // A tiny local relay removes the allocation RUPP wrapper on the way to
        // the test server and forwards raw server QUIC packets back to the
        // same client address. No remote host or account is contacted.
        let relay = std::sync::Arc::new(UdpSocket::bind("127.0.0.1:0").await.unwrap());
        let relay_addr = relay.local_addr().unwrap();
        let relay_task: JoinHandle<()> = {
            let relay = relay.clone();
            tokio::spawn(async move {
                let mut client_addr = None;
                let mut buffer = vec![0u8; MAX_UDP_DATAGRAM];
                loop {
                    let Ok((length, source)) = relay.recv_from(&mut buffer).await else {
                        return;
                    };
                    if source == server_addr {
                        if let Some(client_addr) = client_addr {
                            if relay.send_to(&buffer[..length], client_addr).await.is_err() {
                                return;
                            }
                        }
                    } else if let Ok(Some(routing)) = parse_rupp(&buffer[..length]) {
                        client_addr = Some(source);
                        if relay.send_to(routing.payload, server_addr).await.is_err() {
                            return;
                        }
                    }
                }
            })
        };

        let capabilities = 0x10_0000u64;
        let (release_tx, release_rx) = oneshot::channel();
        let server_task: JoinHandle<()> = tokio::spawn(async move {
            let Ok(request) = server.accept().await.await else {
                return;
            };
            assert_eq!(request.path(), "/wt?X-Rbx-Capabilities=0000000000100000");
            assert_eq!(
                request
                    .headers()
                    .get("x-rbx-capabilities")
                    .map(String::as_str),
                Some("0000000000100000")
            );
            if let Ok(connection) = request
                .accept_with_headers([("x-rbx-capabilities", "0000000000100001")])
                .await
            {
                let mut reliable = ReliableReceiver::accept(&connection).await.unwrap();
                assert_eq!((reliable.application(), reliable.channel()), (4, 7));
                assert_eq!(
                    reliable.next().await.unwrap().as_deref(),
                    Some(&b"client-reliable"[..])
                );

                let datagram = receive_unreliable(&connection).await.unwrap();
                assert_eq!(datagram.wire_id, 14);
                assert_eq!(datagram.sequence, 3);
                assert!(datagram.complete);
                assert_eq!(datagram.payload, b"client-datagram");
                send_unreliable(&connection, 16, 4, b"server-datagram").unwrap();

                let opening = connection.open_uni().await.unwrap();
                let mut server_stream = opening.await.unwrap();
                server_stream
                    .write_all(&reliable_header(4, 8).unwrap())
                    .await
                    .unwrap();
                server_stream
                    .write_all(&frame(b"server-reliable").unwrap())
                    .await
                    .unwrap();
                server_stream.finish().await.unwrap();

                let (mut bidi_send, mut bidi_receive) = connection.accept_bi().await.unwrap();
                let mut client_bidi = [0u8; 12];
                bidi_receive.read_exact(&mut client_bidi).await.unwrap();
                assert_eq!(&client_bidi, b"client-bidi!");
                bidi_send.write_all(b"server-bidi!").await.unwrap();
                bidi_send.finish().await.unwrap();

                let _ = release_rx.await;
            }
        });

        let allocation = IssuedAllocation {
            endpoint: relay_addr,
            backend: Ipv4Addr::new(10, 0, 0, 1),
            token: [0x45; 16],
            certificate_sha256: vec![cert_pin],
            capabilities,
        };
        let (endpoint, connection, socket) = match connect_webtransport(&allocation).await {
            Ok(connected) => connected,
            Err(error) => {
                let server_outcome =
                    tokio::time::timeout(std::time::Duration::from_millis(100), server_task).await;
                panic!("loopback WT failed ({error:?}); fixture task: {server_outcome:?}");
            }
        };
        assert!(socket.stats().datagrams_sent > 0);
        assert!(socket.stats().datagrams_received > 0);
        let negotiated = connection.capability_negotiation().unwrap();
        assert_eq!(negotiated.client_offered(), 0x10_0000);
        assert_eq!(negotiated.server_advertised(), 0x10_0001);
        assert_eq!(negotiated.negotiated(), 0x10_0000);
        assert_eq!(
            connection.clone().capability_negotiation(),
            Some(negotiated)
        );

        let sender = ReliableSender::new(&connection);
        // Header-only open is idempotent and must not emit an empty frame.
        sender.open(4, 7).await.unwrap();
        sender.open(4, 7).await.unwrap();
        // An unavailable live provider must not emit an empty application
        // frame. The peer's next frame must still be the generated payload.
        assert!(!sender.send_generated(4, 7, || None::<Vec<u8>>).await.unwrap());
        let mut prepared = false;
        assert!(sender.send_generated(4, 7, || {
            prepared = true;
            Some(b"client-reliable")
        }).await.unwrap());
        assert!(prepared);
        send_unreliable(&connection, 14, 3, b"client-datagram").unwrap();

        let datagram = receive_unreliable(&connection).await.unwrap();
        assert_eq!(datagram.wire_id, 16);
        assert_eq!(datagram.sequence, 4);
        assert_eq!(datagram.payload, b"server-datagram");

        let mut reliable = ReliableReceiver::accept(&connection).await.unwrap();
        assert_eq!((reliable.application(), reliable.channel()), (4, 8));
        assert_eq!(
            reliable.next().await.unwrap().as_deref(),
            Some(&b"server-reliable"[..])
        );
        assert_eq!(reliable.next().await.unwrap(), None);

        let opening = connection.open_bi().await.unwrap();
        let (mut bidi_send, mut bidi_receive) = opening.await.unwrap();
        bidi_send.write_all(b"client-bidi!").await.unwrap();
        bidi_send.finish().await.unwrap();
        let mut server_bidi = [0u8; 12];
        bidi_receive.read_exact(&mut server_bidi).await.unwrap();
        assert_eq!(&server_bidi, b"server-bidi!");

        sender.finish(4, 7).await.unwrap();

        let _ = release_tx.send(());
        connection.close(wtransport::VarInt::from_u32(0), b"test complete");
        endpoint.close(quinn::VarInt::from_u32(0), b"test complete");

        relay_task.abort();
        server_task.await.unwrap();
    }

    async fn rejection_fixture(bad_pin: bool) -> Result<(), TransportError> {
        use sha2::Digest;
        use std::net::Ipv4Addr;
        use tokio::net::UdpSocket;
        use wtransport::{endpoint::Endpoint as WebTransportEndpoint, Identity, ServerConfig};

        let identity = Identity::self_signed(["127.0.0.1"]).unwrap();
        let cert = identity.certificate_chain().as_ref()[0].der();
        let pin: [u8; 32] = sha2::Sha256::digest(cert).into();
        let server = WebTransportEndpoint::server(
            ServerConfig::builder()
                .with_bind_address("127.0.0.1:0".parse().unwrap())
                .with_identity(identity)
                .build(),
        )
        .unwrap();
        let server_addr = server.local_addr().unwrap();
        let relay = std::sync::Arc::new(UdpSocket::bind("127.0.0.1:0").await.unwrap());
        let relay_addr = relay.local_addr().unwrap();
        let relay_task = {
            let relay = relay.clone();
            tokio::spawn(async move {
                let mut client_addr = None;
                let mut buffer = vec![0u8; MAX_UDP_DATAGRAM];
                loop {
                    let Ok((length, source)) = relay.recv_from(&mut buffer).await else {
                        return;
                    };
                    if source == server_addr {
                        if let Some(client_addr) = client_addr {
                            if relay.send_to(&buffer[..length], client_addr).await.is_err() {
                                return;
                            }
                        }
                    } else if let Ok(Some(routing)) = parse_rupp(&buffer[..length]) {
                        client_addr = Some(source);
                        if relay.send_to(routing.payload, server_addr).await.is_err() {
                            return;
                        }
                    }
                }
            })
        };
        let server_task = tokio::spawn(async move {
            let incoming = server.accept().await;
            match incoming.await {
                Err(_) => assert!(bad_pin),
                Ok(request) => {
                    assert!(!bad_pin);
                    let connection = request
                        .accept_with_headers([("x-rbx-capabilities", "000000000010000g")])
                        .await
                        .unwrap();
                    tokio::time::sleep(std::time::Duration::from_millis(500)).await;
                    drop(connection);
                }
            }
        });
        let allocation = IssuedAllocation {
            endpoint: relay_addr,
            backend: Ipv4Addr::new(10, 0, 0, 1),
            token: [0x67; 16],
            certificate_sha256: vec![if bad_pin { [0; 32] } else { pin }],
            capabilities: 0x10_0000,
        };
        let result = connect_webtransport(&allocation).await.map(|_| ());
        relay_task.abort();
        server_task.abort();
        result
    }

    #[tokio::test]
    async fn loopback_rejects_malformed_server_capabilities() {
        assert!(matches!(
            rejection_fixture(false).await,
            Err(TransportError::InvalidServerCapabilities)
        ));
    }

    #[tokio::test]
    async fn loopback_rejects_bad_certificate_pin() {
        assert!(matches!(
            rejection_fixture(true).await,
            Err(TransportError::InvalidCertificatePins)
        ));
    }
}
