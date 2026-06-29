import os

# Hard-override so central validation (BITTENSOR_NETUIDS, PYLON_ENDPOINT) doesn't run.
# .env is still read (provides DATABASE_URL, SECRET_KEY, etc.) because ENV is not forced.
os.environ["UPSTREAM_PROMETHEUS_URL"] = ""
os.environ.setdefault("DEBUG_TOOLBAR", "False")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-prod")
# On-site mode for tests
os.environ.setdefault("CENTRAL_PROMETHEUS_PROXY_URL", "http://test-central")
os.environ.setdefault("BITTENSOR_NETUID", "12")
os.environ.setdefault("BITTENSOR_WALLET_NAME", "test-wallet")
os.environ.setdefault("BITTENSOR_WALLET_HOTKEY_NAME", "test-hotkey")

from project.settings import *  # noqa: E402,F403

PROMETHEUS_EXPORT_MIGRATIONS = False
