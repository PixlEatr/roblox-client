# virtual input

keys, mouse, touch, gamepads, sensors and record/replay are in. it runs inside Rove, so it won't move your actual mouse.

```lua
local vim = game:GetService("VirtualInputManager")
vim:SendKeyEvent(true, Enum.KeyCode.W, false, game)
vim:WaitForInputEventsProcessed()
vim:SendKeyEvent(false, Enum.KeyCode.W, false, game)
```

GUI clicks, scrolling and TextBox editing use the same input path as normal controls. gamepad state and ContextActionService callbacks work too.

## record / replay

```lua
local recording
vim.RecordingCompleted:Connect(function(json) recording = json end)
vim:StartRecording()
vim:SendMouseMoveEvent(100, 100, game)
vim:StopRecording()
vim:WaitForInputEventsProcessed()
vim:StartPlayingJSON(recording)
```

`StopPlaying()` releases the replay's held inputs. `AdditionalLuaState` keeps a string checkpoint and comes back through `PlaybackCompleted`. `StartPlaying(path)` reads from the Luau workspace; `Dump()` prints the recording and any playback error.

## the limits

- recordings use Rove's JSON format. Roblox benchmark files won't load.
- 10k events, 2 MiB per recording, one-hour playback max.
- gamepads use the first free Gamepad1–8 slot. button/touch states: Begin=0, Change=1, End=2, Cancel=3.
- scroll flags: `StartOfScroll`, `IsPartOfGesture`, `IsResultOfMomentum` (booleans).
- `sendRobloxEvent` and `sendThemeChangeEvent` save local markers. no analytics or OS theme changes.
- all 25 methods are wired locally. exact Roblox timing/device behavior, live games and Windows still need more testing.

[setup](setup.md) · [tests](../tests/runtime/test_virtual_input_extended.py)
