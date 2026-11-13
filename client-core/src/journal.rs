//! Bounded, receipt-ordered world replay with explicit reader leases.
//!
//! Entries are shared through `Arc`; reading or fanning them out never clones
//! a payload. Callers provide a conservative retained-payload byte estimate so
//! the journal can enforce a real budget without serializing records merely
//! to estimate their size. A full journal applies backpressure while any
//! reader still needs its oldest entry. It never silently drops unread data.

use std::{
    collections::{HashMap, VecDeque},
    error::Error,
    fmt,
    mem::size_of,
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc,
    },
};

static NEXT_JOURNAL_ID: AtomicU64 = AtomicU64::new(1);

/// Opaque token identifying a reader lease in one journal.
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct ReaderId {
    journal: u64,
    reader: u64,
}

/// One immutable sequence-numbered journal item.
pub struct JournalEntry<T> {
    /// Receipt-order sequence assigned by the world model.
    pub sequence: u64,
    /// Shared payload. Cloning the entry or payload does not clone `T`.
    pub value: Arc<T>,
}

impl<T> fmt::Debug for JournalEntry<T> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        // Keep diagnostic formatting independent of T: Debug and avoid
        // accidentally printing private world payloads into logs.
        f.debug_struct("JournalEntry")
            .field("sequence", &self.sequence)
            .field("value_ptr", &Arc::as_ptr(&self.value))
            .finish()
    }
}

/// Failed append which owns the original shared payload so the producer can
/// queue it for retry without reconstructing or cloning its data.
#[derive(Debug)]
pub struct AppendFailure<T> {
    pub error: JournalError,
    pub value: Arc<T>,
}

impl<T> AppendFailure<T> {
    pub fn into_parts(self) -> (JournalError, Arc<T>) {
        (self.error, self.value)
    }
}

struct Retained<T> {
    entry: Arc<JournalEntry<T>>,
    accounted_bytes: usize,
}

/// Snapshot of memory and cursor usage maintained by a replay journal.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct JournalStats {
    pub records: usize,
    pub accounted_bytes: usize,
    pub readers: usize,
    pub oldest_sequence: Option<u64>,
    pub newest_sequence: Option<u64>,
    /// Entries discarded through this sequence because of a bounded window or
    /// because every registered reader had acknowledged them.
    pub lost_through: Option<u64>,
}

/// Errors are explicit so callers can pause/retry on backpressure and choose
/// a state resynchronization path after a reader has fallen behind.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum JournalError {
    InvalidLimits,
    ReaderIdExhausted,
    UnknownReader,
    ReaderFromAnotherJournal,
    AcknowledgmentMovedBackwards {
        previous: u64,
        requested: u64,
    },
    AcknowledgmentBeyondNewest {
        newest: Option<u64>,
        requested: u64,
    },
    SequenceNotIncreasing {
        previous: u64,
        requested: u64,
    },
    RecordTooLarge {
        required_bytes: usize,
        max_bytes: usize,
    },
    Backpressure {
        records: usize,
        accounted_bytes: usize,
    },
    Lagged {
        requested_after: u64,
        lost_through: u64,
    },
    ReadBeforeAcknowledged {
        acknowledged: u64,
        requested_after: u64,
    },
    AccountingOverflow,
}

impl fmt::Display for JournalError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidLimits => write!(f, "journal limits must allow at least one record and one byte"),
            Self::ReaderIdExhausted => write!(f, "journal reader ID space exhausted"),
            Self::UnknownReader => write!(f, "unknown replay reader"),
            Self::ReaderFromAnotherJournal => write!(f, "replay reader belongs to another journal"),
            Self::AcknowledgmentMovedBackwards { previous, requested } => write!(
                f,
                "replay acknowledgment moved backwards from {previous} to {requested}"
            ),
            Self::AcknowledgmentBeyondNewest { newest, requested } => write!(
                f,
                "replay acknowledgment {requested} is beyond newest sequence {newest:?}"
            ),
            Self::SequenceNotIncreasing { previous, requested } => write!(
                f,
                "journal sequence {requested} does not follow {previous}"
            ),
            Self::RecordTooLarge { required_bytes, max_bytes } => write!(
                f,
                "journal record requires {required_bytes} bytes, above {max_bytes}-byte limit"
            ),
            Self::Backpressure { records, accounted_bytes } => write!(
                f,
                "journal is full ({records} records, {accounted_bytes} accounted bytes) while a reader needs retained data"
            ),
            Self::Lagged { requested_after, lost_through } => write!(
                f,
                "replay cursor {requested_after} is behind discarded sequence {lost_through}"
            ),
            Self::ReadBeforeAcknowledged { acknowledged, requested_after } => write!(
                f,
                "reader acknowledged through {acknowledged} and cannot read after {requested_after}"
            ),
            Self::AccountingOverflow => write!(f, "journal byte accounting overflow"),
        }
    }
}

