from http import HTTPStatus

import requests
import structlog
from django.conf import settings
from django.http import HttpResponse
from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from ..proxy_outbound import (
    HEADERS_TO_STRIP,
    TIMEOUT,
    build_bittensor_outbound_headers,
    build_forwarded_response,
    decompress_body,
)
from ..sentry_envelope import EnvelopeError, stamp_envelope

logger = structlog.getLogger(__name__)


@csrf_exempt
@require_POST
def sentry_outbound_proxy(request, project_id: int):
    if not settings.CENTRAL_SENTRY_PROXY_URL:
        msg = "CENTRAL_SENTRY_PROXY_URL is not configured"
        logger.error(msg)
        return HttpResponse(status=HTTPStatus.INTERNAL_SERVER_ERROR, content=msg.encode())

    try:
        data = decompress_body(request.body, request.headers)
    except Exception as exc:
        msg = f"Failed to decompress data: {exc}"
        logger.debug(msg)
        return HttpResponse(msg.encode(), status=HTTPStatus.BAD_REQUEST)

    hotkey = settings.BITTENSOR_WALLET().hotkey
    try:
        modified_data = stamp_envelope(data, hotkey=hotkey.ss58_address, netuid=str(settings.BITTENSOR_NETUID))
    except EnvelopeError as exc:
        msg = f"Failed to decode envelope: {exc}"
        logger.debug(msg)
        return HttpResponse(msg.encode(), status=HTTPStatus.BAD_REQUEST)

    remote_url = f"{settings.CENTRAL_SENTRY_PROXY_URL.rstrip('/')}/sentry/inbound"
    try:
        response = requests.post(
            remote_url,
            data=modified_data,
            headers={
                **{k: v for k, v in request.headers.items() if k.lower() not in HEADERS_TO_STRIP},
                **build_bittensor_outbound_headers(modified_data, hotkey, settings.BITTENSOR_NETUID),
            },
            timeout=TIMEOUT,
        )
    except requests.exceptions.RequestException as exc:
        return HttpResponse(status=HTTPStatus.INTERNAL_SERVER_ERROR, content=type(exc).__name__.encode())

    return build_forwarded_response(response)


urlpatterns = [
    path("api/<int:project_id>/envelope/", sentry_outbound_proxy),
]
