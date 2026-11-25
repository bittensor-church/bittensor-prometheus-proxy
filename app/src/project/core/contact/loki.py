import functools
from abc import ABC, abstractmethod
from urllib.parse import unquote, urlparse, urlunparse

import requests
from django.conf import settings

from project.core.proxy_outbound import TIMEOUT, session


class AbstractLokiContact(ABC):
    @abstractmethod
    def push_logs(self, data: bytes, content_type: str, content_encoding: str = "") -> requests.Response: ...


class LokiContact(AbstractLokiContact):
    """Forwards a Loki push payload to the upstream Loki instance."""

    def push_logs(self, data: bytes, content_type: str, content_encoding: str = "") -> requests.Response:
        parsed = urlparse(settings.UPSTREAM_LOKI_URL)
        hostname = parsed.hostname or ""
        if ":" in hostname:
            hostname = f"[{hostname}]"
        netloc = f"{hostname}:{parsed.port}" if parsed.port else hostname
        base = urlunparse((parsed.scheme, netloc, parsed.path, "", "", "")).rstrip("/")
        headers = {"Content-Type": content_type}
        if content_encoding:
            headers["Content-Encoding"] = content_encoding
        username = unquote(parsed.username) if parsed.username else ""
        auth = (username, unquote(parsed.password or "")) if username else None
        return session.post(
            f"{base}/loki/api/v1/push", data=data, headers=headers, auth=auth, timeout=TIMEOUT, allow_redirects=False
        )


@functools.cache
def loki_contact() -> AbstractLokiContact:
    return LokiContact()
