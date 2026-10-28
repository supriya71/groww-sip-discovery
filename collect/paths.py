from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "config.yaml"


def output_dir(source: str) -> Path:
    return REPO_ROOT / "data" / "raw" / source
