//! Bounded, owner-only local console transport and process session lease.
//!
//! The transport accepts one newline-delimited JSON request per Unix socket
//! connection. It keeps at most one peer and reads/writes small slices per
//! poll so a large request cannot monopolize the client loop.

use serde_json::{json, Value};
use std::{
    fs::{self, File, OpenOptions},
    io::{self, Read, Write},
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

pub const MAX_SOURCE_BYTES: usize = 100 * 1024 * 1024;
pub const MAX_REQUEST_BYTES: usize = 104 * 1024 * 1024;
pub const MAX_RESPONSE_BYTES: usize = 1024 * 1024;
const READ_SLICE: usize = 64 * 1024;
const MAX_READ_PER_POLL: usize = 256 * 1024;
const MAX_WRITE_PER_POLL: usize = 256 * 1024;
const PEER_TTL: Duration = Duration::from_secs(60);

#[derive(Debug)]
pub struct SessionLease {
    _file: File,
}

impl SessionLease {
    /// Hold `root/secrets/client-session.lock` until this value is dropped.
    /// The file intentionally remains on disk; the OS lock belongs to the FD.
    pub fn acquire(root: impl AsRef<Path>) -> Result<Self, String> {
        #[cfg(unix)]
        {
            use std::os::unix::{
                fs::{MetadataExt, OpenOptionsExt, PermissionsExt},
                io::AsRawFd,
            };

            let secrets = root.as_ref().join("secrets");
            let directory = fs::symlink_metadata(&secrets)
                .map_err(|_| "Client secrets directory is unavailable".to_owned())?;
            if !directory.file_type().is_dir()
                || directory.uid() != unsafe { libc::geteuid() }
                || directory.permissions().mode() & 0o077 != 0
            {
                return Err("Client secrets directory must be owned and private".into());
            }
            let path = secrets.join("client-session.lock");
            let mut options = OpenOptions::new();
            options.read(true).write(true).create(true).mode(0o600);
            options.custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC);

            let file = options
                .open(&path)
                .map_err(|_| "Cannot safely open the client session lease".to_owned())?;
            let metadata = file
                .metadata()
                .map_err(|_| "Cannot inspect the client session lease")?;
            if !metadata.file_type().is_file() || metadata.uid() != unsafe { libc::geteuid() } {
                return Err("Client session lease must be an owned regular file".into());
            }
            file.set_permissions(fs::Permissions::from_mode(0o600))
                .map_err(|_| "Cannot protect the client session lease".to_owned())?;
            // SAFETY: `file` owns a live descriptor. LOCK_EX|LOCK_NB is the
            // documented flock operation; it does not alter file contents.
            let result = unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) };
            if result != 0 {
                return Err("Another client session already holds the account lease".into());
            }
            Ok(Self { _file: file })
        }
        #[cfg(not(unix))]
        {
            let _ = root;
            Err("Safe client session leases are unsupported on this platform".into())
        }
    }
}

#[cfg(unix)]
#[derive(Debug)]
struct Peer {
    stream: std::os::unix::net::UnixStream,
    data: Vec<u8>,
    response: Option<Vec<u8>>,
    written: usize,
    connected_at: Instant,
}

/// One-request-per-connection local script console.
#[cfg(unix)]
#[derive(Debug)]
pub struct ScriptConsole {
    path: PathBuf,
    inode: u64,
    listener: std::os::unix::net::UnixListener,
    peer: Option<Peer>,
    auth_token: Option<String>,
    requests: u64,
}

