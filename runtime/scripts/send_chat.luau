-- Submitting this file to a chat-enabled live client posts one real message.
local service = game:GetService("TextChatService")
local channel = service:WaitForChild("TextChannels"):WaitForChild("RBXGeneral")
local message = channel:SendAsync("hello", "")
print(message.Status.Name, message.Text)
return message.Status == Enum.TextChatMessageStatus.Success, message.Text
