//! Explicit script HTTP/WebSocket calls. No ambient Roblox authentication.
use base64::{engine::general_purpose::STANDARD, Engine};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    io::{ErrorKind, Read},
    net::TcpStream,
    sync::{Mutex, OnceLock},
    thread,
    time::Duration,
};
use tungstenite::{stream::MaybeTlsStream, Message, WebSocket};

type Socket = WebSocket<MaybeTlsStream<TcpStream>>;
// USSI downloads versioned API dumps through HttpService. Keep a clear upper
// bound, but allow the multi-megabyte documents its serializer needs.
const HTTP_RESPONSE_LIMIT: usize = 16 * 1024 * 1024;
const HTTP_REQUEST_TIMEOUT: Duration = Duration::from_secs(20);
#[derive(Default)]
struct Sockets {
    next: u64,
    sockets: HashMap<u64, Socket>,
}
fn sockets() -> &'static Mutex<Sockets> {
    static S: OnceLock<Mutex<Sockets>> = OnceLock::new();
    S.get_or_init(|| Mutex::new(Sockets::default()))
}
#[derive(Default)]
struct Jobs {
    next: u64,
    pending: HashMap<u64, Option<Value>>,
}
fn jobs() -> &'static Mutex<Jobs> {
    static J: OnceLock<Mutex<Jobs>> = OnceLock::new();
    J.get_or_init(|| Mutex::new(Jobs::default()))
}
fn text<'a>(v: &'a Value, key: &str) -> Result<&'a str, String> {
    v[key].as_str().ok_or_else(|| format!("{key} must be text"))
}
fn timeout(stream: &mut MaybeTlsStream<TcpStream>) -> Result<(), String> {
    let socket = match stream {
        MaybeTlsStream::Plain(s) => s,
        MaybeTlsStream::NativeTls(s) => s.get_mut(),
        _ => return Err("unsupported TLS stream".into()),
    };
    socket
        .set_read_timeout(Some(Duration::from_millis(1)))
        .map_err(|e| e.to_string())?;
    socket
        .set_write_timeout(Some(Duration::from_millis(200)))
        .map_err(|e| e.to_string())
}
fn websocket_url(raw: &str, base: Option<&url::Url>) -> Result<url::Url, String> {
    let mut url = match base {
        Some(base) => base.join(raw),
        None => url::Url::parse(raw),
    }.map_err(|e| e.to_string())?;
    let scheme = match url.scheme() {
        "ws" | "http" => "ws",
        "wss" | "https" => "wss",
        _ => return Err("WS(S) URL required".into()),
    };
    url.set_scheme(scheme).map_err(|_| "Invalid WebSocket URL")?;
    if url.host_str().is_none() || url.fragment().is_some() || !url.username().is_empty() || url.password().is_some() {
        return Err("WebSocket URL requires a host and cannot contain credentials or a fragment".into());
    }
    Ok(url)
}
fn connect_socket(raw: &str) -> Result<Socket, String> {
    let mut url = websocket_url(raw, None)?;
    let config = tungstenite::protocol::WebSocketConfig::default()
        .max_message_size(Some(128 * 1024))
        .max_frame_size(Some(128 * 1024));
    for attempt in 0..=3 {
        // Rebuild the handshake for each destination so Host and TLS SNI
        // follow the redirected URL. No Roblox or previous-origin headers
        // are attached to script WebSockets.
        match tungstenite::client::connect_with_config(url.as_str(), Some(config), 0) {
            Ok((socket, _)) => return Ok(socket),
            Err(tungstenite::Error::Http(response)) if response.status().is_redirection() => {
                if attempt == 3 { return Err("WebSocket redirect limit exceeded".into()); }
                let location = response.headers().get("Location")
                    .ok_or("WebSocket redirect is missing Location")?
                    .to_str().map_err(|e| e.to_string())?;
                let next = websocket_url(location, Some(&url))?;
                if url.scheme() == "wss" && next.scheme() != "wss" {
                    return Err("WebSocket redirect would downgrade TLS".into());
                }
                url = next;
            }
            Err(error) => return Err(error.to_string()),
        }
    }
    unreachable!()
}
fn operation(v: Value) -> Result<Value, String> {
    match text(&v, "operation")? {
        "start" => {
            let request = v["request"].clone();
            if !["http", "connect", "send", "close", "decompile"].contains(&text(&request, "operation")?) {
                return Err("invalid asynchronous operation".into());
            }
            let mut jobs = self::jobs().lock().map_err(|_| "job pool lock failed")?;
            if jobs.pending.len() >= 16 {
                return Err("network job limit exceeded".into());
            }
            jobs.next += 1;
            let id = jobs.next;
            jobs.pending.insert(id, None);
            drop(jobs);
            thread::spawn(move || {
                let result = match operation(request) {
                    Ok(v) => v,
                    Err(e) => json!({"error":e}),
                };
                if let Ok(mut jobs) = self::jobs().lock() {
                    if let Some(slot) = jobs.pending.get_mut(&id) {
                        *slot = Some(result);
                    }
                }
            });
            Ok(json!({"id":id}))
        }
        "result" => {
            let id = v["id"].as_u64().ok_or("job ID required")?;
            let mut jobs = self::jobs().lock().map_err(|_| "job pool lock failed")?;
            let slot = jobs.pending.get_mut(&id).ok_or("unknown network job")?;
            if let Some(value) = slot.take() {
                jobs.pending.remove(&id);
                Ok(json!({"ready":true,"value":value}))
            } else {
                Ok(json!({"ready":false}))
            }
        }
        "decompile" => crate::decompiler::decompile(&v),
        "http" => {
            let url = text(&v, "Url")?;
            if !(url.starts_with("https://") || url.starts_with("http://")) {
                return Err("HTTP(S) URL required".into());
            }
            let method = v["Method"].as_str().unwrap_or("GET");
            if !["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"].contains(&method) {
                return Err("unsupported HTTP method".into());
            }
            let agent = ureq::AgentBuilder::new()
                .timeout(HTTP_REQUEST_TIMEOUT)
                .redirects(3)
                .build();
            let mut request = agent
                .request(method, url)
                .set("User-Agent", "RobloxClientResearch/local");
            if let Some(headers) = v["Headers"].as_object() {
                if headers.len() > 64 {
                    return Err("too many HTTP headers".into());
                }
                for (key, value) in headers {
                    let value = value.as_str().ok_or("header value must be text")?;
                    if key.len() + value.len() > 8192
                        || key.contains(['\r', '\n'])
                        || value.contains(['\r', '\n'])
                    {
                        return Err("invalid HTTP header".into());
                    }
                    request = request.set(key, value);
                }
            }
            let result = if let Some(body) = v["BodyBase64"].as_str() {
                request.send_bytes(&STANDARD.decode(body).map_err(|e| e.to_string())?)
            } else {
                request.call()
            };
            let response = match result {
                Ok(r) => r,
                Err(ureq::Error::Status(_, r)) => r,
                Err(e) => return Err(e.to_string()),
            };
            if method != "HEAD"
                && response
                    .header("Content-Length")
                    .and_then(|value| value.parse::<u64>().ok())
                    .is_some_and(|length| length > HTTP_RESPONSE_LIMIT as u64)
            {
                return Err("HTTP body exceeds configured response limit".into());
            }
            let status = response.status();
            let message = response.status_text().to_string();
            let mut headers = serde_json::Map::new();
            for name in response.headers_names() {
                if let Some(value) = response.header(&name) {
                    headers.insert(name, json!(value));
                }
            }
            let mut body = Vec::new();
            response
                .into_reader()
                .take(HTTP_RESPONSE_LIMIT as u64 + 1)
                .read_to_end(&mut body)
                .map_err(|e| e.to_string())?;
            if body.len() > HTTP_RESPONSE_LIMIT {
                return Err("HTTP body exceeds configured response limit".into());
            }
            Ok(
                json!({"StatusCode":status,"StatusMessage":message,"Success":(200..300).contains(&status),"Headers":headers,"BodyBase64":STANDARD.encode(body)}),
            )
        }
        "connect" => {
            let url = text(&v, "Url")?;
            if !(url.starts_with("wss://") || url.starts_with("ws://")) {
                return Err("WS(S) URL required".into());
            }
            if sockets()
                .lock()
                .map_err(|_| "socket pool lock failed")?
                .sockets
                .len()
                >= 16
            {
                return Err("WebSocket limit exceeded".into());
            }
            let mut socket = connect_socket(url)?;
            timeout(socket.get_mut())?;
            let mut pool = sockets().lock().map_err(|_| "socket pool lock failed")?;
            if pool.sockets.len() >= 16 {
                return Err("WebSocket limit exceeded".into());
            }
            pool.next += 1;
            let id = pool.next;
            pool.sockets.insert(id, socket);
            Ok(json!({"id":id}))
        }
        "send" | "poll" | "close" => {
            let id = v["id"].as_u64().ok_or("socket ID required")?;
            let mut pool = sockets().lock().map_err(|_| "socket pool lock failed")?;
            if v["operation"] == "close" {
                if let Some(mut socket) = pool.sockets.remove(&id) {
                    let _ = socket.close(None);
                }
                return Ok(json!({"closed":true}));
            }
            let socket = pool.sockets.get_mut(&id).ok_or("socket is closed")?;
            if v["operation"] == "send" {
                let data = STANDARD
                    .decode(text(&v, "BodyBase64")?)
                    .map_err(|e| e.to_string())?;
                let message = if v["binary"].as_bool().unwrap_or(false) {
                    Message::Binary(data.into())
                } else {
                    Message::Text(
                        String::from_utf8(data)
                            .map_err(|_| "text WebSocket message must be UTF-8")?
                            .into(),
                    )
                };
                socket.send(message).map_err(|e| e.to_string())?;
                return Ok(json!({}));
            }
            let mut messages = Vec::new();
            let mut closed = false;
            for _ in 0..16 {
                match socket.read() {
                    Ok(Message::Text(s)) => messages.push(STANDARD.encode(s.as_bytes())),
                    Ok(Message::Binary(s)) => messages.push(STANDARD.encode(s)),
                    Ok(Message::Close(_))
                    | Err(tungstenite::Error::ConnectionClosed)
                    | Err(tungstenite::Error::AlreadyClosed) => {
                        closed = true;
                        break;
                    }
                    Ok(_) => {
                        let _ = socket.flush();
                    }
                    Err(tungstenite::Error::Io(e))
                        if matches!(e.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut) =>
                    {
                        break
                    }
                    Err(e) => return Err(e.to_string()),
                }
            }
            if closed {
                pool.sockets.remove(&id);
            }
            Ok(json!({"messages":messages,"closed":closed}))
        }
        _ => Err("unsupported network operation".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{io::Write, net::TcpListener, thread, time::Instant};

    fn response_server(body: Vec<u8>, delay: Duration) -> (String, thread::JoinHandle<()>) {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let address = listener.local_addr().unwrap();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = Vec::new();
            let mut chunk = [0_u8; 256];
            while !request.windows(4).any(|bytes| bytes == b"\r\n\r\n") {
                let count = stream.read(&mut chunk).unwrap();
                assert!(count > 0, "client closed before sending HTTP headers");
                request.extend_from_slice(&chunk[..count]);
                assert!(
                    request.len() <= 16 * 1024,
                    "request headers exceeded fixture limit"
                );
            }
            if !delay.is_zero() {
                thread::sleep(delay);
            }
            let header = format!(
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                body.len()
            );
            if stream.write_all(header.as_bytes()).is_ok() {
                let _ = stream.write_all(&body);
                let _ = stream.flush();
            }
        });
        (format!("http://{address}/fixture"), server)
    }

    fn get(url: &str) -> Result<Value, String> {
        operation(json!({"operation":"http", "Url":url, "Method":"GET"}))
    }

    #[test]
    fn websocket_follows_http_location_and_rebuilds_the_host_header() {
        let destination = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let address = destination.local_addr().unwrap();
        let echo = thread::spawn(move || {
            let (stream, _) = destination.accept().unwrap();
            let mut socket = tungstenite::accept_hdr(stream, |request: &tungstenite::handshake::server::Request, response| {
                assert_eq!(request.headers().get("Host").unwrap(), address.to_string().as_str());
                assert_eq!(request.uri().path(), "/echo");
                Ok(response)
            }).unwrap();
            let message = socket.read().unwrap();
            socket.send(message).unwrap();
        });
        let redirect = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let first = redirect.local_addr().unwrap();
        let server = thread::spawn(move || {
            let (mut stream, _) = redirect.accept().unwrap();
            let mut bytes = Vec::new();
            while !bytes.ends_with(b"\r\n\r\n") {
                let mut byte = [0u8; 1]; stream.read_exact(&mut byte).unwrap(); bytes.push(byte[0]);
                assert!(bytes.len() < 16384);
            }
            write!(stream, "HTTP/1.1 301 Moved Permanently\r\nLocation: http://{address}/echo\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").unwrap();
        });
        let mut socket = connect_socket(&format!("ws://{first}/start")).unwrap();
        socket.send(Message::Text("redirect echo".into())).unwrap();
        assert_eq!(socket.read().unwrap().into_text().unwrap(), "redirect echo");
        server.join().unwrap(); echo.join().unwrap();
    }

    #[test]
    fn websocket_normalizes_secure_and_relative_redirects() {
        let base = websocket_url("wss://example.test/first", None).unwrap();
        assert_eq!(websocket_url("https://other.test/echo", Some(&base)).unwrap().as_str(), "wss://other.test/echo");
        assert_eq!(websocket_url("/next", Some(&base)).unwrap().as_str(), "wss://example.test/next");
        for invalid in ["file:///tmp/data", "ws://", "wss://example.test/#fragment", "wss://user:pass@example.test/"] {
            assert!(websocket_url(invalid, None).is_err(), "{invalid}");
        }
    }

    #[test]
    fn http_reads_multi_megabyte_api_dump_responses() {
        let expected = vec![b'x'; 2 * 1024 * 1024 + 37];
        let (url, server) = response_server(expected.clone(), Duration::ZERO);
        let result = get(&url).unwrap();
        server.join().unwrap();
        assert_eq!(
            STANDARD
                .decode(result["BodyBase64"].as_str().unwrap())
                .unwrap(),
            expected
        );
    }

    #[test]
    fn http_rejects_responses_above_the_configured_limit() {
        let (url, server) = response_server(vec![b'x'; HTTP_RESPONSE_LIMIT + 1], Duration::ZERO);
        let error = get(&url).unwrap_err();
        server.join().unwrap();
        assert!(error.contains("response limit"), "{error}");
    }

    #[test]
    fn http_allows_normal_responses_to_take_longer_than_the_old_timeout() {
        let (url, server) = response_server(b"ok".to_vec(), Duration::from_millis(1500));
        let started = Instant::now();
        let result = get(&url).unwrap();
        server.join().unwrap();
        assert_eq!(
            STANDARD
                .decode(result["BodyBase64"].as_str().unwrap())
                .unwrap(),
            b"ok"
        );
        assert!(started.elapsed() >= Duration::from_millis(1400));
    }
}
pub fn apply(input: &[u8]) -> Vec<u8> {
    let result = serde_json::from_slice(input)
        .map_err(|e| e.to_string())
        .and_then(operation);
    serde_json::to_vec(&match result {
        Ok(v) => v,
        Err(e) => json!({"error":e}),
    })
    .unwrap_or_else(|_| b"{\"error\":\"response encoding failed\"}".to_vec())
}
