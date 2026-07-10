from http import HTTPStatus

import requests
import structlog
from django.conf import settings
from django.http import HttpResponse
from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2

from ..contact import tempo_contact
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


def _upsert_string_attr(attrs, key: str, value: str) -> None:
    """Upsert a string KeyValue into an OTLP repeated resource.attributes field."""
    for kv in attrs:
        if kv.key == key:
            kv.value.string_value = value
            return
    kv = attrs.add()
    kv.key = key
    kv.value.string_value = value


def _parse_traces(data: bytes) -> tuple[trace_service_pb2.ExportTraceServiceRequest | None, HttpResponse | None]:
    try:
        req = trace_service_pb2.ExportTraceServiceRequest()
        req.ParseFromString(data)
        return req, None
    except Exception as e:
        msg = f"Failed to decode traces: {e}"
        logger.debug(msg)
        return None, HttpResponse(msg.encode(), status=HTTPStatus.BAD_REQUEST)


def _validate_trace_hotkeys(write_request, ss58_address: str) -> HttpResponse | None:
    for resource_spans in write_request.resource_spans:
        hotkey_attr = next(
            (kv for kv in resource_spans.resource.attributes if kv.key == "hotkey"),
            None,
        )
        if hotkey_attr is None or hotkey_attr.value.string_value != ss58_address:
            msg = "Resource hotkey attribute missing or does not match authenticated hotkey."
            logger.debug(msg)
            return HttpResponse(status=HTTPStatus.FORBIDDEN, content=msg.encode())
    return None


@csrf_exempt
@require_POST
def traces_outbound_proxy(request):
    if not settings.CENTRAL_TEMPO_PROXY_URL:
        msg = "CENTRAL_TEMPO_PROXY_URL is not configured"
        logger.error(msg)
        return HttpResponse(status=HTTPStatus.INTERNAL_SERVER_ERROR, content=msg.encode())

    try:
        data = decompress_body(request.body, request.headers)
    except BodyTooLargeError:
        return HttpResponse(status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE, content=b"Body too large")
    except Exception as e:
        msg = f"Failed to decompress data: {e}"
        logger.debug(msg)
        return HttpResponse(msg.encode(), status=HTTPStatus.BAD_REQUEST)

    write_request, err = _parse_traces(data)
    if err:
        return err

    wallet = settings.BITTENSOR_WALLET()
    hotkey = wallet.hotkey
    netuid_str = str(settings.BITTENSOR_NETUID)

    for resource_spans in write_request.resource_spans:
        _upsert_string_attr(resource_spans.resource.attributes, "hotkey", hotkey.ss58_address)
        _upsert_string_attr(resource_spans.resource.attributes, "netuid", netuid_str)

    modified_data = write_request.SerializeToString()

    tempo_remote_url = f"{settings.CENTRAL_TEMPO_PROXY_URL.rstrip('/')}/traces/inbound"
    try:
        response = session.post(
            tempo_remote_url,
            data=modified_data,
            headers={
                **{k: v for k, v in request.headers.items() if k.lower() not in HEADERS_TO_STRIP},
                **build_bittensor_outbound_headers(modified_data, hotkey, settings.BITTENSOR_NETUID),
            },
            timeout=TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        return HttpResponse(status=HTTPStatus.INTERNAL_SERVER_ERROR, content=type(e).__name__)

    return build_forwarded_response(response)


@csrf_exempt
@require_POST
def traces_inbound_proxy(request):
    if not settings.UPSTREAM_TEMPO_URL:
        msg = "UPSTREAM_TEMPO_URL is not configured"
        logger.error(msg)
        return HttpResponse(status=HTTPStatus.INTERNAL_SERVER_ERROR, content=msg.encode())

    data = request.body
    auth_result = validate_bittensor_request(request, data)
    if isinstance(auth_result, HttpResponse):
        return auth_result
    ss58_address, _netuid = auth_result

    write_request, err = _parse_traces(data)
    if err:
        return err

    err = _validate_trace_hotkeys(write_request, ss58_address)
    if err:
        return err

    try:
        response = tempo_contact().push_traces(data, content_type=request.content_type or "application/x-protobuf")
    except requests.exceptions.RequestException as e:
        return HttpResponse(status=HTTPStatus.INTERNAL_SERVER_ERROR, content=type(e).__name__)

    return build_forwarded_response(response)


urlpatterns = [
    path("inbound", traces_inbound_proxy),
    path("outbound", traces_outbound_proxy),
]
