#!/usr/bin/env python3
"""Parsuje PDF tekstu jednolitego (typ T) do artykułów: data/parsed/<ELI>.json.

Wymaga `pdftotext` (poppler); zależności Pythona: tylko stdlib. Czyta współrzędne linii
(`pdftotext -bbox-layout`): przypisy rozpoznaje po mniejszej wysokości linii, początek
artykułu po wcięciu, nagłówki działów po wyśrodkowaniu.

Użycie:
    python3 scripts/parse_pdf.py data/raw/DU_2026_795
    python3 scripts/parse_pdf.py data/raw/*
"""
import argparse
import html
import json
import re
import statistics
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PAGE_RE = re.compile(r"<page\b.*?</page>", re.S)
LINE_RE = re.compile(r'<line xMin="([\d.]+)" yMin="([\d.]+)" xMax="[\d.]+" yMax="([\d.]+)">(.*?)</line>', re.S)
WORD_RE = re.compile(r'<word xMin="([\d.]+)" yMin="[\d.]+" xMax="([\d.]+)" yMax="[\d.]+">(.*?)</word>', re.S)

PAGE_NOISE_RE = re.compile(
    r"^(Dziennik Ustaw|– \d+ –|Poz\. \d+|\d{4}-\d{2}-\d{2}|©Kancelaria Sejmu|s\. \d+/\d+|\d+)$"
)
ANNEX_RE = re.compile(r"^Załącznik do obwieszczenia")
ART_RE = re.compile(r"^(Art\.|§)\s+(\d+[a-z]*(?:\[\d+\])?[a-z]*)\.(?:(\d+)\))?\s+(.*)$")
SUB_RE = re.compile(r"^(§\s*\d+[a-z]*(?:\[\d+\])?\.|\d+[a-z]*(?:\[\d+\])?\)|[a-ząćęłńóśźż]{1,2}\))\s")
HEADING_RE = re.compile(
    r"^(KSIĘGA\s+\w+|TYTUŁ\s+[IVXLCDM]+[a-z]*|DZIAŁ\s+[IVXLCDM]+[a-z]*|Dział\s+[IVXLCDM]+[a-z]*"
    r"|Rozdział\s+[IVXLCDM\d]+[a-z]*|ROZDZIAŁ\s+[IVXLCDM\d]+[a-z]*|Oddział\s+\d+[a-z]*)\s*\d*\)?$"
)
FOOTNOTE_START_RE = re.compile(r"^(\d+)\)(?:\s+(.*))?$")
HEADING_ORDER = ["KSIĘGA", "TYTUŁ", "DZIAŁ", "ROZDZIAŁ", "ODDZIAŁ"]

ARTICLE_X = 65  # `Art.` zaczyna się wcięte (x≈72); linia kontynuacji zaczynająca od „Art." jest przy marginesie (x≈51)
CENTER_X = 150  # nagłówki działów są wyśrodkowane
FOOTNOTE_X = 55  # przypis zaczyna się przy lewym marginesie
SMALL_FONT = 0.96  # linia niższa niż 96% mediany to przypis (lub numer przypisu); przypisy mają 9,96–10,29 pt wobec 11,03 pt treści


@dataclass
class Line:
    x: float
    y: float
    height: float
    text: str


def pdf_to_pages(pdf: Path) -> list[list[Line]]:
    xml = subprocess.run(
        ["pdftotext", "-bbox-layout", str(pdf), "-"], capture_output=True, text=True, check=True
    ).stdout
    pages = []
    for page_xml in PAGE_RE.findall(xml):
        lines = []
        for x, y_min, y_max, body in LINE_RE.findall(page_xml):
            text, prev_end = "", None
            for w_min, w_max, word in WORD_RE.findall(body):
                if prev_end is not None and float(w_min) - prev_end > 0.8:
                    text += " "
                text += html.unescape(word)
                prev_end = float(w_max)
            lines.append(Line(float(x), float(y_min), round(float(y_max) - float(y_min), 2), text))
        pages.append(lines)
    return pages


