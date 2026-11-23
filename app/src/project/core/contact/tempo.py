import functools
from abc import ABC, abstractmethod

import requests
from django.conf import settings

from project.core.proxy_outbound import TIMEOUT, session


class AbstractTempoContact(ABC):
    @abstractmethod
    def push_traces(self, data: bytes, content_type: str) -> requests.Response: ...


class TempoContact(AbstractTempoContact):
    """Forwards validated OTLP trace payloads to the upstream Tempo instance."""

    def push_traces(self, data: bytes, content_type: str) -> requests.Response:
        url = f"{settings.UPSTREAM_TEMPO_URL.rstrip('/')}/v1/traces"
        return session.post(
            url, data=data, headers={"Content-Type": content_type}, timeout=TIMEOUT, allow_redirects=False
        )


@functools.cache
def tempo_contact() -> AbstractTempoContact:
    return TempoContact()