#[cfg(unix)]
impl ScriptConsole {
    /// Bind an owner-only Unix socket. Unix endpoints do not require a token;
    /// pass one to enforce a token on every request when desired.
    pub fn bind(path: impl AsRef<Path>, auth_token: Option<String>) -> Result<Self, String> {
        use std::os::unix::{
            fs::{FileTypeExt, MetadataExt, PermissionsExt},
            net::UnixListener,
        };
        let path = path.as_ref().to_path_buf();
        let parent = path
            .parent()
            .ok_or("Console socket needs a parent directory")?;
        let parent_meta = fs::symlink_metadata(parent)
            .map_err(|_| "Console socket parent directory is unavailable".to_owned())?;
        if !parent_meta.file_type().is_dir()
            || parent_meta.uid() != unsafe { libc::geteuid() }
            || parent_meta.permissions().mode() & 0o077 != 0
        {
            return Err("Console socket parent must be an owned private directory".into());
        }

        if let Ok(existing) = fs::symlink_metadata(&path) {
            if !existing.file_type().is_socket() || existing.uid() != unsafe { libc::geteuid() } {
                return Err("Console path is not an owned socket".into());
            }
            match std::os::unix::net::UnixStream::connect(&path) {
                Ok(stream) => {
                    drop(stream); // probe only; never send a request or unlink it
                    return Err("A client console is already running".into());
                }
                Err(error)
                    if matches!(
                        error.kind(),
                        io::ErrorKind::ConnectionRefused | io::ErrorKind::NotFound
                    ) =>
                {
                    // Remove only the same stale owned socket observed above.
                    let current = fs::symlink_metadata(&path)
                        .map_err(|_| "Console path changed during stale socket check".to_owned())?;
                    if current.ino() != existing.ino()
                        || current.uid() != existing.uid()
                        || !current.file_type().is_socket()
                    {
                        return Err("Console path changed during stale socket check".into());
                    }
                    fs::remove_file(&path)
                        .map_err(|_| "Cannot remove stale console socket".to_owned())?;
                }
                Err(_) => return Err("An existing client console may still be active".into()),
            }
        }

        let listener =
            UnixListener::bind(&path).map_err(|_| "Cannot bind local client console".to_owned())?;
        if let Err(_) = fs::set_permissions(&path, fs::Permissions::from_mode(0o600)) {
            let _ = fs::remove_file(&path);
            return Err("Cannot protect local client console socket".into());
        }
        if let Err(_) = listener.set_nonblocking(true) {
            let _ = fs::remove_file(&path);
            return Err("Cannot configure local client console".into());
        }
        let inode = fs::symlink_metadata(&path)
            .map_err(|_| "Cannot inspect local client console socket".to_owned())?
            .ino();
        Ok(Self {
            path,
            inode,
            listener,
            peer: None,
            auth_token,
            requests: 0,
        })
    }

    pub fn requests_handled(&self) -> u64 {
        self.requests
    }

    /// Whether a client request or response still needs servicing. In
    /// particular, dispatching `leave` is not proof its reply has been sent.
    pub fn has_pending_peer(&self) -> bool {
        self.peer.is_some()
    }

