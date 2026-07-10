import gzip
from http import HTTPStatus
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.test import override_settings
from sentry_sdk.envelope import Envelope

from project.core.contact.sentry import UpstreamDsnNotConfiguredError
from project.core.models import Validator
from project.core.views.sentry import ENVELOPE_CONTENT_TYPE, validate_hotkey_tags


def make_envelope(event: dict = {"event_id": "abc"}, type_: str = "event") -> bytes:
    envelope = Envelope()
    match type_:
        case "event":
            envelope.add_event(event)
        case "transaction":
            envelope.add_transaction(event | {"type": "transaction"})
        case _:
            raise ValueError(f"Invalid envelope type: {type_}")
    return envelope.serialize()


def signed_headers(data: bytes, keypair, netuid: int = 12) -> dict[str, str]:
    return {
        "HTTP_BITTENSOR_HOTKEY": keypair.ss58_address,
        "HTTP_BITTENSOR_NETUID": str(netuid),
        "HTTP_BITTENSOR_SIGNATURE": keypair.sign(data).hex(),
    }


@pytest.fixture
def mock_sentry_contact():
    with patch("project.core.views.sentry.sentry_contact") as mocked_sentry_contact:
        contact = MagicMock()
        response = MagicMock()
        response.status_code = HTTPStatus.OK
        response.content = b""
        response.headers = {}
        contact.forward.return_value = response
        mocked_sentry_contact.return_value = contact
        yield contact


@pytest.fixture
def hotkey(keypair) -> str:
    return keypair.ss58_address


@pytest.fixture
def event() -> dict:
    return {"event_id": "abc"}


class TestValidateHotkeyTags:
    def test_accepts_matching_event(self, hotkey: str, event: dict) -> None:
        data = make_envelope(event | {"tags": {"hotkey": hotkey}})
        validate_hotkey_tags(data, hotkey)

    def test_accepts_matching_event_with_array_tags(self, hotkey: str, event: dict) -> None:
        data = make_envelope(event | {"tags": [["hotkey", hotkey]]})
        validate_hotkey_tags(data, hotkey)

    def test_rejects_mismatched_hotkey(self, hotkey: str, event: dict) -> None:
        data = make_envelope(event | {"tags": {"hotkey": "5other"}})
        with pytest.raises(ValueError, match="hotkey"):
            validate_hotkey_tags(data, hotkey)

    def test_rejects_envelope_without_tagged_items(self, hotkey: str) -> None:
        envelope = Envelope()
        data = envelope.serialize()
        with pytest.raises(ValueError, match="No tagged Sentry items"):
            validate_hotkey_tags(data, hotkey)

    def test_rejects_any_mismatched_item(self, event: dict, hotkey: str) -> None:
        envelope = Envelope()
        envelope.add_event(event | {"tags": {"hotkey": hotkey}})
        envelope.add_transaction({"event_id": "def", "type": "transaction", "tags": {"hotkey": "5other"}})
        with pytest.raises(ValueError, match="hotkey"):
            validate_hotkey_tags(envelope.serialize(), hotkey)


