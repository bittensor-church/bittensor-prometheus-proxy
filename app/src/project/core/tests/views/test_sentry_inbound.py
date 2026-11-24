import json
from http import HTTPStatus
from unittest.mock import patch

import pytest
import requests
from django.test import override_settings

from project.core.models import Validator
from project.core.sentry_envelope import serialize_envelope
from project.core.tests.mock_contact import MockSentryContact

DSNS = {12: "https://public@sentry.reef.pl/42", 22: "https://public@sentry.reef.pl/77"}


def _event(tags: dict | None = None) -> tuple[dict, bytes]:
    event: dict = {"message": "boom"}
    if tags is not None:
        event["tags"] = tags
    return {"type": "event"}, json.dumps(event).encode()


def _envelope(*items: tuple[dict, bytes]) -> bytes:
    return serialize_envelope({"event_id": "abc"}, list(items))


def _post(client, keypair, data, netuid="12"):
    return client.post(
        "/sentry/inbound",
        data=data,
        content_type="application/x-sentry-envelope",
        HTTP_BITTENSOR_HOTKEY=keypair.ss58_address,
        HTTP_BITTENSOR_NETUID=netuid,
        HTTP_BITTENSOR_SIGNATURE=keypair.sign(data).hex(),
    )


def _upstream_response(status: int = HTTPStatus.OK) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = b'{"id":"abc"}'
    response.headers["X-Sentry-Rate-Limits"] = "1"
    return response


@pytest.fixture
def upstream():
    mock = MockSentryContact()
    mock.add_behavior("ingest_envelope", _upstream_response())
    with patch("project.core.views.sentry.sentry_contact", return_value=mock):
        yield mock


def test_not_configured_returns_500(client, upstream):
    response = client.post("/sentry/inbound", data=b"data", content_type="application/x-sentry-envelope")
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert upstream.calls["ingest_envelope"] == []


@pytest.mark.django_db
@override_settings(UPSTREAM_SENTRY_DSNS=DSNS, BITTENSOR_NETUIDS=[12, 22])
def test_missing_auth_headers_returns_400(client, keypair, upstream):
    Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True)
    response = client.post("/sentry/inbound", data=_envelope(_event()), content_type="application/x-sentry-envelope")
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert b"Missing required headers" in response.content
    assert upstream.calls["ingest_envelope"] == []


@pytest.mark.django_db
@override_settings(UPSTREAM_SENTRY_DSNS=DSNS, BITTENSOR_NETUIDS=[12, 22])
def test_malformed_body_returns_400(client, keypair, upstream):
    Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True)
    response = _post(client, keypair, b"not-an-envelope")
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert upstream.calls["ingest_envelope"] == []


@pytest.mark.django_db
@override_settings(UPSTREAM_SENTRY_DSNS=DSNS, BITTENSOR_NETUIDS=[12, 22])
def test_unsupported_item_rejects_whole_envelope(client, keypair, upstream):
    Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True)
    data = _envelope(
        _event({"hotkey": keypair.ss58_address, "netuid": "12"}),
        ({"type": "transaction"}, b'{"transaction":"t"}'),
    )
    response = _post(client, keypair, data)
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert b"Unsupported envelope item type" in response.content
    assert upstream.calls["ingest_envelope"] == []


@pytest.mark.django_db
@override_settings(UPSTREAM_SENTRY_DSNS=DSNS, BITTENSOR_NETUIDS=[12, 22])
@pytest.mark.parametrize(
    "tags",
    [None, {}, {"hotkey": "5Fake", "netuid": "12"}, "wrong-netuid"],
)
def test_event_tags_must_match(client, keypair, upstream, tags):
    Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True)
    if tags == "wrong-netuid":
        tags = {"hotkey": keypair.ss58_address, "netuid": "99"}
    response = _post(client, keypair, _envelope(_event(tags)))
    assert response.status_code == HTTPStatus.FORBIDDEN
    assert upstream.calls["ingest_envelope"] == []


@pytest.mark.django_db
@override_settings(UPSTREAM_SENTRY_DSNS={12: DSNS[12]}, BITTENSOR_NETUIDS=[12, 22])
def test_missing_dsn_for_netuid_returns_500(client, keypair, upstream):
    Validator.objects.create(public_key=keypair.ss58_address, netuid=22, active=True)
    data = _envelope(_event({"hotkey": keypair.ss58_address, "netuid": "22"}))
    response = _post(client, keypair, data, netuid="22")
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert b"No Sentry DSN configured for netuid 22" in response.content
    assert upstream.calls["ingest_envelope"] == []


@pytest.mark.django_db
@override_settings(UPSTREAM_SENTRY_DSNS=DSNS, BITTENSOR_NETUIDS=[12, 22])
def test_forwards_matching_envelope_to_netuid_dsn(client, keypair, upstream):
    Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True)
    data = _envelope(
        _event({"hotkey": keypair.ss58_address, "netuid": "12", "server": "a"}),
        ({"type": "attachment", "filename": "a.txt"}, b"line\nline"),
        ({"type": "attachment", "filename": "b.txt", "attachment_type": "event.attachment"}, b"ok"),
        ({"type": "client_report"}, b'{"discarded_events":[]}'),
    )
    response = _post(client, keypair, data)
    assert response.status_code == HTTPStatus.OK
    assert response.content == b'{"id":"abc"}'
    assert response.headers["X-Sentry-Rate-Limits"] == "1"
    assert upstream.calls["ingest_envelope"] == [(DSNS[12], data)]


@pytest.mark.django_db
@override_settings(UPSTREAM_SENTRY_DSNS=DSNS, BITTENSOR_NETUIDS=[12, 22])
@pytest.mark.parametrize(
    ("header", "message"),
    [
        (
            {"type": "attachment", "attachment_type": "event.minidump", "filename": "minidump.dmp"},
            b"Unsupported attachment type: event.minidump",
        ),
        (
            {"type": "attachment", "attachment_type": "event.attachment", "filename": "__sentry-event"},
            b"Unsupported attachment filename: __sentry-event",
        ),
        (
            {"type": "attachment", "filename": "__sentry-breadcrumb1"},
            b"Unsupported attachment filename: __sentry-breadcrumb1",
        ),
    ],
)
def test_event_creating_attachment_is_rejected(client, keypair, upstream, header, message):
    Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True)
    response = _post(client, keypair, _envelope((header, b"payload")))
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.content == message
    assert upstream.calls["ingest_envelope"] == []


@pytest.mark.django_db
@override_settings(UPSTREAM_SENTRY_DSNS=DSNS, BITTENSOR_NETUIDS=[12, 22])
def test_client_report_without_event_is_forwarded(client, keypair, upstream):
    Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True)
    data = _envelope(({"type": "client_report"}, b'{"discarded_events":[]}'))
    response = _post(client, keypair, data)
    assert response.status_code == HTTPStatus.OK
    assert upstream.calls["ingest_envelope"] == [(DSNS[12], data)]


@pytest.mark.django_db
@override_settings(UPSTREAM_SENTRY_DSNS=DSNS, BITTENSOR_NETUIDS=[12, 22])
def test_contact_error_returns_500(client, keypair, upstream):
    Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True)
    upstream.reset()
    upstream.add_behavior("ingest_envelope", requests.ConnectionError("down"))
    data = _envelope(_event({"hotkey": keypair.ss58_address, "netuid": "12"}))
    response = _post(client, keypair, data)
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert response.content == b"ConnectionError"
    assert len(upstream.calls["ingest_envelope"]) == 1
