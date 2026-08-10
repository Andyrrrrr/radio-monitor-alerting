"""Tier 2 classifier: graceful degradation is the contract under test.

The real LLM is never called here — what matters for the alert path is that
every failure mode (no key, exception, timeout, empty response) returns
None instead of raising (AGENTS.md constraint 6). The prompt's quality is
tuned against the corpus, not unit tests.
"""

import asyncio
from typing import Any

import pytest

from vhfwatch.config import DetectConfig
from vhfwatch.detect.classify import Classifier, LLMAssessment


def make_assessment(**overrides: object) -> LLMAssessment:
    fields: dict[str, Any] = {
        "severity": "critical",
        "confidence": 0.9,
        "nature_of_emergency": "vessel taking on water",
        "vessel_name": "Serenity",
        "vessel_description": None,
        "position_as_stated": None,
        "latitude": None,
        "longitude": None,
        "persons_aboard": 3,
        "injuries_reported": None,
        "verbatim_quote": "mayday mayday this is serenity",
        "reasoning": "explicit mayday",
    }
    fields.update(overrides)
    return LLMAssessment(**fields)


class _FakeMessages:
    def __init__(
        self, result: object = None, error: Exception | None = None, delay: float = 0
    ):
        self._result = result
        self._error = error
        self._delay = delay

    async def parse(self, **kwargs: object) -> object:
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error

        class Response:
            parsed_output = self._result

        return Response()


class _FakeClient:
    def __init__(self, messages: _FakeMessages):
        self.messages = messages


def make_classifier(
    messages: _FakeMessages | None, cfg: DetectConfig | None = None
) -> Classifier:
    c = Classifier.__new__(Classifier)
    c._cfg = cfg or DetectConfig()
    c._client = _FakeClient(messages) if messages is not None else None
    return c


def test_no_api_key_means_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VHFWATCH_ANTHROPIC_API_KEY", raising=False)
    classifier = Classifier(DetectConfig())
    assert classifier.available is False
    assert asyncio.run(classifier.classify("mayday mayday")) is None


def test_successful_classification() -> None:
    classifier = make_classifier(_FakeMessages(result=make_assessment()))
    result = asyncio.run(classifier.classify("mayday", context=["earlier traffic"]))
    assert result is not None
    assert result.severity == "critical"
    summary = result.to_summary()
    assert summary.vessel_name == "Serenity"
    assert summary.persons_aboard == 3


def test_api_error_degrades_to_none() -> None:
    classifier = make_classifier(
        _FakeMessages(error=RuntimeError("connection refused"))
    )
    assert asyncio.run(classifier.classify("mayday")) is None


def test_timeout_degrades_to_none() -> None:
    cfg = DetectConfig(llm_timeout_s=0.05)
    classifier = make_classifier(
        _FakeMessages(result=make_assessment(), delay=1.0), cfg
    )
    assert asyncio.run(classifier.classify("mayday")) is None


def test_empty_response_degrades_to_none() -> None:
    classifier = make_classifier(_FakeMessages(result=None))
    assert asyncio.run(classifier.classify("mayday")) is None