class TestContentFormat:
    def test_rejects_non_envelope_content_type(self, client) -> None:
        response = client.post("/sentry/inbound", data=b"{}", content_type="application/json")
        assert response.status_code == HTTPStatus.BAD_REQUEST
        assert response.content == f"Content-Type must be {ENVELOPE_CONTENT_TYPE}".encode()

    @pytest.mark.django_db
    @override_settings(BITTENSOR_NETUIDS=[12])
    def test_accepts_envelope_content_type_with_parameters(
        self, client, keypair, mock_sentry_contact, hotkey: str, event: dict
    ) -> None:
        Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True)
        data = make_envelope(event | {"tags": {"hotkey": hotkey}})
        response = client.post(
            "/sentry/inbound",
            data=data,
            content_type=f"{ENVELOPE_CONTENT_TYPE}; charset=utf-8",
            **signed_headers(data, keypair),
        )
        assert response.status_code == HTTPStatus.OK
        mock_sentry_contact.forward.assert_called_once()

    def test_rejects_malformed_gzip(self, client) -> None:
        response = client.post(
            "/sentry/inbound",
            data=b"not-gzip",
            content_type=ENVELOPE_CONTENT_TYPE,
            headers={"Content-Encoding": "gzip"},
        )
        assert response.status_code == HTTPStatus.BAD_REQUEST
        assert response.content == b"Error decompressing body"

    @pytest.mark.django_db
    @override_settings(BITTENSOR_NETUIDS=[12])
    def test_rejects_malformed_gzip_with_auth_headers(self, client, keypair, hotkey: str, event: dict) -> None:
        Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True)
        data = b"not-gzip"
        response = client.post(
            "/sentry/inbound",
            data=data,
            content_type=ENVELOPE_CONTENT_TYPE,
            headers={"Content-Encoding": "gzip"},
            **signed_headers(data, keypair),
        )
        assert response.status_code == HTTPStatus.BAD_REQUEST
        assert response.content == b"Error decompressing body"

    @pytest.mark.django_db
    @override_settings(BITTENSOR_NETUIDS=[12])
    def test_rejects_oversized_gzip_body(self, client, keypair, hotkey: str, event: dict) -> None:
        Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True)
        uncompressed = b"\x00" * (4 * 2_500_000)
        data = gzip.compress(uncompressed)
        response = client.post(
            "/sentry/inbound",
            data=data,
            content_type=ENVELOPE_CONTENT_TYPE,
            headers={"Content-Encoding": "gzip"},
            **signed_headers(uncompressed, keypair),
        )
        assert response.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
        assert response.content == b"Body too large"

    @pytest.mark.django_db
    @override_settings(BITTENSOR_NETUIDS=[12], DATA_UPLOAD_MAX_MEMORY_SIZE=1024)
    def test_rejects_oversized_uncompressed_body(self, client, keypair, hotkey: str, event: dict) -> None:
        Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True)
        data = b"x" * 2048
        response = client.post(
            "/sentry/inbound",
            data=data,
            content_type=ENVELOPE_CONTENT_TYPE,
            **signed_headers(data, keypair),
        )
        assert response.status_code == HTTPStatus.BAD_REQUEST

    @pytest.mark.django_db
    @override_settings(BITTENSOR_NETUIDS=[12])
    def test_rejects_malformed_envelope(self, client, keypair) -> None:
        Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True)
        data = b"not-an-envelope"
        response = client.post(
            "/sentry/inbound",
            data=data,
            content_type=ENVELOPE_CONTENT_TYPE,
            **signed_headers(data, keypair),
        )
        assert response.status_code == HTTPStatus.BAD_REQUEST
        assert response.content == b"Invalid sentry envelope"

    @pytest.mark.django_db
    @override_settings(BITTENSOR_NETUIDS=[12])
    def test_accepts_gzipped_envelope(self, client, keypair, mock_sentry_contact, hotkey: str, event: dict) -> None:
        Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True)
        data = make_envelope(event | {"tags": {"hotkey": hotkey}})
        response = client.post(
            "/sentry/inbound",
            data=gzip.compress(data),
            content_type=ENVELOPE_CONTENT_TYPE,
            headers={"Content-Encoding": "gzip"},
            **signed_headers(data, keypair),
        )
        assert response.status_code == HTTPStatus.OK
        mock_sentry_contact.forward.assert_called_once()