impl Error for JournalError {}

/// Ordered bounded journal for sharing updates with slower world consumers.
///
/// `max_records` bounds retained record overhead. `max_bytes` charges each
/// record for its deque slot, `JournalEntry<T>` allocation and `Arc` header,
/// plus the caller-supplied payload estimate. This is journal accounting, not
/// process RSS: allocator rounding, container spare capacity, and memory held
/// elsewhere by shared `Arc`s are not measured. For shared payloads, callers
/// should pass a conservative incremental retained-size estimate.
pub struct ReplayJournal<T> {
    id: u64,
    next_reader: u64,
    max_records: usize,
    max_bytes: usize,
    records: VecDeque<Retained<T>>,
    accounted_bytes: usize,
    readers: HashMap<u64, u64>,
    newest_sequence: Option<u64>,
    lost_through: Option<u64>,
}

impl<T> ReplayJournal<T> {
    /// Create an empty journal with fixed record and accounted-byte budgets.
    pub fn with_limits(max_records: usize, max_bytes: usize) -> Result<Self, JournalError> {
        if max_records == 0 || max_bytes == 0 {
            return Err(JournalError::InvalidLimits);
        }
        let id = NEXT_JOURNAL_ID.fetch_add(1, Ordering::Relaxed);
        if id == 0 || id == u64::MAX {
            return Err(JournalError::ReaderIdExhausted);
        }
        Ok(Self {
            id,
            next_reader: 1,
            max_records,
            max_bytes,
            records: VecDeque::new(),
            accounted_bytes: 0,
            readers: HashMap::new(),
            newest_sequence: None,
            lost_through: None,
        })
    }

    /// Append a value using an already shared payload allocation.
    pub fn append_shared(
        &mut self,
        sequence: u64,
        value: Arc<T>,
        retained_payload_bytes: usize,
    ) -> Result<Arc<JournalEntry<T>>, JournalError> {
        self.append_arc(sequence, value, retained_payload_bytes)
            .map_err(|failure| failure.error)
    }

    /// Append an existing shared value. On backpressure, `AppendFailure`
    /// returns the exact input `Arc` for retry after a reader acknowledges.
    pub fn append_arc(
        &mut self,
        sequence: u64,
        value: Arc<T>,
        retained_payload_bytes: usize,
    ) -> Result<Arc<JournalEntry<T>>, AppendFailure<T>> {
        let internal_overhead = size_of::<Retained<T>>()
            .checked_add(size_of::<JournalEntry<T>>())
            .and_then(|bytes| bytes.checked_add(2 * size_of::<usize>()))
            .ok_or_else(|| AppendFailure {
                error: JournalError::AccountingOverflow,
                value: Arc::clone(&value),
            })?;
        let accounted = internal_overhead
            .checked_add(retained_payload_bytes)
            .ok_or_else(|| AppendFailure {
                error: JournalError::AccountingOverflow,
                value: Arc::clone(&value),
            })?;
        self.append_accounted(sequence, value, accounted)
    }

    /// Append a newly owned value and account for its retained payload bytes.
    pub fn append(
        &mut self,
        sequence: u64,
        value: T,
        retained_payload_bytes: usize,
    ) -> Result<Arc<JournalEntry<T>>, JournalError> {
        self.append_shared(sequence, Arc::new(value), retained_payload_bytes)
    }

