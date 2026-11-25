from http import HTTPStatus
from unittest.mock import patch

import pytest
import requests
import snappy
from django.test import override_settings

from project.core.loki_utils import stamp_push
from project.core.models import Validator


def _varint(value: int) -> bytes:
    encoded = bytearray()
    while value > 0x7F:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _field(number: int, value: bytes) -> bytes:
    return _varint((number << 3) | 2) + _varint(len(value)) + value


def _stream(labels: str, line: str) -> bytes:
    return _field(1, labels.encode()) + _field(2, _field(2, line.encode()))


def _push(*streams: bytes) -> bytes:
    return b"".join(_field(1, stream) for stream in streams)


def _upstream(status: int, content: bytes) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = content
    response.headers["X-Scope-OrgID"] = "reef"
    return response


@pytest.fixture
def upstream():
    response = _upstream(HTTPStatus.NO_CONTENT, b"")
    contact = type("Contact", (), {"calls": [], "push_logs": None})()

    def push_logs(data, content_type, content_encoding=""):
        contact.calls.append((data, content_type, content_encoding))
        return response

    contact.push_logs = push_logs
    with patch("project.core.views.loki.loki_contact", return_value=contact):
        yield contact


def _post(client, keypair, data, **extra):
    headers = {
        "HTTP_BITTENSOR_HOTKEY": keypair.ss58_address,
        "HTTP_BITTENSOR_NETUID": "12",
        "HTTP_BITTENSOR_SIGNATURE": keypair.sign(data).hex(),
        "HTTP_CONTENT_ENCODING": "snappy",
    }
    headers.update(extra)
    headers = {key: value for key, value in headers.items() if value is not None}
    return client.post("/loki/inbound", data=data, content_type="application/x-protobuf", **headers)


def test_not_configured_returns_500(client, upstream):
    response = client.post("/loki/inbound", data=b"data", content_type="application/x-protobuf")
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert upstream.calls == []


@pytest.mark.django_db
@override_settings(UPSTREAM_LOKI_URL="https://loki.reef.pl", BITTENSOR_NETUIDS=[12])
def test_missing_auth_headers_returns_400(client, keypair, upstream):
    Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True)
    response = client.post("/loki/inbound", data=b"data", content_type="application/x-protobuf")
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert b"Missing required headers" in response.content
    assert upstream.calls == []


@pytest.mark.django_db
@override_settings(UPSTREAM_LOKI_URL="https://loki.reef.pl", BITTENSOR_NETUIDS=[12])
def test_bad_payload_returns_400(client, keypair, upstream):
    Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True)
    cases = [
        (b"data", {"HTTP_CONTENT_ENCODING": None}),
        (snappy.compress(b""), {"HTTP_CONTENT_ENCODING": "gzip"}),
        (b"not-snappy", {}),
        (snappy.compress(b"\xff\xff"), {}),
        (snappy.compress(_push(_stream("not-labels", "line"))), {}),
    ]
    for data, extra in cases:
        response = _post(client, keypair, data, **extra)
        assert response.status_code == HTTPStatus.BAD_REQUEST, extra
    assert upstream.calls == []


@pytest.mark.django_db
@override_settings(UPSTREAM_LOKI_URL="https://loki.reef.pl", BITTENSOR_NETUIDS=[12])
def test_stream_identity_must_match(client, keypair, upstream):
    Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True)
    good = f'{{hotkey="{keypair.ss58_address}", netuid="12"}}'
    payloads = [
        _push(_stream('{netuid="12"}', "line")),
        _push(_stream('{hotkey="other", netuid="12"}', "line")),
        _push(_stream(f'{{hotkey="{keypair.ss58_address}", netuid="99"}}', "line")),
        _push(_stream(good, "kept"), _stream('{hotkey="other", netuid="12"}', "bad")),
    ]
    for payload in payloads:
        data = snappy.compress(payload)
        response = _post(client, keypair, data)
        assert response.status_code == HTTPStatus.FORBIDDEN
        assert b"does not match" in response.content
    assert upstream.calls == []


@pytest.mark.django_db
@override_settings(UPSTREAM_LOKI_URL="https://loki.reef.pl", BITTENSOR_NETUIDS=[12])
def test_forwards_matching_push_and_copies_response(client, keypair, upstream):
    Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True)
    data = snappy.compress(
        stamp_push(
            _push(_stream('{container="api"}', "one"), _stream('{container="db", hotkey="nope"}', "two")),
            keypair.ss58_address,
            "12",
        )
    )
    upstream_response = _upstream(HTTPStatus.TOO_MANY_REQUESTS, b"slow down")

    def push_logs(body, content_type, content_encoding=""):
        upstream.calls.append((body, content_type, content_encoding))
        return upstream_response

    upstream.push_logs = push_logs

    response = _post(client, keypair, data)

    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert response.content == b"slow down"
    assert response.headers["X-Scope-OrgID"] == "reef"
    assert upstream.calls == [(data, "application/x-protobuf", "snappy")]


@pytest.mark.django_db
@override_settings(UPSTREAM_LOKI_URL="https://loki.reef.pl", BITTENSOR_NETUIDS=[12])
def test_contact_error_returns_500(client, keypair, upstream):
    Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True)
    data = snappy.compress(stamp_push(_push(_stream("{}", "line")), keypair.ss58_address, "12"))

    def push_logs(data, content_type, content_encoding=""):
        raise requests.ConnectionError("down")

    upstream.push_logs = push_logs
    response = _post(client, keypair, data)
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert response.content == b"ConnectionError"
