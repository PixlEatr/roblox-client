//! Local-only bounded request status and receipt history.
//!
//! This intentionally contains no game protocol, transport, or server message
//! codec. It is a generic ledger for correlating host-owned asynchronous work.
use std::collections::{BTreeMap, VecDeque};

const MAX_PENDING: usize = 128;
const MAX_RECEIPTS: usize = 128;

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum WorkState {
    Pending,
    Complete,
    Failed,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WorkStatus<T> {
    pub request_id: u64,
    pub state: WorkState,
    pub value: Option<T>,
    pub error: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Receipt<T> {
    pub sequence: u64,
    pub request_id: u64,
    pub status: WorkState,
    pub value: Option<T>,
    pub error: Option<String>,
}

/// A bounded, local request lifecycle tracker. Callers complete a request only
/// after their own worker reports an actual result; this type fabricates none.
#[derive(Clone, Debug)]
pub struct LocalWorkLedger<T> {
    pending: BTreeMap<u64, WorkStatus<T>>,
    receipts: VecDeque<Receipt<T>>,
    sequence: u64,
    closed: bool,
}
impl<T: Clone> Default for LocalWorkLedger<T> {
    fn default() -> Self {
        Self::new()
    }
}
impl<T: Clone> LocalWorkLedger<T> {
    pub fn new() -> Self {
        Self {
            pending: BTreeMap::new(),
            receipts: VecDeque::new(),
            sequence: 0,
            closed: false,
        }
    }

    pub fn begin(&mut self, request_id: u64) -> Result<(), &'static str> {
        if self.closed {
            return Err("Work ledger is closed");
        }
        if request_id == 0 {
            return Err("Request ID must be positive");
        }
        if self.pending.contains_key(&request_id) {
            return Err("Duplicate request ID");
        }
        if self.pending.len() >= MAX_PENDING {
            return Err("Too many pending requests");
        }
        self.pending.insert(
            request_id,
            WorkStatus {
                request_id,
                state: WorkState::Pending,
                value: None,
                error: None,
            },
        );
        Ok(())
    }

    pub fn complete(&mut self, request_id: u64, value: T) -> Result<(), &'static str> {
        self.finish(request_id, WorkState::Complete, Some(value), None)
    }

    pub fn fail(&mut self, request_id: u64, error: impl Into<String>) -> Result<(), &'static str> {
        let error = error.into();
        if error.len() > 1024 {
            return Err("Request error exceeds the supported length");
        }
        self.finish(request_id, WorkState::Failed, None, Some(error))
    }

    fn finish(
        &mut self,
        request_id: u64,
        state: WorkState,
        value: Option<T>,
        error: Option<String>,
    ) -> Result<(), &'static str> {
        if self.closed {
            return Err("Work ledger is closed");
        }
        let next_sequence = self
            .sequence
            .checked_add(1)
            .ok_or("Receipt sequence exhausted")?;
        let status = self
            .pending
            .get_mut(&request_id)
            .ok_or("Unknown request ID")?;
        if status.state != WorkState::Pending {
            return Err("Request is already complete");
        }
        status.state = state.clone();
        status.value = value.clone();
        status.error = error.clone();
        self.sequence = next_sequence;
        self.receipts.push_back(Receipt {
            sequence: self.sequence,
            request_id,
            status: state,
            value,
            error,
        });
        while self.receipts.len() > MAX_RECEIPTS {
            self.receipts.pop_front();
        }
        Ok(())
    }

    pub fn status(&self, request_id: u64) -> Option<WorkStatus<T>> {
        self.pending.get(&request_id).cloned()
    }

    pub fn read(&self, after: u64) -> Result<(Vec<Receipt<T>>, u64), &'static str> {
        if after > self.sequence
            || self
                .receipts
                .front()
                .is_some_and(|r| after < r.sequence.saturating_sub(1))
        {
            return Err("Invalid or overrun receipt cursor");
        }
        Ok((
            self.receipts
                .iter()
                .filter(|r| r.sequence > after)
                .cloned()
                .collect(),
            self.sequence,
        ))
    }

    /// Retain only requests still owned by the caller's current work set.
    pub fn retain_active(&mut self, active: &[u64]) {
        let active = active
            .iter()
            .copied()
            .collect::<std::collections::HashSet<_>>();
        self.pending.retain(|id, _| active.contains(id));
    }

    pub fn close(&mut self) {
        self.closed = true;
        self.pending.clear();
        self.receipts.clear();
    }
    pub fn is_closed(&self) -> bool {
        self.closed
    }
    pub fn pending_count(&self) -> usize {
        self.pending
            .values()
            .filter(|s| s.state == WorkState::Pending)
            .count()
    }
    pub fn sequence(&self) -> u64 {
        self.sequence
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn reports_only_recorded_local_results_and_preserves_cursor_order() {
        let mut ledger = LocalWorkLedger::<String>::new();
        ledger.begin(5).unwrap();
        assert_eq!(ledger.status(5).unwrap().state, WorkState::Pending);
        ledger.complete(5, "done".to_string()).unwrap();
        ledger.begin(6).unwrap();
        ledger.fail(6, "local failure").unwrap();
        let (receipts, cursor) = ledger.read(0).unwrap();
        assert_eq!(cursor, 2);
        assert_eq!(receipts.len(), 2);
        assert_eq!(receipts[0].value.as_deref(), Some("done"));
        assert_eq!(receipts[1].error.as_deref(), Some("local failure"));
        assert_eq!(ledger.status(7), None);
    }
    #[test]
    fn rejects_duplicate_unknown_and_overrun_reads() {
        let mut ledger = LocalWorkLedger::<()>::new();
        ledger.begin(1).unwrap();
        assert!(ledger.begin(1).is_err());
        assert!(ledger.complete(2, ()).is_err());
        for id in 2..=MAX_RECEIPTS as u64 + 2 {
            ledger.begin(id).unwrap();
            ledger.complete(id, ()).unwrap();
            ledger.retain_active(&[]);
        }
        assert!(ledger.read(0).is_err());
        assert_eq!(ledger.read(ledger.sequence() - 1).unwrap().0.len(), 1);
    }
    #[test]
    fn active_pruning_and_close_are_local_and_bounded() {
        let mut ledger = LocalWorkLedger::<()>::new();
        ledger.begin(1).unwrap();
        ledger.begin(2).unwrap();
        ledger.retain_active(&[2]);
        assert_eq!(ledger.status(1), None);
        assert_eq!(ledger.pending_count(), 1);
        ledger.close();
        assert!(ledger.begin(3).is_err());
        assert_eq!(ledger.pending_count(), 0);
    }
}
