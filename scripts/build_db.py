#!/usr/bin/env python3
"""Buduje bazę SQLite z FTS5 z data/parsed_corpus/*.json → data/lawdb.sqlite.

Tabele: units (ustępy artykułów, jednostki wektoryzacji), acts (akt + metadane z data/inforce), articles (jednostka; `variant` dla równoległych wersji),
footnotes, articles_fts (FTS5, słowa bez ogonków). Indeks trigramowy tylko z --trigram (duży).

Użycie:
    python3 scripts/build_db.py
    python3 scripts/build_db.py --parsed data/parsed --db data/lawdb_codes.sqlite --trigram
"""
import argparse
import glob
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from units import fill_units  # noqa: E402

SCHEMA = """
CREATE TABLE acts (
  eli TEXT PRIMARY KEY, title TEXT, type TEXT, status TEXT, unit_kind TEXT,
  group_code TEXT, stale INTEGER, announced TEXT, legal_status_date TEXT, source TEXT
);
CREATE TABLE articles (
  id INTEGER PRIMARY KEY, eli TEXT NOT NULL REFERENCES acts(eli),
  art TEXT NOT NULL, variant INTEGER, path TEXT, text TEXT NOT NULL, note TEXT
);
CREATE INDEX articles_lookup ON articles(eli, art);
CREATE TABLE footnotes (eli TEXT NOT NULL, number INTEGER NOT NULL, text TEXT NOT NULL, PRIMARY KEY (eli, number));
CREATE VIRTUAL TABLE articles_fts USING fts5(text, path, content='articles', content_rowid='id',
  tokenize='unicode61 remove_diacritics 2');
"""
TRIGRAM = "CREATE VIRTUAL TABLE articles_tri USING fts5(text, content='articles', content_rowid='id', tokenize='trigram');"


def load_records(inforce_glob: str) -> dict:
    records = {}
    for path in glob.glob(inforce_glob):
        with open(path, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                records[r["ELI"]] = r
    return records


def build(parsed_dir: Path, db_path: Path, records: dict, trigram: bool) -> None:
    db_path.unlink(missing_ok=True)
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    db.executescript(SCHEMA + (TRIGRAM if trigram else ""))
    present = {f.stem.replace("_", "/", 2) for f in parsed_dir.glob("*.json")}
    skipped = 0
    for f in sorted(parsed_dir.glob("*.json")):
        act = json.loads(f.read_text(encoding="utf-8"))
        rec = records.get(act["eli"], {})
        bases = [r["id"] for r in rec.get("references", {}).get("Tekst jednolity dla aktu", [])]
        if any(b in present for b in bases):  # obwieszczenie o t.j. powtarza tekst, który niesie akt bazowy
            skipped += 1
            continue
        db.execute(
            "INSERT INTO acts VALUES (?,?,?,?,?,?,?,?,?,?)",
            (act["eli"], act.get("title") if act.get("title") not in (None, act["eli"]) else rec.get("title"),
             rec.get("type"), rec.get("status"), act.get("unitKind"), act.get("group"),
             int(bool(act.get("stale"))), rec.get("announcementDate"), act.get("legalStatusDate"), act.get("source")))
        db.executemany("INSERT INTO footnotes VALUES (?,?,?)",
                       [(act["eli"], int(n), t) for n, t in act.get("footnotes", {}).items()])
        db.executemany(
            "INSERT INTO articles (eli, art, variant, path, text, note) VALUES (?,?,?,?,?,?)",
            [(act["eli"], a["art"], a.get("variant"), " > ".join(a["path"]), a["text"], a.get("note"))
             for a in act["articles"]])
    db.execute("INSERT INTO articles_fts(rowid, text, path) SELECT id, text, path FROM articles")
    if trigram:
        db.execute("INSERT INTO articles_tri(rowid, text) SELECT id, text FROM articles")
    db.commit()
    print(f"jednostek (ustępy): {fill_units(db)}")
    db.close()
    print(f"pominięto obwieszczeń o t.j. (tekst jest w akcie bazowym): {skipped}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--parsed", default="data/parsed_corpus", type=Path)
    parser.add_argument("--db", default="data/lawdb.sqlite", type=Path)
    parser.add_argument("--inforce", default="data/inforce/*.jsonl")
    parser.add_argument("--trigram", action="store_true")
    parser.add_argument("--units-only", action="store_true", help="tylko przebuduj tabelę units w istniejącej bazie")
    args = parser.parse_args()
    if args.units_only:
        db = sqlite3.connect(args.db)
        print(f"jednostek (ustępy): {fill_units(db)}")
        return
    build(args.parsed, args.db, load_records(args.inforce), args.trigram)
    db = sqlite3.connect(args.db)
    print(db.execute("SELECT (SELECT count(*) FROM acts), (SELECT count(*) FROM articles)").fetchone())


if __name__ == "__main__":
    main()
