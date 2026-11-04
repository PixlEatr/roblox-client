# dev checks

For the focused cross-platform checks, run `python rove.py check` (`py` on Windows).

For the broader development suites, from the repo root after setup:

```sh
.venv/bin/python tests/run.py scripts
.venv/bin/python tests/run.py runtime
npm --prefix ui test
```

`tests/run.py` runs all three by default. `scripts/` and `runtime/` here hold Python checks; `ui/` holds browser logic checks; `fixtures/` holds test data.

`.venv/bin/python tests/preview.py --showcase --allow-webgl` opens an offline Rove fixture with a real Luau worker. it never joins a game. the fixture can use installed Roblox art files.

the runtime suite still has the known failures listed in [validation.json](validation.json). optional decompiler/private reference checks skip when their dependencies are absent.

On Windows, replace `.venv/bin/python` with `.venv\Scripts\python.exe`. The broad suites include optional platform-specific research tools; the portability workflow uses the focused suite.
