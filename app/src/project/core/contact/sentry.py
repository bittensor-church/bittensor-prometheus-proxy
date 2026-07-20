import functools
from abc import ABC, abstractmethod

import requests
from constance import config
from sentry_sdk.consts import EndpointType
from sentry_sdk.utils import Dsn

from project.core.proxy_outbound import TIMEOUT, session


class UpstreamDsnNotConfiguredError(KeyError):
    pass


class AbstractSentryContact(ABC):
    @abstractmethod
    def forward(self, data: bytes, headers: dict[str, str], netuid: int) -> requests.Response: ...


class SentryContact(AbstractSentryContact):
    def forward(self, data: bytes, headers: dict[str, str], netuid: int) -> requests.Response:
        try:
            dsn = config.UPSTREAM_SENTRY_DSNS[str(netuid)]
        except KeyError as exc:
            raise UpstreamDsnNotConfiguredError(netuid) from exc

        auth = Dsn(dsn).to_auth()
        return session.post(
            str(auth.get_api_url(EndpointType.ENVELOPE)),
            data=data,
            headers={**headers, "X-Sentry-Auth": str(auth.to_header())},
            timeout=TIMEOUT,
        )


@functools.cache
def sentry_contact() -> AbstractSentryContact:
    return SentryContact()
