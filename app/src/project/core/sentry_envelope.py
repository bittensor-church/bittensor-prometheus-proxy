import json


class EnvelopeError(ValueError):
    pass


def parse_envelope(data: bytes) -> tuple[dict, list[tuple[dict, bytes]]]:
    if not data:
        raise EnvelopeError("Empty envelope.")
    header_line, pos = _take_line(data, 0)
    header = _loads_object(header_line, "envelope header")
    items: list[tuple[dict, bytes]] = []
    while pos < len(data):
        item_line, next_pos = _take_line(data, pos)
        if item_line == b"":
            if next_pos != len(data):
                raise EnvelopeError("Trailing data after envelope.")
            break
        item_header = _loads_object(item_line, "item header")
        item_type = item_header.get("type")
        if not isinstance(item_type, str):
            raise EnvelopeError("Item type is missing.")
        pos = next_pos
        length = item_header.get("length")
        if length is None:
            payload, pos = _take_line(data, pos) if pos < len(data) else (b"", pos)
        else:
            if type(length) is not int or length < 0:
                raise EnvelopeError("Invalid item length.")
            end = pos + length
            if end > len(data):
                raise EnvelopeError("Truncated item payload.")
            payload = data[pos:end]
            pos = end
            if pos < len(data):
                if data[pos] != 0x0A:
                    raise EnvelopeError("Item payload is not newline-terminated.")
                pos += 1
        items.append((item_header, payload))
    return header, items


def serialize_envelope(header: dict, items: list[tuple[dict, bytes]]) -> bytes:
    parts = [_dump(header), b"\n"]
    for item_header, payload in items:
        stamped_header = dict(item_header)
        stamped_header["length"] = len(payload)
        parts.append(_dump(stamped_header))
        parts.append(b"\n")
        parts.append(payload)
        parts.append(b"\n")
    return b"".join(parts)


def stamp_envelope(data: bytes, hotkey: str, netuid: str) -> bytes:
    header, items = parse_envelope(data)
    stamped: list[tuple[dict, bytes]] = []
    for item_header, payload in items:
        if item_header.get("type") == "event":
            payload = _stamp_event(payload, hotkey, netuid)
        stamped.append((item_header, payload))
    return serialize_envelope(header, stamped)


def _stamp_event(payload: bytes, hotkey: str, netuid: str) -> bytes:
    try:
        event = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise EnvelopeError("Invalid event payload.") from exc
    if not isinstance(event, dict):
        raise EnvelopeError("Event payload must be an object.")
    tags = event.get("tags")
    if tags is None:
        tags = {}
    elif not isinstance(tags, dict):
        raise EnvelopeError("Event tags must be an object.")
    event["tags"] = tags | {"hotkey": hotkey, "netuid": netuid}
    return _dump(event)


def _take_line(data: bytes, pos: int) -> tuple[bytes, int]:
    end = data.find(b"\n", pos)
    if end == -1:
        return data[pos:], len(data)
    return data[pos:end], end + 1


def _loads_object(raw: bytes, what: str) -> dict:
    if raw != raw.strip():
        raise EnvelopeError(f"Invalid {what}.")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EnvelopeError(f"Invalid {what}.") from exc
    if not isinstance(value, dict):
        raise EnvelopeError(f"Invalid {what}.")
    return value


def _dump(value: dict) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
