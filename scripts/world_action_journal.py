"""Receipt-ordered replay with bounded retention for a slower live consumer.

The ordinary deque remains the recent history used by diagnostic consumers.
Unread evictions are retained in a private, temporary file until acknowledged.
Only this process's own records are deserialized; no external file is opened.
"""
import collections
import copy
import os
import pickle
import tempfile


class WorldActionJournal(collections.deque):
    def __init__(self, values=(), maxlen=100000, *, max_spill_bytes=100*1024*1024,
                 max_spill_records=262144):
        if type(maxlen) is not int or maxlen < 1:
            raise ValueError('World action history capacity must be positive')
        super().__init__(values,maxlen=maxlen)
        self.max_spill_bytes = max_spill_bytes
        self.max_spill_records = max_spill_records
        self._readers = {}
        self._spill = collections.deque()
        self._file = None
        self._temp_directory = None
        self._file_size = 0
        self._live_bytes = 0
        self._lost_through = 0
        self._last_loss = None

    def retain(self, sequence):
        token = object()
        self._readers[token] = sequence
        return token

    def acknowledge(self, token, sequence):
        previous = self._readers[token]
        if sequence < previous:
            raise ValueError('World replay acknowledgment moved backwards')
        self._readers[token] = sequence
        self._discard_acknowledged()

    def release(self, token):
        self._readers.pop(token, None)
        self._discard_acknowledged()

    def _discard_acknowledged(self):
        through = min(self._readers.values(), default=float('inf'))
        while self._spill and self._spill[0][0] <= through:
            _, _, length = self._spill.popleft()
            self._live_bytes -= length
        if not self._spill:
            self._close_file()

    def _open_file(self):
        directory = None
        if os.name == 'nt':
            from platform_files import protect_private
            from pywintypes import error as WindowsSecurityError
            self._temp_directory = tempfile.TemporaryDirectory(prefix='rcr-world-replay-')
            directory = self._temp_directory.name
            try:
                protect_private(directory)
            except (WindowsSecurityError, ValueError) as error:
                raise OSError('Cannot protect the private world replay directory') from error
        self._file = tempfile.TemporaryFile(mode='w+b', prefix='rcr-world-replay-', dir=directory)

    def _close_file(self):
        if self._file is not None:
            self._file.close()
        if self._temp_directory is not None:
            self._temp_directory.cleanup()
        self._file = self._temp_directory = None
        self._file_size = self._live_bytes = 0

    def _compact(self):
        # Acknowledge only removes a prefix from _spill, so every remaining
        # record occupies one contiguous suffix of the current file. Shift
        # that suffix toward offset zero in bounded chunks. The destination is
        # always before the source; reading each chunk before writing it keeps
        # the forward copy safe even when source and destination overlap.
        if not self._spill:
            self._file.truncate(0)
            self._file.seek(0)
            self._file_size = 0
            return
        source_start = self._spill[0][1]
        source_end = source_start
        destination = 0
        entries = collections.deque()
        chunk_size = 1024 * 1024
        for sequence, offset, length in self._spill:
            if offset != source_end or destination > offset:
                raise OSError('Invalid private world replay offsets')
            entries.append((sequence, destination, length))
            source_end = offset + length
            destination += length
        if source_end != self._file_size or destination != self._live_bytes:
            raise OSError('Private world replay records are not a contiguous suffix')

        total = destination
        copied = 0
        while copied < total:
            size = min(chunk_size, total - copied)
            self._file.seek(source_start + copied)
            value = self._file.read(size)
            if len(value) != size:
                raise OSError('Truncated private world replay buffer')
            self._file.seek(copied)
            written = self._file.write(value)
            if written != size:
                raise OSError('Short write while compacting private world replay buffer')
            copied += size
        self._file.truncate(destination)
        self._spill = entries
        self._file_size = destination
        self._file.seek(destination)

    def _lose_retention(self, sequence, reason, payload_bytes=None):
        self._last_loss = {
            'reason': reason,
            'sequence': sequence,
            'spill_records_before_loss': len(self._spill),
            'spill_bytes_before_loss': self._live_bytes,
            'physical_bytes_before_loss': self._file_size,
            'reader_cursor_min': min(self._readers.values(), default=None),
            'incoming_record_bytes': payload_bytes,
            'max_spill_bytes': self.max_spill_bytes,
            'max_spill_records': self.max_spill_records,
        }
        self._lost_through = max(self._lost_through, sequence)
        self._spill.clear()
        self._close_file()

    def append(self, record):
        if len(self) == self.maxlen:
            evicted = self[0]
            sequence = evicted['sequence']
            # A reader which has already lost data cannot consume a later
            # suffix as if it were a complete history.
            needs_record = any(self._lost_through <= cursor < sequence
                               for cursor in self._readers.values())
            if needs_record:
                try:
                    payload = pickle.dumps(evicted, protocol=5)
                except pickle.PickleError:
                    self._lose_retention(sequence, 'pickle_error')
                else:
                    if self._live_bytes + len(payload) > self.max_spill_bytes:
                        self._lose_retention(sequence, 'byte_limit', len(payload))
                    elif len(self._spill) >= self.max_spill_records:
                        self._lose_retention(sequence, 'record_limit', len(payload))
                    else:
                        try:
                            if self._file is None:
                                self._open_file()
                            elif self._file_size + len(payload) > self.max_spill_bytes:
                                self._compact()
                            self._file.seek(self._file_size)
                            written = self._file.write(payload)
                            if written != len(payload):
                                raise OSError('Short write to private world replay buffer')
                            self._spill.append((sequence, self._file_size, len(payload)))
                            self._file_size += len(payload)
                            self._live_bytes += len(payload)
                        except OSError:
                            self._lose_retention(sequence, 'io_error', len(payload))
        super().append(record)

    def extend(self, values):
        for value in values:
            self.append(value)

    def iter_since(self, sequence):
        oldest = self._spill[0][0] if self._spill else self[0]['sequence'] if self else None
        if sequence < self._lost_through or (oldest is not None and sequence < oldest-1):
            raise ValueError('World action journal was overrun; faithful event replay is unavailable')
        for current, offset, length in self._spill:
            if current <= sequence:
                continue
            self._file.seek(offset)
            payload = self._file.read(length)
            if len(payload) != length:
                raise ValueError('Truncated private world replay buffer')
            yield pickle.loads(payload)
        if self and sequence >= self[len(self)//2]['sequence']:
            recent = []
            for record in reversed(self):
                if record['sequence'] <= sequence:
                    break
                recent.append(record)
            yield from reversed(recent)
        else:
            for record in self:
                if record['sequence'] > sequence:
                    yield record

    def retention_stats(self):
        return {'spill_records': len(self._spill), 'spill_bytes': self._live_bytes,
                'physical_bytes': self._file_size, 'lost_through': self._lost_through,
                'last_loss': dict(self._last_loss) if self._last_loss is not None else None}

    def __deepcopy__(self, memo):
        # Replay leases belong to the runtime consuming the original world.
        # Independent world copies retain the ordinary recent-history view.
        result=type(self)(maxlen=self.maxlen,max_spill_bytes=self.max_spill_bytes,
                          max_spill_records=self.max_spill_records)
        memo[id(self)]=result
        result.extend(copy.deepcopy(list(self),memo))
        return result

    def __copy__(self):
        return type(self)(self,self.maxlen,max_spill_bytes=self.max_spill_bytes,
                          max_spill_records=self.max_spill_records)

    def clear(self):
        if self:
            self._lost_through=max(self._lost_through,self[-1]['sequence'])
        super().clear()
        self._spill.clear()
        self._close_file()

    def __del__(self):
        if getattr(self, '_file', None) is not None or getattr(self, '_temp_directory', None) is not None:
            self._close_file()
