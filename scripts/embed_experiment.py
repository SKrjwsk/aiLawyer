#!/usr/bin/env python3
"""Eksperyment: embeddingi vs BM25 na artykułach z data/lawdb.sqlite.

Wymaga venv z `fastembed` (.venv). Dla każdego zapytania testowego podaje pozycję
artykułu docelowego w rankingu: BM25 (słowa), embeddingi, oraz fuzja RRF obu.

Użycie:
    .venv/bin/python scripts/embed_experiment.py sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
    .venv/bin/python scripts/embed_experiment.py intfloat/multilingual-e5-large --e5
"""
import argparse
import re
import sqlite3
import time
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding

# (zapytanie, akt, artykuł) — artykuły docelowe wskazane z wiedzy o przepisach, nie zweryfikowane przez prawnika
TESTS = [
    ("termin przedawnienia roszczeń", "DU/2026/795", "118"),
    ("zadośćuczynienie za krzywdę zmarłego członka rodziny", "DU/2026/795", "446"),
    ("zasady współżycia społecznego nadużycie prawa", "DU/2026/795", "5"),
    ("wypowiedzenie umowy o pracę na czas nieokreślony", "DU/2026/1245", "30"),
    ("zakaz mobbingu przeciwdziałanie", "DU/2026/1245", "94[3]"),
    ("zabójstwo kto zabija człowieka", "DU/2026/1342", "148"),
    ("kradzież cudzej rzeczy ruchomej", "DU/2026/1342", "278"),
    ("termin do wniesienia apelacji", "DU/2026/468", "369"),
    ("załatwienie sprawy bez zbędnej zwłoki organ administracji", "DU/2025/1691", "35"),
    ("odpowiedzialność członków zarządu za zobowiązania spółki z o.o. egzekucja bezskuteczna", "DU/2024/18", "299"),
    # pytania w języku potocznym, bez słów z przepisu
    ("czy szef może mnie zwolnić gdy jestem na urlopie", "DU/2026/1245", "41"),
    ("ile mam czasu żeby odwołać się od wyroku sądu pierwszej instancji", "DU/2026/468", "369"),
    ("kiedy dług się przedawnia", "DU/2026/795", "118"),
]


def bm25_ranking(db, query: str, limit: int = 200) -> list[int]:
    q = " OR ".join('"%s"' % w for w in re.findall(r"\w+", query) if len(w) > 2)
    return [r[0] for r in db.execute(
        "SELECT rowid FROM articles_fts WHERE articles_fts MATCH ? ORDER BY bm25(articles_fts) LIMIT ?", (q, limit))]


def position(ids: list[int], target: int):
    return ids.index(target) + 1 if target in ids else None


def rrf(*rankings, k: int = 60) -> list[int]:
    score: dict[int, float] = {}
    for ranking in rankings:
        for pos, rid in enumerate(ranking, 1):
            score[rid] = score.get(rid, 0) + 1 / (k + pos)
    return sorted(score, key=score.get, reverse=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("model")
    parser.add_argument("--e5", action="store_true", help='prefiksy "query: " / "passage: "')
    parser.add_argument("--db", default="data/lawdb.sqlite")
    parser.add_argument("--cache", default="data/models")
    args = parser.parse_args()

    db = sqlite3.connect(args.db)
    rows = db.execute("SELECT id, eli, art, variant, text FROM articles ORDER BY id").fetchall()
    ids = [r[0] for r in rows]
    target_id = {}
    for rid, eli, art, variant, _ in rows:
        target_id.setdefault((eli, art), rid)

    model = TextEmbedding(args.model, cache_dir=args.cache)
    cache = Path("data") / ("emb_" + args.model.replace("/", "_") + ".npy")
    if cache.exists():
        matrix = np.load(cache)
    else:
        passages = [("passage: " if args.e5 else "") + r[4][:2000] for r in rows]
        started = time.time()
        matrix = np.array(list(model.embed(passages, batch_size=32)))
        print(f"zakodowano {len(passages)} artykułów w {time.time() - started:.0f} s, wymiar {matrix.shape[1]}")
        np.save(cache, matrix)
    matrix = matrix / np.linalg.norm(matrix, axis=1, keepdims=True)

    print(f"{'zapytanie':64} BM25  embed  RRF")
    hits = {"bm25": 0, "emb": 0, "rrf": 0}
    for query, eli, art in TESTS:
        target = target_id[(eli, art)]
        qvec = np.array(list(model.embed([("query: " if args.e5 else "") + query])))[0]
        qvec = qvec / np.linalg.norm(qvec)
        order = np.argsort(-(matrix @ qvec))[:200]
        emb = [ids[i] for i in order]
        bm = bm25_ranking(db, query)
        fused = rrf(bm[:100], emb[:100])
        p = (position(bm, target), position(emb, target), position(fused, target))
        for key, pos in zip(hits, p):
            hits[key] += 1 if pos and pos <= 3 else 0
        print(f"{query[:62]:64} {str(p[0]):5} {str(p[1]):6} {p[2]}")
    print(f"w pierwszej trójce z {len(TESTS)}: BM25 {hits['bm25']}, embeddingi {hits['emb']}, RRF {hits['rrf']}")


if __name__ == "__main__":
    main()
