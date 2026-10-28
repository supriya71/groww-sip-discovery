"""CLI: python -m collect.run_all <collector>"""

from __future__ import annotations

import sys

COLLECTORS = frozenset({"play_store", "app_store", "tradingqna", "youtube"})


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("Usage: python -m collect.run_all <collector>")
        print(f"Collectors: {', '.join(sorted(COLLECTORS))}")
        return 1

    name = args[0].strip().lower()
    if name not in COLLECTORS:
        print(f"Unknown collector: {name}")
        print(f"Collectors: {', '.join(sorted(COLLECTORS))}")
        return 1

    if name == "play_store":
        from collect.play_store import run_play_store

        return run_play_store()
    if name == "app_store":
        from collect.app_store import run_app_store

        return run_app_store()
    if name == "tradingqna":
        from collect.tradingqna import run_tradingqna

        return run_tradingqna()
    if name == "youtube":
        from collect.youtube import run_youtube

        return run_youtube()

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
