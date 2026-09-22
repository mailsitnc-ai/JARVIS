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
# gmail.modify = read + organize (labels, archive, mark read, trash). gmail.send = send new mail (it
# still never permanently deletes - trash is reversible). Sending is gated by the SENSITIVE 'email_send'
# capability so it always asks first, even under autonomy. Drive stays read-only.
SCOPES = ["https://www.googleapis.com/auth/drive.readonly", "https://www.googleapis.com/auth/gmail.modify",
          "https://www.googleapis.com/auth/gmail.send",
          "https://www.googleapis.com/auth/documents",       # read + create/edit Google Docs
          "https://www.googleapis.com/auth/spreadsheets"]    # read + write Google Sheets

DOCS_MIME = "application/vnd.google-apps.document"
SHEETS_MIME = "application/vnd.google-apps.spreadsheet"


class GoogleError(RuntimeError):
    pass


def _doc_insert_requests(content: str) -> list:
    """Build Docs API requests to insert `content`, styling markdown-ish headings (#, ##, ###) and a
    leading title line, so a created doc looks structured rather than a wall of text."""
    import re as _re

    lines = content.split("\n")
    plain, styles, pos = [], [], 0
    for i, line in enumerate(lines):
        style, text = None, line
        m = _re.match(r"^(#{1,3})\s+(.*)$", line)
        if m:
            style = {1: "HEADING_1", 2: "HEADING_2", 3: "HEADING_3"}[len(m.group(1))]
            text = m.group(2)
        elif i == 0 and line.strip():
            style = "TITLE"                      # first line becomes the document title style
        start = pos
        end = pos + len(text)
        if style and text.strip():
            styles.append((start, end, style))
        plain.append(text)
        pos = end + 1                            # +1 for the newline that joins the lines
    final = "\n".join(plain)
    if not final.strip():
        return []
    reqs = [{"insertText": {"location": {"index": 1}, "text": final}}]
    for start, end, style in styles:
        reqs.append({"updateParagraphStyle": {
            "range": {"startIndex": 1 + start, "endIndex": 1 + end + 1},
            "paragraphStyle": {"namedStyleType": style}, "fields": "namedStyleType"}})
    return reqs


def _store_path():
    return user_dir() / "google.json"


def _load() -> dict:
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
        blob = data.get("dpapi") if isinstance(data, dict) else None
        if blob:
            return json.loads(keystore._decode(blob))  # DPAPI on Windows, obfuscated file elsewhere
    except (OSError, ValueError):
        pass
    return {}


def _save(data: dict) -> None:
    # keystore._encode: DPAPI-encrypt on Windows (byte-identical to before), base64-obfuscate elsewhere.
    write_json_atomic(_store_path(), {"dpapi": keystore._encode(json.dumps(data))})


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

    last_error = None

    def forget_access_token(self) -> None:
        """Drop the cached access token (Google rejected it early) so the next call refreshes."""
        data = _load()
        if data.pop("access_token", None) is not None:
            data.pop("expiry", None)
            _save(data)

    def access_token(self) -> str | None:
        data = _load()
        if not data.get("refresh_token"):
            return None
        if data.get("access_token") and time.time() < data.get("expiry", 0) - 60:
            return data["access_token"]
        token = _post_token({"refresh_token": data["refresh_token"], "client_id": data["client_id"],
                             "client_secret": data["client_secret"], "grant_type": "refresh_token"})
        if "access_token" not in token:
            self.last_error = token.get("error_description") or token.get("error")
            return None
        data["access_token"] = token["access_token"]
        data["expiry"] = time.time() + int(token.get("expires_in", 3600))
        _save(data)
        return data["access_token"]


