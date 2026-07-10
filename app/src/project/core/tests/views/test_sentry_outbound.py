import gzip
from http import HTTPStatus
from unittest.mock import MagicMock, patch

import bittensor
import pytest
from django.test import override_settings
from sentry_sdk.envelope import Envelope

from project.core.views.sentry import ENVELOPE_CONTENT_TYPE, inject_tags

ENVELOPE_CONTENT_TYPE_WITH_CHARSET = f"{ENVELOPE_CONTENT_TYPE}; charset=utf-8"


def make_envelope(event: dict = {"event_id": "abc"}) -> bytes:
    envelope = Envelope()
    envelope.add_event(event)
    return envelope.serialize()


@pytest.fixture
def mock_session():
    with patch("project.core.views.sentry.session") as mock_session:
        yield mock_session


@pytest.fixture
def mock_response(mock_session):
    response = MagicMock()
    response.status_code = HTTPStatus.OK
    response.content = b""
    response.headers = {}
    mock_session.post.return_value = response
    return response


@override_settings(CENTRAL_SENTRY_PROXY_URL="")
def test_not_configured_returns_500(client):
    response = client.post(
        "/sentry/outbound",
        data=make_envelope(),
        content_type=ENVELOPE_CONTENT_TYPE,
    )
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR


def test_rejects_non_envelope_content_type(client, settings):
    settings.CENTRAL_SENTRY_PROXY_URL = "http://test-central"
    response = client.post("/sentry/outbound", data=b"{}", content_type="application/json")
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.content == f"Content-Type must be {ENVELOPE_CONTENT_TYPE}".encode()


def test_accepts_envelope_content_type_with_parameters(client, settings):
    settings.CENTRAL_SENTRY_PROXY_URL = "http://test-central"
    settings.BITTENSOR_WALLET = MagicMock(return_value=MagicMock(hotkey=MagicMock(ss58_address="5Hotkey")))
    with patch("project.core.views.sentry.session") as mock_session:
        mock_response = MagicMock()
        mock_response.status_code = HTTPStatus.OK
        mock_response.content = b""
        mock_response.headers = {}
        mock_session.post.return_value = mock_response

        response = client.post(
            "/sentry/outbound",
            data=make_envelope(),
            content_type=ENVELOPE_CONTENT_TYPE_WITH_CHARSET,
        )

    assert response.status_code == HTTPStatus.OK


