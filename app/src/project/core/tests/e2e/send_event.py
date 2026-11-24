import json
import uuid

import sentry_sdk
from sentry_sdk.transport import HttpTransport

DSN = "http://public@127.0.0.1:18000/1"


class Something(Exception):
    pass


class PrintingTransport(HttpTransport):
    def _handle_response(self, response, envelope=None) -> None:
        body = response.data if isinstance(getattr(response, "data", None), bytes) else b""
        event = envelope.get_event() if envelope is not None else None
        event_id = str((event or {}).get("event_id", "")).replace("-", "").lower()
        try:
            returned = str(json.loads(body).get("id", "")).replace("-", "").lower()
        except ValueError:
            returned = ""
        print(f"status={response.status}")
        print(body.decode(errors="replace"))
        print("accepted=yes" if response.status == 200 and returned == event_id and event_id else "accepted=no")
        super()._handle_response(response, envelope)


def main() -> None:
    sentry_sdk.init(dsn=DSN, transport=PrintingTransport, auto_session_tracking=False)
    raise Something(f"bittensor-prometheus-proxy e2e {uuid.uuid4().hex}")


if __name__ == "__main__":
    main()
