#!/usr/bin/env python3
"""Wyszukiwanie w data/lawdb.sqlite: FTS5 (BM25), wektory (magazyn haszowy) i ich fuzja RRF.

Zakres: `codes` (osiem kodeksów) albo `all` (cały korpus w bazie). Bez fastembed lub bez magazynu
wektorów działa sam FTS5 (`Index.vector_error` mówi dlaczego).

Użycie (.venv z fastembed):
    .venv/bin/python scripts/search.py "kradzież cudzej rzeczy ruchomej" [--scope all] [--mode prefix5] [-n 10]
"""
import argparse
import re
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from embed_store import MAX_CHARS, digest, prepared, store_paths  # noqa: E402
from eval_search import build  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
CODES = {
    "DU/1964/93": "KC", "DU/1997/553": "KK", "DU/1964/296": "KPC", "DU/1974/141": "KP",
    "DU/1960/168": "KPA", "DU/1997/555": "KPK", "DU/1964/59": "KRO", "DU/2000/1037": "KSH",
}
MODES = ("words", "prefix4", "prefix5", "prefix6")
LIMIT = 200  # głębokość rankingów przed fuzją
RRF_K = 60
SHOWN_CHARS = 4000  # tyle znaków treści artykułu pobieramy do wyniku (są artykuły po 1,4 mln znaków)
TJ_FILE = re.compile(r"^D(\d{4})(\d{4})L\.pdf$")  # tekst jednolity z obwieszczenia: D<rok><pozycja>L.pdf


def rrf(*rankings: list[int]) -> list[int]:
    score: dict[int, float] = {}
    for ranking in rankings:
        for pos, article_id in enumerate(ranking, 1):
            score[article_id] = score.get(article_id, 0.0) + 1.0 / (RRF_K + pos)
    return sorted(score, key=score.get, reverse=True)


def source_of(source: str | None) -> dict:
    """Opis źródła tekstu z `acts.source`: etykieta, adres (gdy znany) i ELI obwieszczenia o t.j."""
    if source and source.startswith("http"):
        return {"label": "tekst aktu (HTML)", "url": source, "eli": None}
    m = TJ_FILE.match(source or "")
    if m:
        eli = f"DU/{m[1]}/{int(m[2])}"
        return {"label": f"tekst jednolity, obwieszczenie {eli}",
                "url": f"https://api.sejm.gov.pl/eli/acts/{eli}/text/T/{source}", "eli": eli}
    return {"label": f"PDF {source}" if source else "brak źródła", "url": None, "eli": None}


