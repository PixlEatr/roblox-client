# luau runner

from the repo root:

```sh
python3 runtime/build.py
runtime/build/luau-host --source runtime/smoke.luau -- 2 5
```

this published runner starts a fresh VM each time. it returns JSON with `ok`, `values`, and errors. world snapshots also support logs and local changes.

this snapshot's limits are 128 KiB input, 1–2000 ms execution, and 8–128 MiB VM memory. the newer local build has higher limits; see the root README.

`game`, `workspace`, and `UserSettings` need an engine context or world snapshot. local changes don't automatically reach a server.

`--bytecode` is for trusted compiler output. the VM memory cap doesn't cover the compiler or whole process, and this isn't an OS sandbox.
