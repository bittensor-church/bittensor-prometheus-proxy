import gzip

import pytest

from project.core.proxy_outbound import MAX_DECOMPRESSED_BODY_SIZE, BodyTooLargeError, decompress_body


def test_decompress_plain_body() -> None:
    assert decompress_body(b"hello", {}) == b"hello"


def test_decompress_gzip_body() -> None:
    data = gzip.compress(b"hello")
    assert decompress_body(data, {"Content-Encoding": "gzip"}) == b"hello"


def test_rejects_decompressed_body_over_limit() -> None:
    data = gzip.compress(b"\x00" * (MAX_DECOMPRESSED_BODY_SIZE + 1))
    with pytest.raises(BodyTooLargeError):
        decompress_body(data, {"Content-Encoding": "gzip"})
