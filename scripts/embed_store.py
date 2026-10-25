#!/usr/bin/env python3
"""Magazyn embeddingów z kluczem = hasz tekstu (przetrwa przebudowę bazy).

Pliki: data/emb/<model>/store_hashes.npy (SHA-1, tekst hex) i store_vectors.npy (float16).
`sync` liczy wektory tylko dla jednostek, których hasza nie ma w magazynie, i usuwa wektory
jednostek, których już nie ma w bazie.

Użycie (.venv z fastembed):
    .venv/bin/python scripts/embed_store.py import-chunks MODEL   # jednorazowo: chunk_*.npy → magazyn
    .venv/bin/python scripts/embed_store.py sync MODEL            # po przebudowie bazy
"""
import argparse
import hashlib
import sqlite3
from pathlib import Path

import numpy as np

MAX_CHARS = 1200  # musi zgadzać się z embed_corpus.py


def prepared(text: str, e5: bool) -> str:
    return ("passage: " if e5 else "") + text[:MAX_CHARS]


def digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def store_paths(model: str) -> tuple[Path, Path, Path]:
    base = Path("data/emb") / model.replace("/", "_")
    return base, base / "store_hashes.npy", base / "store_vectors.npy"


def load(model: str) -> dict[str, np.ndarray]:
    _, hashes_path, vectors_path = store_paths(model)
    if not hashes_path.exists():
        return {}
    return dict(zip(np.load(hashes_path).tolist(), np.load(vectors_path)))


def save(model: str, store: dict[str, np.ndarray]) -> None:
    base, hashes_path, vectors_path = store_paths(model)
    keys = sorted(store)
    np.save(hashes_path, np.array(keys))
    np.save(vectors_path, np.array([store[k] for k in keys], dtype=np.float16))


def import_chunks(model: str, db_path: str, e5: bool) -> None:
    """Przenosi wektory zapisane po id artykułu na klucze haszowe. Baza musi być ta sama, co przy liczeniu."""
    base, _, _ = store_paths(model)
    db = sqlite3.connect(db_path)
    store = load(model)
    for ids_file in sorted(base.glob("ids_*.npy")):
        ids = np.load(ids_file)
        vectors = np.load(base / ids_file.name.replace("ids_", "chunk_"))
        texts = dict(db.execute("SELECT id, text FROM articles WHERE id BETWEEN ? AND ?", (int(ids[0]), int(ids[-1]))))
        for article_id, vector in zip(ids.tolist(), vectors):
            store[digest(prepared(texts[article_id], e5))] = vector
    save(model, store)
    print(f"magazyn: {len(store)} wektorów")


def sync(model: str, db_path: str, e5: bool) -> None:
    from fastembed import TextEmbedding

    db = sqlite3.connect(db_path)
    store = load(model)
    current = {}
    for _, text in db.execute("SELECT id, text FROM articles"):
        p = prepared(text, e5)
        current[digest(p)] = p
    missing = [h for h in current if h not in store]
    print(f"jednostek: {len(current)}, w magazynie: {len(current) - len(missing)}, do policzenia: {len(missing)}")
    if missing:
        embedder = TextEmbedding(model, cache_dir="data/models")
        vectors = np.array(list(embedder.embed([current[h] for h in missing], batch_size=64)), dtype=np.float16)
        store.update(zip(missing, vectors))
    store = {h: v for h, v in store.items() if h in current}
    save(model, store)
    print(f"magazyn: {len(store)} wektorów")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["import-chunks", "sync"])
    parser.add_argument("model")
    parser.add_argument("--db", default="data/lawdb.sqlite")
    parser.add_argument("--e5", action="store_true")
    args = parser.parse_args()
    (import_chunks if args.command == "import-chunks" else sync)(args.model, args.db, args.e5)


if __name__ == "__main__":
    main()
