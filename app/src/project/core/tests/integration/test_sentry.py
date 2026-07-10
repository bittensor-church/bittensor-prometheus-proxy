from os import environ
from urllib.parse import urlparse

import pytest
import sentry_sdk
from constance.test import override_config
from django.test import override_settings

from project.core.models import Validator

pytestmark = pytest.mark.integration


@pytest.mark.django_db(transaction=True)
def test_sentry_outbound_to_inbound_to_upstream(live_server, keypair, mock_wallet):
    Validator.objects.create(public_key=keypair.ss58_address, netuid=12, active=True, debug=True)

    parsed = urlparse(live_server.url)
    dsn = f"{parsed.scheme}://public@{parsed.netloc}/sentry/outbound/0"

    upstream_dsn = environ.get("UPSTREAM_SENTRY_DSN", "http://public@localhost:9000/1")
    with (
        override_settings(
            CENTRAL_SENTRY_PROXY_URL=live_server.url,
            BITTENSOR_NETUID=12,
            BITTENSOR_NETUIDS=[12],
            BITTENSOR_WALLET=mock_wallet,
        ),
        override_config(UPSTREAM_SENTRY_DSNS={"12": upstream_dsn}),
    ):
        with sentry_sdk.init(dsn=dsn):
            sentry_sdk.capture_event(
                {
                    "message": "Integration test exception from bittensor-prometheus-proxy",
                    "level": "error",
                    "tags": {"is_test": True},
                }
            )
