from http import HTTPStatus
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings


@pytest.mark.django_db
@override_settings(
    CENTRAL_PROMETHEUS_PROXY_URL="http://test-central",
    BITTENSOR_NETUID=12,
)
@patch("project.core.views.prometheus.session")
@patch("project.core.views.prometheus.settings")
def test_sends_netuid_header(mock_settings, mock_session, client, keypair):
    mock_settings.CENTRAL_PROMETHEUS_PROXY_URL = "http://test-central"
    mock_settings.BITTENSOR_NETUID = 12
    mock_settings.BITTENSOR_WALLET.return_value.hotkey = keypair

    mock_response = MagicMock()
    mock_response.status_code = HTTPStatus.NO_CONTENT
    mock_response.content = b""
    mock_response.headers = {}
    mock_session.post.return_value = mock_response

    client.post(
        "/prometheus/outbound",
        data=b"some-metric-data",
        content_type="application/octet-stream",
        HTTP_HOST="on-site.local:8000",
    )

    sent_headers = mock_session.post.call_args[1]["headers"]
    assert sent_headers["Host"] == "test-central"
    assert sent_headers["Bittensor-Netuid"] == "12"