    /// Accept/read/dispatch/write a bounded slice. Returns whether one request
    /// was dispatched. The caller invokes this during its normal event loop.
    pub fn poll<F>(&mut self, dispatch: &mut F) -> Result<bool, String>
    where
        F: FnMut(Value) -> Result<Value, String>,
    {
        if self.peer.is_none() {
            match self.listener.accept() {
                Ok((stream, _)) => {
                    stream
                        .set_nonblocking(true)
                        .map_err(|_| "Cannot configure console peer")?;
                    self.peer = Some(Peer {
                        stream,
                        data: Vec::new(),
                        response: None,
                        written: 0,
                        connected_at: Instant::now(),
                    });
                }
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => return Ok(false),
                Err(_) => return Err("Cannot accept local console connection".into()),
            }
        }
        let Some(peer) = self.peer.as_mut() else {
            return Ok(false);
        };
        if peer.connected_at.elapsed() > PEER_TTL {
            self.peer = None;
            return Ok(false);
        }

        if peer.response.is_none() {
            let mut chunk = [0u8; READ_SLICE];
            match peer
                .stream
                .read(&mut chunk[..READ_SLICE.min(MAX_READ_PER_POLL)])
            {
                Ok(0) => {
                    self.peer = None;
                    return Ok(false);
                }
                Ok(count) => {
                    if peer.data.len().saturating_add(count) > MAX_REQUEST_BYTES {
                        peer.response = Some(error_response("Source request exceeds size limit"));
                    } else {
                        peer.data.extend_from_slice(&chunk[..count]);
                        if let Some(newline) = peer.data.iter().position(|byte| *byte == b'\n') {
                            if newline + 1 != peer.data.len() {
                                peer.response =
                                    Some(error_response("One request per connection is supported"));
                            } else {
                                let request = parse_request(
                                    &peer.data[..newline],
                                    self.auth_token.as_deref(),
                                );
                                let response = match request {
                                    Ok(request) => match dispatch(request) {
                                        Ok(value) => encode_response(&value).unwrap_or_else(|_| {
                                            error_response("Script response exceeds size limit")
                                        }),
                                        Err(error) => error_response(&error),
                                    },
                                    Err(error) => error_response(&error),
                                };
                                peer.response = Some(response);
                                self.requests = self.requests.saturating_add(1);
                                return Ok(true);
                            }
                        }
                    }
                }
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => return Ok(false),
                Err(_) => {
                    self.peer = None;
                    return Ok(false);
                }
            }
        }

        if let Some(response) = &peer.response {
            let end = response
                .len()
                .min(peer.written.saturating_add(MAX_WRITE_PER_POLL));
            match peer.stream.write(&response[peer.written..end]) {
                Ok(0) => self.peer = None,
                Ok(count) => {
                    peer.written += count;
                    if peer.written >= response.len() {
                        self.peer = None;
                    }
                }
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => {}
                Err(_) => self.peer = None,
            }
        }
        Ok(false)
    }
}

#[cfg(unix)]
impl Drop for ScriptConsole {
    fn drop(&mut self) {
        if let Ok(metadata) = fs::symlink_metadata(&self.path) {
            use std::os::unix::fs::{FileTypeExt, MetadataExt};
            if metadata.ino() == self.inode
                && metadata.file_type().is_socket()
                && metadata.uid() == unsafe { libc::geteuid() }
            {
                let _ = fs::remove_file(&self.path);
            }
        }
    }
}

#[cfg(not(unix))]
#[derive(Debug)]
pub struct ScriptConsole;

#[cfg(not(unix))]
impl ScriptConsole {
    pub fn bind(_path: impl AsRef<Path>, _auth_token: Option<String>) -> Result<Self, String> {
        Err("Safe local script consoles are unsupported on this platform".into())
    }
}

fn parse_request(line: &[u8], token: Option<&str>) -> Result<Value, String> {
    let mut request: Value =
        serde_json::from_slice(line).map_err(|_| "Invalid source request".to_owned())?;
    let object = request.as_object_mut().ok_or("Invalid source request")?;
    if let Some(expected) = token {
        let supplied = object
            .remove("_console_token")
            .and_then(|v| v.as_str().map(str::to_owned))
            .ok_or("Console authentication failed")?;
        if !constant_time_equal(supplied.as_bytes(), expected.as_bytes()) {
            return Err("Console authentication failed".into());
        }
    }
    // The common script command receives stricter checks before it reaches the
    // host. Other operation shapes belong to the dispatcher's own validator.
    if object.contains_key("source") {
        if object
            .get("op")
            .is_some_and(|op| !op.is_null() && op.as_str() != Some("execute"))
        {
            return Err("Invalid script request".into());
        }
        let source = object
            .get("source")
            .and_then(Value::as_str)
            .ok_or("Invalid script request")?;
        if source.trim().is_empty() || source.len() > MAX_SOURCE_BYTES {
            return Err("Invalid script source".into());
        }
        let timeout = object
            .get("timeout_ms")
            .and_then(Value::as_u64)
            .ok_or("Invalid script timeout")?;
        if !(1..=2000).contains(&timeout) {
            return Err("Invalid script timeout".into());
        }
        for key in object.keys() {
            if !matches!(key.as_str(), "op" | "source" | "timeout_ms" | "job_id") {
                return Err("Malformed script request".into());
            }
        }
    }
    Ok(request)
}

