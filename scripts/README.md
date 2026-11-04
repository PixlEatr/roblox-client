# Python modules

**To use Rove, start with [`rove.py`](../rove.py) in the project root.** The files here are implementation modules and developer tools. You do not need to run each one.

## Where to change things

| Area | Start with | Related modules |
| --- | --- | --- |
| Installation and Windows setup | [`setup_terminal.py`](setup_terminal.py), [`setup_windows.py`](setup_windows.py) | `platform_environment.py`, `platform_files.py`, `platform_process.py` |
| Terminal commands | [`terminal_client.py`](terminal_client.py) | `script_console.py`, `chat_console.py` |
| Browser workspace | [`launcher.py`](launcher.py) and [`ui/`](../ui/) | `launcher_*.py`, `browser_input.py`, `web_console.py` |
| Game session lifecycle | [`game_session.py`](game_session.py) | `roblox_session.py`, `native_session.py`, `client_session_lease.py` |
| Received world and instances | [`world_replication.py`](world_replication.py) | `world_snapshot.py`, `world_schema.py`, `wire_reader.py` |
| Luau execution | [`script_runtime.py`](script_runtime.py), [`persistent_runtime.py`](persistent_runtime.py) | `client_luau.py`, `local_runtime.py`; engine bindings live in [`runtime/`](../runtime/) |
| Walking, jumping, and character states | [`physics_controller.py`](physics_controller.py) | `physics_character_states.py`, `movement_intents.py`, `physics_script_bridge.py` |
| Collision, stairs, and platforms | [`physics_ground.py`](physics_ground.py) | `movement_collision.py`, `physics_stairs.py`, `physics_platforms.py` |
| Forces and assembly motion | [`physics_forces.py`](physics_forces.py) | `authored_force_state.py`, `physics_assemblies.py`, `physics_linear_velocity.py` |
| Animation | [`animation_joint_model.py`](animation_joint_model.py) | `animation_assets.py`, `animation_pose_runtime.py`, `locomotion_animation.py` |
| Chat | [`client_chat.py`](client_chat.py) | `chat_input.py`, `chat_protocol.py`, `chat_control_state.py` |
| Diagnostics and state snapshots | [`runtime_diagnostics.py`](runtime_diagnostics.py) | `physics_live_monitor.py`, `received_instance_diagnostics.py`; session lifecycle reporting lives in `game_session.py` |
| Offline checks | [`check_portability.py`](check_portability.py) | `runtime_preflight.py`; test cases live in [`tests/`](../tests/) |

## Developer tools

Files named `probe_*`, `validate_*`, `replay_*`, and `*_ida*` are targeted research or verification tools. Some use local captures, IDA, or a live session. Read the tool before running it; they are not extra installation steps.

Keep the existing module paths stable: imports, fixtures, and launchers use them directly. Put new tests in `tests/scripts/`, and user-facing instructions in `docs/`.

[Project map](../docs/development.md) · [Setup](../docs/setup.md) · [Test guide](../tests/README.md)
