"""Small HTTPS helper. Keep the cookie local and out of the logs."""
import http.client
import json
import os
from pathlib import Path
import stat

ROOT = Path(__file__).resolve().parent.parent
ALLOWED_HOSTS = {"users.roblox.com", "games.roblox.com", "gamejoin.roblox.com", "apis.roblox.com"}


class SessionError(Exception):
    """Error messages in this module contain no request/response secrets."""


class RobloxSession:
    def __init__(self):
        path = ROOT / "secrets/roblox-cookie.txt"
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise SessionError("Cookie file must be a regular owner-only file")
        if not 0 < info.st_size <= 32768:
            raise SessionError("Cookie file has an unexpected size")
        try:
            cookie = path.read_text().strip()
        except Exception:
            raise SessionError("Cookie file could not be read") from None
        if cookie.startswith(".ROBLOSECURITY="):
            cookie = cookie[len(".ROBLOSECURITY="):].rstrip(";")
        if not cookie or any(ord(c) < 33 or ord(c) > 126 or c == ";" for c in cookie):
            raise SessionError("Cookie file must contain only the cookie value")
        self._cookie = cookie
        self._csrf = {}

    def request(self, host, path, method="GET", payload=None, csrf_retry=False):
        if host not in ALLOWED_HOSTS or not path.startswith("/") or any(ord(c) < 32 for c in path):
            raise SessionError("Request destination is not allowed")
        if method not in {"GET", "POST"}:
            raise SessionError("Unsupported method")
        for attempt in range(2 if csrf_retry else 1):
            headers = {"Cookie": ".ROBLOSECURITY=" + self._cookie,
                       "Accept": "application/json", "User-Agent": "LocalRobloxResearch/1.0",
                       "Origin": "https://www.roblox.com", "Referer": "https://www.roblox.com/"}
            body = json.dumps(payload).encode() if payload is not None else None
            if body is not None:
                headers["Content-Type"] = "application/json"
            if host in self._csrf:
                headers["X-CSRF-TOKEN"] = self._csrf[host]
            connection = http.client.HTTPSConnection(host, timeout=20)
            try:
                connection.request(method, path, body=body, headers=headers)
                response = connection.getresponse()
                status = response.status
                token = response.getheader("x-csrf-token")
                challenge = bool(response.getheader("rblx-challenge-id"))
                content_type = response.getheader("content-type", "")
                raw = response.read(2 * 1024 * 1024 + 1)
            except Exception as error:
                raise SessionError("HTTPS request failed: " + type(error).__name__) from None
            finally:
                connection.close()
            if len(raw) > 2 * 1024 * 1024:
                raise SessionError("Response exceeded the size limit")
            if status == 403 and token and csrf_retry and attempt == 0 and not challenge:
                # Retry once with the CSRF token Roblox sent back.
                # Stop at account challenges or redirects.
                self._csrf[host] = token
                continue
            try:
                value = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                value = None
            return {"http_status": status, "challenge_present": challenge,
                    "content_type": content_type, "data": value}
        raise SessionError("Request retry was exhausted")
