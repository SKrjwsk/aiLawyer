#!/usr/bin/env python3
"""Parsuje text.html aktu z Sejm ELI do jednostek redakcyjnych: data/parsed/<ELI>.json.

Format wyjściowy jest zgodny z parse_pdf.py (`articles`: art, path, text). Jednostką główną
jest artykuł (`unit_arti`); gdy akt nie ma artykułów (typowe dla rozporządzeń), jednostką
jest paragraf (`unit_para`, id `§ N`), a gdy brak i tych — ustęp (`unit_pass`).
Tekst jednostki zawiera jej podjednostki (ustępy, punkty, litery), każda w osobnej linii.
Tylko stdlib.

Użycie:
    python3 scripts/parse_html.py data/texts/DU_2024_310
    python3 scripts/parse_html.py data/texts/*
"""
import argparse
import html
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

HEADING_UNITS = {"part", "bran", "chpt", "schp", "book", "titl"}
MAIN_ORDER = ["arti", "para", "pass"]


class Unit:
    def __init__(self, kind: str, parent: "Unit | None"):
        self.kind, self.parent = kind, parent
        self.quoted = False  # cytat lub tekst zastępujący w przepisie nowelizującym — nie jest samodzielną jednostką
        self.label_parts: list[str] = []
        self.text_parts: list[str] = []
        self.children: list[Unit] = []

    @property
    def label(self) -> str:
        return clean(" ".join(self.label_parts))

    @property
    def text(self) -> str:
        return clean(" ".join(self.text_parts))


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(s).replace("\xa0", " ")).strip()


class ActParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Unit("root", None)
        self.current = self.root
        self.div_stack: list[Unit | None] = []  # dla każdego <div>: jednostka, którą otwiera, albo None
        self.in_label = 0
        self.in_text = 0
        self.text_depth: list[bool] = []  # dla każdego <div>: czy otwiera blok tekstu

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "div":
            cls = a.get("class", "")
            m = re.search(r"\bunit_(\w+)", cls) if re.search(r"\bunit\b", cls) else None
            if m:
                unit = Unit(m.group(1), self.current)
                unit.quoted = bool(re.search(r"pro-(cite|rplc)-text", cls)) or self.current.quoted
                self.current.children.append(unit)
                self.current = unit
                self.div_stack.append(unit)
                self.text_depth.append(False)
            else:
                opens_text = a.get("data-template") == "xText"
                self.in_text += opens_text
                self.div_stack.append(None)
                self.text_depth.append(opens_text)
        elif tag == "h3":
            self.in_label += 1
        elif tag in ("br", "p", "tr") and self.in_text:
            self.current.text_parts.append(" ")

    def handle_endtag(self, tag):
        if tag == "div" and self.div_stack:
            unit = self.div_stack.pop()
            if self.text_depth.pop():
                self.in_text -= 1
            if unit is not None:
                self.current = unit.parent or self.root
        elif tag == "h3" and self.in_label:
            self.in_label -= 1

    def handle_data(self, data):
        if self.current is self.root:
            return
        if self.in_label:
            self.current.label_parts.append(data)
        elif self.in_text:
            self.current.text_parts.append(data)


def render(unit: Unit, depth: int = 0) -> str:
    own = f"{unit.label} {unit.text}".strip()
    lines = [own] if own else []
    for child in unit.children:
        if child.kind not in HEADING_UNITS and (child.kind != "arti" or child.quoted):
            lines.append(render(child, depth + 1))
    return "\n".join(l for l in lines if l)


def heading_title(unit: Unit) -> str:
    return unit.label


def walk(unit: Unit, path: list[str], kind: str, out: list[dict]) -> None:
    for child in unit.children:
        if child.quoted:
            continue
        if child.kind == kind:
            out.append({"art": normalize_id(child.label, kind), "path": list(path), "text": render(child)})
        elif child.kind in HEADING_UNITS:
            walk(child, path + [heading_title(child)], kind, out)
        else:
            walk(child, path, kind, out)


def normalize_id(label: str, kind: str) -> str:
    label = re.sub(r"^(Art\.|§)\s*", "", label).rstrip(". ").strip()
    return label if kind == "arti" else f"§ {label}" if kind == "para" else label


def count_kind(unit: Unit, kind: str) -> int:
    return sum((c.kind == kind and not c.quoted) + count_kind(c, kind) for c in unit.children)


def parse(html_text: str) -> dict:
    parser = ActParser()
    parser.feed(html_text)
    kind = next((k for k in MAIN_ORDER if count_kind(parser.root, k)), None)
    units: list[dict] = []
    if kind:
        walk(parser.root, [], kind, units)
    if not units:  # akt bez jednostek redakcyjnych (wyrok, umowa, załącznik): cały tekst jako jedna jednostka
        body = html_text[html_text.find("<body"):]
        body = re.sub(r"<(script|style)\b.*?</\1>", " ", body, flags=re.S | re.I)
        body = re.sub(r"</(p|div|tr|li|h\d)>", "\n", body, flags=re.I)
        text = "\n".join(clean(line) for line in html.unescape(re.sub(r"<[^>]+>", " ", body)).split("\n") if clean(line))
        units, kind = [{"art": "całość", "path": [], "text": text}], "whole"
    return {"articles": units, "unitKind": kind, "footnotes": {}}


def parse_act(text_dir: Path, out_dir: Path) -> str:
    source = json.loads((text_dir / "source.json").read_text(encoding="utf-8"))
    path = text_dir / "text.html"
    if not path.exists():
        return f"{source['eli']}: nie HTML"
    result = parse(path.read_text(encoding="utf-8", errors="replace"))
    result.update({"eli": source["eli"], "group": source["group"], "source": source["url"], "stale": source["stale"]})
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{source['eli'].replace('/', '_')}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return f"{source['eli']}: {len(result['articles'])} jednostek ({result['unitKind']})"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("text_dirs", nargs="+", type=Path)
    parser.add_argument("--out", default="data/parsed_html", type=Path)
    args = parser.parse_args()
    for text_dir in args.text_dirs:
        try:
            print(parse_act(text_dir, args.out))
        except (OSError, KeyError, ValueError) as e:
            print(f"{text_dir}: błąd {e!r}", file=sys.stderr)


if __name__ == "__main__":
    main()
