"""Schema-checked TextChatService chat for the existing 0x100000 session.

Native 0.745 sender 1038655ac emits V4 only when the exact server feature
EnableClientToServerMessageReplicateSignalV4 is enabled; otherwise it emits V3.
Both add verified and escaped booleans; V4 adds presetId and presetChatVersion.
No input verification attestation or preset is fabricated.
The normal item-7 writer 104d80008 supplies the framing already exercised by
FrameworkInitRpc. Native 103879c58 defines the modern dictionary response keys.
Both dictionary and legacy replies are decoded; only a matching server reply
confirms a post. Content stays local/private; summary exposes structural state.
"""
import collections
import struct
import time
import uuid
from chat_control_state import ChatControlState
from login_packets import encode_signed

from game_wire import encode_string
from replication_out import guid


STATUSES = {1: 'Unknown', 2: 'Success', 3: 'Sending', 4: 'TextFilterFailed',
            5: 'Floodchecked', 6: 'InvalidPrivacySettings',
            7: 'InvalidTextChannelPermissions', 8: 'MessageTooLong',
            9: 'ModerationTimeout'}
SEND_EVENT = 'ClientToServerMessageReplicateSignalV4'
SEND_EVENT_V3 = 'ClientToServerMessageReplicateSignalV3'
V4_FEATURE = 'EnableClientToServerMessageReplicateSignalV4'
READY_EVENT = 'ClientToServerChatIsLoadedSignal'
RECEIVE_EVENT = 'ServerToClientMessageReplicateSignal'
GLOBAL_SEND_EVENT = 'ClientToServerUniverseChatMessageSignalV1'
GLOBAL_RECEIVE_EVENT = 'ServerToClientUniverseChatMessageSignalV1'
SIGNATURES = {SEND_EVENT: (2, 2, 2, 28, 28, 9, 9, 2, 2),
              'ClientToServerMessageReplicateSignalV3': (2, 2, 2, 28, 28, 9, 9),
              'ClientToServerMessageReplicateSignalV2': (2, 2, 2, 28, 28), READY_EVENT: (),
              RECEIVE_EVENT: (2, 2, 2, 2, 28, 28, 44, 7),
              'ServerToClientMessageReplicateSignalV2': (31,),
              GLOBAL_SEND_EVENT: (2, 2, 2, 44, 9), GLOBAL_RECEIVE_EVENT: (31,),
              'ServerToClientUniverseChatChannelAllocatedSignalV1': (2,)}


def _text(value, *, name='text', limit=200, empty=False):
    if type(value) is not str:
        raise ValueError(name + ' must be a string')
    try:
        encoded = value.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError(name + ' must contain valid Unicode') from None
    if len(value) > limit or len(encoded) > limit * 4 or '\0' in value:
        raise ValueError(name + ' exceeds the supported length or contains NUL')
    if not empty and not value.strip():
        raise ValueError(name + ' must not be blank')
    return value


def _received_text(value, limit=16384):
    if not isinstance(value, bytes) or len(value) > limit:
        raise ValueError('Malformed received chat text')
    return value.decode('utf-8')


def delivery_error(result):
    """Describe an unconfirmed outcome without inventing a rejection reason."""
    state = result['state']
    if state == 'response_timeout':
        message = ('chat outcome: response_timeout. Roblox did not return a chat '
                   'result within 15 seconds; delivery is unconfirmed.')
        if result.get('input_verification') == 'unverified':
            message += (' This scripted request had no typed-input receipt. '
                        'For typed chat, use :chat <text> or the browser chat field.')
        return message
    if state == 'transport_outcome_unknown':
        return ('chat outcome: transport_outcome_unknown. The connection failed '
                'during submission; delivery is unconfirmed. The message was not retried.')
    return 'chat outcome: ' + state