class Index:
    def __init__(self, db_path: Path = ROOT / "data" / "lawdb.sqlite", model: str = MODEL):
        self.db = sqlite3.connect(db_path)
        self.model = model
        rows = self.db.execute("SELECT id, eli, substr(text, 1, ?) FROM articles", (MAX_CHARS,)).fetchall()
        self.ids = np.array([r[0] for r in rows])
        self.in_codes = np.array([r[1] in CODES for r in rows])
        self.vector_error = None
        self.missing_vectors = 0
        try:
            self._load_vectors(rows)
        except Exception as e:  # brak fastembed, magazynu albo modelu — zostaje sam FTS5
            self.vector_error = f"{type(e).__name__}: {e}"

    def _load_vectors(self, rows: list) -> None:
        from fastembed import TextEmbedding

        _, hashes_path, vectors_path = store_paths(self.model)
        hash_index = {h: i for i, h in enumerate(np.load(ROOT / hashes_path).tolist())}
        self.store = np.load(ROOT / vectors_path).astype(np.float32)
        self.store /= np.linalg.norm(self.store, axis=1, keepdims=True)
        self.rows_of = np.array([hash_index.get(digest(prepared(r[2], False)), -1) for r in rows])
        self.missing_vectors = int((self.rows_of < 0).sum())
        self.embedder = TextEmbedding(self.model, cache_dir=str(ROOT / "data" / "models"))

    def fts(self, text: str, mode: str, scope: str, limit: int = LIMIT) -> list[int]:
        query = build(text, mode)
        if not query:
            return []
        scope_sql = f" AND a.eli IN ({','.join('?' * len(CODES))})" if scope == "codes" else ""
        sql = ("SELECT a.id FROM articles_fts f JOIN articles a ON a.id = f.rowid "
               f"WHERE articles_fts MATCH ?{scope_sql} ORDER BY bm25(articles_fts, 1.0, 0.3) LIMIT ?")
        args = [query, *(CODES if scope == "codes" else ()), limit]
        return [r[0] for r in self.db.execute(sql, args)]

    def vector(self, text: str, scope: str, limit: int = LIMIT) -> list[int]:
        if self.vector_error:
            return []
        query = np.array(list(self.embedder.embed([text])))[0].astype(np.float32)
        query /= np.linalg.norm(query)
        sims = np.where(self.rows_of >= 0, (self.store @ query)[self.rows_of], -np.inf)
        if scope == "codes":
            sims = np.where(self.in_codes, sims, -np.inf)
        top = np.argpartition(-sims, limit)[:limit]
        top = top[np.argsort(-sims[top])]
        return self.ids[top[np.isfinite(sims[top])]].tolist()

    def articles(self, article_ids: list[int]) -> list[dict]:
        """Artykuły z metadanymi aktu w kolejności `article_ids`."""
        if not article_ids:
            return []
        marks = ",".join("?" * len(article_ids))
        rows = self.db.execute(
            "SELECT a.id, a.eli, a.art, a.variant, a.path, substr(a.text, 1, ?), length(a.text), a.note, "
            f"c.title, c.status, c.stale, c.source FROM articles a JOIN acts c ON c.eli = a.eli WHERE a.id IN ({marks})",
            (SHOWN_CHARS, *article_ids)).fetchall()
        by_id = {}
        for (aid, eli, art, variant, path, text, length, note, title, status, stale, source) in rows:
            by_id[aid] = {
                "id": aid, "eli": eli, "art": art, "variant": variant, "path": path, "text": text, "length": length,
                "note": note, "title": title, "status": status, "stale": bool(stale), "code": CODES.get(eli),
                "source": source_of(source),
            }
        return [by_id[i] for i in article_ids]

    def search(self, text: str, scope: str = "codes", mode: str = "prefix5", limit: int = 10) -> dict:
        """Trzy rankingi (fts, vector, hybrid) po `limit` artykułów oraz czasy w ms."""
        return self.search_many([text], scope, mode, limit)

    def search_many(self, queries: list[str], scope: str = "codes", mode: str = "prefix5", limit: int = 10) -> dict:
        """Jak `search`, ale dla kilku zapytań (np. przepisanych przez LLM): rankingi zapytań łączone RRF."""
        t0 = time.perf_counter()
        fts_lists = [self.fts(q, mode, scope) for q in queries]
        t1 = time.perf_counter()
        vec_lists = [self.vector(q, scope) for q in queries]
        t2 = time.perf_counter()
        fts = rrf(*fts_lists) if len(queries) > 1 else fts_lists[0]
        vec = rrf(*vec_lists) if len(queries) > 1 else vec_lists[0]
        hybrid = rrf(*fts_lists, *vec_lists)
        shown = {"fts": fts[:limit], "vector": vec[:limit], "hybrid": hybrid[:limit]}
        cache = {a["id"]: a for a in self.articles(list(dict.fromkeys(i for ids in shown.values() for i in ids)))}
        result = {name: [cache[i] for i in ids] for name, ids in shown.items()}
        result["ms"] = {"fts": round((t1 - t0) * 1000), "vector": round((t2 - t1) * 1000)}
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("query")
    parser.add_argument("--scope", choices=["codes", "all"], default="codes")
    parser.add_argument("--mode", choices=MODES, default="prefix5")
    parser.add_argument("-n", type=int, default=10)
    args = parser.parse_args()
    index = Index()
    if index.vector_error:
        print(f"wektory niedostępne ({index.vector_error})", file=sys.stderr)
    result = index.search(args.query, args.scope, args.mode, args.n)
    for name in ("fts", "vector", "hybrid"):
        print(f"\n== {name}")
        for pos, a in enumerate(result[name], 1):
            variant = f" [wariant {a['variant']}]" if a["variant"] else ""
            print(f"{pos:>3}. {a['code'] or a['eli']} art. {a['art']}{variant}  {a['text'][:90]!r}")
    print(f"\nczas: FTS {result['ms']['fts']} ms, wektory {result['ms']['vector']} ms")


if __name__ == "__main__":
    main()
