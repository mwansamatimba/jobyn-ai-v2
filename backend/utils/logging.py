"""Logging configuration helpers."""

import logging
import sys


def configure_logging(level: str) -> None:
    """Configure the root logger with a consistent, structured format."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        stream=sys.stdout,
        force=True,
    )
