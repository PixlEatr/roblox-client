//! Reliable channel streams and unreliable application datagrams for a
//! negotiated WebTransport session.

use super::TransportError;
use crate::channels::{
    encode_datagram, frame, parse_datagram, parse_reliable_header, reliable_header, FrameDecoder,
    MAX_FRAME,
};
use std::collections::{HashMap, VecDeque};
use tokio::sync::Mutex;

const RELIABLE_READ_CHUNK: usize = 2048;

/// One outgoing reliable stream per application/channel pair.
pub struct ReliableSender {
    connection: wtransport::Connection,
    streams: Mutex<HashMap<(u8, u32), wtransport::SendStream>>,
}

impl ReliableSender {
    pub fn new(connection: &wtransport::Connection) -> Self {
        Self {
            connection: connection.clone(),
            streams: Mutex::new(HashMap::new()),
        }
    }

    /// Announce a channel without sending an empty application message.
    pub async fn open(&self, application: u8, channel: u32) -> Result<(), TransportError> {
        let header = reliable_header(application, channel)?;
        let mut streams = self.streams.lock().await;
        if !streams.contains_key(&(application, channel)) {
            let opening = self
                .connection
                .open_uni()
                .await
                .map_err(|_| TransportError::StreamFailure)?;
            let mut stream = opening.await.map_err(|_| TransportError::StreamFailure)?;
            stream
                .write_all(&header)
                .await
                .map_err(|_| TransportError::StreamFailure)?;
            streams.insert((application, channel), stream);
        }
        Ok(())
    }

    /// Send one length-framed message, opening and announcing its channel on
    /// the first call. Both the channel frame and per-message frame are bounded.
    pub async fn send(
        &self,
        application: u8,
        channel: u32,
        payload: &[u8],
    ) -> Result<(), TransportError> {
        self.send_generated(application, channel, || Some(payload))
            .await
            .map(|_| ())
    }

    /// Construct a time-sensitive message after any stream-open and writer-lock
    /// waits. Returning None suppresses the write (for example, when a required
    /// live observation is unavailable). True means write_all actually succeeded.
    pub async fn send_generated<P: AsRef<[u8]>>(
        &self,
        application: u8,
        channel: u32,
        make_payload: impl FnOnce() -> Option<P>,
    ) -> Result<bool, TransportError> {
        let channel_header = reliable_header(application, channel)?;
        let mut streams = self.streams.lock().await;
        if !streams.contains_key(&(application, channel)) {
            let opening = self
                .connection
                .open_uni()
                .await
                .map_err(|_| TransportError::StreamFailure)?;
            let mut stream = opening.await.map_err(|_| TransportError::StreamFailure)?;
            stream
                .write_all(&channel_header)
                .await
                .map_err(|_| TransportError::StreamFailure)?;
            streams.insert((application, channel), stream);
        }
        let Some(payload) = make_payload() else { return Ok(false); };
        let message = frame(payload.as_ref())?;
        streams
            .get_mut(&(application, channel))
            .expect("inserted reliable channel")
            .write_all(&message)
            .await
            .map_err(|_| TransportError::StreamFailure)?;
        Ok(true)
    }

    /// Finish a channel stream after its final message.
    pub async fn finish(&self, application: u8, channel: u32) -> Result<(), TransportError> {
        let mut streams = self.streams.lock().await;
        if let Some(mut stream) = streams.remove(&(application, channel)) {
            stream
                .finish()
                .await
                .map_err(|_| TransportError::StreamFailure)?;
        }
        Ok(())
    }
}

/// Incoming reliable messages from one announced application/channel stream.
pub struct ReliableReceiver {
    application: u8,
    channel: u32,
    stream: wtransport::RecvStream,
    decoder: FrameDecoder,
    pending: VecDeque<Vec<u8>>,
    ended: bool,
}

