"""Typed configuration: TOML file plus VHFWATCH_* environment overrides.

The sections here mirror config/config.example.toml one-to-one, with
`extra="forbid"` so a typo'd key in someone's config.toml fails loudly at
startup instead of being silently ignored. If you add a field here, add it
to the example file with a comment in the same change — the template and
this module must stay in sync (docs/conventions.md §2).

Precedence, highest first:
  1. VHFWATCH_* environment variables (nested with "__",
     e.g. VHFWATCH_SEGMENTER__HANG_MS=500)
  2. the TOML file
  3. the defaults below (which match the example file)

Secrets are env-only and are NOT modeled here until the features that need
them land — never add a secret to the TOML schema.
"""

import tomllib
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

DEFAULT_CONFIG_PATH = Path("config/config.toml")


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CalibrationConfig(_Section):
    # Measured by scripts/calibrate.py — per-machine, never copied between
    # rigs (docs/decisions.md D11).
    noise_floor_dbfs: float = -45.0
    drift_warn_db: float = 6.0


class GeneralConfig(_Section):
    channel: str = "16"  # label only; the radio is tuned by hand
    site_name: str = "poc-desk"
    local_timezone: str = "America/Los_Angeles"  # display only; DB is UTC
    data_dir: Path = Path("data")


class AudioConfig(_Section):
    source: Literal["live", "file", "directory"] = "live"
    device: str = ""  # substring match on input device name; "" = default
    input_channels: int = 2
    use_channel: int = 0  # slice, never average (docs/hardware.md §3.7)
    input_gain_db: float = 0.0  # post-capture digital gain; cannot fix clipping
    capture_rate: int = 48000  # native device rate
    target_rate: int = 16000  # everything downstream; Whisper wants 16 kHz
    blocksize: int = 1024
    calibration: CalibrationConfig = CalibrationConfig()


class SegmenterConfig(_Section):
    frame_ms: int = 20
    open_threshold_db: float = -42.0  # ≈ noise_floor + 10 dB (squelch closed)
    close_threshold_db: float = -48.0  # hysteresis: must be lower than open
    open_frames: int = 2  # consecutive frames above threshold to open
    hang_ms: int = 800  # below threshold before closing
    preroll_ms: int = 300  # MANDATORY — squelch clips first syllables
    min_duration_ms: int = 600  # discard shorter
    max_duration_ms: int = 120000  # force close a stuck transmitter
    clip_warn_dbfs: float = -0.5
    noise_ema_alpha: float = 0.05  # noise-floor smoothing; higher = adapts faster

    @model_validator(mode="after")
    def _hysteresis_holds(self) -> Self:
        # Equal thresholds mean no hysteresis at all, so boundary chatter
        # would fragment transmissions — the exact failure hang time exists
        # to prevent (docs/architecture.md §5.2).
        if self.close_threshold_db >= self.open_threshold_db:
            raise ValueError(
                "close_threshold_db must be below open_threshold_db "
                f"(got close={self.close_threshold_db}, open={self.open_threshold_db})"
            )
        return self


class AsrConfig(_Section):
    engine: Literal["mlx", "faster_whisper", "deepgram"] = "mlx"
    model: str = "small.en"
    temperature: float = 0.0  # determinism for tuning runs
    beam_size: int = 5
    condition_on_previous_text: bool = False  # stops hallucination loops
    no_speech_threshold: float = 0.6
    logprob_threshold: float = -1.0
    initial_prompt: str = (
        "Marine VHF radio traffic on channel 16. Mayday, pan-pan, securite, "
        "Coast Guard, vessel, motor vessel, sailing vessel, position, "
        "latitude, longitude, persons on board, taking on water, "
        "man overboard, abandoning ship, over, out, standing by, radio check."
    )


class HallucinationConfig(_Section):
    min_snr_db: float = 3.0  # skip ASR below this
    max_repeat_tokens: int = 3  # same token repeated more than this = reject
    blocklist: list[str] = [
        "thank you for watching",
        "thanks for watching",
        "please subscribe",
        "subtitles by",
        "amara.org",
        "transcription by",
        "[music]",
        "[applause]",
        "[inaudible]",
    ]


class DetectConfig(_Section):
    fuzzy_min_ratio: float = 0.82
    llm_min_words: int = 8  # classify longer transcripts even with no match
    llm_timeout_s: float = 5.0
    llm_model: str = "claude-haiku-4-5-20251001"
    llm_context_count: int = 3  # preceding transmissions passed as context


class CorrelatorConfig(_Section):
    quiet_period_s: int = 300  # no activity → close incident
    vessel_match_ratio: float = 0.80
    max_incident_duration_s: int = 3600


class PushoverConfig(_Section):
    priority_critical: int = 2  # emergency: bypasses DND, re-alerts until acked
    priority_urgent: int = 1
    retry_s: int = 60
    expire_s: int = 1800


class AlertingConfig(_Section):
    channels_critical: list[str] = ["console", "macos", "pushover"]
    channels_urgent: list[str] = ["console", "macos", "pushover"]
    channels_watch: list[str] = ["console"]
    channels_routine: list[str] = []
    escalate_after_s: int = 60  # no ack → escalate
    dedupe_window_s: int = 300
    pushover: PushoverConfig = PushoverConfig()


class WebConfig(_Section):
    host: str = "127.0.0.1"
    port: int = 8080
    base_url: str = "http://127.0.0.1:8080"  # used to build alert deep links
    token_ttl_days: int = 365


class StorageConfig(_Section):
    archive_codec: str = "opus"  # archival
    archive_bitrate: str = "24k"
    playback_codec: str = "aac"  # generated at ingest — iOS Safari compat
    retention_days: int = 90  # 0 = keep forever; set deliberately


class HealthConfig(_Section):
    no_audio_alert_hours: int = 6  # total silence on Ch 16 means something broke
    heartbeat_timeout_s: int = 120
    self_test_hour: int = 9  # local hour for daily end-to-end test
    min_free_disk_gb: int = 5


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VHFWATCH_",
        env_nested_delimiter="__",
        extra="forbid",
    )

    general: GeneralConfig = GeneralConfig()
    audio: AudioConfig = AudioConfig()
    segmenter: SegmenterConfig = SegmenterConfig()
    asr: AsrConfig = AsrConfig()
    hallucination: HallucinationConfig = HallucinationConfig()
    detect: DetectConfig = DetectConfig()
    correlator: CorrelatorConfig = CorrelatorConfig()
    alerting: AlertingConfig = AlertingConfig()
    web: WebConfig = WebConfig()
    storage: StorageConfig = StorageConfig()
    health: HealthConfig = HealthConfig()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Env before init: load_config() passes the parsed TOML as init
        # kwargs, and env vars must override the file. Sources are
        # deep-merged, so VHFWATCH_SEGMENTER__HANG_MS overrides just that
        # one key, not the whole [segmenter] table.
        return (env_settings, init_settings)


def load_config(path: Path | None = None) -> Settings:
    """Load settings from a TOML file with env var overrides.

    An explicitly-passed path must exist — a typo'd --config that silently
    falls back to defaults is exactly the kind of quiet failure this project
    treats as the worst outcome. Only the *default* path may be absent
    (fresh checkout, nothing copied from the example yet), in which case the
    documented defaults apply.
    """
    if path is None:
        path = DEFAULT_CONFIG_PATH
        if not path.exists():
            return Settings()
    elif not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")

    with path.open("rb") as f:
        data = tomllib.load(f)
    return Settings(**data)
