"""Put cloud/worker.js live, from here, without touching the dashboard.

Cloudflare's code editor lives in a cross-origin iframe that refuses synthetic keystrokes, so every
change to the worker used to mean a human pasting 150 lines. This uploads it through the API instead:

    python3 cloud/deploy.py

`keep_bindings` is the important part - it tells Cloudflare to leave the KV namespace and every
secret exactly as they are. Without it an upload would wipe GROQ_KEY and WA_TOKEN, and those are not
recoverable. The API token itself lives with the other keys: `jarvis setkey cloudflare`.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ACCOUNT = "b02817301ab9d883fdb4213afe2d77f2"
SCRIPT = "jarvis"
WORKER = ROOT / "cloud" / "worker.js"
METADATA = {"main_module": "worker.js",
            "compatibility_date": "2026-01-01",
            # leave the KV namespace and the secrets alone - we do not know their values and never
            # want to find out what happens if they vanish
            "keep_bindings": ["kv_namespace", "secret_text", "plain_text"]}


def body(code: str) -> tuple[bytes, str]:
    """A multipart upload: the metadata, then the module itself."""
    line = f"----jarvis{uuid.uuid4().hex}"
    parts = [
        f"--{line}\r\nContent-Disposition: form-data; name=\"metadata\"\r\n"
        f"Content-Type: application/json\r\n\r\n{json.dumps(METADATA)}\r\n",
        f"--{line}\r\nContent-Disposition: form-data; name=\"worker.js\"; filename=\"worker.js\"\r\n"
        f"Content-Type: application/javascript+module\r\n\r\n{code}\r\n",
        f"--{line}--\r\n",
    ]
    return "".join(parts).encode("utf-8"), f"multipart/form-data; boundary={line}"


def deploy() -> int:
    from core import keystore

    token = keystore.get_key("cloudflare")
    if not token:
        print("No Cloudflare API token stored. Run: jarvis setkey cloudflare")
        return 1
    if not WORKER.exists():
        print(f"{WORKER} is missing.")
        return 1
    data, kind = body(WORKER.read_text())
    request = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/workers/scripts/{SCRIPT}",
        data=data, method="PUT",
        headers={"Authorization": f"Bearer {token}", "Content-Type": kind})
    try:
        with urllib.request.urlopen(request, timeout=60) as answer:
            out = json.loads(answer.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:600]
        print(f"Cloudflare refused it ({exc.code}): {detail}")
        return 1
    except (urllib.error.URLError, OSError) as exc:
        print(f"Couldn't reach Cloudflare: {exc}")
        return 1
    if not out.get("success"):
        print("Cloudflare refused it:", json.dumps(out.get("errors"))[:600])
        return 1
    result = out.get("result") or {}
    print(f"Deployed {SCRIPT}: {len(WORKER.read_text())} bytes, "
          f"modified {result.get('modified_on', 'just now')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(deploy())