class TestAuthentication:
    @pytest.mark.django_db
    @override_settings(BITTENSOR_NETUIDS=[12])
    def test_missing_auth_headers_returns_400(self, client, keypair, hotkey: str, event: dict) -> None:
        Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True)
        data = make_envelope(event | {"tags": {"hotkey": hotkey}})
        response = client.post("/sentry/inbound", data=data, content_type=ENVELOPE_CONTENT_TYPE)
        assert response.status_code == HTTPStatus.BAD_REQUEST
        assert b"Missing required headers" in response.content

    @pytest.mark.django_db
    @override_settings(BITTENSOR_NETUIDS=[12])
    def test_netuid_not_in_allowed_list_returns_403(self, client, keypair, hotkey: str, event: dict) -> None:
        Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True)
        data = make_envelope(event | {"tags": {"hotkey": hotkey}})
        response = client.post(
            "/sentry/inbound",
            data=data,
            content_type=ENVELOPE_CONTENT_TYPE,
            **signed_headers(data, keypair, netuid=99),
        )
        assert response.status_code == HTTPStatus.FORBIDDEN
        assert b"Netuid not supported" in response.content

    @pytest.mark.django_db
    @override_settings(BITTENSOR_NETUIDS=[12])
    def test_hotkey_not_active_for_netuid_returns_403(self, client, keypair, hotkey: str, event: dict) -> None:
        Validator.objects.create(public_key=keypair.ss58_address, netuid=22, active=True)
        data = make_envelope(event | {"tags": {"hotkey": hotkey}})
        response = client.post(
            "/sentry/inbound",
            data=data,
            content_type=ENVELOPE_CONTENT_TYPE,
            **signed_headers(data, keypair),
        )
        assert response.status_code == HTTPStatus.FORBIDDEN
        assert b"Validator not active" in response.content

    @pytest.mark.django_db
    @override_settings(BITTENSOR_NETUIDS=[12])
    def test_bad_signature_returns_400(self, client, keypair, hotkey: str, event: dict) -> None:
        Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True)
        data = make_envelope(event | {"tags": {"hotkey": hotkey}})
        response = client.post(
            "/sentry/inbound",
            data=data,
            content_type=ENVELOPE_CONTENT_TYPE,
            HTTP_BITTENSOR_HOTKEY=keypair.ss58_address,
            HTTP_BITTENSOR_NETUID="12",
            HTTP_BITTENSOR_SIGNATURE=keypair.sign(b"other-data").hex(),
        )
        assert response.status_code == HTTPStatus.BAD_REQUEST
        assert b"Bad signature" in response.content

    @pytest.mark.django_db
    @override_settings(BITTENSOR_NETUIDS=[12])
    def test_missing_hotkey_tag_returns_403(self, client, keypair, hotkey: str, event: dict) -> None:
        Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True)
        data = make_envelope(event)
        response = client.post(
            "/sentry/inbound",
            data=data,
            content_type=ENVELOPE_CONTENT_TYPE,
            **signed_headers(data, keypair),
        )
        assert response.status_code == HTTPStatus.FORBIDDEN
        assert b"hotkey" in response.content

    @pytest.mark.django_db
    @override_settings(BITTENSOR_NETUIDS=[12])
    def test_mismatched_hotkey_tag_returns_403(self, client, keypair, hotkey: str, event: dict) -> None:
        Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True)
        data = make_envelope(event | {"tags": {"hotkey": "5other"}})
        response = client.post(
            "/sentry/inbound",
            data=data,
            content_type=ENVELOPE_CONTENT_TYPE,
            **signed_headers(data, keypair),
        )
        assert response.status_code == HTTPStatus.FORBIDDEN
        assert b"hotkey" in response.content


class TestValidRequestForwarded:
    @pytest.mark.django_db
    @override_settings(BITTENSOR_NETUIDS=[12])
    def test_valid_request_forwarded(self, client, keypair, mock_sentry_contact, hotkey: str, event: dict) -> None:
        Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True)
        data = make_envelope(event | {"tags": {"hotkey": hotkey}})
        response = client.post(
            "/sentry/inbound",
            data=data,
            content_type=ENVELOPE_CONTENT_TYPE,
            HTTP_HOST="central.example",
            HTTP_BITTENSOR_HOTKEY=keypair.ss58_address,
            HTTP_BITTENSOR_NETUID="12",
            HTTP_BITTENSOR_SIGNATURE=keypair.sign(data).hex(),
            HTTP_X_CUSTOM_HEADER="kept",
        )
        assert response.status_code == HTTPStatus.OK
        mock_sentry_contact.forward.assert_called_once()
        assert mock_sentry_contact.forward.call_args.args[0] == data
        assert mock_sentry_contact.forward.call_args.kwargs["headers"]["X-Custom-Header"] == "kept"
        assert "Host" not in mock_sentry_contact.forward.call_args.kwargs["headers"]
        assert "Bittensor-Hotkey" not in mock_sentry_contact.forward.call_args.kwargs["headers"]
        assert mock_sentry_contact.forward.call_args.kwargs["netuid"] == 12

    @pytest.mark.django_db
    @override_settings(BITTENSOR_NETUIDS=[12])
    def test_upstream_dsn_not_configured_returns_500(
        self, client, keypair, mock_sentry_contact, hotkey: str, event: dict
    ) -> None:
        Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True)
        mock_sentry_contact.forward.side_effect = UpstreamDsnNotConfiguredError(12)
        data = make_envelope(event | {"tags": {"hotkey": hotkey}})
        response = client.post(
            "/sentry/inbound",
            data=data,
            content_type=ENVELOPE_CONTENT_TYPE,
            **signed_headers(data, keypair),
        )
        assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR

    @pytest.mark.django_db
    @override_settings(BITTENSOR_NETUIDS=[12])
    def test_upstream_request_error_returns_500(
        self, client, keypair, mock_sentry_contact, hotkey: str, event: dict
    ) -> None:
        Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True)
        mock_sentry_contact.forward.side_effect = requests.exceptions.ConnectionError()
        data = make_envelope(event | {"tags": {"hotkey": hotkey}})
        response = client.post(
            "/sentry/inbound",
            data=data,
            content_type=ENVELOPE_CONTENT_TYPE,
            **signed_headers(data, keypair),
        )
        assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
        assert response.content == b"ConnectionError"
