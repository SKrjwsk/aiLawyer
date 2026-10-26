#!/usr/bin/env python3
"""Test na podkorpusie 6 kodeksów/ustaw z pytań testowych: wektory na artykułach vs na ustępach/paragrafach.

Wektory liczone w pamięci (bez zapisu na dysk). Podaje pozycję artykułu docelowego (po zdeduplikowaniu do artykułów).

Użycie (.venv z fastembed):
    .venv/bin/python scripts/eval_split.py [MODEL]
"""
import sqlite3
import sys

import numpy as np
from fastembed import TextEmbedding

from eval_search import TESTS
from units import split_units

MODEL = sys.argv[1] if len(sys.argv) > 1 else "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MAX_CHARS = 1200


def embed(embedder, texts: list[str]) -> np.ndarray:
    m = np.array(list(embedder.embed([t[:MAX_CHARS] for t in texts], batch_size=64)), dtype=np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def main() -> None:
    db = sqlite3.connect("data/lawdb.sqlite")
    elis = sorted({e.replace("/", "/") for _, e, _ in TESTS})
    rows = db.execute(f"SELECT id, eli, art, text FROM articles WHERE eli IN ({','.join('?' * len(elis))})", elis).fetchall()
    units = [(aid, u) for aid, _, art, text in rows for u in split_units(art, text)]
    print(f"artykułów: {len(rows)}, jednostek (ustępy): {len(units)}")
    embedder = TextEmbedding(MODEL, cache_dir="data/models")
    art_ids = np.array([r[0] for r in rows])
    art_m = embed(embedder, [r[3] for r in rows])
    unit_ids = np.array([u[0] for u in units])
    unit_m = embed(embedder, [u[1] for u in units])
    print(f"{'zapytanie':60}{'artykuł':>9}{'ustęp':>9}")
    hits = [0, 0]
    for text, eli, art in TESTS:
        target = next(r[0] for r in rows if r[1] == eli and r[2] == art)
        q = embed(embedder, [text])[0]
        out = []
        for i, (ids, m) in enumerate(((art_ids, art_m), (unit_ids, unit_m))):
            order = ids[np.argsort(-(m @ q))]
            seen = list(dict.fromkeys(order.tolist()))
            pos = seen.index(target) + 1
            hits[i] += pos <= 3
            out.append(str(pos))
        print(f"{text[:58]:60}" + "".join(f"{o:>9}" for o in out))
    print(f"{'w pierwszej trójce z ' + str(len(TESTS)):60}" + "".join(f"{h:>9}" for h in hits))


if __name__ == "__main__":
    main()
