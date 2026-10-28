"""Run each dashboard page under AppTest (no .env)."""

from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

for k in list(os.environ):
    if "API_KEY" in k or "TOKEN" in k:
        os.environ.pop(k, None)

PAGES: list[tuple[str, str]] = [
    ("Overview", "page_overview"),
    ("Opportunity ranking", "page_opportunities"),
    ("Discovery questions", "page_questions"),
    ("Metric tree", "page_metric_tree"),
    ("Segments & sources", "page_segments"),
    ("Evidence explorer", "page_evidence"),
    ("Ask the data", "page_ask"),
    ("Validation", "page_validation"),
]


def _snippet(fn_name: str) -> str:
    root = str(REPO).replace("\\", "/")
    return textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, r"{root}")
        import streamlit as st
        from app.streamlit_app import {fn_name}, _require_bundle

        _require_bundle()
        {fn_name}()
        """
    )


def main() -> int:
    from streamlit.testing.v1 import AppTest

    for label, fn_name in PAGES:
        at = AppTest.from_string(_snippet(fn_name), default_timeout=240)
        at.run(timeout=240)
        errs = [e.value for e in at.error if e.value]
        if errs:
            print(f"{label}: FAIL")
            for e in errs[:2]:
                print(f"  {e[:240]}")
            return 1
        print(f"{label}: PASS")

    print(f"All {len(PAGES)} pages PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
