//! Nonblocking terminal input for the client owner's main event loop.
//!
//! A single reader thread performs blocking stdin reads. It can retain at most
//! one queued event plus the one bounded line/paste currently being read. The
//! main thread only calls `poll`, so the VM and network loop keep advancing.

use std::{
    io::{self, Read, Write},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc::{self, Receiver, RecvTimeoutError, SyncSender, TryRecvError},
        Arc,
    },
    thread::{self, JoinHandle},
};

pub const MAX_SOURCE_BYTES: usize = 100 * 1024 * 1024;
const EVENT_QUEUE_CAPACITY: usize = 1;
const READ_BUFFER_BYTES: usize = 8192;
const MAX_LINE_BYTES: usize = 8 * 1024 * 1024;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TerminalEvent {
    Execute(String),
    Status(u64),
    Quit,
    Help,
    /// The input stream reached EOF. This is not a request to quit the client.
    InputClosed,
    InputError(String),
}

#[derive(Debug)]
pub struct Terminal {
    receiver: Option<Receiver<TerminalEvent>>,
    source_complete: Option<SyncSender<()>>,
    awaiting_source: Arc<AtomicBool>,
    stop: Arc<AtomicBool>,
    reader: Option<JoinHandle<()>>,
    interactive: bool,
}

impl Terminal {
    /// Start one bounded input reader. `interactive=false` suppresses prompts,
    /// which is appropriate for piped scripts and redirected stdin.
    pub fn start<R>(reader: R, interactive: bool) -> io::Result<Self>
    where
        R: Read + Send + 'static,
    {
        let (sender, receiver) = mpsc::sync_channel(EVENT_QUEUE_CAPACITY);
        let (source_complete, source_complete_reader) = mpsc::sync_channel(1);
        let stop = Arc::new(AtomicBool::new(false));
        let worker_stop = Arc::clone(&stop);
        let awaiting_source = Arc::new(AtomicBool::new(false));
        let worker_awaiting_source = Arc::clone(&awaiting_source);
        let worker = thread::Builder::new()
            .name("rove-terminal-input".into())
            .spawn(move || {
                read_commands(
                    reader,
                    sender,
                    source_complete_reader,
                    worker_awaiting_source,
                    worker_stop,
                    interactive,
                    MAX_LINE_BYTES,
                )
            })?;
        Ok(Self {
            receiver: Some(receiver),
            source_complete: Some(source_complete),
            awaiting_source,
            stop,
            reader: Some(worker),
            interactive,
        })
    }

    pub fn is_interactive(&self) -> bool {
        self.interactive
    }

    /// Return the next event without waiting. Call this repeatedly from the
    /// same owner loop that polls the VM and network session.
    pub fn poll(&self) -> Option<TerminalEvent> {
        match self.receiver.as_ref()?.try_recv() {
            Ok(event) => Some(event),
            Err(TryRecvError::Empty | TryRecvError::Disconnected) => None,
        }
    }

    /// Release the one-source memory credit after an `Execute(source)` event
    /// has been handled and its source string dropped. The input reader waits
    /// for this acknowledgment before reading the next source, keeping queued
    /// plus active script text bounded to one 100 MiB source and one 8 KiB
    /// input chunk. Individual lines are capped at 8 MiB. Returns false after
    /// shutdown or if no credit can be sent.
    pub fn source_complete(&self) -> bool {
        if !self.awaiting_source.swap(false, Ordering::AcqRel) {
            return false;
        }
        self.source_complete
            .as_ref()
            .is_some_and(|sender| sender.try_send(()).is_ok())
    }

    /// Signal the reader and close the event receiver. Returns true if the
    /// reader has exited. Generic blocking `Read` implementations cannot be
    /// forcibly interrupted portably; false means the thread may still be
    /// blocked in its current read and will stop after that read returns.
    pub fn shutdown(&mut self) -> bool {
        self.stop.store(true, Ordering::Release);
        self.receiver.take(); // unblock a worker waiting to send into a full queue
        self.source_complete.take(); // unblock a worker awaiting source consumption
        let Some(reader) = self.reader.as_ref() else {
            return true;
        };
        if reader.is_finished() {
            if let Some(reader) = self.reader.take() {
                let _ = reader.join();
            }
            true
        } else {
            false
        }
    }
}