impl ReliableReceiver {
    /// Accept the next peer-initiated reliable channel stream, validate its
    /// seven-byte channel header, and install the bounded incremental frame
    /// decoder.
    pub async fn accept(connection: &wtransport::Connection) -> Result<Self, TransportError> {
        let mut stream = connection
            .accept_uni()
            .await
            .map_err(|_| TransportError::StreamFailure)?;
        let mut header = [0u8; 7];
        stream
            .read_exact(&mut header)
            .await
            .map_err(|_| TransportError::StreamFailure)?;
        let (application, channel) = parse_reliable_header(&header)?;
        Ok(Self {
            application,
            channel,
            stream,
            decoder: FrameDecoder::new(MAX_FRAME),
            pending: VecDeque::new(),
            ended: false,
        })
    }

    pub fn application(&self) -> u8 {
        self.application
    }

    pub fn channel(&self) -> u32 {
        self.channel
    }

    /// Return the next complete message. `None` means the stream ended cleanly;
    /// an EOF in a partial frame is reported as malformed input.
    pub async fn next(&mut self) -> Result<Option<Vec<u8>>, TransportError> {
        if let Some(message) = self.pending.pop_front() {
            return Ok(Some(message));
        }
        if self.ended {
            return Ok(None);
        }

        let mut scratch = [0u8; RELIABLE_READ_CHUNK];
        loop {
            match self.stream.read(&mut scratch).await.map_err(read_error)? {
                Some(count) if count != 0 => {
                    self.pending.extend(self.decoder.feed(&scratch[..count])?);
                    if let Some(message) = self.pending.pop_front() {
                        return Ok(Some(message));
                    }
                }
                Some(_) => continue,
                None => {
                    self.decoder.finish()?;
                    self.ended = true;
                    return Ok(None);
                }
            }
        }
    }
}

fn read_error(error: wtransport::error::StreamReadError) -> TransportError {
    use wtransport::error::StreamReadError;
    match error {
        StreamReadError::Reset(code) => TransportError::StreamReset {
            code: code.into_inner(),
        },
        StreamReadError::NotConnected => TransportError::StreamNotConnected,
        StreamReadError::QuicProto => TransportError::StreamProtocolError,
    }
}

#[cfg(test)]
mod read_error_tests {
    use super::*;
    #[test]
    fn preserves_peer_reset_codes_and_distinguishes_connection_failure() {
        use wtransport::error::StreamReadError;
        assert_eq!(
            read_error(StreamReadError::Reset(17u32.into())),
            TransportError::StreamReset { code: 17 }
        );
        assert_eq!(
            read_error(StreamReadError::NotConnected),
            TransportError::StreamNotConnected
        );
        assert_eq!(
            read_error(StreamReadError::QuicProto),
            TransportError::StreamProtocolError
        );
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct UnreliablePacket {
    pub wire_id: u32,
    pub sequence: u64,
    pub total: u64,
    pub offset: u64,
    pub complete: bool,
    pub payload: Vec<u8>,
}

/// Send one bounded, unfragmented application datagram using the shared wire
/// codec. The caller owns per-channel wire-ID allocation and sequence state.
pub fn send_unreliable(
    connection: &wtransport::Connection,
    wire_id: u32,
    sequence: u64,
    payload: &[u8],
) -> Result<(), TransportError> {
    let packet = encode_datagram(wire_id, sequence, payload)?;
    connection
        .send_datagram(packet)
        .map_err(|_| TransportError::DatagramFailure)
}

/// Receive and parse one application datagram. Fragment metadata is preserved
/// for the caller; this method does not pretend a fragment is a full message.
pub async fn receive_unreliable(
    connection: &wtransport::Connection,
) -> Result<UnreliablePacket, TransportError> {
    let datagram = connection
        .receive_datagram()
        .await
        .map_err(|_| TransportError::DatagramFailure)?;
    let payload = datagram.payload();
    let packet = parse_datagram(&payload)?;
    Ok(UnreliablePacket {
        wire_id: packet.wire_id,
        sequence: packet.sequence,
        total: packet.total,
        offset: packet.offset,
        complete: packet.complete,
        payload: packet.payload.to_vec(),
    })
}
