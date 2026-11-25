from http import HTTPStatus
from urllib.parse import urlsplit

import requests
import snappy
import structlog
from django.conf import settings
from django.http import HttpResponse
from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from ..contact import loki_contact
from ..loki_utils import LokiPushError, _parse_labels, stamp_push, stream_labels
from ..proxy_auth import validate_bittensor_request
from ..proxy_outbound import (
    HEADERS_TO_STRIP,
    TIMEOUT,
    build_bittensor_outbound_headers,
    build_forwarded_response,
    session,
)

logger = structlog.getLogger(__name__)


@csrf_exempt
@require_POST
def loki_outbound_proxy(request):
    if not settings.CENTRAL_LOKI_PROXY_URL:
        msg = "CENTRAL_LOKI_PROXY_URL is not configured"
        logger.error(msg)
        return HttpResponse(status=HTTPStatus.INTERNAL_SERVER_ERROR, content=msg.encode())

    if request.headers.get("Content-Encoding", "").strip().lower() != "snappy":
        msg = "Content-Encoding must be snappy"
        logger.debug(msg)
        return HttpResponse(status=HTTPStatus.BAD_REQUEST, content=msg.encode())

    hotkey = settings.BITTENSOR_WALLET().hotkey
    try:
        decoded = snappy.uncompress(request.body)
    except Exception as exc:
        msg = f"Failed to decompress logs: {exc}"
        logger.debug(msg)
        return HttpResponse(status=HTTPStatus.BAD_REQUEST, content=msg.encode())
    try:
        modified = snappy.compress(stamp_push(decoded, hotkey.ss58_address, str(settings.BITTENSOR_NETUID)))
    except LokiPushError as exc:
        msg = f"Failed to decode logs: {exc}"
        logger.debug(msg)
        return HttpResponse(status=HTTPStatus.BAD_REQUEST, content=msg.encode())

    remote_url = f"{settings.CENTRAL_LOKI_PROXY_URL.rstrip('/')}/loki/inbound"
    try:
        response = session.post(
            remote_url,
            data=modified,
            headers={
                **{key: value for key, value in request.headers.items() if key.lower() not in HEADERS_TO_STRIP},
                **build_bittensor_outbound_headers(modified, hotkey, settings.BITTENSOR_NETUID),
                "Content-Type": "application/x-protobuf",
                "Content-Encoding": "snappy",
                "Host": urlsplit(remote_url).netloc,
            },
            timeout=TIMEOUT,
            allow_redirects=False,
        )
    except requests.exceptions.RequestException as exc:
        return HttpResponse(status=HTTPStatus.INTERNAL_SERVER_ERROR, content=type(exc).__name__)

    return build_forwarded_response(response)


@csrf_exempt
@require_POST
def loki_inbound_proxy(request):
    if not settings.UPSTREAM_LOKI_URL:
        msg = "UPSTREAM_LOKI_URL is not configured"
        logger.error(msg)
        return HttpResponse(status=HTTPStatus.INTERNAL_SERVER_ERROR, content=msg.encode())

    data = request.body
    auth_result = validate_bittensor_request(request, data)
    if isinstance(auth_result, HttpResponse):
        return auth_result
    ss58_address, netuid = auth_result

    if request.headers.get("Content-Encoding", "").strip().lower() != "snappy":
        msg = "Content-Encoding must be snappy"
        logger.debug(msg)
        return HttpResponse(status=HTTPStatus.BAD_REQUEST, content=msg.encode())

    try:
        decoded = snappy.uncompress(data)
    except Exception as exc:
        msg = f"Failed to decompress logs: {exc}"
        logger.debug(msg)
        return HttpResponse(status=HTTPStatus.BAD_REQUEST, content=msg.encode())
    try:
        label_sets = [_parse_labels(labels) for labels in stream_labels(decoded)]
    except LokiPushError as exc:
        msg = f"Failed to decode logs: {exc}"
        logger.debug(msg)
        return HttpResponse(status=HTTPStatus.BAD_REQUEST, content=msg.encode())

    expected_netuid = str(netuid)
    for labels in label_sets:
        if labels.get("hotkey") != ss58_address or labels.get("netuid") != expected_netuid:
            msg = "Stream hotkey or netuid does not match the authenticated validator."
            logger.debug(msg)
            return HttpResponse(status=HTTPStatus.FORBIDDEN, content=msg.encode())

    try:
        response = loki_contact().push_logs(
            data,
            content_type=request.content_type or "application/x-protobuf",
            content_encoding="snappy",
        )
    except requests.exceptions.RequestException as exc:
        return HttpResponse(status=HTTPStatus.INTERNAL_SERVER_ERROR, content=type(exc).__name__)

    return build_forwarded_response(response)


urlpatterns = [
    path("inbound", loki_inbound_proxy),
    path("outbound", loki_outbound_proxy),
]