impl Drop for Terminal {
    fn drop(&mut self) {
        let _ = self.shutdown();
        // If the supplied Read is currently blocking, dropping the JoinHandle
        // detaches that one reader thread. Do not claim shutdown cancelled it.
        self.reader.take();
    }
}

#[derive(Debug)]
enum PasteState {
    Off,
    Collect(Vec<u8>),
    Discard,
}

#[derive(Debug)]
struct CommandParser {
    source_limit: usize,
    paste: PasteState,
}

impl CommandParser {
    fn new(source_limit: usize) -> Self {
        Self {
            source_limit,
            paste: PasteState::Off,
        }
    }

    fn in_paste(&self) -> bool {
        !matches!(self.paste, PasteState::Off)
    }

    fn line(&mut self, raw: &[u8]) -> Vec<TerminalEvent> {
        let line = raw.strip_suffix(b"\r").unwrap_or(raw);
        if !matches!(self.paste, PasteState::Off) {
            if line == b":end" {
                return self.end_paste();
            }
            match &mut self.paste {
                PasteState::Collect(source) => {
                    let extra = line.len().saturating_add(1);
                    if source.len().saturating_add(extra) > self.source_limit {
                        self.paste = PasteState::Discard;
                    } else {
                        source.extend_from_slice(line);
                        source.push(b'\n');
                    }
                }
                PasteState::Discard => {}
                PasteState::Off => unreachable!(),
            }
            return Vec::new();
        }

        let text = match std::str::from_utf8(line) {
            Ok(text) => text,
            Err(_) => {
                return vec![TerminalEvent::InputError(
                    "Terminal input is not valid UTF-8".into(),
                )]
            }
        };
        if text.is_empty() {
            return Vec::new();
        }
        match text {
            ":quit" | ":exit" => vec![TerminalEvent::Quit],
            ":help" => vec![TerminalEvent::Help],
            ":paste" => {
                self.paste = PasteState::Collect(Vec::new());
                Vec::new()
            }
            _ if text.starts_with(":status") => parse_status(text),
            _ => {
                if line.len() > self.source_limit {
                    vec![TerminalEvent::InputError(
                        "Script source exceeds 100 MiB".into(),
                    )]
                } else {
                    vec![TerminalEvent::Execute(text.to_owned())]
                }
            }
        }
    }

    fn line_owned(&mut self, mut raw: Vec<u8>) -> Vec<TerminalEvent> {
        if raw.last() == Some(&b'\r') {
            raw.pop();
        }
        if matches!(self.paste, PasteState::Off) {
            if let Ok(text) = std::str::from_utf8(&raw) {
                let command = matches!(text, ":quit" | ":exit" | ":help" | ":paste")
                    || text.starts_with(":status");
                if !text.is_empty() && !command {
                    return if raw.len() > self.source_limit {
                        vec![TerminalEvent::InputError(
                            "Script source exceeds 100 MiB".into(),
                        )]
                    } else {
                        vec![TerminalEvent::Execute(
                            String::from_utf8(raw).expect("UTF-8 checked above"),
                        )]
                    };
                }
            }
        }
        self.line(&raw)
    }

    fn end_paste(&mut self) -> Vec<TerminalEvent> {
        let paste = std::mem::replace(&mut self.paste, PasteState::Off);
        match paste {
            PasteState::Off => Vec::new(),
            PasteState::Discard => {
                vec![TerminalEvent::InputError(
                    "Pasted script exceeds the source limit".into(),
                )]
            }
            PasteState::Collect(source) => match String::from_utf8(source) {
                Ok(source) => vec![TerminalEvent::Execute(source)],
                Err(_) => vec![TerminalEvent::InputError(
                    "Pasted script is not valid UTF-8".into(),
                )],
            },
        }
    }

