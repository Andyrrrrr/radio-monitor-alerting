"""Hallucination gating around the ASR stage.

Load-bearing, not defensive polish (AGENTS.md): Whisper confidently invents
text on noise-only and near-silent audio — "Thank you for watching",
repeated phrases — and unguarded, that text fires watchwords. Two gates:

- **Pre-ASR** (`gate_transmission`): don't transcribe what can't be speech.
  Cheap, and it also keeps noise out of the ASR queue entirely.
- **Post-ASR** (`gate_transcript`): reject transcripts the model itself
  flagged as dubious (`no_speech_prob`, `avg_logprob`), known artifact
  phrases, and repetition loops.

Both return a rejection reason string (None = passed) so every discard is
logged with its reason — a rejected segment that was a real call is exactly
what corpus review must be able to surface (docs/architecture.md §5.4).
Pure functions over config: no I/O, fully testable.
"""

from vhfwatch.config import AsrConfig, HallucinationConfig
from vhfwatch.models import Transcript, Transmission


def gate_transmission(tx: Transmission, cfg: HallucinationConfig) -> str | None:
    """Pre-ASR gate. Returns a rejection reason, or None to transcribe.

    Duration gating already happened in the segmenter (min_duration_ms), so
    the job here is SNR. A missing SNR estimate (no noise floor measured
    yet, e.g. the first transmission after startup) passes through — biasing
    toward transcribing unknowns rather than silently skipping a real call.
    """
    if tx.est_snr_db is not None and tx.est_snr_db < cfg.min_snr_db:
        return f"snr {tx.est_snr_db:.1f} dB below min {cfg.min_snr_db:.1f} dB"
    return None


def gate_transcript(
    t: Transcript, cfg: HallucinationConfig, asr_cfg: AsrConfig
) -> str | None:
    """Post-ASR gate. Returns a rejection reason, or None to keep.

    The no_speech/logprob thresholds are the same values the Whisper decoder
    was configured with — the decoder applies them per-segment internally,
    but the merged whole-transmission stats still need checking because a
    single hallucinated segment inside a real transmission passes the
    decoder's own filter.
    """
    text = t.text.strip()
    if not text:
        return "empty transcript"

    if t.no_speech_prob is not None and t.no_speech_prob > asr_cfg.no_speech_threshold:
        return (
            f"no_speech_prob {t.no_speech_prob:.2f} above "
            f"{asr_cfg.no_speech_threshold:.2f}"
        )
    if t.avg_logprob is not None and t.avg_logprob < asr_cfg.logprob_threshold:
        return f"avg_logprob {t.avg_logprob:.2f} below {asr_cfg.logprob_threshold:.2f}"

    lowered = text.lower()
    for artifact in cfg.blocklist:
        if artifact in lowered:
            return f"blocklisted artifact: {artifact!r}"

    repeated = _max_consecutive_repeat(lowered.split())
    if repeated > cfg.max_repeat_tokens:
        return f"token repeated {repeated}x consecutively"

    return None


def _max_consecutive_repeat(tokens: list[str]) -> int:
    """Longest run of one token. "mayday mayday mayday" (3) is real distress
    procedure; the loops Whisper produces on noise run far longer."""
    best = 0
    run = 0
    prev: str | None = None
    for tok in tokens:
        run = run + 1 if tok == prev else 1
        prev = tok
        best = max(best, run)
    return best