    fn append_accounted(
        &mut self,
        sequence: u64,
        value: Arc<T>,
        accounted_bytes: usize,
    ) -> Result<Arc<JournalEntry<T>>, AppendFailure<T>> {
        macro_rules! fail {
            ($error:expr) => {
                return Err(AppendFailure {
                    error: $error,
                    value,
                })
            };
        }
        if let Some(previous) = self.newest_sequence {
            if sequence <= previous {
                fail!(JournalError::SequenceNotIncreasing {
                    previous,
                    requested: sequence,
                });
            }
        }
        if accounted_bytes > self.max_bytes {
            fail!(JournalError::RecordTooLarge {
                required_bytes: accounted_bytes,
                max_bytes: self.max_bytes,
            });
        }

        self.prune_acknowledged();
        loop {
            let new_total = match self.accounted_bytes.checked_add(accounted_bytes) {
                Some(value) => value,
                None => fail!(JournalError::AccountingOverflow),
            };
            if self.records.len() < self.max_records && new_total <= self.max_bytes {
                break;
            }
            let Some(oldest) = self.records.front() else {
                break;
            };
            if self.reader_needs(oldest.entry.sequence) {
                fail!(JournalError::Backpressure {
                    records: self.records.len(),
                    accounted_bytes: self.accounted_bytes,
                });
            }
            self.discard_oldest();
        }

        let entry = Arc::new(JournalEntry { sequence, value });
        self.records.push_back(Retained {
            entry: Arc::clone(&entry),
            accounted_bytes,
        });
        self.accounted_bytes = self.accounted_bytes.saturating_add(accounted_bytes);
        self.newest_sequence = Some(sequence);
        Ok(entry)
    }

    /// Register a reader after `sequence`. Existing history remains available
    /// only if it has not already aged out; use `read_after` to detect lag.
    pub fn retain(&mut self, sequence: u64) -> Result<ReaderId, JournalError> {
        if self.newest_sequence.is_some_and(|newest| sequence > newest) {
            return Err(JournalError::AcknowledgmentBeyondNewest {
                newest: self.newest_sequence,
                requested: sequence,
            });
        }
        let reader = self.next_reader;
        self.next_reader = self
            .next_reader
            .checked_add(1)
            .ok_or(JournalError::ReaderIdExhausted)?;
        self.readers.insert(reader, sequence);
        Ok(ReaderId {
            journal: self.id,
            reader,
        })
    }

    /// Return up to `limit` entries after this reader's cursor without copying
    /// payloads. The caller should acknowledge only after applying the batch.
    pub fn read_after(
        &self,
        reader: ReaderId,
        sequence: u64,
        limit: usize,
    ) -> Result<Vec<Arc<JournalEntry<T>>>, JournalError> {
        let cursor = self.reader_cursor(reader)?;
        if sequence < cursor {
            return Err(JournalError::ReadBeforeAcknowledged {
                acknowledged: cursor,
                requested_after: sequence,
            });
        }
        self.read_from(sequence, limit)
    }

    /// Read without a lease, for bounded diagnostics or bootstrap callers.
    /// A stale cursor returns `Lagged` instead of presenting an incomplete tail.
    pub fn read_from(
        &self,
        sequence: u64,
        limit: usize,
    ) -> Result<Vec<Arc<JournalEntry<T>>>, JournalError> {
        if let Some(lost) = self.lost_through {
            if sequence < lost {
                return Err(JournalError::Lagged {
                    requested_after: sequence,
                    lost_through: lost,
                });
            }
        }
        Ok(self
            .records
            .iter()
            .filter(|record| record.entry.sequence > sequence)
            .take(limit)
            .map(|record| Arc::clone(&record.entry))
            .collect())
    }

    /// Advance a reader after it has applied records through `sequence`.
    /// Acknowledgments are monotonic and cannot refer to future data.
    pub fn acknowledge(&mut self, reader: ReaderId, sequence: u64) -> Result<(), JournalError> {
        let previous = self.reader_cursor(reader)?;
        if sequence < previous {
            return Err(JournalError::AcknowledgmentMovedBackwards {
                previous,
                requested: sequence,
            });
        }
        if self.newest_sequence.is_none_or(|newest| sequence > newest) {
            return Err(JournalError::AcknowledgmentBeyondNewest {
                newest: self.newest_sequence,
                requested: sequence,
            });
        }
        self.readers.insert(reader.reader, sequence);
        self.prune_acknowledged();
        Ok(())
    }

