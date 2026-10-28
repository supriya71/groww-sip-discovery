"""Simulate clean clone: load export bundle + optional embedder; report peak RSS."""

from __future__ import annotations

import sys
import tracemalloc
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# Block .env for this test
import os

os.environ.pop("GROQ_API_KEY", None)
for k in list(os.environ):
    if k.endswith("_API_KEY") or k.endswith("_TOKEN"):
        os.environ.pop(k, None)


def main() -> int:
    tracemalloc.start()
    peak = 0

    def snap(label: str) -> None:
        nonlocal peak
        _, p = tracemalloc.get_traced_memory()
        peak = max(peak, p)
        print(f"  {label}: {p / 1024 / 1024:.1f} MiB traced")

    export = REPO / "data" / "export"
    if not (export / "meta.json").is_file():
        print("FAIL: data/export/meta.json missing — run python -m pipeline.export")
        return 1

    import json

    meta = json.loads((export / "meta.json").read_text(encoding="utf-8"))
    snap("meta.json")
    for name in (
        "overview.json",
        "opportunities.json",
        "discovery_questions.json",
        "metric_tree.json",
        "segments.json",
        "validation.json",
    ):
        json.loads((export / name).read_text(encoding="utf-8"))
    snap("all JSON aggregates")

    evidence = []
    for line in (export / "evidence.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            evidence.append(json.loads(line))
    snap(f"evidence.jsonl ({len(evidence)} rows)")

    from app.data_bundle import load_ask_embeddings, load_ask_corpus

    corpus = []
    p = export / "ask_corpus.jsonl"
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                corpus.append(json.loads(line))
    snap(f"ask_corpus ({len(corpus)} rows)")

    npz = export / "ask_embeddings.npz"
    if npz.is_file():
        import numpy as np

        data = np.load(npz, allow_pickle=True)
        _ = data["embeddings"]
        snap("ask_embeddings.npz loaded")

    from fastembed import TextEmbedding

    model = TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")
    vec = list(model.embed(["test SIP pause"]))[0]
    snap(f"fastembed encode dim={len(vec)}")

    try:
        import psutil

        rss = psutil.Process().memory_info().rss / 1024 / 1024
        print(f"\nPeak traced (tracemalloc): {peak / 1024 / 1024:.1f} MiB")
        print(f"Current RSS (psutil): {rss:.1f} MiB")
    except ImportError:
        print(f"\nPeak traced (tracemalloc): {peak / 1024 / 1024:.1f} MiB")
        print("(Install psutil for RSS; traced peak is conservative)")

    print(f"Export tagged={meta.get('tagged_count')} relevant={meta.get('relevant_count')}")
    print("PASS: bundle loads without .env")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
