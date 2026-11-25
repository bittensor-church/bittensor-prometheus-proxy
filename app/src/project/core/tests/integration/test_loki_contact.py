import json
import time
import uuid
from http import HTTPStatus

import pytest
import requests
from django.test import override_settings

from project.core.contact.loki import LokiContact

pytestmark = pytest.mark.integration

LOKI_URL = "http://localhost:3100"
LOKI_URL_WITH_AUTH = "http://loki:secret@localhost:3100"


def _streams(*lines: str, probe: str) -> bytes:
    now = time.time_ns()
    return json.dumps(
        {
            "streams": [
                {
                    "stream": {"job": "loki-contact", "probe": probe, "n": str(index)},
                    "values": [[str(now + index), line]],
                }
                for index, line in enumerate(lines)
            ]
        }
    ).encode()


def _query(selector: str, expected: int) -> list[dict]:
    deadline = time.time() + 10
    result: list[dict] = []
    while time.time() < deadline:
        response = requests.get(f"{LOKI_URL}/loki/api/v1/query_range", params={"query": selector}, timeout=5)
        response.raise_for_status()
        result = response.json()["data"]["result"]
        if len(result) >= expected:
            return result
        time.sleep(0.2)
    return result


@pytest.fixture
def contact():
    return LokiContact()


@override_settings(UPSTREAM_LOKI_URL=LOKI_URL)
def test_push_logs_is_queryable(contact):
    probe = uuid.uuid4().hex
    line = f"hello-{probe}"
    response = contact.push_logs(_streams(line, probe=probe), content_type="application/json")
    assert response.status_code == HTTPStatus.NO_CONTENT

    result = _query(f'{{job="loki-contact",probe="{probe}"}}', expected=1)
    assert [(item["stream"]["n"], item["values"][0][1]) for item in result] == [("0", line)]


@override_settings(UPSTREAM_LOKI_URL=LOKI_URL_WITH_AUTH)
def test_push_multiple_streams_with_basic_auth(contact):
    probe = uuid.uuid4().hex
    response = contact.push_logs(_streams("one", "two", probe=probe), content_type="application/json")
    assert response.status_code == HTTPStatus.NO_CONTENT

    result = _query(f'{{probe="{probe}"}}', expected=2)
    assert sorted((item["stream"]["n"], item["values"][0][1]) for item in result) == [("0", "one"), ("1", "two")]


@override_settings(UPSTREAM_LOKI_URL=LOKI_URL)
def test_push_invalid_body_returns_4xx(contact):
    response = contact.push_logs(b"not-json", content_type="application/json")
    assert HTTPStatus.BAD_REQUEST <= response.status_code < HTTPStatus.INTERNAL_SERVER_ERROR
