"""Podział artykułu na ustępy/paragrafy — jednostki wektoryzacji (tabela `units`)."""
import re
import sqlite3

UNIT_START = re.compile(r"^(?=§ \d+[a-z]*\.|\d+[a-z]*\. )", re.M)

UNITS_SCHEMA = """
CREATE TABLE IF NOT EXISTS units (
  id INTEGER PRIMARY KEY, article_id INTEGER NOT NULL REFERENCES articles(id), ord INTEGER NOT NULL, text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS units_article ON units(article_id);
"""


def split_units(art: str, text: str) -> list[str]:
    parts = [p.strip() for p in UNIT_START.split(text) if p.strip()]
    if len(parts) <= 1:
        return [text]
    return [p if p.startswith("Art.") else f"Art. {art}. {p}" for p in parts]


def fill_units(db: sqlite3.Connection) -> int:
    """Przebudowuje tabelę units z articles (artykuł bez ustępów = jedna jednostka)."""
    db.executescript("DROP TABLE IF EXISTS units;" + UNITS_SCHEMA)
    for article_id, art, text in db.execute("SELECT id, art, text FROM articles").fetchall():
        db.executemany("INSERT INTO units (article_id, ord, text) VALUES (?,?,?)",
                       [(article_id, i, u) for i, u in enumerate(split_units(art, text))])
    db.commit()
    return db.execute("SELECT count(*) FROM units").fetchone()[0]