    fn eof(&mut self) -> Vec<TerminalEvent> {
        let mut events = Vec::new();
        if self.in_paste() {
            self.paste = PasteState::Off;
            events.push(TerminalEvent::InputError(
                "EOF ended an unfinished :paste block; source was discarded".into(),
            ));
        }
        events.push(TerminalEvent::InputClosed);
        events
    }
}

fn parse_status(text: &str) -> Vec<TerminalEvent> {
    let mut fields = text.split_ascii_whitespace();
    if fields.next() != Some(":status") {
        return vec![TerminalEvent::Execute(text.to_owned())];
    }
    let Some(id_text) = fields.next() else {
        return vec![TerminalEvent::InputError("Usage: :status JOB_ID".into())];
    };
    if fields.next().is_some()
        || id_text.is_empty()
        || !id_text.bytes().all(|byte| byte.is_ascii_digit())
    {
        return vec![TerminalEvent::InputError("Usage: :status JOB_ID".into())];
    }
    match id_text.parse::<u64>() {
        Ok(id) => vec![TerminalEvent::Status(id)],
        Err(_) => vec![TerminalEvent::InputError(
            "Job ID is outside the supported range".into(),
        )],
    }
}

fn send(sender: &SyncSender<TerminalEvent>, stop: &AtomicBool, event: TerminalEvent) -> bool {
    if stop.load(Ordering::Acquire) {
        return false;
    }
    sender.send(event).is_ok()
}

fn prompt(interactive: bool, paste: bool) {
    if interactive {
        let text = if paste { "...> " } else { "luau> " };
        let mut stderr = io::stderr().lock();
        let _ = stderr.write_all(text.as_bytes());
        let _ = stderr.flush();
    }
}

fn process_line(
    parser: &mut CommandParser,
    line: Vec<u8>,
    sender: &SyncSender<TerminalEvent>,
    source_complete: &Receiver<()>,
    awaiting_source: &AtomicBool,
    stop: &AtomicBool,
    interactive: bool,
) -> bool {
    let events = parser.line_owned(line);
    let quit = events.iter().any(|event| *event == TerminalEvent::Quit);
    for event in events {
        let is_source = matches!(&event, TerminalEvent::Execute(_));
        if is_source {
            awaiting_source.store(true, Ordering::Release);
        }
        if !send(sender, stop, event) {
            return false;
        }
        if is_source {
            loop {
                if stop.load(Ordering::Acquire) {
                    return false;
                }
                match source_complete.recv_timeout(std::time::Duration::from_millis(20)) {
                    Ok(()) => break,
                    Err(RecvTimeoutError::Timeout) => continue,
                    Err(RecvTimeoutError::Disconnected) => return false,
                }
            }
        }
    }
    if !quit {
        prompt(interactive, parser.in_paste());
    }
    !quit
}

