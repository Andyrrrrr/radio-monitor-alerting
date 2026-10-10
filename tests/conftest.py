"""Test-suite-wide guards."""

import pytest

# Every credential the system reads from the environment. The operator keeps
# real ones in a sourced env file, and a test that builds default channels
# would otherwise SEND REAL MESSAGES to a real phone the moment someone runs
# the suite from that shell. Live sources now announce themselves on start,
# which makes that easy to trigger by accident.
_SECRETS = (
    "VHFWATCH_PUSHOVER_TOKEN",
    "VHFWATCH_PUSHOVER_USER",
    "VHFWATCH_TELEGRAM_TOKEN",
    "VHFWATCH_TELEGRAM_CHAT_ID",
    "VHFWATCH_ANTHROPIC_API_KEY",
    "VHFWATCH_DEEPGRAM_API_KEY",
)


@pytest.fixture(autouse=True)
def hermetic_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _SECRETS:
        monkeypatch.delenv(name, raising=False)
