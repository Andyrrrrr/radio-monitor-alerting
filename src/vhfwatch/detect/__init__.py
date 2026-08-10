"""Detection: hallucination gating, Tier 1 watchwords, Tier 2 LLM."""

from vhfwatch.detect.classify import Classifier
from vhfwatch.detect.detector import Detector
from vhfwatch.detect.hallucination import gate_transcript, gate_transmission
from vhfwatch.detect.watchwords import WatchwordMatcher

__all__ = [
    "Classifier",
    "Detector",
    "WatchwordMatcher",
    "gate_transcript",
    "gate_transmission",
]
