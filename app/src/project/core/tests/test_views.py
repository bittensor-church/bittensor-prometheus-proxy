from http import HTTPStatus
from unittest.mock import MagicMock, patch

import bittensor
import pytest
import snappy
from django.test import override_settings

from project.core.models import Validator
from project.core.prometheus_protobuf import remote_pb2


def _make_write_request_body(hotkey: str) -> bytes:
    """Build a minimal snappy-compressed Prometheus remote_write body."""
    wr = remote_pb2.WriteRequest()
    ts = wr.timeseries.add()
    ts.labels.add(name="__name__", value="test_metric")
    ts.labels.add(name="hotkey", value=hotkey)
    ts.samples.add(value=1.0, timestamp=0)
    return snappy.compress(wr.SerializeToString())


def _make_keypair():
    return bittensor.Keypair.create_from_mnemonic(
        "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
    )


@pytest.mark.django_db
@override_settings(UPSTREAM_PROMETHEUS_URL="http://upstream", BITTENSOR_NETUIDS=[12])
def test_inbound__missing_netuid_header(client):
    kp = _make_keypair()
    Validator.objects.create(public_key=kp.ss58_address, netuid=12, active=True)
    data = _make_write_request_body(kp.ss58_address)
    response = client.post(
        "/prometheus_inbound_proxy/",
        data=data,
        content_type="application/octet-stream",
        HTTP_BITTENSOR_HOTKEY=kp.ss58_address,
        HTTP_BITTENSOR_SIGNATURE=kp.sign(data).hex(),
        # no HTTP_BITTENSOR_NETUID
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert b"Missing required headers" in response.content


@pytest.mark.django_db
@override_settings(UPSTREAM_PROMETHEUS_URL="http://upstream", BITTENSOR_NETUIDS=[12])
def test_inbound__netuid_not_in_allowed_list(client):
    kp = _make_keypair()
    Validator.objects.create(public_key=kp.ss58_address, netuid=12, active=True)
    data = _make_write_request_body(kp.ss58_address)
    response = client.post(
        "/prometheus_inbound_proxy/",
        data=data,
        content_type="application/octet-stream",
        HTTP_BITTENSOR_HOTKEY=kp.ss58_address,
        HTTP_BITTENSOR_SIGNATURE=kp.sign(data).hex(),
        HTTP_BITTENSOR_NETUID="99",  # not in [12]
    )
    assert response.status_code == HTTPStatus.FORBIDDEN
    assert b"Netuid not supported" in response.content


@pytest.mark.django_db
@override_settings(UPSTREAM_PROMETHEUS_URL="http://upstream", BITTENSOR_NETUIDS=[12])
def test_inbound__hotkey_not_active_for_netuid(client):
    kp = _make_keypair()
    # active on netuid 22, NOT 12
    Validator.objects.create(public_key=kp.ss58_address, netuid=22, active=True)
    data = _make_write_request_body(kp.ss58_address)
    response = client.post(
        "/prometheus_inbound_proxy/",
        data=data,
        content_type="application/octet-stream",
        HTTP_BITTENSOR_HOTKEY=kp.ss58_address,
        HTTP_BITTENSOR_SIGNATURE=kp.sign(data).hex(),
        HTTP_BITTENSOR_NETUID="12",
    )
    assert response.status_code == HTTPStatus.FORBIDDEN
    assert b"Validator not active" in response.content


@pytest.mark.django_db
@override_settings(UPSTREAM_PROMETHEUS_URL="http://upstream", BITTENSOR_NETUIDS=[12])
@patch("project.core.views.session")
def test_inbound__valid_request_forwarded(mock_session, client):
    mock_response = MagicMock()
    mock_response.status_code = HTTPStatus.NO_CONTENT
    mock_response.content = b""
    mock_response.headers = {}
    mock_session.post.return_value = mock_response

    kp = _make_keypair()
    Validator.objects.create(public_key=kp.ss58_address, netuid=12, active=True)
    data = _make_write_request_body(kp.ss58_address)
    response = client.post(
        "/prometheus_inbound_proxy/",
        data=data,
        content_type="application/octet-stream",
        HTTP_BITTENSOR_HOTKEY=kp.ss58_address,
        HTTP_BITTENSOR_SIGNATURE=kp.sign(data).hex(),
        HTTP_BITTENSOR_NETUID="12",
    )
    assert response.status_code == HTTPStatus.NO_CONTENT
    mock_session.post.assert_called_once()


@pytest.mark.django_db
@override_settings(
    CENTRAL_PROMETHEUS_PROXY_URL="http://test-central",
    BITTENSOR_NETUID=12,
)
@patch("project.core.views.session")
@patch("project.core.views.settings")
def test_outbound__sends_netuid_header(mock_settings, mock_session, client):
    mock_settings.CENTRAL_PROMETHEUS_PROXY_URL = "http://test-central"
    mock_settings.BITTENSOR_NETUID = 12
    kp = _make_keypair()
    mock_settings.BITTENSOR_WALLET.return_value.hotkey = kp

    mock_response = MagicMock()
    mock_response.status_code = HTTPStatus.NO_CONTENT
    mock_response.content = b""
    mock_response.headers = {}
    mock_session.post.return_value = mock_response

    data = b"some-metric-data"
    client.post(
        "/prometheus_outbound_proxy/",
        data=data,
        content_type="application/octet-stream",
    )

    call_kwargs = mock_session.post.call_args
    sent_headers = call_kwargs[1]["headers"]
    assert sent_headers["Bittensor-Netuid"] == "12"