fn encode_response(value: &Value) -> Result<Vec<u8>, String> {
    let mut data = serde_json::to_vec(value).map_err(|_| "Invalid console result".to_owned())?;
    data.push(b'\n');
    if data.len() > MAX_RESPONSE_BYTES {
        return Err("Script response exceeds size limit".into());
    }
    Ok(data)
}

fn error_response(message: &str) -> Vec<u8> {
    let safe = if message.len() > 4096 {
        let end = message
            .char_indices()
            .map(|(index, _)| index)
            .take_while(|index| *index <= 4096)
            .last()
            .unwrap_or(0);
        &message[..end]
    } else {
        message
    };
    encode_response(&json!({"ok": false, "values": [], "error_type": "ValueError", "error": safe}))
        .unwrap_or_else(|_| b"{\"ok\":false,\"values\":[],\"error\":\"Console error\"}\n".to_vec())
}

fn constant_time_equal(a: &[u8], b: &[u8]) -> bool {
    let mut diff = a.len() ^ b.len();
    for index in 0..a.len().max(b.len()) {
        diff |=
            usize::from(a.get(index).copied().unwrap_or(0) ^ b.get(index).copied().unwrap_or(0));
    }
    diff == 0
}

/// Send a single JSON request over a local Unix socket and read its response.
#[cfg(unix)]
pub fn send_request(
    path: impl AsRef<Path>,
    request: Value,
    timeout: Duration,
) -> Result<Value, String> {
    send_request_inner(path.as_ref(), request, timeout)
}

/// Send a request to a listener configured with a local bearer token.
#[cfg(unix)]
pub fn send_request_with_token(
    path: impl AsRef<Path>,
    mut request: Value,
    token: &str,
    timeout: Duration,
) -> Result<Value, String> {
    let object = request.as_object_mut().ok_or("Invalid console request")?;
    if object.contains_key("_console_token") {
        return Err("Console token field is reserved".into());
    }
    object.insert("_console_token".into(), Value::String(token.to_owned()));
    send_request_inner(path.as_ref(), request, timeout)
}

#[cfg(unix)]
fn send_request_inner(path: &Path, request: Value, timeout: Duration) -> Result<Value, String> {
    use std::{net::Shutdown, os::unix::net::UnixStream};
    let mut stream =
        UnixStream::connect(path).map_err(|_| "Cannot connect to local console".to_owned())?;
    stream
        .set_read_timeout(Some(timeout))
        .map_err(|_| "Cannot configure console timeout")?;
    stream
        .set_write_timeout(Some(timeout))
        .map_err(|_| "Cannot configure console timeout")?;
    let mut bytes =
        serde_json::to_vec(&request).map_err(|_| "Invalid console request".to_owned())?;
    bytes.push(b'\n');
    if bytes.len() > MAX_REQUEST_BYTES {
        return Err("Source request exceeds size limit".into());
    }
    stream
        .write_all(&bytes)
        .map_err(|_| "Cannot send local console request".to_owned())?;
    stream
        .shutdown(Shutdown::Write)
        .map_err(|_| "Cannot finish local console request")?;
    let mut response = Vec::with_capacity(4096);
    let mut chunk = [0u8; READ_SLICE];
    loop {
        let count = stream
            .read(&mut chunk)
            .map_err(|_| "Cannot read local console response".to_owned())?;
        if count == 0 {
            break;
        }
        if response.len().saturating_add(count) > MAX_RESPONSE_BYTES {
            return Err("Console response exceeds size limit".into());
        }
        response.extend_from_slice(&chunk[..count]);
        if response.contains(&b'\n') {
            break;
        }
    }
    let end = response
        .iter()
        .position(|b| *b == b'\n')
        .ok_or("Client closed the console without a result")?;
    serde_json::from_slice(&response[..end]).map_err(|_| "Invalid console response".into())
}