class ChatProtocol:
    def __init__(self, user_id, *, capability_bits=0x100000, clock=time.monotonic):
        if type(user_id) is not int or user_id <= 0:
            raise ValueError('Chat requires the server-issued account identity')
        if type(capability_bits) is not int or capability_bits != 0x100000:
            raise ValueError('Unsupported chat capability set')
        self.user_id, self.clock = user_id, clock
        self.world = None
        self.service = None
        self._own_player_key = None
        self._player_keys = set()
        self._attach_revision = None
        self._attach_schema = None
        self.event_ids = {}
        self.send_event = None
        self._server_v4_enabled = False
        self._send_event_selection_source = 'native_default'
        self.cursor = None
        self.ready_sent = False
        self.last_send = None
        self.pending = collections.OrderedDict()
        self.messages = collections.deque(maxlen=128)
        self.sequence = 0
        self.counts = collections.Counter()
        # Host-only, one-use receipts from the local chat input. Luau source,
        # TextBox.Text assignments and the raw send socket cannot create these.
        self._typed_submissions = {}
        self.control = ChatControlState()

    @staticmethod
    def _parse_server_v4_feature(config):
        if not isinstance(config, dict):
            raise ValueError('Server feature configuration must be a mapping')
        if V4_FEATURE not in config:
            return None, None
        value = config[V4_FEATURE]
        if not isinstance(value, str):
            raise ValueError('V4 server feature must be a boolean string')
        normalized = value.strip().casefold()
        if normalized in ('true', '1'):
            return True, 'server_feature'
        if normalized in ('false', '0'):
            return False, 'server_feature'
        raise ValueError('V4 server feature must be true, false, 1, or 0')

    def _desired_send_event(self, enabled=None):
        if enabled is None:
            enabled = self._server_v4_enabled
        return SEND_EVENT if enabled else SEND_EVENT_V3

    def configure_server_features(self, config):
        """Apply the exact server-owned V4 gate; scripts cannot override it."""
        enabled, source = self._parse_server_v4_feature(config)
        if enabled is None:
            return self.send_event or self._desired_send_event()
        selected = self._desired_send_event(enabled)
        if self.ready_sent and selected != self.send_event:
            raise ValueError('Chat send-event choice is frozen after readiness')
        if self.world is not None and selected not in self.event_ids:
            raise ValueError('Server-selected chat event is unavailable: ' + selected)
        self._server_v4_enabled = enabled
        self._send_event_selection_source = source
        if self.world is not None:
            self.send_event = selected
        return selected

    def record_typed_submission(self, request):
        """Bind an actual console input submission to its native Luau request.

        This is called only by execute_user_chat after running the host-generated
        SendAsync call. It is not an RPC or a script binding. Exact matching is a
        conservative subset of native TextBoxService's normalized text history.
        """
        token = request['request_id']
        if type(token) is not int or token < 1 or token in self._typed_submissions:
            raise ValueError('Invalid or duplicate typed chat request')
        if request.get('route', 'channel') not in ('channel', 'universe'):
            raise ValueError('Unsupported typed chat route')
        _text(request['text'])
        now = self.clock()
        self._typed_submissions = {k: v for k, v in self._typed_submissions.items()
                                   if now < v['expires']}
        if len(self._typed_submissions) >= 8:
            raise ValueError('Too many pending typed chat submissions')
        self._typed_submissions[token] = {
            'text': request['text'], 'channel_id': request['channel_id'],
            'metadata': request.get('metadata', ''), 'expires': now + 5,
            'route': request.get('route', 'channel')}

    def _consume_typed_submission(self, token, text, metadata, target, route='channel'):
        receipt = self._typed_submissions.pop(token, None)
        if receipt is None:
            return False
        if (self.clock() >= receipt['expires'] or receipt['text'] != text
                or receipt['metadata'] != metadata
                or receipt['channel_id'] != '%d:%d' % target
                or receipt.get('route', 'channel') != route):
            raise ValueError('Typed chat input expired or changed before submission')
        return True

    @staticmethod
    def _class(world, key):
        node = world.instances.get(key)
        return world.schema['classes'][node['class']]['name'] if node else None

    def attach(self, world):
        if self.world is not None and world is not self.world:
            raise ValueError('Chat cannot cross world sessions')
        if world is None or world.client_scope is None or 13 not in world.tags:
            return False
        same_world = self.world is world
        if same_world:
            self.control.consume(world)
        if same_world and self._attach_schema is not world.schema:
            raise ValueError('World schema changed after chat attachment')
        roots = [key for key in world.top_containers
                 if self._class(world, key) == 'TextChatService']
        if len(roots) > 1:
            raise ValueError('Ambiguous TextChatService')
        if not roots:
            return False
        if same_world:
            if self.service not in roots:
                raise ValueError('TextChatService was replaced')
            if self._player_cache_is_current(world) and not self._player_identity_may_have_changed(world):
                if self._cached_player_is_valid(world):
                    return True
        own, player_keys = self._scan_players(world)
        if len(own) > 1:
            raise ValueError('Ambiguous local Player')
        if not own:
            self._clear_player_cache()
            return False
        if self.world is world:
            self._own_player_key = own[0]
            self._player_keys = player_keys
            self._attach_revision = getattr(world, 'revision', None)
            return True
        service = roots[0]
        ci = world.instances[service]['class']
        selected_send_event = self._desired_send_event()
        resolved = {}
        required_events = (selected_send_event, READY_EVENT, RECEIVE_EVENT)
        optional_events = ('ServerToClientMessageReplicateSignalV2',
                           GLOBAL_SEND_EVENT, GLOBAL_RECEIVE_EVENT)
        for name in required_events + optional_events:
            types = SIGNATURES[name]
            found = [i for i in world.schema['classes'][ci]['events']
                     if world.schema['events'][i]['name'] == name]
            if not found and name in optional_events:
                continue
            if len(found) != 1:
                if name == selected_send_event and not found:
                    raise ValueError('Server-selected chat event is unavailable: ' + name)
                raise ValueError('Missing or ambiguous chat event ' + name)
            index = found[0]
            event = world.schema['events'][index]
            if (type(index) is not int or not 0 <= index <= 65535
                    or event['id'] != index or event['class'] != ci
                    or tuple(a['type'] for a in event['arguments']) != types):
                raise ValueError('Unsupported chat event signature ' + name)
            resolved[name] = index
        # Cache the alternate outbound descriptor only if its metadata also
        # matches. This permits a server config packet arriving after attach
        # but before the one-shot ready signal to select it safely.
        for name in (SEND_EVENT, SEND_EVENT_V3):
            if name in resolved:
                continue
            found = [i for i in world.schema['classes'][ci]['events']
                     if world.schema['events'][i]['name'] == name]
            if len(found) != 1:
                continue
            index = found[0]
            event = world.schema['events'][index]
            if (type(index) is int and 0 <= index <= 65535
                    and event['id'] == index and event['class'] == ci
                    and tuple(a['type'] for a in event['arguments']) == SIGNATURES[name]):
                resolved[name] = index
        send_event = selected_send_event
        if send_event not in resolved:
            raise ValueError('Server-selected chat event is unavailable: ' + send_event)
        self.world, self.service, self.event_ids = world, service, resolved
        self.control.consume(world)
        self.send_event = send_event
        self._own_player_key = own[0]
        self._player_keys = player_keys
        self._attach_revision = getattr(world, 'revision', None)
        self._attach_schema = world.schema
        # Consume the receipt journal, without replaying historical chat as new.
        self.cursor = world.event_sequence
        return True

    def _clear_player_cache(self):
        self._own_player_key = None
        self._player_keys = set()
        self._attach_revision = None

    def _scan_players(self, world):
        own, players = [], set()
        for key, node in world.instances.items():
            if self._class(world, key) != 'Player':
                continue
            players.add(key)
            if (node['properties'].get('UserId') == self.user_id
                    and node.get('parent') in world.top_containers
                    and self._class(world, node.get('parent')) == 'Players'):
                own.append(key)
        return own, players

    def _player_cache_is_current(self, world):
        return (self._own_player_key is not None and self._attach_revision is not None
                and isinstance(self._player_keys, set)
                and self._attach_schema is world.schema)

    def _cached_player_is_valid(self, world):
        key = self._own_player_key
        node = world.instances.get(key)
        return bool(node and self._class(world, key) == 'Player'
                    and node['properties'].get('UserId') == self.user_id
                    and node.get('parent') in world.top_containers
                    and self._class(world, node.get('parent')) == 'Players')

    def _player_identity_may_have_changed(self, world):
        """Check only new journal entries; fall back on any gap or bad shape."""
        revision = getattr(world, 'revision', None)
        actions = getattr(world, 'actions', None)
        cursor = self._attach_revision
        if type(revision) is not int or type(cursor) is not int or actions is None:
            return True
        if revision < cursor:
            return True
        if revision == cursor:
            return False
        if not actions:
            return True
        try:
            if (actions[0].get('sequence') > cursor + 1
                    or actions[-1].get('sequence') != revision):
                return True
            expected = revision
            for action in reversed(actions):
                sequence = action.get('sequence')
                if type(sequence) is not int:
                    return True
                if sequence <= cursor:
                    break
                if sequence != expected:
                    return True
                expected -= 1
                kind = action.get('kind')
                key = action.get('key')
                if kind == 'create':
                    instance = action.get('instance')
                    if not isinstance(instance, dict):
                        return True
                    class_name = self._class_name(world, instance.get('class'))
                    if class_name is None or class_name == 'Player':
                        return True
                elif kind == 'remove' and key in self._player_keys:
                    return True
                elif (kind == 'set' and key in self._player_keys
                      and {'Parent', 'UserId'} & set(action.get('fields', {}))):
                    return True
            if expected != cursor:
                return True
            self._attach_revision = revision
            return False
        except (IndexError, KeyError, TypeError, AttributeError):
            return True

    @staticmethod
    def _class_name(world, class_index):
        if type(class_index) is not int or not 0 <= class_index < len(world.schema['classes']):
            return None
        return world.schema['classes'][class_index]['name']

    def _descendant_of_service(self, key):
        seen = set()
        while key in self.world.instances and key not in seen:
            if key == self.service:
                return True
            seen.add(key)
            key = self.world.instances[key].get('parent')
        return False

    def _channel(self, name):
        _text(name, name='channel', limit=128)
        wanted = name.encode('utf-8')
        found = [key for key, node in self.world.instances.items()
                 if self._class(self.world, key) == 'TextChannel'
                 and node['properties'].get('Name') == wanted
                 and self._descendant_of_service(key)]
        if len(found) != 1:
            raise ValueError('Chat channel is unavailable or ambiguous')
        return found[0]

    def _source(self, channel, *, check_send=True):
        found = [key for key, node in self.world.instances.items()
                 if self._class(self.world, key) == 'TextSource'
                 and node.get('parent') == channel
                 and node['properties'].get('UserIdReplicated') == self.user_id]
        if len(found) != 1:
            raise ValueError('No unique local TextSource in this channel')
        if check_send and self.world.instances[found[0]]['properties'].get('CanSend') is False:
            raise ValueError('The server disabled sending in this channel')
        return found[0]

    def channels(self):
        if self.world is None:
            return []
        result = []
        for key, node in self.world.instances.items():
            if self._class(self.world, key) != 'TextChannel' or not self._descendant_of_service(key):
                continue
            name = node['properties'].get('Name')
            if not isinstance(name, bytes):
                continue
            try:
                source = self._source(key, check_send=False)
                permission = self.world.instances[source]['properties'].get('CanSend')
                result.append({'name': name.decode('utf-8'), 'member': True,
                               'can_send_received': permission if type(permission) is bool else None})
            except ValueError:
                result.append({'name': name.decode('utf-8'), 'member': False,
                               'can_send_received': None})
        if (self.control.allocated_global and GLOBAL_SEND_EVENT in self.event_ids
                and GLOBAL_RECEIVE_EVENT in self.event_ids):
            result.append({'name': 'RBXGlobal', 'member': True,
                           'can_send_received': None, 'route': 'universe'})
        return result

    def require_global_route(self):
        if self.world is None or not self.attach(self.world):
            raise ValueError('Chat is not initialized')
        if not self.control.allocated_global:
            raise ValueError('The server has not allocated global chat')
        if GLOBAL_SEND_EVENT not in self.event_ids or GLOBAL_RECEIVE_EVENT not in self.event_ids:
            raise ValueError('The server global chat protocol is unavailable')

    def _packet(self, name, arguments=b''):
        return b'\x83\x07' + guid(self.service) + struct.pack('<H', self.event_ids[name]) + arguments + b'\0'

    def ready(self, world, send):
        if not self.attach(world):
            return False
        if not self.ready_sent:
            send(1, self._packet(READY_EVENT))
            self.ready_sent = True
        return True

    def send(self, text, send, *, channel='RBXGeneral', metadata='', channel_key=None,
             input_request_id=None):
        if not self.ready_sent or not self.attach(self.world):
            raise ValueError('Chat is not initialized')
        _text(text)
        _text(metadata, name='metadata', empty=True)
        now = self.clock()
        if self.last_send is not None and now - self.last_send < 1:
            raise ValueError('Wait at least one second between chat messages')
        if sum(row['state'] == 'submitted' for row in self.pending.values()) >= 8:
            raise ValueError('Too many pending chat messages')
        universe = channel == 'RBXGlobal' and channel_key is None
        if universe:
            self.require_global_route()
            target, source = self.service, None
        else:
            target = self._channel(channel) if channel_key is None else channel_key
        if not universe and (not isinstance(target, tuple) or len(target) != 2
                or any(type(part) is not int for part in target)
                or self._class(self.world, target) != 'TextChannel'
                or not self._descendant_of_service(target)):
            raise ValueError('Chat channel reference is not a server TextChannel')
        if not universe:
            channel = _received_text(self.world.instances[target]['properties'].get('Name'))
            source = self._source(target)
        verified = self._consume_typed_submission(input_request_id, text, metadata, target,
                                                  'universe' if universe else 'channel')
        # Native TextChannel::SendAsync (103850900 -> 103850120) builds
        # decimal local UserId + '-' + a fresh braced UUID. The shared native
        # UUID entry 102c77588 passes includeBraces=true to 102c77590; the
        # unbraced entry is a different function (102c776c8). Evidence:
        # outputs/chat-analysis/uuid-native-format.json.
        message_id = f'{self.user_id}-{{{uuid.uuid4()}}}'
        arguments = encode_string(text) + encode_string(metadata) + encode_string(message_id)
        if universe:
            # Native 103880b14 always uses Universe V1 for ordinary text.
            arguments += encode_signed(self.user_id) + bytes((verified,))
        else:
            arguments += guid(source) + guid(target)
        # Native 10386639c obtains verified from TextBoxService's input history
        # (103e53e24). Only the dedicated user-input path supplies an exact,
        # one-use receipt; arbitrary script strings remain unverified. Raw text
        # is not escaped and no preset is claimed.
        if not universe and self.send_event != 'ClientToServerMessageReplicateSignalV2':
            arguments += bytes((verified, False))
        if not universe and self.send_event == SEND_EVENT:
            arguments += encode_string('') + encode_string('')
        payload = self._packet(GLOBAL_SEND_EVENT if universe else self.send_event, arguments)
        # Never retry an ambiguous transport outcome: it could duplicate a post.
        self.last_send = now
        row = {'message_id': message_id, 'state': 'submitted', 'channel': channel,
               'source_key': source, 'channel_key': target, 'deadline': now + 15,
               'server_status': None, 'server_confirmed': False,
               'input_verification': 'typed_submission' if verified else 'unverified'}
        if universe:
            row['route'] = 'universe'
        self.pending[message_id] = row
        while len(self.pending) > 128:
            self.pending.popitem(last=False)
        self.counts['send_attempts'] += 1
        if universe:
            self.counts['universe_send_attempts'] += 1
        self.counts['typed_send_attempts' if verified else 'unverified_send_attempts'] += 1
        try:
            send(1, payload)
        except Exception:
            row['state'] = 'transport_outcome_unknown'
            self.counts['transport_errors'] += 1
            raise
        self.counts['submitted'] += 1
        self.counts['submitted_typed' if verified else 'submitted_unverified'] += 1
        return self.status(message_id)

    def status(self, message_id):
        if type(message_id) is not str or message_id not in self.pending:
            raise ValueError('Unknown chat message ID')
        return {k: v for k, v in self.pending[message_id].items()
                if k not in ('source_key', 'channel_key', 'deadline')}

    def runtime_status(self, message_id):
        result = self.status(message_id)
        if 'response' in result:
            row = self.pending[message_id]
            result['response'] = {**result['response'],
                                  'source_id': ('%d:%d' % row['source_key']
                                                if row['source_key'] is not None else None),
                                  'channel_id': '%d:%d' % row['channel_key']}
        return result

    def _receive(self, arguments, *, translation=b'', universe=False, sender_user_id=None):
        if not isinstance(arguments, list) or len(arguments) != 8:
            raise ValueError('Malformed server chat response')
        text, prefix, metadata, message_id = map(_received_text, arguments[:4])
        translation = _received_text(translation)
        source, channel, timestamp, status = arguments[4:]
        if (source is not None and (not isinstance(source, tuple) or len(source) != 2)
                or (channel is not None or not universe)
                   and (not isinstance(channel, tuple) or len(channel) != 2)
                or type(timestamp) is not int or type(status) is not int):
            raise ValueError('Malformed server chat response fields')
        pending = self.pending.get(message_id)
        if pending:
            if universe != (pending.get('route') == 'universe'):
                raise ValueError('Chat response route does not match the submitted message')
            if universe:
                if type(sender_user_id) is not int or sender_user_id != self.user_id:
                    raise ValueError('Global chat sender does not match the submitted message')
            elif pending['source_key'] != source or pending['channel_key'] != channel:
                raise ValueError('Chat response references do not match the submitted message')
        self.sequence += 1
        node = self.world.instances.get(channel)
        name = node['properties'].get('Name') if node else None
        message = {'sequence': self.sequence, 'message_id': message_id,
                   'text': text, 'translation': translation,
                   'prefix_text': prefix, 'metadata': metadata,
                   'channel': ('RBXGlobal' if universe else
                               _received_text(name) if isinstance(name, bytes) else None),
                   'timestamp': timestamp, 'status': STATUSES.get(status, 'UnknownStatus'),
                   'status_value': status, 'server_received': True}
        if universe:
            message['route'] = 'universe'
        self.messages.append(message)
        self.counts['received'] += 1
        self.counts['responses_observed'] += 1
        if pending and pending['state'] in ('submitted', 'response_timeout',
                                            'server_status_unknown', 'transport_outcome_unknown'):
            pending['server_status'] = message['status']
            pending['server_confirmed'] = status == 2
            pending['state'] = ('server_success' if status == 2 else 'submitted' if status == 3
                                else 'server_rejected' if status in range(4, 10)
                                else 'server_status_unknown')
            pending['response'] = message

    def _receive_dictionary(self, arguments, *, universe=False):
        # Native 103879c58 resolves these exact lower-camel-case fields.
        if (not isinstance(arguments,list) or len(arguments)!=1
                or not isinstance(arguments[0],list) or len(arguments[0])>64):
            raise ValueError('Malformed dictionary chat response')
        data = {}
        for pair in arguments[0]:
            if not isinstance(pair,(list,tuple)) or len(pair)!=2 or not isinstance(pair[0],bytes):
                raise ValueError('Malformed dictionary chat field')
            key = _received_text(pair[0],128)
            if key in data: raise ValueError('Duplicate dictionary chat field')
            data[key] = pair[1]
        names = ('text','translation','prefixText','metadata','messageId',
                 'textSource','textChannel','timestamp','status')
        if any(name not in data for name in names):
            raise ValueError('Missing dictionary chat field')
        source = data['textSource']
        sender = data.get('senderUserId')
        if universe:
            if source is not None:
                if (not isinstance(source, tuple) or len(source) != 2
                        or any(type(part) is not int for part in source)
                        or self._class(self.world, source) != 'TextSource'):
                    raise ValueError('Global chat source is not a TextSource')
                source_user = self.world.instances[source]['properties'].get('UserIdReplicated')
                if sender is not None and sender != source_user:
                    raise ValueError('Global chat sender fields disagree')
                sender = source_user
            if type(sender) is not int or sender < 0:
                raise ValueError('Global chat sender identity is unavailable')
        elif source is None and type(sender) is int and sender == self.user_id:
            message_id = _received_text(data['messageId'])
            pending = self.pending.get(message_id)
            if pending is not None: source = pending['source_key']
        self._receive([data['text'],data['prefixText'],data['metadata'],data['messageId'],
                       source,data['textChannel'],data['timestamp'],data['status']],
                      translation=data['translation'], universe=universe, sender_user_id=sender)

    def poll(self, world):
        if not self.attach(world):
            return
        journal = list(world.event_journal)
        if journal and self.cursor < journal[0][0] - 1:
            raise ValueError('Chat event journal overflow; response state is incomplete')
        for sequence, event in journal:
            if sequence <= self.cursor:
                continue
            self.cursor = sequence
            if event['key'] != self.service:
                continue
            if event['event'] == self.event_ids[RECEIVE_EVENT]:
                try:
                    self._receive(event['arguments'])
                except (ValueError, UnicodeError):
                    self.counts['malformed_responses'] += 1
            elif event['event'] == self.event_ids.get('ServerToClientMessageReplicateSignalV2'):
                try:
                    self._receive_dictionary(event['arguments'])
                except (ValueError,UnicodeError):
                    self.counts['malformed_responses'] += 1
            elif event['event'] == self.event_ids.get(GLOBAL_RECEIVE_EVENT):
                try:
                    self._receive_dictionary(event['arguments'], universe=True)
                except (ValueError, UnicodeError):
                    self.counts['malformed_responses'] += 1
        now = self.clock()
        for row in self.pending.values():
            if row['state'] == 'submitted' and now >= row['deadline']:
                row['state'] = 'response_timeout'

    def read(self, after=0):
        if type(after) is not int or after < 0 or after > self.sequence:
            raise ValueError('Invalid chat receipt cursor')
        if self.messages and after < self.messages[0]['sequence'] - 1:
            raise ValueError('Chat receipt history was overrun')
        return {'messages': [m for m in self.messages if m['sequence'] > after],
                'cursor': self.sequence}

    def summary(self):
        return {'initialized': self.ready_sent, 'submitted': self.counts['submitted'],
                'send_event': self.send_event,
                'send_event_selection_source': self._send_event_selection_source,
                'global_send_event': GLOBAL_SEND_EVENT if self.control.allocated_global else None,
                'control_state': self.control.summary(),
                'universe_send_attempts': self.counts['universe_send_attempts'],
                'send_attempts': self.counts['send_attempts'],
                'typed_send_attempts': self.counts['typed_send_attempts'],
                'unverified_send_attempts': self.counts['unverified_send_attempts'],
                'received': self.counts['received'],
                'responses_observed': self.counts['responses_observed'],
                'submitted_typed': self.counts['submitted_typed'],
                'submitted_unverified': self.counts['submitted_unverified'],
                'transport_errors': self.counts['transport_errors'],
                'pending': sum(row['state'] == 'submitted' for row in self.pending.values()),
                'server_success': sum(row['state'] == 'server_success' for row in self.pending.values()),
                'server_rejected': sum(row['state'] == 'server_rejected' for row in self.pending.values()),
                'response_timeouts': sum(row['state'] == 'response_timeout' for row in self.pending.values()),
                'malformed_responses': self.counts['malformed_responses'],
                'unsupported_responses': self.counts['unsupported_responses']}
