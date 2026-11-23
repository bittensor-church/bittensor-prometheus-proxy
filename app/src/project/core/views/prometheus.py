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

from ..metrics import metrics_counter, series_counter
from ..prometheus_protobuf import remote_pb2
from ..proxy_auth import validate_bittensor_request
from ..proxy_outbound import (
    TIMEOUT,
    build_bittensor_outbound_headers,
    build_forwarded_response,
    session,
)

logger = structlog.getLogger(__name__)


def _validate_timeseries(ts, ss58_address: str) -> tuple[str | None, str]:
    """Return (error_message, metric_name). error_message is None if the timeseries is valid."""
    name = "<undefined>"
    hotkey = None
    for label in ts.labels:
        if label.name == "__name__":
            name = label.value
        if label.name == "hotkey":
            hotkey = label.value
    if not hotkey:
        return "Received no hotkey", name
    if hotkey != ss58_address:
        return f"Received invalid hotkey. Expected {ss58_address} got {hotkey}", name
    return None, name


@csrf_exempt
@require_POST
def prometheus_outbound_proxy(request):
    if not settings.CENTRAL_PROMETHEUS_PROXY_URL:
        msg = "CENTRAL_PROMETHEUS_PROXY_URL is not configured"
        logger.error(msg)
        return HttpResponse(status=HTTPStatus.INTERNAL_SERVER_ERROR, content=msg.encode())
    data = request.body

    prometheus_remote_url = f"{settings.CENTRAL_PROMETHEUS_PROXY_URL.rstrip('/')}/prometheus/inbound"

    wallet = settings.BITTENSOR_WALLET()
    try:
        response = session.post(
            prometheus_remote_url,
            data=data,
            headers={
                **request.headers,
                **build_bittensor_outbound_headers(data, wallet.hotkey, settings.BITTENSOR_NETUID),
                "Host": urlsplit(prometheus_remote_url).netloc,
            },
            timeout=TIMEOUT,
            allow_redirects=False,
        )
    except requests.exceptions.RequestException as e:
        logger.info(f"Sending to central prometheus proxy failed: {e}")
        return HttpResponse(status=HTTPStatus.INTERNAL_SERVER_ERROR, content=type(e).__name__)

    logger.debug(f"Central prometheus proxy replied with {response.status_code}, {response.content[:200]}")
    return build_forwarded_response(response)


@csrf_exempt
@require_POST
def prometheus_inbound_proxy(request):
    if not settings.UPSTREAM_PROMETHEUS_URL:
        msg = "UPSTREAM_PROMETHEUS_URL is not configured"
        logger.error(msg)
        return HttpResponse(status=HTTPStatus.INTERNAL_SERVER_ERROR, content=msg.encode())

    data = request.body
    auth_result = validate_bittensor_request(request, data)
    if isinstance(auth_result, HttpResponse):
        return auth_result
    ss58_address, _netuid = auth_result

    try:
        decompressed_data = snappy.uncompress(data)
    except Exception as e:
        msg = f"Failed to decompress data: {e}"
        logger.debug(msg)
        return HttpResponse(msg.encode(), status=HTTPStatus.BAD_REQUEST)

    try:
        write_request = remote_pb2.WriteRequest()
        write_request.ParseFromString(decompressed_data)
    except Exception as e:
        msg = f"Failed to decode metrics: {e}"
        logger.debug(msg)
        return HttpResponse(msg, status=HTTPStatus.BAD_REQUEST)

    series_count = 0
    metrics = set()
    for ts in write_request.timeseries:
        error_msg, name = _validate_timeseries(ts, ss58_address)
        if error_msg:
            logger.info("Metric: %s. %s", name, error_msg)
            return HttpResponse(status=HTTPStatus.FORBIDDEN, content=f"Metric: {name}. {error_msg}".encode())
        series_count += 1
        metrics.add(name)

    series_counter.labels(ss58_address).inc(series_count)
    metrics_counter.labels(ss58_address).inc(len(metrics))
    logger.debug("%s sent %s metrics and %s series", ss58_address, len(metrics), series_count)

    prometheus_remote_url = f"{settings.UPSTREAM_PROMETHEUS_URL.rstrip('/')}/api/v1/write"

    try:
        response = session.post(
            prometheus_remote_url,
            data=data,
            headers=dict(request.headers),
            timeout=TIMEOUT,
            allow_redirects=False,
        )
    except requests.exceptions.RequestException as e:
        return HttpResponse(status=HTTPStatus.INTERNAL_SERVER_ERROR, content=type(e).__name__)

    return build_forwarded_response(response)


urlpatterns = [
    path("inbound", prometheus_inbound_proxy),
    path("outbound", prometheus_outbound_proxy),
]
