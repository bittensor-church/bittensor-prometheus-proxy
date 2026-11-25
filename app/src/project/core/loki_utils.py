class LokiPushError(Exception):
    pass


def stamp_push(data: bytes, hotkey: str, netuid: str) -> bytes:
    stamped = []
    for number, wire_type, value in _iter_fields(data):
        if number == 1 and wire_type == 2:
            value = _stamp_stream(value, hotkey, netuid)
        stamped.append(_encode_field(number, wire_type, value))
    return b"".join(stamped)


def stream_labels(data: bytes) -> list[str]:
    labels = []
    for number, wire_type, value in _iter_fields(data):
        if number == 1 and wire_type == 2:
            labels.append(_stream_labels(value))
    return labels


def _stamp_stream(stream: bytes, hotkey: str, netuid: str) -> bytes:
    labels = "{}"
    rest = []
    for number, wire_type, value in _iter_fields(stream):
        if number == 1 and wire_type == 2:
            labels = _decode_labels(value)
        else:
            rest.append(_encode_field(number, wire_type, value))
    parsed = _parse_labels(labels)
    parsed["hotkey"] = hotkey
    parsed["netuid"] = netuid
    return _encode_field(1, 2, _format_labels(parsed).encode()) + b"".join(rest)


def _stream_labels(stream: bytes) -> str:
    labels = "{}"
    for number, wire_type, value in _iter_fields(stream):
        if number == 1 and wire_type == 2:
            labels = _decode_labels(value)
    return labels


def _decode_labels(value: bytes) -> str:
    try:
        return value.decode()
    except UnicodeDecodeError as exc:
        raise LokiPushError("invalid stream labels") from exc


def _parse_labels(text: str) -> dict[str, str]:
    if len(text) < 2 or text[0] != "{" or text[-1] != "}":
        raise LokiPushError("invalid stream labels")
    body = text[1:-1].strip()
    labels: dict[str, str] = {}
    index = 0
    while index < len(body):
        while index < len(body) and body[index] == " ":
            index += 1
        if index >= len(body):
            break
        start = index
        if not _name_start(body[index]):
            raise LokiPushError("invalid stream labels")
        index += 1
        while index < len(body) and _name_cont(body[index]):
            index += 1
        name = body[start:index]
        if index >= len(body) or body[index] != "=":
            raise LokiPushError("invalid stream labels")
        index += 1
        if index >= len(body) or body[index] != '"':
            raise LokiPushError("invalid stream labels")
        index += 1
        chars: list[str] = []
        closed = False
        while index < len(body):
            char = body[index]
            index += 1
            if char == "\\":
                unescaped, index = _unescape(body, index)
                chars.append(unescaped)
            elif char == '"':
                closed = True
                break
            else:
                chars.append(char)
        if not closed:
            raise LokiPushError("invalid stream labels")
        labels[name] = "".join(chars)
        while index < len(body) and body[index] == " ":
            index += 1
        if index < len(body):
            if body[index] != ",":
                raise LokiPushError("invalid stream labels")
            index += 1
    return labels


def _format_labels(labels: dict[str, str]) -> str:
    return "{" + ", ".join(f"{name}={_quote(labels[name])}" for name in sorted(labels)) + "}"


def _quote(value: str) -> str:
    chars = ['"']
    for char in value:
        if char in {"\\", '"', "\n", "\r", "\t", "\a", "\b", "\f", "\v"}:
            chars.append(f"\\{char}")
        elif char.isprintable():
            chars.append(char)
        else:
            code = ord(char)
            if code < 0x100:
                chars.append(f"\\x{code:02x}")
            elif code < 0x10000:
                chars.append(f"\\u{code:04x}")
            else:
                chars.append(f"\\U{code:08x}")
    chars.append('"')
    return "".join(chars)


def _unescape(body: str, index: int) -> tuple[str, int]:
    if index >= len(body):
        raise LokiPushError("invalid stream labels")
    char = body[index]
    index += 1
    simple = {
        "\\": "\\",
        '"': '"',
        "'": "'",
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "a": "\a",
        "b": "\b",
        "f": "\f",
        "v": "\v",
    }
    if char in simple:
        return simple[char], index
    width = {"x": 2, "u": 4, "U": 8}.get(char)
    if width is not None:
        digits = body[index : index + width]
        if len(digits) != width:
            raise LokiPushError("invalid stream labels")
        try:
            return chr(int(digits, 16)), index + width
        except ValueError as exc:
            raise LokiPushError("invalid stream labels") from exc
    if char in "01234567":
        digits = char
        while len(digits) < 3 and index < len(body) and body[index] in "01234567":
            digits += body[index]
            index += 1
        return chr(int(digits, 8)), index
    raise LokiPushError("invalid stream labels")


def _name_start(char: str) -> bool:
    return char.isascii() and (char.isalpha() or char == "_")


def _name_cont(char: str) -> bool:
    return char.isascii() and (char.isalnum() or char == "_")


def _iter_fields(data: bytes):
    index = 0
    size = len(data)
    while index < size:
        key, index = _read_varint(data, index)
        number = key >> 3
        wire_type = key & 7
        if number <= 0:
            raise LokiPushError("invalid protobuf")
        if wire_type == 0:
            start = index
            _, index = _read_varint(data, index)
            yield number, wire_type, data[start:index]
        elif wire_type == 1:
            yield number, wire_type, _take(data, index, 8)
            index += 8
        elif wire_type == 2:
            length, index = _read_varint(data, index)
            yield number, wire_type, _take(data, index, length)
            index += length
        elif wire_type == 5:
            yield number, wire_type, _take(data, index, 4)
            index += 4
        else:
            raise LokiPushError("invalid protobuf")


def _take(data: bytes, index: int, length: int) -> bytes:
    end = index + length
    if length < 0 or end > len(data):
        raise LokiPushError("invalid protobuf")
    return data[index:end]


def _read_varint(data: bytes, index: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if index >= len(data) or shift > 63:
            raise LokiPushError("invalid protobuf")
        byte = data[index]
        index += 1
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            return result, index
        shift += 7


def _encode_field(number: int, wire_type: int, value: bytes) -> bytes:
    encoded = _write_varint((number << 3) | wire_type)
    if wire_type == 2:
        return encoded + _write_varint(len(value)) + value
    return encoded + value


def _write_varint(value: int) -> bytes:
    encoded = bytearray()
    while value > 0x7F:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)
