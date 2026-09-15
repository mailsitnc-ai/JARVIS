"""A tiny localhost control channel so `jarvis toggle` in a terminal can drive the running daemon.

The daemon writes %APPDATA%\\JARVIS\\daemon.json with its port and a random token; only
requests carrying that token are obeyed.
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import threading

from core.config import user_dir


def state_path():
    return user_dir() / "daemon.json"


def _readline(sock: socket.socket, limit: int = 65536) -> bytes:
    data = b""
    while b"\n" not in data and len(data) < limit:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return data.split(b"\n", 1)[0]


class ControlServer(threading.Thread):
    def __init__(self, handler, port: int):
        super().__init__(name="jarvis-control", daemon=True)
        self.handler = handler
        self.token = secrets.token_hex(16)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            self.sock.bind(("127.0.0.1", int(port)))
        except OSError:
            self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.listen(4)
        state_path().write_text(json.dumps({"pid": os.getpid(), "port": self.port, "token": self.token}), encoding="utf-8")

    def run(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.settimeout(3)
                    message = json.loads(_readline(conn) or b"{}")
                    if not isinstance(message, dict) or message.get("token") != self.token:
                        reply = {"ok": False, "error": "unauthorized"}
                    else:
                        reply = self.handler(str(message.get("cmd", "")))
                    conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
                except (OSError, ValueError):
                    continue

    def close(self) -> None:
        try:
            self.sock.close()
        finally:
            try:
                state = json.loads(state_path().read_text(encoding="utf-8"))
                if state.get("pid") == os.getpid():
                    state_path().unlink()
            except (OSError, ValueError):
                pass


def send(command: str, timeout: float = 3.0) -> dict | None:
    """Send a command to the daemon. Returns its reply, or None if it is not running."""
    try:
        state = json.loads(state_path().read_text(encoding="utf-8"))
        with socket.create_connection(("127.0.0.1", int(state["port"])), timeout=timeout) as sock:
            sock.sendall((json.dumps({"token": state["token"], "cmd": command}) + "\n").encode("utf-8"))
            reply = json.loads(_readline(sock) or b"null")
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return reply if isinstance(reply, dict) else None
