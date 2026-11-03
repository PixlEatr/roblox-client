"""Source generation for the local console's explicitly submitted chat input."""
from chat_protocol import _text


_SUPPORTED_CHANNELS = frozenset(('RBXGeneral', 'RBXGlobal'))


def user_chat_source(text, channel='RBXGeneral'):
    _text(text)
    if type(channel) is not str or channel not in _SUPPORTED_CHANNELS:
        raise ValueError('Chat channel must be RBXGeneral or RBXGlobal')
    # Lua decimal byte escapes preserve Unicode without accepting source code.
    literal = '"' + ''.join('\\%03d' % byte for byte in text.encode('utf-8')) + '"'
    if channel == 'RBXGlobal':
        return '''local service = game:GetService("TextChatService")
local message = service:SendUniverseChatMessageAsync(''' + literal + ''')
print("Chat:", message.Status.Name, message.Text)
return message.Status.Name, message.Text
'''
    return '''local channel = game:GetService("TextChatService").TextChannels.RBXGeneral
local message = channel:SendAsync(''' + literal + ''')
print("Chat:", message.Status.Name, message.Text)
return message.Status.Name, message.Text
'''