fn read_commands<R: Read>(
    mut input: R,
    sender: SyncSender<TerminalEvent>,
    source_complete: Receiver<()>,
    awaiting_source: Arc<AtomicBool>,
    stop: Arc<AtomicBool>,
    interactive: bool,
    line_limit: usize,
) {
    let mut parser = CommandParser::new(MAX_SOURCE_BYTES);
    let mut line = Vec::new();
    let mut discarding_line = false;
    let mut buffer = [0u8; READ_BUFFER_BYTES];
    prompt(interactive, false);
    loop {
        if stop.load(Ordering::Acquire) {
            return;
        }
        match input.read(&mut buffer) {
            Ok(0) => {
                if discarding_line {
                    let message = if parser.in_paste() {
                        parser.paste = PasteState::Discard;
                        None
                    } else {
                        Some("Terminal input line exceeds 8 MiB")
                    };
                    if let Some(message) = message {
                        if !process_line_error(&sender, &stop, message) {
                            return;
                        }
                    }
                }
                if !discarding_line
                    && !line.is_empty()
                    && !process_line(
                        &mut parser,
                        std::mem::take(&mut line),
                        &sender,
                        &source_complete,
                        &awaiting_source,
                        &stop,
                        interactive,
                    )
                {
                    return;
                }
                for event in parser.eof() {
                    if !send(&sender, &stop, event) {
                        return;
                    }
                }
                return;
            }
            Ok(count) => {
                for &byte in &buffer[..count] {
                    if byte == b'\n' {
                        if discarding_line {
                            discarding_line = false;
                            line = Vec::new();
                            if parser.in_paste() {
                                parser.paste = PasteState::Discard;
                            } else {
                                if !process_line_error(
                                    &sender,
                                    &stop,
                                    "Terminal input line exceeds 8 MiB",
                                ) {
                                    return;
                                }
                            }
                            prompt(interactive, parser.in_paste());
                        } else if !process_line(
                            &mut parser,
                            std::mem::take(&mut line),
                            &sender,
                            &source_complete,
                            &awaiting_source,
                            &stop,
                            interactive,
                        ) {
                            return;
                        }
                    } else if !discarding_line {
                        if line.len() >= line_limit {
                            line.clear();
                            discarding_line = true;
                        } else {
                            line.push(byte);
                        }
                    }
                }
            }
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(_) => {
                let _ = send(
                    &sender,
                    &stop,
                    TerminalEvent::InputError("Terminal input could not be read".into()),
                );
                let _ = send(&sender, &stop, TerminalEvent::InputClosed);
                return;
            }
        }
    }
}

