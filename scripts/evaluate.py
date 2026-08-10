#!/usr/bin/env python
"""Precision/recall over a hand-labeled corpus — THE tuning instrument.

Every threshold change should be justified by a before/after from this
script, not by how the change felt (docs/conventions.md §6).

Labels live in data/labels.jsonl, one JSON object per line:

    {"file": "20260810-193000Z-ch16.wav", "label": "distress"}
    {"file": "20260810-194500Z-ch16.wav", "label": "routine", "notes": "radio check"}

Labels: "distress" (should alert CRITICAL), "urgency" (should alert
URGENT), "routine" (must not alert). A file counts as a predicted positive
when any detection reaches URGENT or above — the notification bar.

    python scripts/evaluate.py --corpus data/corpus --labels data/labels.jsonl
"""

import argparse
import asyncio
import json
import tempfile
from collections import Counter
from pathlib import Path

from vhfwatch import pipeline
from vhfwatch.alerting.router import AlertRouter
from vhfwatch.asr import create_transcriber
from vhfwatch.audio.sources import FileAudioSource
from vhfwatch.config import AlertingConfig, load_config
from vhfwatch.log import configure_logging
from vhfwatch.store import Database

POSITIVE_LABELS = {"distress", "urgency"}
ALERTING_SEVERITIES = {"critical", "urgent"}


def load_labels(path: Path) -> list[dict[str, str]]:
    rows = []
    with path.open() as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("label") not in POSITIVE_LABELS | {"routine"}:
                raise SystemExit(
                    f"{path}:{line_no}: label must be distress/urgency/routine"
                )
            rows.append(row)
    return rows


async def evaluate_file(
    cfg, corpus: Path, filename: str, transcriber
) -> list[tuple[str, list[str]]]:
    """Run one file through the pipeline; returns (severity, terms) per
    detection. Uses a throwaway data dir so evaluation never pollutes the
    real store."""
    with tempfile.TemporaryDirectory() as tmp:
        run_cfg = cfg.model_copy(deep=True)
        run_cfg.general.data_dir = Path(tmp)
        source = FileAudioSource(
            corpus / filename,
            blocksize=cfg.audio.blocksize,
            channel=cfg.audio.use_channel,
        )
        # No alert channels: evaluation must never page anyone.
        router = AlertRouter(
            AlertingConfig(channels_critical=[], channels_urgent=[]), {}
        )
        await pipeline.run(run_cfg, source, transcriber, router)

        with Database(Path(tmp) / "vhfwatch.db") as db:
            rows = db._conn.execute(
                "SELECT severity, matched_terms FROM detection"
            ).fetchall()
        return [(r["severity"], json.loads(r["matched_terms"])) for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=Path("data/corpus"))
    parser.add_argument("--labels", type=Path, default=Path("data/labels.jsonl"))
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--log-level", default="warning")
    args = parser.parse_args()

    configure_logging(args.log_level)
    cfg = load_config(args.config)
    labels = load_labels(args.labels)
    if not labels:
        raise SystemExit(f"no labels in {args.labels}")

    transcriber = create_transcriber(cfg.asr)

    tp = fp = fn = tn = 0
    fp_terms: Counter[str] = Counter()
    mismatches: list[str] = []

    for row in labels:
        filename, label = row["file"], row["label"]
        if not (args.corpus / filename).exists():
            print(f"!! missing file, skipped: {filename}")
            continue
        detections = asyncio.run(evaluate_file(cfg, args.corpus, filename, transcriber))
        predicted = any(sev in ALERTING_SEVERITIES for sev, _ in detections)
        expected = label in POSITIVE_LABELS

        if predicted and expected:
            tp += 1
        elif predicted and not expected:
            fp += 1
            for _, terms in detections:
                fp_terms.update(terms)
            mismatches.append(f"FALSE POSITIVE  {filename} ({row.get('notes', '')})")
        elif not predicted and expected:
            fn += 1
            mismatches.append(f"MISSED          {filename} ({row.get('notes', '')})")
        else:
            tn += 1

    total = tp + fp + fn + tn
    precision = tp / (tp + fp) if tp + fp else float("nan")
    recall = tp / (tp + fn) if tp + fn else float("nan")

    print(f"\nfiles evaluated: {total}   (engine: {transcriber.name})")
    print(f"  true positives:  {tp}")
    print(f"  false positives: {fp}")
    print(f"  missed:          {fn}")
    print(f"  true negatives:  {tn}")
    print(f"\nprecision: {precision:.2f}   recall: {recall:.2f}")
    print(
        "(bias check: this project prefers precision — a missed detection "
        "has other paths, a false CRITICAL erodes trust. docs/decisions.md D1)"
    )
    if fp_terms:
        print("\nfalse-positive watchword counts (candidates for pruning):")
        for term, count in fp_terms.most_common():
            print(f"  {count:3d}  {term}")
    if mismatches:
        print("\nmismatches to review:")
        for m in mismatches:
            print(f"  {m}")


if __name__ == "__main__":
    main()
