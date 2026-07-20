import gzip
import io

import requests
from django.conf import settings
from django.http import HttpResponse
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

_retries = Retry(
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
session.mount("http://", HTTPAdapter(max_retries=_retries))
session.mount("https://", HTTPAdapter(max_retries=_retries))

HOP_BY_HOP_HEADERS = frozenset(
    {"connection", "keep-alive", "public", "proxy-authenticate", "transfer-encoding", "upgrade"}
)

# Headers that describe the encoding and size of the body. Must be stripped whenever the body
# is decompressed or modified before forwarding, since they no longer match the actual bytes sent.
BODY_ENCODING_HEADERS = frozenset({"content-encoding", "content-length"})

# Stripped from headers forwarded in either direction: hop-by-hop headers must never cross a
# proxy boundary, and body-encoding headers go stale whenever we decompress/modify the body.
# requests also auto-decompresses response.content, so those go stale there too; Django
# recalculates Content-Length from the actual content passed to HttpResponse.
HEADERS_TO_STRIP = HOP_BY_HOP_HEADERS | BODY_ENCODING_HEADERS

MAX_DECOMPRESSED_BODY_SIZE = 3 * settings.DATA_UPLOAD_MAX_MEMORY_SIZE


class BodyTooLargeError(ValueError): ...


def _gzip_decompress_limited(data: bytes, max_size: int) -> bytes:
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as gzip_file:
        chunks: list[bytes] = []
        total = 0
        while chunk := gzip_file.read(64 * 1024):
            total += len(chunk)
            if total > max_size:
                raise BodyTooLargeError
            chunks.append(chunk)
        return b"".join(chunks)


def decompress_body(data: bytes, headers) -> bytes:
    encoding = headers.get("Content-Encoding", "")
    if not encoding:
        return data
    if any(e.strip().lower() in ("gzip", "x-gzip") for e in encoding.split(",")):
        return _gzip_decompress_limited(data, MAX_DECOMPRESSED_BODY_SIZE)
    raise ValueError(f"Unsupported Content-Encoding: {encoding}")


def build_forwarded_response(response):
    return HttpResponse(
        status=response.status_code,
        headers={k: v for k, v in response.headers.items() if k.lower() not in HEADERS_TO_STRIP},
        content=response.content,
    )


def build_bittensor_outbound_headers(data: bytes, hotkey, netuid: int) -> dict[str, str]:
    """Return Bittensor auth headers for an outbound proxy request."""
    return {
        "Bittensor-Hotkey": hotkey.ss58_address,
        "Bittensor-Netuid": str(netuid),
        "Bittensor-Signature": hotkey.sign(data).hex(),
    }