fn process_line_error(
    sender: &SyncSender<TerminalEvent>,
    stop: &AtomicBool,
    message: &str,
) -> bool {
    send(sender, stop, TerminalEvent::InputError(message.into()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{io::Cursor, time::Duration};

    fn parse(input: &[&[u8]], limit: usize) -> Vec<TerminalEvent> {
        let mut parser = CommandParser::new(limit);
        input.iter().flat_map(|line| parser.line(line)).collect()
    }

    #[test]
    fn multiline_paste_preserves_utf8_and_line_order() {
        let mut parser = CommandParser::new(64);
        assert!(parser.line(b":paste").is_empty());
        assert!(parser.line("print('héllo')".as_bytes()).is_empty());
        assert!(parser.line(b"return 42").is_empty());
        assert_eq!(
            parser.line(b":end"),
            vec![TerminalEvent::Execute("print('héllo')\nreturn 42\n".into())]
        );
        assert_eq!(parser.line(b":status 7"), vec![TerminalEvent::Status(7)]);
        assert_eq!(
            parser.line(b"print('next')"),
            vec![TerminalEvent::Execute("print('next')".into())]
        );
    }

    #[test]
    fn status_and_control_commands_are_strict_and_ordered() {
        assert_eq!(
            parse(&[b":help", b":status 12", b":quit"], 64),
            vec![
                TerminalEvent::Help,
                TerminalEvent::Status(12),
                TerminalEvent::Quit,
            ]
        );
        assert!(matches!(
            parse(&[b":status 1 extra"], 64).as_slice(),
            [TerminalEvent::InputError(_)]
        ));
        assert!(matches!(
            parse(&[b":status true"], 64).as_slice(),
            [TerminalEvent::InputError(_)]
        ));
        assert_eq!(
            parse(&[b" :quit"], 64),
            vec![TerminalEvent::Execute(" :quit".into())]
        );
    }

    #[test]
    fn source_limit_and_invalid_utf8_report_errors_without_losing_next_line() {
        let events = parse(&[b"123456789", b"\xff", b"return 1"], 8);
        assert!(
            matches!(events.as_slice(), [TerminalEvent::InputError(_), TerminalEvent::InputError(_), TerminalEvent::Execute(s)] if s == "return 1")
        );
        let mut parser = CommandParser::new(8);
        parser.line(b":paste");
        parser.line(b"12345678");
        assert!(matches!(
            parser.line(b":end").as_slice(),
            [TerminalEvent::InputError(_)]
        ));
    }

    #[test]
    fn eof_discards_unfinished_paste_and_reports_closed_input() {
        let mut parser = CommandParser::new(32);
        parser.line(b":paste");
        parser.line(b"return 1");
        assert_eq!(
            parser.eof(),
            vec![
                TerminalEvent::InputError(
                    "EOF ended an unfinished :paste block; source was discarded".into()
                ),
                TerminalEvent::InputClosed,
            ]
        );
        assert_eq!(parser.eof(), vec![TerminalEvent::InputClosed]);
    }

    #[test]
    fn reader_is_nonblocking_to_owner_and_preserves_event_order_through_eof() {
        let mut terminal = Terminal::start(
            Cursor::new(b"return 'hi'\n:status 9\n:help\n".to_vec()),
            false,
        )
        .unwrap();
        assert!(!terminal.is_interactive());
        let mut events = Vec::new();
        for _ in 0..1000 {
            while let Some(event) = terminal.poll() {
                let closed = event == TerminalEvent::InputClosed;
                match event {
                    TerminalEvent::Execute(source) => {
                        events.push(TerminalEvent::Execute(source.clone()));
                        drop(source);
                        assert!(terminal.source_complete());
                    }
                    other => events.push(other),
                }
                if closed {
                    break;
                }
            }
            if events.last() == Some(&TerminalEvent::InputClosed) {
                break;
            }
            thread::sleep(Duration::from_millis(1));
        }
        assert_eq!(
            events,
            vec![
                TerminalEvent::Execute("return 'hi'".into()),
                TerminalEvent::Status(9),
                TerminalEvent::Help,
                TerminalEvent::InputClosed,
            ]
        );
        let mut stopped = false;
        for _ in 0..100 {
            if terminal.shutdown() {
                stopped = true;
                break;
            }
            thread::sleep(Duration::from_millis(1));
        }
        assert!(stopped, "finite input reader did not exit after EOF");
    }

    #[test]
    fn eof_processes_final_unterminated_plain_line() {
        let terminal = Terminal::start(Cursor::new(b"return 4".to_vec()), false).unwrap();
        let mut events = Vec::new();
        for _ in 0..100 {
            if let Some(event) = terminal.poll() {
                let closed = event == TerminalEvent::InputClosed;
                match event {
                    TerminalEvent::Execute(source) => {
                        events.push(TerminalEvent::Execute(source.clone()));
                        drop(source);
                        assert!(terminal.source_complete());
                    }
                    other => events.push(other),
                }
                if closed {
                    break;
                }
            } else {
                thread::sleep(Duration::from_millis(1));
            }
        }
        assert_eq!(
            events,
            vec![
                TerminalEvent::Execute("return 4".into()),
                TerminalEvent::InputClosed
            ]
        );
    }

    #[test]
    fn source_credit_pauses_reader_until_main_loop_releases_text() {
        let terminal =
            Terminal::start(Cursor::new(b"return 1\n:status 2\n".to_vec()), false).unwrap();
        let source = loop {
            if let Some(TerminalEvent::Execute(source)) = terminal.poll() {
                break source;
            }
            thread::sleep(Duration::from_millis(1));
        };
        assert_eq!(source, "return 1");
        drop(source);
        thread::sleep(Duration::from_millis(10));
        assert!(
            terminal.poll().is_none(),
            "reader passed a held source credit"
        );
        assert!(terminal.source_complete());
        let mut rest = Vec::new();
        for _ in 0..100 {
            if let Some(event) = terminal.poll() {
                let closed = event == TerminalEvent::InputClosed;
                rest.push(event);
                if closed {
                    break;
                }
            } else {
                thread::sleep(Duration::from_millis(1));
            }
        }
        assert_eq!(
            rest,
            vec![TerminalEvent::Status(2), TerminalEvent::InputClosed]
        );
    }
}
