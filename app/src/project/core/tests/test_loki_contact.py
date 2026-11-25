import base64
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from django.test import override_settings

from project.core.contact.loki import LokiContact


def _serve(received: dict):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            received["path"] = self.path
            received["body"] = self.rfile.read(length)
            received["content_type"] = self.headers.get("Content-Type")
            received["content_encoding"] = self.headers.get("Content-Encoding")
            received["authorization"] = self.headers.get("Authorization")
            self.send_response(204)
            self.end_headers()

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    return server, thread


def test_push_logs_sets_basic_auth_and_encoding():
    received = {}
    server, thread = _serve(received)
    body = b'{"streams":[]}'
    try:
        url = f"http://loki:p%40ss@127.0.0.1:{server.server_address[1]}"
        with override_settings(UPSTREAM_LOKI_URL=url):
            response = LokiContact().push_logs(body, content_type="application/json", content_encoding="snappy")
    finally:
        server.shutdown()
        thread.join()

    assert response.status_code == 204
    assert received == {
        "path": "/loki/api/v1/push",
        "body": body,
        "content_type": "application/json",
        "content_encoding": "snappy",
        "authorization": "Basic " + base64.b64encode(b"loki:p@ss").decode(),
    }


def test_push_logs_omits_auth_and_encoding_when_unset():
    received = {}
    server, thread = _serve(received)
    body = b'{"streams":[]}'
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        with override_settings(UPSTREAM_LOKI_URL=url):
            response = LokiContact().push_logs(body, content_type="application/x-protobuf")
    finally:
        server.shutdown()
        thread.join()

    assert response.status_code == 204
    assert received["path"] == "/loki/api/v1/push"
    assert received["body"] == body
    assert received["content_type"] == "application/x-protobuf"
    assert received["content_encoding"] is None
    assert received["authorization"] is None
