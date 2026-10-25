#!/usr/bin/env python3
"""Pobiera aktualny tekst aktów z korpusu (rdzeń: status `obowiązujący` i `akt posiada tekst jednolity`).

Źródło tekstu zależy od grupy aktu (zob. PLAN.md, F3):
  D  niezmieniany                      → text.html albo PDF typu O
  A  zmieniany, ma tekst jednolity     → PDF typu T z najnowszego obwieszczenia
  B  zmieniany, brak t.j., jest U      → PDF typu U
  C  zmieniany, brak t.j., brak U      → jak D, z oznaczeniem tekst_nieaktualny

Wynik: data/texts/<ELI>/ (plik tekstu + source.json). Istniejące pomija.

Użycie:
    python3 scripts/fetch_corpus.py --sample 100          # próba do oszacowania rozmiaru
    python3 scripts/fetch_corpus.py                       # cały rdzeń z data/inforce
"""
import argparse
import glob
import http.client
import json
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = "https://api.sejm.gov.pl/eli/acts"
USER_AGENT = "aiLawyer-fetch/0.1"
CORE_STATUSES = {"obowiązujący", "akt posiada tekst jednolity"}
WORKERS = 4


def get(url: str, retries: int = 8) -> bytes | None:
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(min(1.5 ** attempt, 15))
        except (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError):
            time.sleep(min(1.5 ** attempt, 15))
    raise RuntimeError(f"nie udało się pobrać {url}")


def group_of(record: dict) -> str:
    refs = record["references"]
    if not refs.get("Akty zmieniające"):
        return "D"
    if refs.get("Inf. o tekście jednolitym"):
        return "A"
    return "B" if any(t["type"] == "U" for t in record["texts"]) else "C"


def file_of(record: dict, kind: str) -> str | None:
    return next((t["fileName"] for t in record["texts"] if t["type"] == kind), None)


def act_url(eli: str) -> str:
    return f"{BASE}/{eli}"


def latest_consolidated(record: dict) -> str:
    """ELI najnowszego obwieszczenia o tekście jednolitym (DU: wyższy rok, potem pozycja)."""
    ids = [r["id"] for r in record["references"]["Inf. o tekście jednolitym"]]
    return max(ids, key=lambda i: tuple(int(p) for p in i.split("/")[1:]))


def resolve(record: dict) -> tuple[str, str, str, bool]:
    """Zwraca (adres pliku, nazwa pliku lokalnego, grupa, tekst_nieaktualny)."""
    group, eli = group_of(record), record["ELI"]
    if group == "A":
        announcement = latest_consolidated(record)
        meta = json.loads(get(act_url(announcement)))
        if file_of(meta, "T"):  # obwieszczenie Marszałka Sejmu: PDF z załącznikiem
            name = file_of(meta, "T")
            return f"{act_url(announcement)}/text/T/{name}", name, group, False
        if meta["textHTML"]:  # obwieszczenia ministrów: tekst jednolity w HTML
            return f"{act_url(announcement)}/text.html", "text.html", group, False
        name = file_of(meta, "O")
        return f"{act_url(announcement)}/text/O/{urllib.parse.quote(name)}", name, group, False
    if group == "B":
        name = file_of(record, "U")
        return f"{act_url(eli)}/text/U/{name}", name, group, False
    if record["textHTML"]:
        return f"{act_url(eli)}/text.html", "text.html", group, group == "C"
    kind = next(k for k in ("O", "T", "I") if file_of(record, k))
    name = file_of(record, kind)
    return f"{act_url(eli)}/text/{kind}/{urllib.parse.quote(name)}", name, group, group == "C"


def fetch(record: dict, out_dir: Path) -> dict:
    eli = record["ELI"]
    target = out_dir / eli.replace("/", "_")
    marker = target / "source.json"
    if marker.exists():
        return json.loads(marker.read_text(encoding="utf-8"))
    url, name, group, stale = resolve(record)
    body = get(url)
    result = {"eli": eli, "group": group, "file": name, "url": url, "stale": stale, "bytes": len(body or b"")}
    if body:
        target.mkdir(parents=True, exist_ok=True)
        (target / name).write_bytes(body)
        marker.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    else:
        result["error"] = "404"
    return result


def load_core(inforce_glob: str) -> list[dict]:
    records = []
    for path in sorted(glob.glob(inforce_glob)):
        with open(path, encoding="utf-8") as f:
            records += [r for r in map(json.loads, f) if r["status"] in CORE_STATUSES]
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--sample", type=int, help="losowa próba N aktów (do oszacowania rozmiaru)")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--workers", type=int, default=WORKERS)
    parser.add_argument("--reverse", action="store_true", help="od końca listy (drugi proces równolegle)")
    parser.add_argument("--inforce", default="data/inforce/*.jsonl")
    parser.add_argument("--out", default="data/texts", type=Path)
    args = parser.parse_args()

    records = load_core(args.inforce)
    if args.sample:
        records = random.Random(args.seed).sample(records, args.sample)
    if args.reverse:
        records.reverse()
    args.out.mkdir(parents=True, exist_ok=True)

    def safe(record):
        try:
            return fetch(record, args.out)
        except (RuntimeError, TypeError, KeyError) as e:
            return {"eli": record["ELI"], "group": group_of(record), "error": repr(e), "bytes": 0}

    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(safe, records))

    errors = [r for r in results if r.get("error")]
    print(f"akty: {len(results)}, błędy: {len(errors)}", file=sys.stderr)
    for group in "ABCD":
        sizes = [r["bytes"] for r in results if r["group"] == group and not r.get("error")]
        if sizes:
            print(f"grupa {group}: {len(sizes)} aktów, średnio {sum(sizes) / len(sizes) / 1024:.0f} KB, maks {max(sizes) / 1024:.0f} KB")
    for r in errors[:5]:
        print("BŁĄD", r["eli"], r.get("error"), file=sys.stderr)


if __name__ == "__main__":
    main()
