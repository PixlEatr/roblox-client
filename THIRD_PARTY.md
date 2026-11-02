# third-party stuff

- Luau: https://github.com/luau-lang/luau , commit
  `2a3d8937c19c8fd9f7b72b6ec9068c62760e6588` (release 745).
  The host's required libraries are in `vendor/luau/`. Original comments and
  `LICENSE.txt` / `lua_LICENSE.txt` are kept intact.
- nlohmann/json: version 3.12.0, https://github.com/nlohmann/json . The header
  and its MIT license notice are in `runtime/vendor/`.
- The signature table is public verification data extracted from Roblox build
  `0.745.0.7451137`. Its provenance is in the adjacent JSON. It has no signing
  key or account credentials. The Roblox app is not bundled.
- Python dependencies keep their own licenses and are installed separately.

No license has been chosen for the original project code yet. Upstream license
notices still apply to their respective files.
