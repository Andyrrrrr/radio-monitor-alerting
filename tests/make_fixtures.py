"""Regenerate the committed synthetic fixtures.

    python -m tests.make_fixtures

Fixtures are committed so the roadmap's exit-criterion command works on a
fresh clone; this script is committed so anyone can see exactly what's in
them and that no real radio traffic is involved (AGENTS.md constraint 8).
"""

from pathlib import Path

import numpy as np

from tests.synth import silence, tone

FIXTURES = Path(__file__).parent / "fixtures"


def sample_wav() -> np.ndarray:
    """Two 'transmissions' against the default segmenter thresholds:

    1. 500–3100 ms: speech with a 400 ms mid-sentence pause (< 800 ms hang,
       so it must stay one transmission),
    2. 5100–5900 ms: a short reply on a different pitch.

    Total 7.4 s. Tones stand in for speech; levels are -20 dBFS against the
    default -42 dB open threshold.
    """
    return np.concatenate(
        [
            silence(500),
            tone(1200, -20.0, freq=400.0),
            silence(400),  # breath pause — must NOT split
            tone(1000, -20.0, freq=400.0),
            silence(2000),  # real gap — must split
            tone(800, -20.0, freq=600.0),
            silence(1500),
        ]
    )


def main() -> None:
    from tests.synth import write_wav16

    FIXTURES.mkdir(exist_ok=True)
    write_wav16(FIXTURES / "sample.wav", sample_wav())
    print(f"wrote {FIXTURES / 'sample.wav'}")


if __name__ == "__main__":
    main()