    /// Release the reader lease. Remaining leases continue to protect unread
    /// records; with no readers, capacity is reclaimed on subsequent appends.
    pub fn release(&mut self, reader: ReaderId) -> Result<(), JournalError> {
        self.reader_cursor(reader)?;
        self.readers.remove(&reader.reader);
        self.prune_acknowledged();
        Ok(())
    }

    pub fn stats(&self) -> JournalStats {
        JournalStats {
            records: self.records.len(),
            accounted_bytes: self.accounted_bytes,
            readers: self.readers.len(),
            oldest_sequence: self.records.front().map(|record| record.entry.sequence),
            newest_sequence: self.newest_sequence,
            lost_through: self.lost_through,
        }
    }

    fn reader_cursor(&self, reader: ReaderId) -> Result<u64, JournalError> {
        if reader.journal != self.id {
            return Err(JournalError::ReaderFromAnotherJournal);
        }
        self.readers
            .get(&reader.reader)
            .copied()
            .ok_or(JournalError::UnknownReader)
    }

    fn reader_needs(&self, sequence: u64) -> bool {
        self.readers.values().any(|cursor| *cursor < sequence)
    }

    fn prune_acknowledged(&mut self) {
        let Some(through) = self.readers.values().copied().min() else {
            return;
        };
        while self
            .records
            .front()
            .is_some_and(|record| record.entry.sequence <= through)
        {
            self.discard_oldest();
        }
    }

