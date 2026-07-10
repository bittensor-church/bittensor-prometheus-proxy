from unittest.mock import MagicMock, patch

from project.core.contact.sentry import SentryContact


def test_forward_uses_string_netuid_key() -> None:
    dsn = "https://public@example.com/1"
    with (
        patch("project.core.contact.sentry.config") as mock_config,
        patch("project.core.contact.sentry.session.post") as mock_post,
    ):
        mock_config.UPSTREAM_SENTRY_DSNS = {"12": dsn}
        mock_post.return_value = MagicMock()
        SentryContact().forward(b"{}", {}, netuid=12)
    assert mock_post.called
