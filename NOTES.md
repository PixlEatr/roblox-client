# still wip

current runtime, terminal, launcher, renderer and export adapter are now in this repo. builds use the bundled Luau source.

1,771 script tests and 17 renderer tests pass. the runtime suite has 544 passes, 9 failures and 1 skip; details are in `PUBLICATION-CHECK.json`.

the filesystem aliases, API inventory, pivot-event behavior and some older Rust-host expectations still need work. long live sessions and the reported disconnects have not been retested for this upload.
