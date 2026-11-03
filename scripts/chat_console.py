"""Opt-in, owner-only chat console attached to the existing session loop."""
from chat_protocol import ChatProtocol
from script_console import ScriptConsole


class ChatConsole:
    def __init__(self, path, user_id, *, capability_bits=0x100000):
        self.chat = ChatProtocol(user_id, capability_bits=capability_bits)
        self.client = None
        self.failure = None
        self.console = ScriptConsole(path, None, dispatch=self.dispatch)

    def dispatch(self, request):
        operation = request.get('op')
        allowed = {'channels': {'op'}, 'read': {'op', 'after'},
                   'status': {'op', 'message_id'},
                   'send': {'op', 'text', 'channel', 'metadata'}}
        if type(operation) is not str or operation not in allowed or set(request) - allowed[operation]:
            raise ValueError('Invalid chat request')
        if self.failure:
            raise RuntimeError('Chat stopped after a protocol or transport failure')
        if operation == 'channels':
            return {'ok': True, 'initialized': self.chat.ready_sent,
                    'channels': self.chat.channels()}
        if operation == 'read':
            return {'ok': True, **self.chat.read(request.get('after', 0))}
        if operation == 'status':
            return {'ok': True, **self.chat.status(request.get('message_id'))}
        if self.client is None:
            raise ValueError('The game session is not ready')
        result = self.chat.send(request.get('text'), self.client.send,
                                channel=request.get('channel', 'RBXGeneral'),
                                metadata=request.get('metadata', ''))
        return {'ok': True, **result}

    def tick(self, client, world):
        self.client = client
        if not self.failure:
            try:
                if self.chat.ready(world, client.send):
                    self.chat.poll(world)
            except Exception as error:
                # No raw exception or chat content goes into public reports.
                self.failure = type(error).__name__
        self.console.poll()
        client.report['chat'] = {**self.chat.summary(), 'healthy': self.failure is None}
        if self.failure:
            client.report['chat']['error_type'] = self.failure

    def runtime_submit(self, request):
        if self.failure:
            raise RuntimeError('Chat stopped after a protocol or transport failure')
        if self.client is None or not self.chat.ready_sent:
            raise ValueError('The chat service is not ready')
        reference = request['channel_id']
        if type(reference) is not str or len(reference) > 32:
            raise ValueError('Invalid chat target reference')
        route = request.get('route', 'channel')
        if route == 'universe':
            self.chat.require_global_route()
            expected = '%d:%d' % self.chat.service
            if reference != expected:
                raise ValueError('Universe chat target does not match TextChatService')
            return self.chat.send(request['text'], self.client.send,
                                  channel='RBXGlobal',
                                  metadata=request.get('metadata', ''),
                                  input_request_id=request['request_id'])
        if route != 'channel':
            raise ValueError('Unsupported chat route')
        parts = reference.split(':')
        if len(parts) != 2 or not all(part.isascii() and part.isdecimal() for part in parts):
            raise ValueError('TextChannel is not a server instance')
        return self.chat.send(request['text'], self.client.send,
                              metadata=request.get('metadata', ''),
                              channel_key=tuple(map(int, parts)),
                              input_request_id=request['request_id'])

    def runtime_status(self, message_id):
        if self.failure:
            raise RuntimeError('Chat stopped after a protocol or transport failure')
        return self.chat.runtime_status(message_id)

    def close(self):
        self.chat._typed_submissions.clear()
        self.console.close()
