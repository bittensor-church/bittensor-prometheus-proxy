import gzip
import json
import tracemalloc
from http import HTTPStatus
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.test import override_settings

from project.core.proxy_outbound import TIMEOUT, decompress_body
from project.core.sentry_envelope import parse_envelope, serialize_envelope


def _envelope(tags: dict | None = None) -> bytes:
    event: dict = {"message": "boom", "level": "error"}
    if tags is not None:
        event["tags"] = tags
    return serialize_envelope({"event_id": "abc"}, [({"type": "event"}, json.dumps(event).encode())])


def _upstream(**kwargs):
    response = MagicMock()
    response.status_code = kwargs.get("status", HTTPStatus.ACCEPTED)
    response.content = kwargs.get("content", b'{"id":"abc"}')
    response.headers = kwargs.get("headers", {})
    return response


def test_not_configured_returns_500(client):
    response = client.post("/api/1/envelope/", data=b"data", content_type="application/x-sentry-envelope")
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR


@patch("project.core.views.sentry.requests.post")
@patch("project.core.views.sentry.settings")
def test_malformed_body_returns_400(mock_settings, mock_post, client):
    mock_settings.CENTRAL_SENTRY_PROXY_URL = "http://test-central"
    response = client.post("/api/1/envelope/", data=b"not-an-envelope", content_type="application/x-sentry-envelope")
    assert response.status_code == HTTPStatus.BAD_REQUEST
    mock_post.assert_not_called()


@patch("project.core.views.sentry.requests.post")
@patch("project.core.views.sentry.settings")
def test_stamps_signs_and_copies_response(mock_settings, mock_post, client, keypair):
    mock_settings.CENTRAL_SENTRY_PROXY_URL = "http://test-central"
    mock_settings.BITTENSOR_NETUID = 12
    mock_settings.BITTENSOR_WALLET.return_value.hotkey = keypair
    mock_post.return_value = _upstream(headers={"X-Sentry-Rate-Limits": "1"})

    response = client.post(
        "/api/1/envelope/",
        data=_envelope(tags={"hotkey": "5Fake", "netuid": "1", "server": "a"}),
        content_type="application/x-sentry-envelope",
    )

    assert response.status_code == HTTPStatus.ACCEPTED
    assert response.content == b'{"id":"abc"}'
    assert response.headers["X-Sentry-Rate-Limits"] == "1"

    forwarded = mock_post.call_args.kwargs["data"]
    _, items = parse_envelope(forwarded)
    assert json.loads(items[0][1])["tags"] == {"hotkey": keypair.ss58_address, "netuid": "12", "server": "a"}
    sent_headers = mock_post.call_args.kwargs["headers"]
    assert mock_post.call_args.args[0] == "http://test-central/sentry/inbound"
    assert mock_post.call_args.kwargs["timeout"] == TIMEOUT
    assert sent_headers["Bittensor-Hotkey"] == keypair.ss58_address
    assert sent_headers["Bittensor-Netuid"] == "12"
    assert keypair.verify(forwarded, "0x" + sent_headers["Bittensor-Signature"])
    assert mock_post.call_count == 1


@patch("project.core.views.sentry.requests.post")
@patch("project.core.views.sentry.settings")
def test_gzipped_body_is_forwarded_uncompressed(mock_settings, mock_post, client, keypair):
    mock_settings.CENTRAL_SENTRY_PROXY_URL = "http://test-central/"
    mock_settings.BITTENSOR_NETUID = 12
    mock_settings.BITTENSOR_WALLET.return_value.hotkey = keypair
    mock_post.return_value = _upstream()

    response = client.post(
        "/api/42/envelope/",
        data=gzip.compress(_envelope()),
        content_type="application/x-sentry-envelope",
        HTTP_CONTENT_ENCODING="gzip",
    )

    assert response.status_code == HTTPStatus.ACCEPTED
    forwarded = mock_post.call_args.kwargs["data"]
    _, items = parse_envelope(forwarded)
    assert json.loads(items[0][1])["tags"]["netuid"] == "12"
    sent_headers = {k.lower(): v for k, v in mock_post.call_args.kwargs["headers"].items()}
    assert "content-encoding" not in sent_headers
    assert mock_post.call_args.args[0] == "http://test-central/sentry/inbound"


@patch("project.core.views.sentry.requests.post")
@patch("project.core.views.sentry.settings")
def test_upstream_error_returns_500(mock_settings, mock_post, client, keypair):
    mock_settings.CENTRAL_SENTRY_PROXY_URL = "http://test-central"
    mock_settings.BITTENSOR_NETUID = 12
    mock_settings.BITTENSOR_WALLET.return_value.hotkey = keypair
    mock_post.side_effect = requests.exceptions.ConnectionError("down")

    response = client.post("/api/1/envelope/", data=_envelope(), content_type="application/x-sentry-envelope")

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert response.content == b"ConnectionError"
    assert mock_post.call_count == 1


def test_gzip_output_is_capped_at_the_upload_limit():
    raw = b"x" * 32
    compressed = gzip.compress(raw)
    with override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=len(raw)):
        assert decompress_body(compressed, {"Content-Encoding": "gzip"}) == raw
    with override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=len(raw) - 1):
        with pytest.raises(ValueError, match="exceeds"):
            decompress_body(compressed, {"Content-Encoding": "gzip"})


def test_gzip_decompress_does_not_expand_the_whole_body():
    raw = b"\0" * (4 * 1024 * 1024)
    compressed = gzip.compress(raw)
    tracemalloc.start()
    with override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=32 * 1024):
        with pytest.raises(ValueError, match="exceeds"):
            decompress_body(compressed, {"Content-Encoding": "gzip"})
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert peak < len(raw)


@patch("project.core.views.sentry.requests.post")
@patch("project.core.views.sentry.settings")
def test_oversized_gzip_is_rejected_before_forwarding(mock_settings, mock_post, client):
    mock_settings.CENTRAL_SENTRY_PROXY_URL = "http://test-central"
    raw = b"\0" * 4096
    compressed = gzip.compress(raw)
    with override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=len(compressed)):
        response = client.post(
            "/api/1/envelope/",
            data=compressed,
            content_type="application/x-sentry-envelope",
            HTTP_CONTENT_ENCODING="gzip",
        )

    assert len(raw) > len(compressed)
    assert response.status_code == HTTPStatus.BAD_REQUEST
    mock_post.assert_not_called()
