import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from copytrade_worker import db as worker_db


@pytest.fixture
def db():
    conn = worker_db.connect(":memory:")
    yield conn
    conn.close()


def add_user(conn, owner, notify=None, bot_key="ed25519:test", vault="", created_at=0):
    vault = f"v.{owner}" if vault == "" else vault
    settings = {} if notify is None else {"notify": notify}
    conn.execute(
        "INSERT INTO users(owner_id, vault_id, bot_public_key, settings, created_at) VALUES (?,?,?,?,?)",
        (owner, vault, bot_key, json.dumps(settings), created_at),
    )
    conn.commit()


def add_activity(conn, owner, kind, payload=None, created_at=1):
    cur = conn.execute(
        "INSERT INTO activity(owner_id, kind, payload, created_at) VALUES (?,?,?,?)",
        (owner, kind, json.dumps(payload or {}), created_at),
    )
    conn.commit()
    return cur.lastrowid


class FakeServer:
    """Local HTTP server. `responses` is a list of (status, body_dict, headers) popped per request;
    when empty, replies 200 {"ok": true, "result": ...}."""

    def __init__(self):
        self.requests = []
        self.responses = []
        self.default = (200, {"ok": True, "result": {}}, {})
        server = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n)
                server.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                status, payload, headers = server.responses.pop(0) if server.responses else server.default
                data = json.dumps(payload).encode()
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.close()


@pytest.fixture
def no_sleep():
    calls = []
    return calls, (lambda s: calls.append(s))
