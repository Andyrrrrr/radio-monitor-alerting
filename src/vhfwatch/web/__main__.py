"""Entry point: python -m vhfwatch.web

Deliberately a separate process from the pipeline (docs/decisions.md D8).
"""

import argparse
from pathlib import Path

import uvicorn

from vhfwatch.config import load_config
from vhfwatch.log import configure_logging
from vhfwatch.web.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="vhfwatch.web")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    configure_logging(args.log_level)
    cfg = load_config(args.config)
    uvicorn.run(
        create_app(cfg),
        host=cfg.web.host,
        port=cfg.web.port,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
