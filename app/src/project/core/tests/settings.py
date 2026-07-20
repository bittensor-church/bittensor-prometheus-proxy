import os

# Hard-override so tests are hermetic and can't be swayed by variables the developer
# has exported locally (e.g. for running the app against a real backend).
# .env is still read (provides DATABASE_URL, etc.) because ENV is not forced.
os.environ["UPSTREAM_PROMETHEUS_URL"] = ""
os.environ["UPSTREAM_TEMPO_URL"] = ""
os.environ["CENTRAL_TEMPO_PROXY_URL"] = ""
os.environ["CENTRAL_SENTRY_PROXY_URL"] = ""
os.environ["DEBUG_TOOLBAR"] = "False"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-prod"
# On-site mode for tests
os.environ["CENTRAL_PROMETHEUS_PROXY_URL"] = "http://test-central"
os.environ["BITTENSOR_NETUID"] = "12"
os.environ["BITTENSOR_WALLET_NAME"] = "test-wallet"
os.environ["BITTENSOR_WALLET_HOTKEY_NAME"] = "test-hotkey"

from project.settings import *  # noqa: E402,F403

PROMETHEUS_EXPORT_MIGRATIONS = False

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}