def test_rejects_malformed_gzip(client, settings):
    settings.CENTRAL_SENTRY_PROXY_URL = "http://test-central"
    response = client.post(
        "/sentry/outbound",
        data=b"not-gzip",
        content_type=ENVELOPE_CONTENT_TYPE,
        headers={"Content-Encoding": "gzip"},
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.content == b"Error decompressing body"


@pytest.mark.django_db
def test_accepts_gzipped_envelope(mock_session, settings, client, keypair):
    settings.CENTRAL_SENTRY_PROXY_URL = "http://test-central"
    settings.BITTENSOR_NETUID = 12
    settings.BITTENSOR_WALLET = MagicMock(return_value=MagicMock(hotkey=keypair))

    mock_response = MagicMock()
    mock_response.status_code = HTTPStatus.OK
    mock_response.content = b""
    mock_response.headers = {}
    mock_session.post.return_value = mock_response

    response = client.post(
        "/sentry/outbound",
        data=gzip.compress(make_envelope()),
        content_type=ENVELOPE_CONTENT_TYPE,
        headers={"Content-Encoding": "gzip"},
    )

    assert response.status_code == HTTPStatus.OK
    envelope = Envelope.deserialize(mock_session.post.call_args.kwargs["data"])
    event = next(item.payload.json for item in envelope.items if item.type == "event")
    assert event["tags"] == {"hotkey": keypair.ss58_address, "netuid": "12"}


def test_rejects_malformed_envelope(client, settings):
    settings.CENTRAL_SENTRY_PROXY_URL = "http://test-central"
    settings.BITTENSOR_WALLET = MagicMock(return_value=MagicMock(hotkey=MagicMock(ss58_address="5Hotkey")))
    response = client.post("/sentry/outbound", data=b"not-an-envelope", content_type=ENVELOPE_CONTENT_TYPE)
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.content == b"Invalid sentry envelope"


@pytest.mark.django_db
def test_data_injection(mock_session, mock_response, settings, client, keypair):
    settings.CENTRAL_SENTRY_PROXY_URL = "http://test-central"
    settings.BITTENSOR_NETUID = 12
    settings.BITTENSOR_WALLET = MagicMock(return_value=MagicMock(hotkey=keypair))

    client.post(
        "/sentry/outbound",
        data=make_envelope({"event_id": "abc", "tags": {"one": "two"}}),
        content_type=ENVELOPE_CONTENT_TYPE,
    )

    sent_headers = mock_session.post.call_args.kwargs["headers"]
    assert sent_headers["Bittensor-Hotkey"] == keypair.ss58_address
    assert sent_headers["Bittensor-Netuid"] == "12"
    assert "Bittensor-Signature" in sent_headers

    envelope = Envelope.deserialize(mock_session.post.call_args.kwargs["data"])
    event = next(item.payload.json for item in envelope.items if item.type == "event")
    assert event["event_id"] == "abc"
    assert event["tags"] == {"hotkey": keypair.ss58_address, "netuid": "12", "one": "two"}


@pytest.mark.django_db
def test_overwrites_existing_hotkey(mock_session, mock_response, settings, client, keypair):
    settings.CENTRAL_SENTRY_PROXY_URL = "http://test-central"
    settings.BITTENSOR_NETUID = 12
    settings.BITTENSOR_WALLET = MagicMock(return_value=MagicMock(hotkey=keypair))

    client.post(
        "/sentry/outbound",
        data=make_envelope({"event_id": "abc", "tags": {"hotkey": "5FakeHotkey", "netuid": "99"}}),
        content_type=ENVELOPE_CONTENT_TYPE,
    )

    envelope = Envelope.deserialize(mock_session.post.call_args.kwargs["data"])
    event = next(item.payload.json for item in envelope.items if item.type == "event")
    assert event["tags"]["hotkey"] == keypair.ss58_address
    assert event["tags"]["netuid"] == "12"


@pytest.mark.django_db
def test_signature_covers_modified_envelope(mock_session, mock_response, settings, client, keypair):
    settings.CENTRAL_SENTRY_PROXY_URL = "http://test-central"
    settings.BITTENSOR_NETUID = 12

    class MockWallet:
        hotkey = keypair

    settings.BITTENSOR_WALLET = MockWallet

    client.post(
        "/sentry/outbound",
        data=make_envelope({"event_id": "abc", "tags": {"one": "two"}}),
        content_type=ENVELOPE_CONTENT_TYPE,
    )

    forwarded_data = mock_session.post.call_args.kwargs["data"]
    sent_headers = mock_session.post.call_args.kwargs["headers"]
    assert bittensor.Keypair(keypair.ss58_address).verify(forwarded_data, "0x" + sent_headers["Bittensor-Signature"])


@pytest.mark.django_db
def test_forwards_to_central_url(mock_session, mock_response, settings, client, keypair):
    settings.CENTRAL_SENTRY_PROXY_URL = "http://test-central"
    settings.BITTENSOR_NETUID = 12
    settings.BITTENSOR_WALLET = MagicMock(return_value=MagicMock(hotkey=keypair))

    client.post("/sentry/outbound", data=make_envelope(), content_type=ENVELOPE_CONTENT_TYPE)

    assert mock_session.post.call_args.args[0] == "http://test-central/sentry/inbound"


@pytest.mark.django_db
def test_accepts_sdk_envelope_path(mock_session, mock_response, settings, client, keypair):
    settings.CENTRAL_SENTRY_PROXY_URL = "http://test-central"
    settings.BITTENSOR_NETUID = 12
    settings.BITTENSOR_WALLET = MagicMock(return_value=MagicMock(hotkey=keypair))

    response = client.post(
        "/sentry/outbound/api/123/envelope/",
        data=make_envelope(),
        content_type=ENVELOPE_CONTENT_TYPE,
    )

    assert response.status_code == HTTPStatus.OK
    assert mock_session.post.call_args.args[0] == "http://test-central/sentry/inbound"


def test_patch_tags_updates_event_items() -> None:
    data = make_envelope({"event_id": "abc", "tags": {"foo": "bar"}})
    patched = inject_tags(data, {"hotkey": "5abc", "netuid": "12"})
    envelope = Envelope.deserialize(patched)
    event = next(item.payload.json for item in envelope.items if item.type == "event")
    assert event["tags"] == {"foo": "bar", "hotkey": "5abc", "netuid": "12"}


def test_patch_tags_updates_transaction_items() -> None:
    envelope = Envelope()
    envelope.add_transaction({"event_id": "abc", "type": "transaction", "tags": {"foo": "bar"}})
    patched = inject_tags(envelope.serialize(), {"hotkey": "5abc", "netuid": "12"})
    result = Envelope.deserialize(patched)
    transaction = next(item.payload.json for item in result.items if item.type == "transaction")
    assert transaction["tags"] == {"foo": "bar", "hotkey": "5abc", "netuid": "12"}


def test_patch_tags_updates_array_form_tags() -> None:
    data = make_envelope({"event_id": "abc", "tags": [["foo", "bar"]]})
    patched = inject_tags(data, {"hotkey": "5abc", "netuid": "12"})
    event = next(item.payload.json for item in Envelope.deserialize(patched).items if item.type == "event")
    assert event["tags"] == {"foo": "bar", "hotkey": "5abc", "netuid": "12"}
