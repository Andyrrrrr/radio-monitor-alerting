"""Config loading: TOML, env override, and the guardrail validators.

These tests double as the sync check between config.py and
config/config.example.toml — if a key is renamed in one place but not the
other, test_example_config_loads fails (extra="forbid").
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from vhfwatch.config import Settings, load_config

EXAMPLE = Path(__file__).parent.parent / "config" / "config.example.toml"


def test_example_config_loads() -> None:
    cfg = load_config(EXAMPLE)
    # Spot-check one value per depth: top-level section, nested section.
    assert cfg.segmenter.preroll_ms == 300
    assert cfg.audio.use_channel == 0
    assert cfg.audio.calibration.noise_floor_dbfs == -45.0
    assert cfg.general.data_dir == Path("data")


def test_env_overrides_toml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VHFWATCH_SEGMENTER__HANG_MS", "500")
    cfg = load_config(EXAMPLE)
    assert cfg.segmenter.hang_ms == 500
    # The rest of the [segmenter] table still comes from the file.
    assert cfg.segmenter.preroll_ms == 300


def test_missing_explicit_path_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_config(Path("config/no-such-file.toml"))


def test_missing_default_path_falls_back_to_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Fresh checkout: nothing copied from the example yet.
    monkeypatch.chdir(tmp_path)
    cfg = load_config()
    assert cfg.segmenter.preroll_ms == 300


def test_unknown_key_rejected(tmp_path: Path) -> None:
    # A typo'd key must fail at startup, not be silently ignored.
    bad = tmp_path / "config.toml"
    bad.write_text("[segmenter]\nhang_time_ms = 800\n")
    with pytest.raises(ValidationError):
        load_config(bad)


def test_hysteresis_must_hold() -> None:
    with pytest.raises(ValidationError, match="close_threshold_db"):
        Settings(
            segmenter={"open_threshold_db": -42.0, "close_threshold_db": -42.0}  # type: ignore[arg-type]
        )
