#!/usr/bin/env python3
"""Liczy embeddingi wszystkich jednostek z data/lawdb.sqlite → data/emb/<model>/chunk_*.npy (+ ids).

Wznawialne (pomija gotowe porcje). Wymaga .venv z fastembed.

Użycie:
    HF_HUB_DISABLE_XET=1 .venv/bin/python scripts/embed_corpus.py sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
"""
import argparse
import sqlite3
import time
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding

CHUNK = 5000
MAX_CHARS = 1200


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("model")
    parser.add_argument("--db", default="data/lawdb.sqlite")
    parser.add_argument("--e5", action="store_true")
    args = parser.parse_args()

    out = Path("data/emb") / args.model.replace("/", "_")
    out.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(args.db)
    ids = [r[0] for r in db.execute("SELECT id FROM articles ORDER BY id")]
    model = TextEmbedding(args.model, cache_dir="data/models")
    started = time.time()
    for n, start in enumerate(range(0, len(ids), CHUNK)):
        path = out / f"chunk_{n:04d}.npy"
        if path.exists():
            continue
        batch = ids[start:start + CHUNK]
        rows = db.execute(
            f"SELECT id, text FROM articles WHERE id BETWEEN ? AND ? ORDER BY id", (batch[0], batch[-1])).fetchall()
        texts = [("passage: " if args.e5 else "") + t[:MAX_CHARS] for _, t in rows]
        vectors = np.array(list(model.embed(texts, batch_size=64)), dtype=np.float16)
        np.save(path, vectors)
        np.save(out / f"ids_{n:04d}.npy", np.array([r[0] for r in rows]))
        print(f"porcja {n + 1}/{-(-len(ids) // CHUNK)}  {time.time() - started:.0f} s", flush=True)


if __name__ == "__main__":
    main()
