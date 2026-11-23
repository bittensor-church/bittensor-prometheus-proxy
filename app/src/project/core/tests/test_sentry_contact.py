import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import requests

from project.core.contact.sentry import SentryContact
from project.core.sentry_envelope import EnvelopeError, parse_envelope, serialize_envelope
from project.core.tests.mock_contact import MockSentryContact


@contextmanager
def _listen(status: int, *, location: str | None = None):
    received: list[tuple[str, str | None, str | None, bytes]] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            received.append((self.path, self.headers.get("X-Sentry-Auth"), self.headers.get("Authorization"), body))
            payload = b'{"id":"abc"}'
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if location:
                self.send_header("Location", location)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            received.append((self.path, None, None, b""))
            payload = b"login"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, received
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _envelope() -> bytes:
    return serialize_envelope(
        {"event_id": "abc", "dsn": "http://unused@on-site/1"},
        [
            ({"type": "event"}, b'{"message":"boom"}'),
            ({"type": "attachment", "filename": "a.txt"}, b"line\nline"),
        ],
    )


def test_ingest_rewrites_dsn_and_posts_once():
    with _listen(200) as (server, received):
        port = server.server_address[1]
        dsn = f"http://public-key:secret@127.0.0.1:{port}/prefix/42"
        response = SentryContact().ingest_envelope(dsn, _envelope())

    assert response.status_code == 200
    assert response.content == b'{"id":"abc"}'
    assert len(received) == 1
    path, auth, authorization, body = received[0]
    assert path == "/prefix/api/42/envelope/"
    assert authorization is None
    assert auth == "Sentry sentry_version=7, sentry_client=bittensor-prometheus-proxy/0, sentry_key=public-key"
    header, items = parse_envelope(body)
    assert header["dsn"] == dsn
    assert header["event_id"] == "abc"
    assert items[0][1] == b'{"message":"boom"}'
    assert items[1][1] == b"line\nline"


def test_redirect_is_not_followed():
    with _listen(302, location="/login") as (server, received):
        port = server.server_address[1]
        response = SentryContact().ingest_envelope(f"http://public@127.0.0.1:{port}/42", _envelope())

    assert response.status_code == 302
    assert len(received) == 1
    assert received[0][0] == "/api/42/envelope/"


def test_upstream_error_is_returned_without_retry():
    with _listen(500) as (server, received):
        port = server.server_address[1]
        response = SentryContact().ingest_envelope(f"http://public@127.0.0.1:{port}/7", _envelope())

    assert response.status_code == 500
    assert len(received) == 1


def test_malformed_envelope_is_not_sent():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with pytest.raises(EnvelopeError):
        SentryContact().ingest_envelope(f"http://public@127.0.0.1:{port}/1", b"nope")


def test_connection_error_propagates():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with pytest.raises(requests.ConnectionError):
        SentryContact().ingest_envelope(f"http://public@127.0.0.1:{port}/1", _envelope())


@pytest.mark.parametrize(
    "dsn",
    [
        "",
        "http://sentry.reef.pl/1",
        "https://key@sentry.reef.pl",
        "https://key@sentry.reef.pl/",
        "ftp://key@host/1",
        "http://bad key@host/1",
    ],
)
def test_invalid_dsn_raises(dsn):
    with pytest.raises(ValueError, match="Invalid Sentry DSN"):
        SentryContact().ingest_envelope(dsn, _envelope())


def test_mock_records_call_and_can_fail():
    mock = MockSentryContact()
    ok = requests.Response()
    ok.status_code = 200
    mock.add_behavior("ingest_envelope", ok)
    mock.add_behavior("ingest_envelope", requests.ConnectionError("down"))

    assert mock.ingest_envelope("http://public@sentry/1", b"body") is ok
    with pytest.raises(requests.ConnectionError):
        mock.ingest_envelope("http://public@sentry/1", b"again")
    assert mock.calls["ingest_envelope"] == [("http://public@sentry/1", b"body"), ("http://public@sentry/1", b"again")]


def test_plain_dsn_uses_project_path():
    with _listen(200) as (server, received):
        port = server.server_address[1]
        SentryContact().ingest_envelope(f"http://only-key@127.0.0.1:{port}/99", _envelope())
    assert received[0][0] == "/api/99/envelope/"
