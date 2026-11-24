import functools
from abc import ABC, abstractmethod
from urllib.parse import urlparse

import requests

from project.core.proxy_outbound import TIMEOUT
from project.core.sentry_envelope import parse_envelope, serialize_envelope


class AbstractSentryContact(ABC):
    @abstractmethod
    def ingest_envelope(self, dsn: str, envelope: bytes) -> requests.Response: ...


class SentryContact(AbstractSentryContact):
    """Forwards one Sentry envelope to the project identified by a DSN."""

    def ingest_envelope(self, dsn: str, envelope: bytes) -> requests.Response:
        url, public_key, public_dsn = _envelope_endpoint(dsn)
        header, items = parse_envelope(envelope)
        header["dsn"] = public_dsn
        return requests.post(
            url,
            data=serialize_envelope(header, items),
            headers={
                "Content-Type": "application/x-sentry-envelope",
                "X-Sentry-Auth": (
                    f"Sentry sentry_version=7, sentry_client=bittensor-prometheus-proxy/0, sentry_key={public_key}"
                ),
            },
            timeout=TIMEOUT,
            allow_redirects=False,
        )


def _envelope_endpoint(dsn: str) -> tuple[str, str, str]:
    parsed = urlparse(dsn)
    path = parsed.path.rstrip("/")
    prefix, _, project_id = path.rpartition("/")
    host = parsed.hostname
    if host and ":" in host:
        host = f"[{host}]"
    public_key = parsed.username or ""
    if (
        parsed.scheme not in ("http", "https")
        or not host
        or not public_key
        or any(char in public_key for char in "\r\n, ")
        or not project_id.isdigit()
    ):
        raise ValueError("Invalid Sentry DSN.")
    if parsed.port:
        host = f"{host}:{parsed.port}"
    endpoint = f"{parsed.scheme}://{host}{prefix}/api/{project_id}/envelope/"
    public_dsn = f"{parsed.scheme}://{public_key}@{host}{prefix}/{project_id}"
    return endpoint, public_key, public_dsn


@functools.cache
def sentry_contact() -> AbstractSentryContact:
    return SentryContact()
