# local luau runner

build with `python3 runtime/build.py` from the project root.

```sh
runtime/build/luau-host --source runtime/smoke.luau -- 2 5
runtime/build/luau-host --source runtime/tests/engine_model.luau --context-file runtime/tests/local_context.txt
```

`--source` runs real Luau. `--bytecode` loads trusted standard compiler output.
choose one. inputs are capped at 128 KiB. use `--timeout-ms` from 1 to 2000 and
`--memory-mb` from 8 to 128. each call starts fresh.

without an engine context, game/workspace/UserSettings aren't installed.
the offline fixture gives basic services, identity, parenting, and lookup
methods. the world-snapshot mode adds local replicated instances. neither is
a complete Roblox engine, and local mutations aren't sent to the server.

results are JSON with `ok` and `values`. return nil, booleans, finite numbers,
or bounded strings. world mode can also capture logs and local mutations.
strings preserve bytes as matching JSON code points. Python can recover the
bytes with `.encode('latin1')`.

these worker/VM limits aren't an OS sandbox. compiler allocations are outside
the VM allocation cap. only use bytecode whose compiler output you trust.
