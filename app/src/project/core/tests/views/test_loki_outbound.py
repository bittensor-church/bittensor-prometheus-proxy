from http import HTTPStatus
from unittest.mock import MagicMock, patch

import pytest
import snappy

from project.core.loki_utils import stream_labels


def _varint(value: int) -> bytes:
    encoded = bytearray()
    while value > 0x7F:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _field(number: int, value: bytes) -> bytes:
    return _varint((number << 3) | 2) + _varint(len(value)) + value


def _stream(labels: str, line: str, hash_value: int = 0) -> bytes:
    entry = _field(2, line.encode())
    encoded = _field(1, labels.encode()) + _field(2, entry)
    if hash_value:
        encoded += _varint((3 << 3) | 0) + _varint(hash_value)
    return encoded


def _push(*streams: bytes) -> bytes:
    return b"".join(_field(1, stream) for stream in streams)


def _snappy_push(*streams: bytes) -> bytes:
    return snappy.compress(_push(*streams))


def test_not_configured_returns_500(client):
    response = client.post(
        "/loki/outbound", data=b"data", content_type="application/x-protobuf", HTTP_CONTENT_ENCODING="snappy"
    )
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR


@pytest.mark.django_db
@patch("project.core.views.loki.session")
@patch("project.core.views.loki.settings")
def test_bad_payload_returns_400(mock_settings, mock_session, client):
    mock_settings.CENTRAL_LOKI_PROXY_URL = "http://test-central"
    mock_settings.BITTENSOR_NETUID = 12
    cases = [
        ({}, b"data"),
        ({"HTTP_CONTENT_ENCODING": "gzip"}, snappy.compress(b"")),
        ({"HTTP_CONTENT_ENCODING": "snappy"}, b"not-snappy"),
        ({"HTTP_CONTENT_ENCODING": "snappy"}, snappy.compress(b"\xff\xff")),
        ({"HTTP_CONTENT_ENCODING": "snappy"}, _snappy_push(_stream("not-labels", "line"))),
    ]
    for extra, body in cases:
        response = client.post("/loki/outbound", data=body, content_type="application/x-protobuf", **extra)
        assert response.status_code == HTTPStatus.BAD_REQUEST, extra
    mock_session.post.assert_not_called()


@pytest.mark.django_db
@patch("project.core.views.loki.session")
@patch("project.core.views.loki.settings")
def test_stamps_signs_and_copies_response(mock_settings, mock_session, client, keypair):
    mock_settings.CENTRAL_LOKI_PROXY_URL = "http://central.example:8443"
    mock_settings.BITTENSOR_NETUID = 12
    mock_settings.BITTENSOR_WALLET.return_value.hotkey = keypair

    mock_response = MagicMock()
    mock_response.status_code = HTTPStatus.TOO_MANY_REQUESTS
    mock_response.content = b"slow down"
    mock_response.headers = {"X-Retry": "1", "Content-Encoding": "snappy"}
    mock_session.post.return_value = mock_response

    body = _snappy_push(
        _stream('{container="a\\"b", logstream="stdout"}', "first-line", hash_value=7),
        _stream('{hotkey="other", netuid="1"}', "second-line"),
    )
    response = client.post(
        "/loki/outbound",
        data=body,
        content_type="application/x-protobuf",
        HTTP_CONTENT_ENCODING="snappy",
        HTTP_HOST="on-site.local:8000",
    )

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert response.content == b"slow down"
    assert response.headers["X-Retry"] == "1"
    assert "Content-Encoding" not in response.headers

    sent = mock_session.post.call_args.kwargs
    assert mock_session.post.call_args.args[0] == "http://central.example:8443/loki/inbound"
    headers = sent["headers"]
    assert headers["Host"] == "central.example:8443"
    assert headers["Content-Type"] == "application/x-protobuf"
    assert headers["Content-Encoding"] == "snappy"
    assert headers["Bittensor-Hotkey"] == keypair.ss58_address
    assert headers["Bittensor-Netuid"] == "12"
    assert keypair.verify(sent["data"], "0x" + headers["Bittensor-Signature"])

    decoded = snappy.uncompress(sent["data"])
    assert stream_labels(decoded) == [
        f'{{container="a\\"b", hotkey="{keypair.ss58_address}", logstream="stdout", netuid="12"}}',
        f'{{hotkey="{keypair.ss58_address}", netuid="12"}}',
    ]
    assert b"first-line" in decoded
    assert b"second-line" in decoded
    assert b"\x18\x07" in decoded
