"""Paths and tunables. Everything is overridable by environment variable so
tests can point the archive and warehouse at a temporary directory."""
from __future__ import annotations

import os
from pathlib import Path


def _path_from_env(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else default


# config.py sits at packages/ptb-core/src/ptb/core/, so the repo root is six
# levels up: core -> ptb -> src -> ptb-core -> packages -> repo.
REPO_ROOT = Path(__file__).resolve().parents[5]
DATA_DIR = _path_from_env("PTB_DATA_DIR", REPO_ROOT / "data")
RAW_DIR = _path_from_env("PTB_RAW_DIR", DATA_DIR / "raw")
DB_PATH = _path_from_env("PTB_DB_PATH", DATA_DIR / "ptb.duckdb")

USER_AGENT = os.environ.get(
    "PTB_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
)
REQUEST_TIMEOUT = float(os.environ.get("PTB_REQUEST_TIMEOUT", "30"))
