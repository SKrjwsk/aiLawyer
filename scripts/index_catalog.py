#!/usr/bin/env python3
"""Buduje katalog aktów (lekkie metadane) z Sejm ELI API do data/catalog/<wydawca>_<rok>.jsonl.

Użycie:
    python3 scripts/index_catalog.py DU            # wszystkie lata wydawcy
    python3 scripts/index_catalog.py DU --years 2025 2026

Istniejące pliki lat pomija, poza bieżącym rokiem (--refresh nadpisuje wszystkie).
"""
import argparse
import datetime
import http.client
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://api.sejm.gov.pl/eli/acts"
USER_AGENT = "aiLawyer-fetch/0.1"
FIELDS = (
    "ELI", "type", "status", "inForce", "textHTML", "textPDF", "title",
    "announcementDate", "legalStatusDate", "keywords",
)


def get_json(url: str, retries: int = 5):
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, http.client.HTTPException, ConnectionError, json.JSONDecodeError) as e:
            print(f"  {getattr(e, 'reason', e)} {url} (próba {attempt}/{retries})", file=sys.stderr)
            time.sleep(2 * attempt)
    raise RuntimeError(f"nie udało się pobrać {url}")


def slim(item: dict) -> dict:
    row = {k: item.get(k) for k in FIELDS}
    row["consolidatedFor"] = [r["id"] for r in item.get("references", {}).get("Tekst jednolity dla aktu", [])]
    return row


def index_year(publisher: str, year: int, out_path: Path) -> int:
    data = get_json(f"{BASE}/{publisher}/{year}")
    items, total = data["items"], data["totalCount"]
    if len(items) != total:
        raise RuntimeError(f"{publisher}/{year}: pobrano {len(items)} z {total}")
    with out_path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(slim(item), ensure_ascii=False) + "\n")
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("publisher", help="DU albo MP")
    parser.add_argument("--years", nargs="*", type=int)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--out", default="data/catalog", type=Path)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    years = args.years
    if not years:
        publishers = get_json(BASE)
        years = next(p["years"] for p in publishers if p["code"] == args.publisher)

    current_year = datetime.date.today().year
    for year in years:
        out_path = args.out / f"{args.publisher}_{year}.jsonl"
        if out_path.exists() and not args.refresh and year != current_year:
            continue
        print(f"{args.publisher}/{year}: {index_year(args.publisher, year, out_path)}")
        time.sleep(0.5)


if __name__ == "__main__":
    main()
