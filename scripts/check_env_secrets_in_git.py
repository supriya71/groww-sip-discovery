"""Pass/fail: .env values must not appear in tracked files or git history."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def parse_env_keys(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def git_grep_value(value: str) -> bool:
    if len(value) < 8:
        return False
    r = subprocess.run(
        ["git", "grep", "-F", value, "--", "."],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    return r.returncode == 0


def git_history_has_value(value: str) -> bool:
    if len(value) < 8:
        return False
    r = subprocess.run(
        ["git", "log", "--all", "-S", value, "--oneline"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    return bool(r.stdout.strip())


def main() -> int:
    env_path = REPO / ".env"
    keys = parse_env_keys(env_path)
    failed = False
    for name, value in sorted(keys.items()):
        if not value:
            print(f"{name}: PASS (empty in .env)")
            continue
        in_tree = git_grep_value(value)
        in_hist = git_history_has_value(value)
        if in_tree or in_hist:
            print(f"{name}: FAIL")
            failed = True
        else:
            print(f"{name}: PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
