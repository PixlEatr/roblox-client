"""Read-only TextChatService control-event state from a replicated event journal.

This tracks only a few server control signals. In particular, access label
"Enabled" is recorded as a label and is not treated as permission to send.
"""
from __future__ import annotations


_CONTROL_EVENTS = {
    'ServerToClientUniverseChatChannelAllocatedSignalV1': (2,),
    'ServerToClientPresetChatConfigChangedSignal': (9, 2),
    'ServerToClientPresetChatUserAccessChangedSignal': (2,),
}
_MAX_JOURNAL_ROWS = 4096
_MAX_VERSION_BYTES = 4096


class ChatControlState:
    """Bounded, session-local tracker for validated TextChatService controls."""

    def __init__(self):
        self._world = None
        self.cursor = 0
        self.allocated_global = False
        self.config_seen = False
        self.config_enabled = None
        self.access_seen = False
        self.access_label = 'unknown'

    def _clear_evidence(self):
        self.allocated_global = False
        self.config_seen = False
        self.config_enabled = None
        self.access_seen = False
        self.access_label = 'unknown'

    @staticmethod
    def _class_name(world, key):
        if not isinstance(key, tuple) or len(key) != 2:
            return None
        node = world.instances.get(key)
        if not isinstance(node, dict):
            return None
        ci = node.get('class')
        classes = world.schema.get('classes', [])
        if type(ci) is not int or not 0 <= ci < len(classes):
            return None
        return classes[ci].get('name')

    @staticmethod
    def _well_formed_event(world, event):
        if not isinstance(event, dict):
            return None
        key, event_id = event.get('key'), event.get('event')
        if not isinstance(key, tuple) or len(key) != 2:
            return None
        instances = world.instances
        target = instances.get(key)
        classes = world.schema.get('classes', [])
        events = world.schema.get('events', [])
        if not isinstance(target, dict):
            return None
        target_class = target.get('class')
        if (type(target_class) is not int or not 0 <= target_class < len(classes)
                or classes[target_class].get('name') != 'TextChatService'):
            return None
        if type(event_id) is not int or not 0 <= event_id < len(events):
            return None
        descriptor = events[event_id]
        if (not isinstance(descriptor, dict) or descriptor.get('id') != event_id
                or descriptor.get('class') != target_class):
            return None
        class_events = classes[target_class].get('events', [])
        if event_id not in class_events:
            return None
        name = descriptor.get('name')
        expected = _CONTROL_EVENTS.get(name)
        if expected is None:
            return None
        args_schema = descriptor.get('arguments')
        if not isinstance(args_schema, list):
            return None
        actual_types = tuple(arg.get('type') if isinstance(arg, dict) else None
                             for arg in args_schema)
        if actual_types != expected:
            return None
        args = event.get('arguments')
        if not isinstance(args, list) or len(args) != len(expected):
            return None
        return name, args

    def _apply(self, name, args):
        if name == 'ServerToClientUniverseChatChannelAllocatedSignalV1':
            # Only the exact known channel name is evidence for global allocation.
            if type(args[0]) is bytes and len(args[0]) <= 128 and args[0] == b'global':
                self.allocated_global = True
        elif name == 'ServerToClientPresetChatConfigChangedSignal':
            enabled, version = args
            if (type(enabled) is bool and type(version) is bytes
                    and len(version) <= _MAX_VERSION_BYTES):
                self.config_seen = True
                self.config_enabled = enabled
        elif name == 'ServerToClientPresetChatUserAccessChangedSignal':
            label = args[0]
            if type(label) is bytes and len(label) <= 128:
                self.access_seen = True
                self.access_label = 'Enabled' if label == b'Enabled' else 'unknown'

    def consume(self, world):
        """Consume newly journaled controls, including available pre-attach history.

        A gap invalidates previous evidence. Valid controls still present after
        that gap are applied in order, so a later observed allocation can count.
        """
        if world is None or not hasattr(world, 'event_journal'):
            raise ValueError('Chat control state requires a world event journal')
        if self._world is None:
            self._world = world
        elif self._world is not world:
            raise ValueError('Chat control state cannot cross world sessions')
        current = getattr(world, 'event_sequence', None)
        journal = world.event_journal
        if type(current) is not int or current < 0 or not hasattr(journal, '__iter__'):
            self._clear_evidence()
            self.cursor = 0
            return self.summary()

        rows = list(journal)
        if len(rows) > _MAX_JOURNAL_ROWS:
            rows = rows[-_MAX_JOURNAL_ROWS:]
        sequences = [row[0] for row in rows
                     if isinstance(row, (tuple, list)) and len(row) == 2
                     and type(row[0]) is int]
        if (current < self.cursor or (current > self.cursor and not sequences)
                or (sequences and sequences[-1] != current)
                or (sequences and sequences[0] > self.cursor + 1)):
            self._clear_evidence()

        expected = self.cursor + 1
        for row in rows:
            if not isinstance(row, (tuple, list)) or len(row) != 2:
                continue
            sequence, event = row
            if type(sequence) is not int or sequence <= self.cursor:
                continue
            if sequence != expected:
                self._clear_evidence()
            expected = sequence + 1
            valid = self._well_formed_event(world, event)
            if valid is not None:
                self._apply(*valid)

        self.cursor = current
        return self.summary()

    def summary(self):
        """Expose only safe control-state booleans and the fixed access label."""
        return {
            'allocated_global': self.allocated_global,
            'config_seen': self.config_seen,
            'config_enabled': self.config_enabled,
            'access_seen': self.access_seen,
            'access_label': self.access_label,
        }
