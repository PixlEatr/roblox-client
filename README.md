# roblox client

an unfinished custom roblox client. real luau, packet handling, and world replication. still a work in progress.

latest local progress — nov 2, 2026:

- the alt spawns, falls, walks, and has visible walking animations in private-server tests.
- the compatibility worker passed 83/83 UNC checks with no missing aliases, plus 215 regression tests.
- regular script files allow 100 MiB and the VM defaults to a 512 MiB memory cap.
- sUNC still gets stuck. the offline run hits missing TestService support, and the live pending-job issue is being investigated.

this GitHub source snapshot is older than those local results. full engine support, all executor APIs, and a browser renderer still aren't done. UNC passing doesn't mean every game works.

for the runner included here:

```sh
python3 runtime/build.py
runtime/build/luau-host --source runtime/smoke.luau -- 2 5
```

[runner notes](runtime/README.md) · [dev notes](NOTES.md) · [credits](THIRD_PARTY.md)
