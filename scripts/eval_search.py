#!/usr/bin/env python3
"""Ewaluacja wyszukiwania leksykalnego (FTS5) na data/lawdb.sqlite.

Dla każdego zapytania testowego podaje pozycję artykułu docelowego w rankingu BM25 dla trzech
trybów budowy zapytania: pełne słowa, prefiks N znaków (prymitywny stemming fleksji), prefiks + AND
dla słów rzadkich. Etykiety (akt, artykuł) pochodzą z wiedzy o przepisach, nie od prawnika.

Użycie:
    python3 scripts/eval_search.py
"""
import re
import sqlite3
import sys

# (zapytanie, akt bazowy, artykuł)
TESTS = [
    ("termin przedawnienia roszczeń", "DU/1964/93", "118"),
    ("zadośćuczynienie za krzywdę zmarłego członka rodziny", "DU/1964/93", "446"),
    ("zasady współżycia społecznego nadużycie prawa", "DU/1964/93", "5"),
    ("wypowiedzenie umowy o pracę na czas nieokreślony", "DU/1974/141", "30"),
    ("zakaz mobbingu przeciwdziałanie", "DU/1974/141", "94[3]"),
    ("zabójstwo kto zabija człowieka", "DU/1997/553", "148"),
    ("kradzież cudzej rzeczy ruchomej", "DU/1997/553", "278"),
    ("termin do wniesienia apelacji", "DU/1964/296", "369"),
    ("załatwienie sprawy bez zbędnej zwłoki organ administracji", "DU/1960/168", "35"),
    ("odpowiedzialność członków zarządu za zobowiązania spółki z o.o. egzekucja bezskuteczna", "DU/2000/1037", "299"),
    ("czy szef może mnie zwolnić gdy jestem na urlopie", "DU/1974/141", "41"),
    ("ile mam czasu żeby odwołać się od wyroku sądu pierwszej instancji", "DU/1964/296", "369"),
    ("kiedy dług się przedawnia", "DU/1964/93", "118"),
]
STOP = {"czy", "jest", "mnie", "gdy", "może", "ile", "mam", "żeby", "się", "kto", "bez", "oraz", "dla", "przy", "nad", "pod"}


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"\w+", text.lower()) if len(w) > 2 and w not in STOP]


def build(text: str, mode: str) -> str:
    ws = words(text)
    if mode == "words":
        return " OR ".join(f'"{w}"' for w in ws)
    n = int(mode.removeprefix("prefix"))
    return " OR ".join(f'"{w[:n]}"*' if len(w) > n else f'"{w}"' for w in ws)


def rank(db, text: str, mode: str, eli: str, art: str, limit: int = 200):
    rows = db.execute(
        "SELECT a.eli, a.art FROM articles_fts f JOIN articles a ON a.id = f.rowid "
        "WHERE articles_fts MATCH ? ORDER BY bm25(articles_fts, 1.0, 0.3) LIMIT ?", (build(text, mode), limit)).fetchall()
    for pos, (e, a) in enumerate(rows, 1):
        if e == eli and a == art:
            return pos
    return None


def main() -> None:
    db = sqlite3.connect(sys.argv[1] if len(sys.argv) > 1 else "data/lawdb.sqlite")
    modes = ["words", "prefix4", "prefix5", "prefix6"]
    print(f"{'zapytanie':60}" + "".join(f"{m:>9}" for m in modes))
    hits = {m: 0 for m in modes}
    for text, eli, art in TESTS:
        row = []
        for m in modes:
            p = rank(db, text, m, eli, art)
            hits[m] += bool(p and p <= 3)
            row.append(str(p))
        print(f"{text[:58]:60}" + "".join(f"{r:>9}" for r in row))
    print(f"{'w pierwszej trójce z ' + str(len(TESTS)):60}" + "".join(f"{hits[m]:>9}" for m in modes))


if __name__ == "__main__":
    main()
