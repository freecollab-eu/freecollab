#!/usr/bin/env python3
"""A minimal customer-owned signed FreeCollab app with a durable callback queue.

Set FREECOLLAB_APP_SECRET and FREECOLLAB_API_BASE=https://your-instance/api,
then expose this loopback server through your HTTPS reverse proxy and install the
signed app recipe pointing at /freecollab. Use a private persistent directory for
FREECOLLAB_APP_DB. Never use a user JWT or share this secret with a browser.
"""
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_):
        return None


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def main():
    secret = os.environ.get("FREECOLLAB_APP_SECRET", "")
    api_base = os.environ.get("FREECOLLAB_API_BASE", "").rstrip("/")
    parsed = urlsplit(api_base)
    if len(secret) < 32 or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SystemExit("Set an installation signing secret and an HTTPS FREECOLLAB_API_BASE without credentials or query.")
    database = Path(os.environ.get("FREECOLLAB_APP_DB", "freecollab-app.sqlite3")).absolute()
    database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not database.exists():
        descriptor = os.open(database, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
    os.chmod(database, 0o600)
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE IF NOT EXISTS event(id TEXT PRIMARY KEY, digest TEXT NOT NULL, callback TEXT NOT NULL, body BLOB NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL DEFAULT 0, delivered INTEGER NOT NULL DEFAULT 0)")
    stopped = threading.Event()
    opener = build_opener(NoRedirect)

    class Receiver(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Never log received user arguments or signing headers.

        def do_POST(self):
            if self.path != "/freecollab" or self.headers.get("Transfer-Encoding"):
                self.send_error(404); return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 32768:
                    raise ValueError("body bound")
                body = self.rfile.read(length)
                stamp = self.headers.get("X-FreeCollab-Timestamp", "")
                if not re.fullmatch(r"\d{10,11}", stamp) or abs(int(time.time())-int(stamp)) > 300:
                    raise ValueError("timestamp")
                expected = "v1="+hmac.new(secret.encode(), stamp.encode()+b"."+body, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(expected, self.headers.get("X-FreeCollab-Signature", "")):
                    raise ValueError("signature")
                packet = json.loads(body)
                identifier = packet["id"]
                if (not re.fullmatch(r"[a-f0-9]{32}", identifier) or self.headers.get("X-FreeCollab-Event-ID") != identifier
                        or packet["schema_version"] != 1 or packet["type"] != "app.command"
                        or not re.fullmatch(r"[a-f0-9]{32}", packet["installation_id"])
                        or not isinstance(packet["generation"], int) or packet["generation"] < 1
                        or not isinstance(packet["channel_id"], str) or not 1 <= len(packet["channel_id"]) <= 128
                        or packet["command"] != "invoke" or set(packet["arguments"]) != {"text"}
                        or not isinstance(packet["arguments"]["text"], str) or len(packet["arguments"]["text"]) > 2000):
                    raise ValueError("command schema")
                digest = hashlib.sha256(body).hexdigest()
                # No code/URL from the invoked arguments is executed. A real app
                # performs its own bounded business operation here, using this
                # stable ID as its durable deduplication key.
                callback = canonical({"event_id": "result_"+identifier, "generation": packet["generation"],
                    "channel_id": packet["channel_id"], "card": {"title": "App command received",
                    "text": packet["arguments"]["text"], "fields": [{"label": "Command", "value": "/invoke"}]}})
                with sqlite3.connect(database, timeout=10) as db:
                    previous = db.execute("SELECT digest FROM event WHERE id=?", (identifier,)).fetchone()
                    if previous and not hmac.compare_digest(previous[0], digest):
                        self.send_error(409); return
                    db.execute("INSERT OR IGNORE INTO event(id,digest,callback,body) VALUES(?,?,?,?)",
                               (identifier, digest, "/apps/"+packet["installation_id"]+"/callback", callback))
                self.send_response(202); self.send_header("Content-Length", "0"); self.end_headers()
            except Exception:
                self.send_error(401, "Invalid signed app command")

    def callbacks():
        while not stopped.wait(1):
            with sqlite3.connect(database, timeout=10) as db:
                pending = db.execute("SELECT id,callback,body,attempts FROM event WHERE delivered=0 AND attempts<8 AND due<=? ORDER BY rowid LIMIT 1", (time.time(),)).fetchone()
            if not pending:
                continue
            identifier, path, body, attempts = pending
            stamp = str(int(time.time()))
            signature = "v1="+hmac.new(secret.encode(), stamp.encode()+b"."+body, hashlib.sha256).hexdigest()
            request = Request(api_base+path, data=body, headers={"Content-Type": "application/json", "X-FreeCollab-Timestamp": stamp,
                              "X-FreeCollab-Signature": signature}, method="POST")
            success, permanent = False, False
            try:
                with opener.open(request, timeout=25) as response:
                    success = 200 <= response.status < 300
            except HTTPError as failure:
                permanent = failure.code not in {408, 425, 429} and failure.code < 500
            except Exception:
                pass
            with sqlite3.connect(database, timeout=10) as db:
                # Stable callback event ID makes a timeout retry safe even if
                # FreeCollab accepted the preceding response before disconnect.
                db.execute("UPDATE event SET attempts=?,due=?,delivered=? WHERE id=?",
                    (8 if permanent else attempts+1, time.time()+min(3600, 30*2**attempts), int(success), identifier))

    worker = threading.Thread(target=callbacks, daemon=True)
    worker.start()
    server = ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("FREECOLLAB_APP_PORT", "8090"))), Receiver)
    server.timeout = 30
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stopped.set(); server.shutdown(); server.server_close(); worker.join(timeout=30)


if __name__ == "__main__":
    main()