def join_lines(lines: list[str]) -> str:
    text = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if not text:
            text = line
        elif SUB_RE.match(line):
            text += "\n" + line
        elif text.endswith("-") and line[0].islower():
            text = text[:-1] + line
        else:
            text += " " + line
    return text


def split_page(lines: list[Line], body_height: float) -> tuple[list[Line], list[Line]]:
    """Zwraca (treść, przypisy): przypisy to końcowy ciąg niskich linii strony."""
    lines = [l for l in lines if not PAGE_NOISE_RE.match(l.text.strip()) and not ANNEX_RE.match(l.text)]
    cut = len(lines)
    while cut > 0 and lines[cut - 1].height < body_height * SMALL_FONT:
        cut -= 1
    return lines[:cut], lines[cut:]


def collect_footnotes(note_lines: list[Line], notes: dict[str, str]) -> None:
    number, buf = None, []
    note_lines = sorted(note_lines, key=lambda l: l.y)  # kolejność czytania bywa zaburzona: numer przypisu ląduje na końcu

    def flush():
        if number is not None:
            notes[number] = join_lines(buf)

    for line in note_lines:
        m = FOOTNOTE_START_RE.match(line.text.strip())
        if m and line.x <= FOOTNOTE_X:
            flush()
            number, buf = m.group(1), [m.group(2) or ""]
        else:
            buf.append(line.text)
    flush()


def normalize_superscripts(articles: list[dict]) -> int:
    """W części PDF-ów indeks górny jest zwykłą cyfrą (Art. 4¹ → "41"), w innych `4[1]`.

    Gdy dokument nie używa nawiasów, odtwarza `4[1]` z ciągłości numeracji: id zaczynające się
    od ostatniego numeru bazowego, dłuższe od niego i inne niż bazowy+1, to indeks górny.
    Zwraca liczbę poprawionych id.
    """
    if any("[" in a["art"] for a in articles):
        return 0
    fixed, base = 0, None
    for a in articles:
        m = re.fullmatch(r"(\d+)([a-z]*)", a["art"])
        if not m:
            continue
        digits, letters = m.groups()
        if base is not None:
            b = str(base)
            if digits.startswith(b) and len(digits) > len(b) and int(digits) != base + 1:
                a["art"] = f"{b}[{digits[len(b):]}]{letters}"
                fixed += 1
                continue
        base = int(digits)
    return fixed


def mark_variants(articles: list[dict]) -> None:
    """Tekst jednolity bywa zawiera równoległe wersje tego samego artykułu (zmiana z datą w przyszłości)."""
    totals: dict[str, int] = {}
    for a in articles:
        totals[a["art"]] = totals.get(a["art"], 0) + 1
    counter: dict[str, int] = {}
    for a in articles:
        if totals[a["art"]] > 1:
            counter[a["art"]] = counter.get(a["art"], 0) + 1
            a["variant"] = counter[a["art"]]


