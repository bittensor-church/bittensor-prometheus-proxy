from http import HTTPStatus
from urllib.parse import urljoin

import requests
import snappy
import structlog
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

from project.core.models import Validator

from .metrics import metrics_counter, series_counter
from .prometheus_protobuf import remote_pb2

logger = structlog.getLogger(__name__)
retries = Retry(
    total=3,
    connect=3,
    read=3,
    redirect=3,
    backoff_factor=0.1,
    status_forcelist=(),
    raise_on_status=False,
    allowed_methods=False,  # default excludes POST; False retries all methods (safe here since Prometheus remote write is idempotent)
)

TIMEOUT = 15

session = requests.Session()
session.mount("http://", HTTPAdapter(max_retries=retries))
session.mount("https://", HTTPAdapter(max_retries=retries))


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

    prometheus_remote_url = urljoin(settings.CENTRAL_PROMETHEUS_PROXY_URL, "prometheus_inbound_proxy/")

    wallet = settings.BITTENSOR_WALLET()
    try:
        response = session.post(
            prometheus_remote_url,
            data=data,
            headers={
                **request.headers,
                "Bittensor-Signature": wallet.hotkey.sign(data).hex(),
                "Bittensor-Hotkey": wallet.hotkey.ss58_address,
                "Bittensor-Netuid": str(settings.BITTENSOR_NETUID),
            },
            timeout=TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        logger.info(f"Sending to central prometheus proxy failed: {e}")
        return HttpResponse(status=HTTPStatus.INTERNAL_SERVER_ERROR, content=type(e).__name__)

    logger.debug(f"Central prometheus proxy replied with {response.status_code}, {response.content[:200]}")
    return HttpResponse(
        status=response.status_code,
        headers={
            k: v
            for k, v in response.headers.items()
            if k.lower()
            not in ["connection", "keep-alive", "public", "proxy-authenticate", "transfer-encoding", "upgrade"]
        },
        content=response.content,
    )


@csrf_exempt
@require_POST
def prometheus_inbound_proxy(request):
    if not settings.UPSTREAM_PROMETHEUS_URL:
        msg = "UPSTREAM_PROMETHEUS_URL is not configured"
        logger.error(msg)
        return HttpResponse(status=HTTPStatus.INTERNAL_SERVER_ERROR, content=msg.encode())

    data = request.body
    ss58_address = request.headers.get("Bittensor-Hotkey")
    signature = request.headers.get("Bittensor-Signature")
    netuid_raw = request.headers.get("Bittensor-Netuid")

    if not ss58_address or not signature or not netuid_raw:
        msg = "Missing required headers."
        logger.debug(msg)
        return HttpResponse(status=HTTPStatus.BAD_REQUEST, content=msg)

    try:
        netuid = int(netuid_raw)
    except ValueError:
        msg = "Invalid Bittensor-Netuid header."
        logger.debug(msg)
        return HttpResponse(status=HTTPStatus.BAD_REQUEST, content=msg)

    if netuid not in settings.BITTENSOR_NETUIDS:
        msg = f"Netuid not supported: {netuid}"
        logger.debug(msg)
        return HttpResponse(status=HTTPStatus.FORBIDDEN, content=msg)

    if not Validator.objects.filter(active=True, netuid=netuid, public_key=ss58_address).exists():
        msg = "Validator not active."
        logger.debug(msg)
        return HttpResponse(status=HTTPStatus.FORBIDDEN, content=msg)

    import bittensor

    sender_keypair = bittensor.Keypair(ss58_address)
    if not sender_keypair.verify(data, "0x" + signature):
        msg = "Bad signature."
        logger.debug(msg)
        return HttpResponse(status=HTTPStatus.BAD_REQUEST, content=msg)

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

    prometheus_remote_url = urljoin(settings.UPSTREAM_PROMETHEUS_URL, "api/v1/write")

    try:
        response = session.post(
            prometheus_remote_url,
            data=data,
            headers=request.headers,
            timeout=TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        return HttpResponse(status=HTTPStatus.INTERNAL_SERVER_ERROR, content=type(e).__name__)

    return HttpResponse(
        status=response.status_code,
        headers=response.headers,
        content=response.content,
    )