#[cfg(not(unix))]
pub fn send_request(
    _path: impl AsRef<Path>,
    _request: Value,
    _timeout: Duration,
) -> Result<Value, String> {
    Err("Safe local script consoles are unsupported on this platform".into())
}

#[cfg(not(unix))]
pub fn send_request_with_token(
    _path: impl AsRef<Path>,
    _request: Value,
    _token: &str,
    _timeout: Duration,
) -> Result<Value, String> {
    Err("Safe local script consoles are unsupported on this platform".into())
}

#[cfg(unix)]
#[cfg(test)]
mod tests {
    use super::*;
    use std::{os::unix::fs::PermissionsExt, thread};

    fn temp_dir() -> PathBuf {
        static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);
        let path = std::env::temp_dir().join(format!(
            "rove-console-test-{}-{}-{}",
            unsafe { libc::geteuid() },
            std::process::id(),
            NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
        path
    }

    #[test]
    fn lease_is_private_exclusive_and_released_on_drop() {
        let root = temp_dir();
        fs::create_dir(root.join("secrets")).unwrap();
        fs::set_permissions(root.join("secrets"), fs::Permissions::from_mode(0o700)).unwrap();
        let first = SessionLease::acquire(&root).unwrap();
        assert!(SessionLease::acquire(&root).is_err());
        let metadata = fs::metadata(root.join("secrets/client-session.lock")).unwrap();
        assert_eq!(metadata.permissions().mode() & 0o777, 0o600);
        drop(first);
        assert!(SessionLease::acquire(&root).is_ok());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn socket_is_private_and_active_endpoint_is_never_replaced() {
        let root = temp_dir();
        let path = root.join("client.sock");
        let server = ScriptConsole::bind(&path, None).unwrap();
        assert_eq!(
            fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o600
        );
        assert!(ScriptConsole::bind(&path, None).is_err());
        assert!(path.exists());
        drop(server);
        assert!(!path.exists());
        fs::remove_dir(root).unwrap();
    }

    #[test]
    fn one_request_roundtrip_preserves_source_bytes() {
        let root = temp_dir();
        let path = root.join("client.sock");
        let mut server = ScriptConsole::bind(&path, None).unwrap();
        let server_path = path.clone();
        let thread = thread::spawn(move || {
            let mut seen = None;
            for _ in 0..1000 {
                let dispatched = server
                    .poll(&mut |value| {
                        seen = value
                            .get("source")
                            .and_then(Value::as_str)
                            .map(str::to_owned);
                        Ok(json!({"ok":true,"values":[]}))
                    })
                    .unwrap();
                if dispatched {
                    assert!(
                        server.has_pending_peer(),
                        "reply is still queued after dispatch"
                    );
                }
                if server.requests_handled() == 1 && !server.has_pending_peer() {
                    break;
                }
                thread::sleep(Duration::from_millis(1));
            }
            seen
        });
        let source = "local x = 'héllo'\nreturn x";
        let response = send_request(
            &server_path,
            json!({"source":source,"timeout_ms":1000}),
            Duration::from_secs(2),
        )
        .unwrap();
        assert_eq!(response["ok"], true);
        assert_eq!(thread.join().unwrap().as_deref(), Some(source));
        fs::remove_dir(root).unwrap();
    }

    #[test]
    fn authenticated_console_rejects_bad_token_before_dispatch() {
        let root = temp_dir();
        let path = root.join("client.sock");
        let mut server = ScriptConsole::bind(&path, Some("expected".into())).unwrap();
        let server_thread = thread::spawn(move || {
            for _ in 0..1000 {
                let _ = server.poll(&mut |_| panic!("unauthenticated request dispatched"));
                thread::sleep(Duration::from_millis(1));
            }
        });
        let denied = send_request_with_token(
            &path,
            json!({"source":"return 1","timeout_ms":5}),
            "wrong",
            Duration::from_secs(2),
        )
        .unwrap();
        assert_eq!(denied["ok"], false);
        server_thread.join().unwrap();
        fs::remove_dir(root).unwrap();
    }
}
