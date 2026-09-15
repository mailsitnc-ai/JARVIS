"""Google Drive + Gmail access for JARVIS, via OAuth 2.0 (installed-app loopback flow).

Standard library only. The user creates a free OAuth client (Desktop app) in Google Cloud once and
runs `jarvis google setup` + `jarvis google login`; tokens are stored DPAPI-encrypted, like API keys.
Read-only scopes (drive.readonly, gmail.readonly) - JARVIS reads and searches, it doesn't modify.
"""
from __future__ import annotations

import base64
import http.server
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from . import keystore
from .config import user_dir, write_json_atomic

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly", "https://www.googleapis.com/auth/gmail.readonly"]


class GoogleError(RuntimeError):
    pass


def _store_path():
    return user_dir() / "google.json"


def _load() -> dict:
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
        blob = data.get("dpapi") if isinstance(data, dict) else None
        if blob:
            return json.loads(keystore._dpapi(base64.b64decode(blob), protect=False).decode("utf-8"))
    except (OSError, ValueError):
        pass
    return {}


def _save(data: dict) -> None:
    encrypted = base64.b64encode(keystore._dpapi(json.dumps(data).encode("utf-8"), protect=True)).decode("ascii")
    write_json_atomic(_store_path(), {"dpapi": encrypted})


def _post_token(fields: dict) -> dict:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(TOKEN_URL, data=body, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except ValueError:
            return {"error": f"HTTP {exc.code}"}
    except (urllib.error.URLError, OSError) as exc:
        return {"error": str(getattr(exc, "reason", exc))}


class GoogleAuth:
    def set_credentials(self, client_id: str, client_secret: str) -> None:
        data = _load()
        data["client_id"] = client_id.strip()
        data["client_secret"] = client_secret.strip()
        _save(data)

    def has_credentials(self) -> bool:
        data = _load()
        return bool(data.get("client_id") and data.get("client_secret"))

    def is_connected(self) -> bool:
        return bool(_load().get("refresh_token"))

    def status(self) -> str:
        if not self.has_credentials():
            return "no OAuth credentials (run: jarvis google setup)"
        return "connected" if self.is_connected() else "credentials set, not logged in (run: jarvis google login)"

    def logout(self) -> None:
        data = _load()
        for key in ("refresh_token", "access_token", "expiry"):
            data.pop(key, None)
        _save(data)

    def login(self, open_browser: bool = True, timeout: float = 180) -> str:
        data = _load()
        if not (data.get("client_id") and data.get("client_secret")):
            return "No Google credentials yet. Run: jarvis google setup"

        holder: dict = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                holder.update(code=params.get("code", [None])[0], state=params.get("state", [None])[0],
                              error=params.get("error", [None])[0])
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<h2>JARVIS is connected to Google. You can close this tab.</h2>")

            def log_message(self, *_args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        redirect = f"http://127.0.0.1:{server.server_address[1]}/"
        state = secrets.token_urlsafe(16)
        query = urllib.parse.urlencode({
            "client_id": data["client_id"], "redirect_uri": redirect, "response_type": "code",
            "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent", "state": state,
        })
        auth_url = f"{AUTH_URL}?{query}"
        if open_browser:
            import webbrowser

            webbrowser.open(auth_url)
        server.timeout = timeout
        try:
            server.handle_request()  # blocks until Google redirects back once
        finally:
            server.server_close()

        if holder.get("error"):
            return f"Google login failed: {holder['error']}"
        if not holder.get("code") or holder.get("state") != state:
            return "Google login didn't complete (no code returned)."
        token = _post_token({"code": holder["code"], "client_id": data["client_id"],
                             "client_secret": data["client_secret"], "redirect_uri": redirect,
                             "grant_type": "authorization_code"})
        if "access_token" not in token:
            return f"Token exchange failed: {token.get('error_description', token.get('error', 'unknown'))}"
        data["refresh_token"] = token.get("refresh_token") or data.get("refresh_token")
        data["access_token"] = token["access_token"]
        data["expiry"] = time.time() + int(token.get("expires_in", 3600))
        _save(data)
        return "Connected to Google Drive and Gmail."

    def access_token(self) -> str | None:
        data = _load()
        if not data.get("refresh_token"):
            return None
        if data.get("access_token") and time.time() < data.get("expiry", 0) - 60:
            return data["access_token"]
        token = _post_token({"refresh_token": data["refresh_token"], "client_id": data["client_id"],
                             "client_secret": data["client_secret"], "grant_type": "refresh_token"})
        if "access_token" not in token:
            return None
        data["access_token"] = token["access_token"]
        data["expiry"] = time.time() + int(token.get("expires_in", 3600))
        _save(data)
        return data["access_token"]


class GoogleClient:
    def __init__(self, auth: GoogleAuth | None = None):
        self.auth = auth or GoogleAuth()

    def _get(self, url: str) -> dict:
        token = self.auth.access_token()
        if not token:
            raise GoogleError("not connected to Google")
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(request, timeout=25) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise GoogleError(f"HTTP {exc.code}: {exc.read(300).decode('utf-8', 'replace')}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise GoogleError(str(getattr(exc, "reason", exc))) from exc

    def search_drive(self, query: str, limit: int = 5) -> str:
        term = query.replace("\\", "\\\\").replace("'", "\\'")
        q = urllib.parse.quote(f"(name contains '{term}' or fullText contains '{term}') and trashed = false")
        url = (f"https://www.googleapis.com/drive/v3/files?q={q}&pageSize={limit}"
               "&orderBy=modifiedTime desc&fields=files(name,webViewLink,mimeType)")
        files = self._get(url).get("files", [])
        if not files:
            return f"No Drive files matching '{query}'."
        lines = [f"- {f.get('name', '(untitled)')}  {f.get('webViewLink', '')}".rstrip() for f in files]
        return f"Found {len(files)} in your Drive for '{query}':\n" + "\n".join(lines)

    def search_gmail(self, query: str, limit: int = 5) -> str:
        listing = self._get(f"https://gmail.googleapis.com/gmail/v1/users/me/messages"
                            f"?q={urllib.parse.quote(query)}&maxResults={limit}")
        messages = listing.get("messages", [])
        if not messages:
            return f"No emails matching '{query}'."
        lines = []
        for message in messages[:limit]:
            detail = self._get(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message['id']}"
                               "?format=metadata&metadataHeaders=Subject&metadataHeaders=From")
            headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
            lines.append(f"- {headers.get('Subject', '(no subject)')} — {headers.get('From', '')}")
        return f"Found {len(messages)} emails for '{query}':\n" + "\n".join(lines)