    fn discard_oldest(&mut self) {
        if let Some(record) = self.records.pop_front() {
            self.accounted_bytes -= record.accounted_bytes;
            self.lost_through = Some(self.lost_through.map_or(record.entry.sequence, |lost| {
                lost.max(record.entry.sequence)
            }));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{JournalError, ReplayJournal};
    use std::sync::Arc;

    #[test]
    fn arc_reads_share_payload_without_deep_copy() {
        let payload = Arc::new(vec![7_u8; 64]);
        let mut journal = ReplayJournal::with_limits(4, 512).unwrap();
        let retained = journal
            .append_shared(1, Arc::clone(&payload), payload.len())
            .unwrap();
        let reader = journal.retain(0).unwrap();
        let rows = journal.read_after(reader, 0, 8).unwrap();
        assert_eq!(Arc::strong_count(&payload), 2); // caller and the one retained entry
        assert!(Arc::ptr_eq(&payload, &retained.value));
        assert!(Arc::ptr_eq(&payload, &rows[0].value));
        assert_eq!(rows[0].sequence, 1);
    }

    #[test]
    fn slowest_reader_controls_acknowledged_reclamation() {
        let mut journal = ReplayJournal::with_limits(8, 2048).unwrap();
        let slow = journal.retain(0).unwrap();
        let fast = journal.retain(0).unwrap();
        for sequence in 1..=4 {
            journal.append(sequence, vec![sequence as u8], 1).unwrap();
        }
        journal.acknowledge(fast, 4).unwrap();
        assert_eq!(journal.stats().records, 4);
        let rows = journal.read_after(slow, 0, 8).unwrap();
        assert_eq!(
            rows.iter().map(|row| row.sequence).collect::<Vec<_>>(),
            [1, 2, 3, 4]
        );
        journal.acknowledge(slow, 2).unwrap();
        assert_eq!(journal.stats().oldest_sequence, Some(3));
        assert_eq!(journal.stats().records, 2);
        journal.release(slow).unwrap();
        journal.release(fast).unwrap();
    }

    #[test]
    fn full_journal_backpressures_unread_data_then_recovers_on_ack() {
        let mut journal = ReplayJournal::with_limits(2, 2048).unwrap();
        let reader = journal.retain(0).unwrap();
        journal.append(1, "one", 3).unwrap();
        journal.append(2, "two", 3).unwrap();
        assert!(matches!(
            journal.append(3, "three", 5),
            Err(JournalError::Backpressure { records: 2, .. })
        ));
        assert_eq!(journal.stats().newest_sequence, Some(2));
        journal.acknowledge(reader, 1).unwrap();
        journal.append(3, "three", 5).unwrap();
        assert_eq!(journal.read_after(reader, 1, 8).unwrap()[0].sequence, 2);
        journal.acknowledge(reader, 3).unwrap();
        assert_eq!(journal.stats().records, 0);
    }

    #[test]
    fn backpressure_returns_the_same_arc_for_retry() {
        let mut journal = ReplayJournal::<String>::with_limits(1, 2048).unwrap();
        let reader = journal.retain(0).unwrap();
        journal.append(1, String::from("first"), 5).unwrap();
        let waiting = Arc::new(String::from("second"));
        let failure = journal
            .append_arc(2, Arc::clone(&waiting), waiting.len())
            .unwrap_err();
        let (error, returned) = failure.into_parts();
        assert!(matches!(
            error,
            JournalError::Backpressure { records: 1, .. }
        ));
        assert!(Arc::ptr_eq(&waiting, &returned));

        journal.acknowledge(reader, 1).unwrap();
        let entry = journal.append_arc(2, returned, 6).unwrap();
        assert_eq!(&*entry.value, "second");
    }

    #[test]
    fn record_and_byte_budgets_bound_the_recent_window_without_readers() {
        let overhead = std::mem::size_of::<super::Retained<Vec<u8>>>()
            + std::mem::size_of::<super::JournalEntry<Vec<u8>>>()
            + 2 * std::mem::size_of::<usize>();
        let mut journal = ReplayJournal::with_limits(2, overhead * 2 + 4).unwrap();
        journal.append(1, vec![10; 2], 2).unwrap();
        journal.append(2, vec![20; 2], 2).unwrap();
        journal.append(3, vec![30; 2], 2).unwrap();
        assert_eq!(journal.stats().records, 2);
        assert!(matches!(
            journal.read_from(0, 10),
            Err(JournalError::Lagged {
                lost_through: 1,
                ..
            })
        ));
        let rows = journal.read_from(1, 10).unwrap();
        assert_eq!(
            rows.iter().map(|row| row.sequence).collect::<Vec<_>>(),
            [2, 3]
        );
        assert!(journal.stats().accounted_bytes <= overhead * 2 + 4);
    }

    #[test]
    fn readers_are_journal_scoped_and_acknowledgments_are_validated() {
        let mut first = ReplayJournal::<()>::with_limits(3, 1024).unwrap();
        let mut second = ReplayJournal::<()>::with_limits(3, 1024).unwrap();
        first.append(1, (), 0).unwrap();
        let reader = first.retain(0).unwrap();
        assert!(matches!(
            second.read_after(reader, 0, 1),
            Err(JournalError::ReaderFromAnotherJournal)
        ));
        assert!(matches!(
            first.acknowledge(reader, 2),
            Err(JournalError::AcknowledgmentBeyondNewest { .. })
        ));
        first.acknowledge(reader, 1).unwrap();
        assert!(matches!(
            first.read_after(reader, 0, 1),
            Err(JournalError::ReadBeforeAcknowledged { .. })
        ));
        assert!(matches!(
            first.acknowledge(reader, 0),
            Err(JournalError::AcknowledgmentMovedBackwards { .. })
        ));
        first.release(reader).unwrap();
        assert_eq!(first.release(reader), Err(JournalError::UnknownReader));
    }

    #[test]
    fn overflow_and_oversized_record_fail_without_advancing_sequence() {
        let mut journal = ReplayJournal::with_limits(2, 128).unwrap();
        assert!(matches!(
            journal.append(1, 0_u8, usize::MAX),
            Err(JournalError::AccountingOverflow) | Err(JournalError::RecordTooLarge { .. })
        ));
        journal.append(1, 0_u8, 1).unwrap();
        assert!(matches!(
            journal.append(1, 1_u8, 1),
            Err(JournalError::SequenceNotIncreasing { .. })
        ));
    }
}