class GoogleClient:
    def __init__(self, auth: GoogleAuth | None = None):
        self.auth = auth or GoogleAuth()

    def _authed(self, call, *args, **kwargs):
        """Run an API call; if Google says 401 (token revoked/expired early), refresh once and retry.
        If the refresh itself fails, say why in plain words instead of dumping the raw 401."""
        try:
            return call(*args, **kwargs)
        except GoogleError as exc:
            if not str(exc).startswith("HTTP 401"):
                raise
        self.auth.forget_access_token()
        try:
            return call(*args, **kwargs)
        except GoogleError as exc:
            reason = getattr(self.auth, "last_error", None) or str(exc)
            if "client" in str(reason).lower():
                raise GoogleError("Google rejected JARVIS's OAuth client ID/secret. Run  jarvis google setup  "
                                  "with the client ID and secret from Google Cloud Console (make a new Desktop "
                                  "client if yours was deleted), then  jarvis google login") from exc
            raise GoogleError(f"Google sign-in expired ({reason}). Run: jarvis google login") from exc

    def _get(self, *args, **kwargs):
        return self._authed(self._get_once, *args, **kwargs)

    def _get_once(self, url: str) -> dict:
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

    def _send(self, *args, **kwargs):
        return self._authed(self._send_once, *args, **kwargs)

    def _send_once(self, method: str, url: str, body: dict) -> dict:
        token = self.auth.access_token()
        if not token:
            raise GoogleError("not connected to Google")
        request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method=method,
                                         headers={"Authorization": f"Bearer {token}",
                                                  "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=25) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            raise GoogleError(f"HTTP {exc.code}: {exc.read(300).decode('utf-8', 'replace')}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise GoogleError(str(getattr(exc, "reason", exc))) from exc

    # ---- Google Sheets ------------------------------------------------------------------------

    def search_sheets(self, query: str, limit: int = 5) -> str:
        files = self._find_files(query, SHEETS_MIME, limit)
        if not files:
            return f"No Google Sheets matching '{query}'."
        lines = [f"- {f.get('name', '(untitled)')}  {f.get('webViewLink', '')}".rstrip() for f in files]
        return f"Found {len(files)} Google Sheet(s) for '{query}':\n" + "\n".join(lines)

    def read_sheet(self, name_or_id: str, cell_range: str = "A1:Z50") -> str:
        sid = self._resolve_id(name_or_id, SHEETS_MIME)
        if not sid:
            return f"I couldn't find a Google Sheet called '{name_or_id}'."
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{urllib.parse.quote(cell_range)}"
        rows = self._get(url).get("values", [])
        if not rows:
            return f"'{name_or_id}' has no data in {cell_range}."
        widths = [max(len(str(r[i])) if i < len(r) else 0 for r in rows) for i in range(max(len(r) for r in rows))]
        out = [" | ".join(str(r[i] if i < len(r) else "").ljust(widths[i]) for i in range(len(widths))) for r in rows]
        return f"{name_or_id} ({cell_range}):\n" + "\n".join(out[:50])

    def create_sheet(self, title: str) -> str:
        result = self._post("https://sheets.googleapis.com/v4/spreadsheets", {"properties": {"title": title or "Untitled"}})
        sid = result.get("spreadsheetId")
        if not sid:
            raise GoogleError("couldn't create the spreadsheet")
        return f"https://docs.google.com/spreadsheets/d/{sid}/edit"

    def write_sheet(self, name_or_id: str, cell_range: str, values: list) -> str:
        sid = self._resolve_id(name_or_id, SHEETS_MIME)
        if not sid:
            return f"I couldn't find a Google Sheet called '{name_or_id}'."
        url = (f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{urllib.parse.quote(cell_range)}"
               "?valueInputOption=USER_ENTERED")
        res = self._send("PUT", url, {"values": values})
        return f"Updated {res.get('updatedCells', 0)} cell(s) in '{name_or_id}'."

    def append_row(self, name_or_id: str, values: list) -> str:
        sid = self._resolve_id(name_or_id, SHEETS_MIME)
        if not sid:
            return f"I couldn't find a Google Sheet called '{name_or_id}'."
        url = (f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/A1:append"
               "?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS")
        row = values if values and isinstance(values[0], list) else [values]
        self._send("POST", url, {"values": row})
        return f"Added a row to '{name_or_id}'."

    def _get_text(self, url: str) -> str:
        token = self.auth.access_token()
        if not token:
            raise GoogleError("not connected to Google")
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(request, timeout=25) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raise GoogleError(f"HTTP {exc.code}: {exc.read(300).decode('utf-8', 'replace')}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise GoogleError(str(getattr(exc, "reason", exc))) from exc

    def _find_files(self, name: str, mime: str, limit: int = 5) -> list:
        term = name.replace("\\", "\\\\").replace("'", "\\'")
        params = urllib.parse.urlencode({
            "q": f"mimeType='{mime}' and (name contains '{term}' or fullText contains '{term}') and trashed = false",
            "pageSize": limit, "orderBy": "modifiedTime desc", "fields": "files(id,name,webViewLink)"})
        return self._get(f"https://www.googleapis.com/drive/v3/files?{params}").get("files", [])

    def _resolve_id(self, name_or_id: str, mime: str) -> str | None:
        import re as _re
        if _re.fullmatch(r"[A-Za-z0-9_-]{25,}", name_or_id.strip()):
            return name_or_id.strip()                       # already a Drive/Docs id
        files = self._find_files(name_or_id, mime, limit=1)
        return files[0]["id"] if files else None

    # ---- Google Docs --------------------------------------------------------------------------

    def search_docs(self, query: str, limit: int = 5) -> str:
        files = self._find_files(query, DOCS_MIME, limit)
        if not files:
            return f"No Google Docs matching '{query}'."
        lines = [f"- {f.get('name', '(untitled)')}  {f.get('webViewLink', '')}".rstrip() for f in files]
        return f"Found {len(files)} Google Doc(s) for '{query}':\n" + "\n".join(lines)

    def read_doc(self, name_or_id: str, max_chars: int = 4000) -> str:
        did = self._resolve_id(name_or_id, DOCS_MIME)
        if not did:
            return f"I couldn't find a Google Doc called '{name_or_id}'."
        text = self._get_text(f"https://www.googleapis.com/drive/v3/files/{did}/export?mimeType=text/plain").strip()
        if not text:
            return "(that doc is empty)"
        return text[:max_chars] + (" ..." if len(text) > max_chars else "")

    def create_doc(self, title: str, content: str = "") -> str:
        doc = self._post("https://docs.googleapis.com/v1/documents", {"title": title or "Untitled"})
        did = doc.get("documentId")
        if not did:
            raise GoogleError("couldn't create the document")
        reqs = _doc_insert_requests(content) if content else []
        if reqs:
            self._post(f"https://docs.googleapis.com/v1/documents/{did}:batchUpdate", {"requests": reqs})
        return f"https://docs.google.com/document/d/{did}/edit"

    def append_to_doc(self, name_or_id: str, text: str) -> str:
        did = self._resolve_id(name_or_id, DOCS_MIME)
        if not did:
            return f"I couldn't find a Google Doc called '{name_or_id}'."
        doc = self._get(f"https://docs.googleapis.com/v1/documents/{did}?fields=body(content(endIndex))")
        end = 1
        for el in doc.get("body", {}).get("content", []):
            if isinstance(el, dict) and "endIndex" in el:
                end = el["endIndex"]
        index = max(1, end - 1)
        reqs = [{"insertText": {"location": {"index": index}, "text": "\n" + text}}]
        self._post(f"https://docs.googleapis.com/v1/documents/{did}:batchUpdate", {"requests": reqs})
        return f"https://docs.google.com/document/d/{did}/edit"

    def search_drive(self, query: str, limit: int = 5) -> str:
        term = query.replace("\\", "\\\\").replace("'", "\\'")
        params = urllib.parse.urlencode({          # urlencode escapes the spaces in orderBy/fields too
            "q": f"(name contains '{term}' or fullText contains '{term}') and trashed = false",
            "pageSize": limit,
            "orderBy": "modifiedTime desc",
            "fields": "files(name,webViewLink,mimeType)",
        })
        url = f"https://www.googleapis.com/drive/v3/files?{params}"
        files = self._get(url).get("files", [])
        if not files:
            return f"No Drive files matching '{query}'."
        lines = [f"- {f.get('name', '(untitled)')}  {f.get('webViewLink', '')}".rstrip() for f in files]
        return f"Found {len(files)} in your Drive for '{query}':\n" + "\n".join(lines)

    def _post(self, *args, **kwargs):
        return self._authed(self._post_once, *args, **kwargs)

    def _post_once(self, url: str, body: dict) -> dict:
        token = self.auth.access_token()
        if not token:
            raise GoogleError("not connected to Google")
        request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                         headers={"Authorization": f"Bearer {token}",
                                                  "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=25) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            raise GoogleError(f"HTTP {exc.code}: {exc.read(300).decode('utf-8', 'replace')}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise GoogleError(str(getattr(exc, "reason", exc))) from exc

    def _message_ids(self, query: str, limit: int = 200) -> list[str]:
        ids: list[str] = []
        page = None
        while len(ids) < limit:
            url = (f"https://gmail.googleapis.com/gmail/v1/users/me/messages?q={urllib.parse.quote(query)}"
                   f"&maxResults={min(100, limit - len(ids))}" + (f"&pageToken={page}" if page else ""))
            data = self._get(url)
            ids += [m["id"] for m in data.get("messages", [])]
            page = data.get("nextPageToken")
            if not page:
                break
        return ids[:limit]

    def ensure_label(self, name: str) -> str:
        for label in self._get("https://gmail.googleapis.com/gmail/v1/users/me/labels").get("labels", []):
            if label.get("name", "").lower() == name.lower():
                return label["id"]
        created = self._post("https://gmail.googleapis.com/gmail/v1/users/me/labels",
                             {"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"})
        return created.get("id", "")

    def _batch_modify(self, ids: list[str], add=None, remove=None) -> None:
        for i in range(0, len(ids), 100):
            self._post("https://gmail.googleapis.com/gmail/v1/users/me/messages/batchModify",
                       {"ids": ids[i:i + 100], "addLabelIds": add or [], "removeLabelIds": remove or []})

    def gmail_organize(self, query: str, action: str, label: str | None = None, limit: int = 300) -> str:
        """Organize inbox mail matching a Gmail query. action: archive | read | trash | label."""
        action = (action or "").lower()
        ids = self._message_ids(query, limit)
        if not ids:
            return f"No emails matching '{query}'."
        if action == "archive":
            self._batch_modify(ids, remove=["INBOX"]); verb = "Archived"
        elif action == "read":
            self._batch_modify(ids, remove=["UNREAD"]); verb = "Marked read"
        elif action == "trash":
            self._batch_modify(ids, add=["TRASH"], remove=["INBOX"]); verb = "Moved to Trash"
        elif action == "label":
            if not label:
                return "Tell me which label to apply."
            self._batch_modify(ids, add=[self.ensure_label(label)]); verb = f"Labelled '{label}'"
        else:
            return f"Unknown Gmail action '{action}'. Use archive, read, trash or label."
        return f"{verb}: {len(ids)} email(s) matching '{query}'."

    def my_address(self) -> str | None:
        """The signed-in account's own email address (for 'send it to myself')."""
        try:
            return self._get("https://gmail.googleapis.com/gmail/v1/users/me/profile").get("emailAddress")
        except GoogleError:
            return None

    def gmail_send(self, to: str, subject: str, body: str, cc: str | None = None) -> str:
        """Send a plain-text email from the signed-in account. Needs the gmail.send scope."""
        import base64
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["To"] = to
        if cc:
            msg["Cc"] = cc
        msg["Subject"] = subject or "(no subject)"
        msg.set_content(body or "")
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        self._post("https://gmail.googleapis.com/gmail/v1/users/me/messages/send", {"raw": raw})
        return to

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
