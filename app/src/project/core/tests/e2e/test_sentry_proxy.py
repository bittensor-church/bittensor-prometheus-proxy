import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

HERE = Path(__file__).resolve().parent
COMPOSE_FILE = HERE / "docker-compose.yml"
SENDER = HERE / "send_event.py"
TARGET_DSN = "E2E_TESTS_SENTRY_TARGET_DSN"


def _compose() -> list[str]:
    return ["env", f"{TARGET_DSN}={os.environ[TARGET_DSN]}", "docker", "compose", "-f", str(COMPOSE_FILE)]


def test_sentry_event_reaches_upstream() -> None:
    assert os.environ.get(TARGET_DSN), f"{TARGET_DSN} is required"
    compose = _compose()
    up = subprocess.run([*compose, "up", "--build", "--detach", "--wait"], check=False)
    failed = up.returncode != 0
    try:
        assert up.returncode == 0
        sent = subprocess.run([sys.executable, str(SENDER)], check=False, capture_output=True, text=True)
        print(sent.stdout, file=sys.stderr)
        print(sent.stderr, file=sys.stderr)
        failed = "accepted=yes" not in sent.stdout or "Something" not in sent.stderr
        assert not failed, sent.stdout + sent.stderr
    finally:
        if failed:
            subprocess.run([*compose, "logs", "--no-color"], check=False)
        subprocess.run([*compose, "down", "-v", "--remove-orphans"], check=False)
