import json
from http import HTTPStatus

import requests
import structlog
from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.urls import path, re_path
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from sentry_sdk.envelope import Envelope, PayloadRef

from ..contact.sentry import UpstreamDsnNotConfiguredError, sentry_contact
from ..proxy_auth import validate_bittensor_request
from ..proxy_outbound import (
    HEADERS_TO_STRIP,
    TIMEOUT,
    BodyTooLargeError,
    build_bittensor_outbound_headers,
    build_forwarded_response,
    decompress_body,
    session,
)

logger = structlog.getLogger(__name__)

ENVELOPE_CONTENT_TYPE = "application/x-sentry-envelope"
SUPPORTED_ENVELOPE_TYPES = {"event", "transaction"}
OUTBOUND_HEADERS_TO_STRIP = HEADERS_TO_STRIP | frozenset({"host"})
UPSTREAM_HEADERS_TO_STRIP = HEADERS_TO_STRIP | frozenset(
    {
        "host",
        "bittensor-hotkey",
        "bittensor-netuid",
        "bittensor-signature",
    }
)


class HotkeyTagMismatchError(ValueError): ...


def inject_tags(data: bytes, new_tags: dict) -> bytes:
    envelope = Envelope.deserialize(data)
    for item in envelope.items:
        if item.type not in SUPPORTED_ENVELOPE_TYPES:
            continue
        payload = item.payload.json
        payload["tags"] = dict(payload.get("tags", {})) | new_tags
        item.payload = PayloadRef(json.dumps(payload).encode())
    return envelope.serialize()


def validate_hotkey_tags(data: bytes, ss58_address: str) -> None:
    envelope = Envelope.deserialize(data)
    tagged_items = [item for item in envelope.items if item.type in SUPPORTED_ENVELOPE_TYPES]
    if not tagged_items:
        raise HotkeyTagMismatchError("No tagged Sentry items in envelope")
    for item in tagged_items:
        hotkey = dict(item.payload.json.get("tags", {})).get("hotkey")
        if hotkey != ss58_address:
            raise HotkeyTagMismatchError("Event hotkey tag missing or does not match authenticated hotkey.")


@csrf_exempt
@require_POST
def sentry_outbound_proxy(request: HttpRequest, _sentry_path: str = "") -> HttpResponse:
    if not settings.CENTRAL_SENTRY_PROXY_URL:
        msg = "CENTRAL_SENTRY_PROXY_URL is not configured"
        logger.error(msg)
        return HttpResponse(status=HTTPStatus.INTERNAL_SERVER_ERROR, content=msg.encode())

    if request.content_type != ENVELOPE_CONTENT_TYPE:
        msg = f"Content-Type must be {ENVELOPE_CONTENT_TYPE}"
        logger.error(msg, content_type=request.content_type)
        return HttpResponse(status=HTTPStatus.BAD_REQUEST, content=msg.encode())

    try:
        data = decompress_body(request.body, request.headers)
    except BodyTooLargeError:
        return HttpResponse(status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE, content=b"Body too large")
    except Exception:
        msg = "Error decompressing body"
        logger.exception(msg)
        return HttpResponse(status=HTTPStatus.BAD_REQUEST, content=msg.encode())

    hotkey = settings.BITTENSOR_WALLET().hotkey
    try:
        data = inject_tags(data, new_tags={"hotkey": hotkey.ss58_address, "netuid": str(settings.BITTENSOR_NETUID)})
    except Exception:
        msg = "Invalid sentry envelope"
        logger.exception(msg)
        return HttpResponse(status=HTTPStatus.BAD_REQUEST, content=msg.encode())

    sentry_remote_url = f"{settings.CENTRAL_SENTRY_PROXY_URL.rstrip('/')}/sentry/inbound"

    try:
        response = session.post(
            sentry_remote_url,
            data=data,
            headers={
                **{k: v for k, v in request.headers.items() if k.lower() not in OUTBOUND_HEADERS_TO_STRIP},
                **build_bittensor_outbound_headers(data, hotkey, settings.BITTENSOR_NETUID),
            },
            timeout=TIMEOUT,
        )
    except requests.exceptions.RequestException as exc:
        logger.info("Sending to central sentry proxy failed", error=str(exc))
        return HttpResponse(status=HTTPStatus.INTERNAL_SERVER_ERROR, content=type(exc).__name__)

    logger.debug(
        "Central sentry proxy replied",
        status_code=response.status_code,
        response_preview=response.content[:200],
    )
    return build_forwarded_response(response)


@csrf_exempt
@require_POST
def sentry_inbound_proxy(request: HttpRequest) -> HttpResponse:
    if request.content_type != ENVELOPE_CONTENT_TYPE:
        msg = f"Content-Type must be {ENVELOPE_CONTENT_TYPE}"
        logger.error(msg, content_type=request.content_type)
        return HttpResponse(status=HTTPStatus.BAD_REQUEST, content=msg.encode())

    try:
        data = decompress_body(request.body, request.headers)
    except BodyTooLargeError:
        return HttpResponse(status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE, content=b"Body too large")
    except Exception:
        msg = "Error decompressing body"
        logger.exception(msg)
        return HttpResponse(status=HTTPStatus.BAD_REQUEST, content=msg.encode())

    auth_result = validate_bittensor_request(request, data)
    if isinstance(auth_result, HttpResponse):
        return auth_result
    ss58_address, netuid = auth_result

    try:
        validate_hotkey_tags(data, ss58_address)
    except HotkeyTagMismatchError as exc:
        msg = str(exc)
        logger.debug(msg)
        return HttpResponse(status=HTTPStatus.FORBIDDEN, content=msg.encode())
    except Exception:
        msg = "Invalid sentry envelope"
        logger.exception(msg)
        return HttpResponse(status=HTTPStatus.BAD_REQUEST, content=msg.encode())

    try:
        response = sentry_contact().forward(
            data,
            headers={k: v for k, v in request.headers.items() if k.lower() not in UPSTREAM_HEADERS_TO_STRIP},
            netuid=netuid,
        )
    except UpstreamDsnNotConfiguredError:
        msg = f"Upstream Sentry DSN not configured for netuid {netuid}"
        logger.error(msg)
        return HttpResponse(status=HTTPStatus.INTERNAL_SERVER_ERROR, content=msg.encode())
    except requests.exceptions.RequestException as exc:
        return HttpResponse(status=HTTPStatus.INTERNAL_SERVER_ERROR, content=type(exc).__name__)

    return build_forwarded_response(response)


urlpatterns = [
    re_path(r"^outbound(?P<_sentry_path>/.*)?$", sentry_outbound_proxy),
    path("inbound", sentry_inbound_proxy),
]
