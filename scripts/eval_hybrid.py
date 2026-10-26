#!/usr/bin/env python3
"""Ewaluacja pełnego korpusu: FTS5 (prefix5), wektory (magazyn haszowy) i ich fuzja RRF.

Te same zapytania i etykiety co w eval_search.py. Podaje pozycję artykułu docelowego w każdym trybie.

Użycie (.venv z fastembed):
    .venv/bin/python scripts/eval_hybrid.py [MODEL]
"""
import sqlite3
import sys

import numpy as np
from fastembed import TextEmbedding

from embed_store import digest, prepared, store_paths
from eval_search import TESTS, build

MODEL = sys.argv[1] if len(sys.argv) > 1 else "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
LIMIT = 200
RRF_K = 60


def fts_ids(db, text: str) -> list[int]:
    rows = db.execute(
        "SELECT a.id FROM articles_fts f JOIN articles a ON a.id = f.rowid "
        "WHERE articles_fts MATCH ? ORDER BY bm25(articles_fts, 1.0, 0.3) LIMIT ?", (build(text, "prefix5"), LIMIT)).fetchall()
    return [r[0] for r in rows]


def rrf(*rankings: list[int]) -> list[int]:
    score: dict[int, float] = {}
    for ranking in rankings:
        for pos, article_id in enumerate(ranking, 1):
            score[article_id] = score.get(article_id, 0.0) + 1.0 / (RRF_K + pos)
    return sorted(score, key=score.get, reverse=True)


def position(ranking: list[int], target_ids: set[int]):
    for pos, article_id in enumerate(ranking, 1):
        if article_id in target_ids:
            return pos
    return None


def main() -> None:
    db = sqlite3.connect("data/lawdb.sqlite")
    _, hashes_path, vectors_path = store_paths(MODEL)
    hash_index = {h: i for i, h in enumerate(np.load(hashes_path).tolist())}
    matrix = np.load(vectors_path).astype(np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)

    ids, rows_of = [], []
    for article_id, text in db.execute("SELECT id, text FROM articles"):
        ids.append(article_id)
        rows_of.append(hash_index[digest(prepared(text, False))])
    ids = np.array(ids)
    rows_of = np.array(rows_of)

    embedder = TextEmbedding(MODEL, cache_dir="data/models")
    modes = ["fts", "wektor", "rrf"]
    print(f"{'zapytanie':60}" + "".join(f"{m:>9}" for m in modes))
    hits = {m: 0 for m in modes}
    for text, eli, art in TESTS:
        target = {r[0] for r in db.execute("SELECT id FROM articles WHERE eli LIKE ? AND art = ?", (eli.replace("/", "%"), art))}
        query = np.array(list(embedder.embed([text])))[0].astype(np.float32)
        query /= np.linalg.norm(query)
        sims = matrix[rows_of] @ query
        vec = ids[np.argsort(-sims)[:LIMIT]].tolist()
        fts = fts_ids(db, text)
        row = []
        for mode, ranking in zip(modes, (fts, vec, rrf(fts, vec))):
            p = position(ranking, target)
            hits[mode] += bool(p and p <= 3)
            row.append(str(p))
        print(f"{text[:58]:60}" + "".join(f"{r:>9}" for r in row))
    print(f"{'w pierwszej trójce z ' + str(len(TESTS)):60}" + "".join(f"{hits[m]:>9}" for m in modes))


if __name__ == "__main__":
    main()
