from http import HTTPStatus
from unittest.mock import MagicMock, patch

import pytest
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2


def test_not_configured_returns_500(client):
    # test settings has CENTRAL_TEMPO_PROXY_URL="" — view returns 500 immediately
    response = client.post("/traces/outbound", data=b"data", content_type="application/x-protobuf")
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR


@patch("project.core.views.traces.session")
@patch("project.core.views.traces.settings")
def test_malformed_body_returns_400(mock_settings, mock_session, client):
    mock_settings.CENTRAL_TEMPO_PROXY_URL = "http://test-central"
    response = client.post("/traces/outbound", data=b"not-valid-protobuf", content_type="application/x-protobuf")
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.django_db
@patch("project.core.views.traces.session")
@patch("project.core.views.traces.settings")
def test_injects_hotkey_and_netuid(mock_settings, mock_session, client, keypair, make_traces_body):
    mock_settings.CENTRAL_TEMPO_PROXY_URL = "http://test-central"
    mock_settings.BITTENSOR_NETUID = 12
    mock_settings.BITTENSOR_WALLET.return_value.hotkey = keypair

    mock_response = MagicMock()
    mock_response.status_code = HTTPStatus.NO_CONTENT
    mock_response.content = b""
    mock_response.headers = {}
    mock_session.post.return_value = mock_response

    client.post("/traces/outbound", data=make_traces_body(), content_type="application/x-protobuf")

    forwarded_body = mock_session.post.call_args.kwargs["data"]
    parsed = trace_service_pb2.ExportTraceServiceRequest()
    parsed.ParseFromString(forwarded_body)
    attrs = {kv.key: kv.value.string_value for kv in parsed.resource_spans[0].resource.attributes}
    assert attrs["hotkey"] == keypair.ss58_address
    assert attrs["netuid"] == "12"


@pytest.mark.django_db
@patch("project.core.views.traces.session")
@patch("project.core.views.traces.settings")
def test_overwrites_existing_hotkey(mock_settings, mock_session, client, keypair, make_traces_body):
    mock_settings.CENTRAL_TEMPO_PROXY_URL = "http://test-central"
    mock_settings.BITTENSOR_NETUID = 12
    mock_settings.BITTENSOR_WALLET.return_value.hotkey = keypair

    mock_response = MagicMock()
    mock_response.status_code = HTTPStatus.NO_CONTENT
    mock_response.content = b""
    mock_response.headers = {}
    mock_session.post.return_value = mock_response

    client.post("/traces/outbound", data=make_traces_body(hotkey="5FakeHotkey"), content_type="application/x-protobuf")

    forwarded_body = mock_session.post.call_args.kwargs["data"]
    parsed = trace_service_pb2.ExportTraceServiceRequest()
    parsed.ParseFromString(forwarded_body)
    attrs = {kv.key: kv.value.string_value for kv in parsed.resource_spans[0].resource.attributes}
    assert attrs["hotkey"] == keypair.ss58_address  # replaced with real hotkey


@pytest.mark.django_db
@patch("project.core.views.traces.session")
@patch("project.core.views.traces.settings")
def test_sends_auth_headers(mock_settings, mock_session, client, keypair, make_traces_body):
    mock_settings.CENTRAL_TEMPO_PROXY_URL = "http://central.example:8443"
    mock_settings.BITTENSOR_NETUID = 12
    mock_settings.BITTENSOR_WALLET.return_value.hotkey = keypair

    mock_response = MagicMock()
    mock_response.status_code = HTTPStatus.NO_CONTENT
    mock_response.content = b""
    mock_response.headers = {}
    mock_session.post.return_value = mock_response

    client.post(
        "/traces/outbound",
        data=make_traces_body(),
        content_type="application/x-protobuf",
        HTTP_HOST="on-site.local:8000",
    )

    sent_headers = mock_session.post.call_args.kwargs["headers"]
    assert sent_headers["Host"] == "central.example:8443"
    assert sent_headers["Bittensor-Hotkey"] == keypair.ss58_address
    assert sent_headers["Bittensor-Netuid"] == "12"
    assert "Bittensor-Signature" in sent_headers