def parse(pages: list[list[Line]]) -> dict:
    all_lines = [l for p in pages for l in p]
    # wysokość czcionki treści: z linii otwierających jednostki (tabele i załączniki zawyżają medianę wszystkich linii)
    heights = [l.height for l in all_lines if ART_RE.match(l.text.strip()) and l.x >= ARTICLE_X]
    body_height = statistics.median(heights or [l.height for l in all_lines if len(l.text) > 40] or [11.0])
    has_articles = any(l.x >= ARTICLE_X and l.text.strip().startswith("Art.") and ART_RE.match(l.text.strip())
                       for l in all_lines)

    body_start = next((n for n, p in enumerate(pages) if any(ANNEX_RE.match(l.text) for l in p)), 0)
    lines: list[Line] = []
    notes: dict[str, str] = {}
    for n, page in enumerate(pages):
        if n < body_start:
            continue
        body, note_lines = split_page(page, body_height)
        collect_footnotes(note_lines, notes)
        lines += body

    # prawdziwe jednostki zaczynają się w stałym wcięciu; artykuły cytowane w nowelizacjach są wcięte głębiej
    starts_x = [round(l.x) for l in lines if l.x >= ARTICLE_X and ART_RE.match(l.text.strip())]
    unit_x = max(set(starts_x), key=starts_x.count) if starts_x else None

    units, heading_path, current = [], [], None
    i = 0
    while i < len(lines):
        line, text = lines[i], lines[i].text.strip()
        art = ART_RE.match(text) if line.x >= ARTICLE_X and abs(line.x - (unit_x or line.x)) <= 4 else None
        if art and art.group(1) == "§" and has_articles:  # w aktach z artykułami § to część artykułu
            art = None
        heading = HEADING_RE.match(text) if line.x >= CENTER_X else None
        if heading:
            level = text.split()[0].upper()
            title = text
            nxt = lines[i + 1] if i + 1 < len(lines) else None
            if nxt and nxt.x >= CENTER_X and not HEADING_RE.match(nxt.text.strip()):
                title += " — " + nxt.text.strip()
                i += 1
            depth = HEADING_ORDER.index(level)
            heading_path = [h for h in heading_path if HEADING_ORDER.index(h[0]) < depth] + [(level, title)]
            current = None
        elif art:
            current = {"art": art.group(2) if art.group(1) == "Art." else "§ " + art.group(2), "note": art.group(3), "path": [h[1] for h in heading_path], "lines": [text]}
            units.append(current)
        elif current is not None:
            current["lines"].append(text)
        i += 1

    articles = []
    for u in units:
        article = {"art": u["art"], "path": u["path"], "text": join_lines(u["lines"])}
        if u["note"] in notes:
            article["note"] = notes[u["note"]]
        articles.append(article)
    fixed = normalize_superscripts(articles)
    mark_variants(articles)
    kind = "arti" if has_articles else "para"
    if not articles:  # brak jednostek (skan z warstwą OCR, formularz, tabela): cały tekst jako jedna jednostka
        whole = join_lines([l.text for l in lines])
        articles, kind = [{"art": "całość", "path": [], "text": whole}], "whole"
    return {"articles": articles, "unitKind": kind, "footnotes": notes, "superscriptsRestored": fixed}


def parse_act(raw_dir: Path, out_dir: Path) -> str:
    if (raw_dir / "source.json").exists():  # katalog z fetch_corpus.py
        source = json.loads((raw_dir / "source.json").read_text(encoding="utf-8"))
        meta = {"ELI": source["eli"], "title": source["eli"], "legalStatusDate": None}
        pdf = raw_dir / source["file"] if source["file"].endswith(".pdf") else None
    else:  # katalog z fetch_acts.py
        meta = json.loads((raw_dir / "meta.json").read_text(encoding="utf-8"))
        pdf = next(iter(sorted(raw_dir.glob("*L.pdf"))), None)
    if pdf is None or not pdf.exists():
        return f"{meta['ELI']}: brak PDF"
    result = parse(pdf_to_pages(pdf))
    result.update({"eli": meta["ELI"], "title": meta["title"], "source": pdf.name,
                   "legalStatusDate": meta.get("legalStatusDate")})
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{meta['ELI'].replace('/', '_')}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return f"{meta['ELI']}: {len(result['articles'])} artykułów, {len(result['footnotes'])} przypisów"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("raw_dirs", nargs="+", type=Path)
    parser.add_argument("--out", default="data/parsed", type=Path)
    args = parser.parse_args()
    for raw_dir in args.raw_dirs:
        try:
            print(parse_act(raw_dir, args.out))
        except (subprocess.CalledProcessError, FileNotFoundError, KeyError, statistics.StatisticsError) as e:
            print(f"{raw_dir}: błąd {e!r}", file=sys.stderr)


if __name__ == "__main__":
    main()
