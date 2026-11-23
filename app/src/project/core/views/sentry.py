import json
from http import HTTPStatus
from urllib.parse import urlsplit

import requests
import structlog
from django.conf import settings
from django.http import HttpResponse
from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from ..contact import sentry_contact
from ..proxy_auth import validate_bittensor_request
from ..proxy_outbound import (
    HEADERS_TO_STRIP,
    TIMEOUT,
    build_bittensor_outbound_headers,
    build_forwarded_response,
    decompress_body,
)
from ..sentry_envelope import EnvelopeError, parse_envelope, stamp_envelope

logger = structlog.getLogger(__name__)

ACCEPTED_ITEM_TYPES = frozenset({"event", "attachment", "client_report"})


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
                "Host": urlsplit(remote_url).netloc,
            },
            timeout=TIMEOUT,
            allow_redirects=False,
        )
    except requests.exceptions.RequestException as exc:
        return HttpResponse(status=HTTPStatus.INTERNAL_SERVER_ERROR, content=type(exc).__name__.encode())

    return build_forwarded_response(response)


class DataError(Exception): ...


class UnsupportedAttachmentType(DataError): ...


class UnsupportedAttachmentFilename(DataError): ...


class UnsupportedEnvelopeItemType(DataError): ...


class AuthenticationError(Exception): ...


def check_event_tags(payload: bytes, ss58_address: str, netuid: int) -> None:
    try:
        event = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise DataError(f"Failed to decode envelope: {exc}") from exc

    if not isinstance(event, dict):
        raise DataError("Event payload must be an object.")

    tags = event.get("tags")
    if not isinstance(tags, dict) or tags.get("hotkey") != ss58_address or tags.get("netuid") != str(netuid):
        raise AuthenticationError("Event hotkey or netuid tag missing or does not match the authenticated request.")


def check_attachment(item_header: dict) -> None:
    if item_header.get("attachment_type", "event.attachment") != "event.attachment":
        raise UnsupportedAttachmentType
    filename = item_header.get("filename")
    if isinstance(filename, str) and (filename == "__sentry-event" or filename.startswith("__sentry-breadcrumb")):
        raise UnsupportedAttachmentFilename


@csrf_exempt
@require_POST
def sentry_inbound_proxy(request):
    if not settings.UPSTREAM_SENTRY_DSNS:
        msg = "UPSTREAM_SENTRY_DSNS is not configured"
        logger.error(msg)
        return HttpResponse(status=HTTPStatus.INTERNAL_SERVER_ERROR, content=msg.encode())

    data = request.body
    auth_result = validate_bittensor_request(request, data)
    if isinstance(auth_result, HttpResponse):
        return auth_result
    ss58_address, netuid = auth_result

    try:
        _, items = parse_envelope(data)
    except EnvelopeError as exc:
        msg = f"Failed to decode envelope: {exc}"
        logger.debug(msg)
        return HttpResponse(msg.encode(), status=HTTPStatus.BAD_REQUEST)

    for item_header, payload in items:
        if item_header.get("type") not in ACCEPTED_ITEM_TYPES:
            msg = f"Unsupported envelope item type: {item_header.get('type')}"
            logger.debug(msg)
            return HttpResponse(msg.encode(), status=HTTPStatus.BAD_REQUEST)
        try:
            type_ = item_header.get("type")
            match type_:
                case "event":
                    check_event_tags(payload, ss58_address, netuid)
                case "attachment":
                    check_attachment(item_header)
                case "client_report":
                    pass
                case _:
                    raise UnsupportedEnvelopeItemType(type_)
        except (DataError, AuthenticationError) as exc:
            logger.debug(str(exc))
            return HttpResponse(
                status=HTTPStatus.FORBIDDEN if isinstance(exc, AuthenticationError) else HTTPStatus.BAD_REQUEST,
                content=str(exc).encode(),
            )

    if not (dsn := settings.UPSTREAM_SENTRY_DSNS.get(netuid)):
        msg = f"No Sentry DSN configured for netuid {netuid}."
        logger.error(msg)
        return HttpResponse(status=HTTPStatus.INTERNAL_SERVER_ERROR, content=msg.encode())

    try:
        response = sentry_contact().ingest_envelope(dsn, data)
    except requests.exceptions.RequestException as exc:
        return HttpResponse(status=HTTPStatus.INTERNAL_SERVER_ERROR, content=type(exc).__name__.encode())

    return build_forwarded_response(response)


urlpatterns = [
    path("api/<int:project_id>/envelope/", sentry_outbound_proxy),
    path("sentry/inbound", sentry_inbound_proxy),
]
