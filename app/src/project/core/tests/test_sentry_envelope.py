import json

import pytest

from project.core.sentry_envelope import EnvelopeError, parse_envelope, serialize_envelope, stamp_envelope

HOTKEY = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"


def create_event_item(event: dict, **headers) -> tuple[dict, bytes]:
    item_header = {"type": "event", "content_type": "application/json", **headers}
    encoded_event = json.dumps(event).encode()
    return item_header, encoded_event


def _tags(envelope: bytes) -> dict:
    _, items = parse_envelope(envelope)
    event = json.loads(items[0][1])
    return event["tags"]


def test_stamp_sets_hotkey_and_netuid():
    raw = serialize_envelope({"event_id": "abc"}, [create_event_item({"message": "boom", "level": "error"})])
    stamped = stamp_envelope(raw, HOTKEY, "12")
    assert _tags(stamped) == {"hotkey": HOTKEY, "netuid": "12"}


def test_stamp_overwrites_existing_tags():
    raw = serialize_envelope(
        {"event_id": "abc"},
        [create_event_item({"message": "boom", "tags": {"hotkey": "5Fake", "netuid": "1", "server": "a"}})],
    )
    stamped = stamp_envelope(raw, HOTKEY, "12")
    assert _tags(stamped) == {"hotkey": HOTKEY, "netuid": "12", "server": "a"}


def test_stamp_leaves_attachment_and_client_report_bytes():
    attachment = b"\xef\xbb\xbfHello\r\n"
    report = b'{"timestamp":"2020-02-07T14:16:00Z","discarded_events":[]}'
    raw = serialize_envelope(
        {"event_id": "abc"},
        [
            create_event_item({"message": "boom"}),
            ({"type": "attachment", "filename": "hello.txt"}, attachment),
            ({"type": "client_report"}, report),
        ],
    )
    _, items = parse_envelope(stamp_envelope(raw, HOTKEY, "12"))
    assert items[1][1] == attachment
    assert items[2][1] == report
    assert json.loads(items[0][1])["tags"]["hotkey"] == HOTKEY


def test_parse_length_delimited_spec_examples():
    raw = (
        b'{"event_id":"9ec79c33ec9942ab8353589fcb2e04dc"}\n'
        b'{"type":"attachment","length":10,"content_type":"text/plain","filename":"hello.txt"}\n'
        b"\xef\xbb\xbfHello\r\n\n"
        b'{"type":"event","length":41,"content_type":"application/json","filename":"application.log"}\n'
        b'{"message":"hello world","level":"error"}\n'
    )
    header, items = parse_envelope(raw)
    assert header["event_id"] == "9ec79c33ec9942ab8353589fcb2e04dc"
    assert items[0][1] == b"\xef\xbb\xbfHello\r\n"
    assert json.loads(items[1][1])["message"] == "hello world"

    omitted_newline = raw[: raw.rfind(b"\n")]
    header, items = parse_envelope(omitted_newline)
    assert items[1][1] == b'{"message":"hello world","level":"error"}'


def test_parse_implicit_length_and_empty_items():
    header, items = parse_envelope(b'{}\n{"type":"attachment"}\nhelloworld')
    assert header == {}
    assert items == [({"type": "attachment"}, b"helloworld")]

    header, items = parse_envelope(
        b'{"event_id":"abc"}\n{"type":"attachment","length":0}\n\n{"type":"attachment","length":0}\n'
    )
    assert [item[1] for item in items] == [b"", b""]


def test_round_trip_preserves_header_and_payload():
    raw = serialize_envelope(
        {"event_id": "abc", "dsn": "http://public@proxy/1"},
        [({"type": "attachment", "filename": "a.bin"}, b"line1\nline2")],
    )
    header, items = parse_envelope(raw)
    assert header["dsn"] == "http://public@proxy/1"
    assert items[0][1] == b"line1\nline2"
    assert parse_envelope(serialize_envelope(header, items)) == (header, items)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not-json\n",
        b"[]\n",
        b"{}\n[]\n",
        b'{}\n{"length":1}\n',
        b'{}\n{"type":"event","length":5}\n',
        b'{}\n{"type":"event","length":1}\nX!',
        b'{}\n{"type":"event","length":2}\n{} \n',
        b" {}\n",
    ],
)
def test_malformed_envelope_raises(raw):
    with pytest.raises(EnvelopeError):
        parse_envelope(raw)


def test_bad_event_payload_raises():
    raw = serialize_envelope({"event_id": "abc"}, [({"type": "event"}, b"not-json")])
    with pytest.raises(EnvelopeError):
        stamp_envelope(raw, HOTKEY, "12")

    for payload in (b'{"tags":["hotkey","x"]}', b'{"tags":[]}', b'{"tags":false}', b'{"tags":0}', b'{"tags":""}'):
        raw = serialize_envelope({"event_id": "abc"}, [({"type": "event"}, payload)])
        with pytest.raises(EnvelopeError):
            stamp_envelope(raw, HOTKEY, "12")
