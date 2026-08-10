"""Tier 2 detection: LLM classification of transcripts.

The LLM adds semantic understanding Tier 1 can't have — "we're taking on
water fast, three people aboard" contains no watchword — but it is a
convenience layered on evidence, never a requirement (AGENTS.md constraint
6). Every failure mode here (no API key, network down, timeout, bad
response) degrades to returning None, and the caller alerts on the Tier 1
result alone. Nothing in this module may block or raise into the alert path.

The prompt's core rule is **never infer**: report only what was stated,
null for everything else. A fabricated latitude sent to a rescuer is far
worse than a null one (docs/architecture.md §5.5).
"""

import asyncio
import os
from typing import Literal

import structlog
from pydantic import BaseModel, ConfigDict

from vhfwatch.config import DetectConfig
from vhfwatch.models import IncidentSummary

logger = structlog.get_logger(__name__)

_API_KEY_ENV = "VHFWATCH_ANTHROPIC_API_KEY"

_SYSTEM_PROMPT = """\
You classify marine VHF channel 16 radio transcripts for a distress-monitoring
system. The transcripts come from automatic speech recognition of noisy radio
audio and often contain transcription errors.

Rules, in priority order:
1. Report ONLY what is stated in the transcripts. Use null for anything not
   explicitly stated. NEVER infer a position, vessel name, or casualty count —
   a fabricated value could misdirect a rescue.
2. verbatim_quote must be copied exactly from the transcript text, choosing
   the span that best supports your assessment.
3. A Coast Guard MAYDAY RELAY broadcast is genuine distress traffic, not a
   false alarm.
4. Radio checks, routine position reports, marina chatter, and drills
   ("this is a drill", "exercise") are routine.
5. severity meanings: critical = explicit or clearly implied distress
   (mayday, sinking, abandoning ship); urgent = urgency traffic or distress
   semantics without the keyword (pan-pan, man overboard, taking on water,
   fire, medical); watch = ambiguous but worth a human glance; routine =
   everything else.
6. confidence is YOUR confidence in the severity call, 0 to 1, considering
   how garbled the transcript is."""


class LLMAssessment(BaseModel):
    """Structured output schema for the classifier.

    Mirrors IncidentSummary plus the severity fields the detector needs.
    Kept as a separate pydantic model (not the dataclass) because this is
    the wire contract with the LLM, validated by the SDK.
    """

    model_config = ConfigDict(extra="forbid")

    severity: Literal["critical", "urgent", "watch", "routine"]
    confidence: float
    nature_of_emergency: str | None
    vessel_name: str | None
    vessel_description: str | None
    position_as_stated: str | None
    latitude: float | None
    longitude: float | None
    persons_aboard: int | None
    injuries_reported: str | None
    verbatim_quote: str
    reasoning: str

    def to_summary(self) -> IncidentSummary:
        return IncidentSummary(
            nature_of_emergency=self.nature_of_emergency,
            vessel_name=self.vessel_name,
            vessel_description=self.vessel_description,
            position_as_stated=self.position_as_stated,
            latitude=self.latitude,
            longitude=self.longitude,
            persons_aboard=self.persons_aboard,
            injuries_reported=self.injuries_reported,
            verbatim_quote=self.verbatim_quote,
            reasoning=self.reasoning,
        )


class Classifier:
    """Anthropic-backed Tier 2 classifier with hard graceful degradation.

    `available` is False when there's no API key — the pipeline runs
    Tier-1-only and says so once at startup instead of erroring per
    transmission (local-first: the network is an optional upgrade).
    """

    def __init__(self, cfg: DetectConfig) -> None:
        self._cfg = cfg
        api_key = os.environ.get(_API_KEY_ENV)
        self._client = None
        if api_key:
            # Imported lazily so offline installs never need the package
            # at import time; anthropic is in the base deps but keeping the
            # constructor the only touchpoint makes the degradation obvious.
            import anthropic

            # max_retries=0: the whole call has a ~5 s budget in the alert
            # path; a retry would blow it. A failed classification is fine.
            self._client = anthropic.AsyncAnthropic(
                api_key=api_key, timeout=cfg.llm_timeout_s, max_retries=0
            )
        else:
            logger.info(
                "classify.disabled",
                reason=f"{_API_KEY_ENV} not set — running Tier 1 only",
            )

    @property
    def available(self) -> bool:
        return self._client is not None

    async def classify(
        self, text: str, context: list[str] | None = None
    ) -> LLMAssessment | None:
        """Classify one transcript; None means classification unavailable.

        `context` is the preceding few transcripts, oldest first — distress
        traffic is a conversation, and a 4-second clip alone is often
        uninterpretable. On ANY failure this returns None and logs why;
        the caller must treat None as "Tier 1 stands alone", never as
        "not distress".
        """
        if self._client is None:
            return None

        parts: list[str] = []
        if context:
            joined = "\n".join(f"- {c}" for c in context)
            parts.append(f"Preceding transmissions (oldest first):\n{joined}")
        parts.append(f"Transmission to classify:\n{text}")
        prompt = "\n\n".join(parts)

        try:
            # wait_for is belt-and-braces over the client timeout: the alert
            # must not wait on a stuck connection (constraint 6).
            response = await asyncio.wait_for(
                self._client.messages.parse(
                    model=self._cfg.llm_model,
                    max_tokens=1024,
                    system=_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                    output_format=LLMAssessment,
                ),
                timeout=self._cfg.llm_timeout_s,
            )
        except Exception as e:  # noqa: BLE001 — degradation IS the contract here
            logger.warning(
                "classify.failed",
                error=str(e),
                error_type=type(e).__name__,
            )
            return None

        assessment = response.parsed_output
        if assessment is None:
            logger.warning("classify.failed", error="no parsed output in response")
            return None
        logger.debug(
            "classify.result",
            severity=assessment.severity,
            confidence=assessment.confidence,
        )
        return assessment
