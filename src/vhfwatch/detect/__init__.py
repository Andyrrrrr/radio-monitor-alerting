"""Detection: hallucination gating, Tier 1 watchwords, Tier 2 LLM."""

from vhfwatch.detect.classify import Classifier
from vhfwatch.detect.detector import Detector
from vhfwatch.detect.hallucination import (
    gate_transcript,
    gate_transmission,
    unrunnable_checks,
)
from vhfwatch.detect.places import PlaceMatcher
from vhfwatch.detect.watchwords import WatchwordMatcher

__all__ = [
    "Classifier",
    "Detector",
    "PlaceMatcher",
    "WatchwordMatcher",
    "gate_transcript",
    "gate_transmission",
    "unrunnable_checks",
]
