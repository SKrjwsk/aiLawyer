#!/usr/bin/env python3
"""Pobiera pełne rekordy aktów obowiązujących (inForce=1) z Sejm ELI API.

Wynik: data/inforce/<wydawca>_<rok>.jsonl — jeden pełny rekord ELI na linię
(z polami `texts` i `references`). Dzieli zapytania po latach, żeby stronicowanie
było deterministyczne. Istniejące pliki lat pomija, poza bieżącym rokiem.

Użycie:
    python3 scripts/index_inforce.py DU MP
    python3 scripts/index_inforce.py DU --years 2025 2026 --refresh
"""
import argparse
import datetime
import http.client
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = "https://api.sejm.gov.pl/eli/acts"
USER_AGENT = "aiLawyer-fetch/0.1"
PAGE = 10  # większe strony bywają ucinane po stronie API (IncompleteRead)
WORKERS = 4


class FetchError(RuntimeError):
    pass


def get_json(url: str, retries: int):
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, http.client.HTTPException, ConnectionError, json.JSONDecodeError) as e:
            print(f"  {getattr(e, 'reason', e)} (próba {attempt}/{retries})", file=sys.stderr)
            time.sleep(min(1.5 ** attempt, 15))
    raise FetchError(url)


def search(publisher: str, year: int, offset: int, limit: int, retries: int) -> dict:
    query = urllib.parse.urlencode({
        "publisher": publisher, "year": year, "inForce": "1",
        "sortBy": "position", "sortDir": "asc", "limit": limit, "offset": offset,
    })
    return get_json(f"{BASE}/search?{query}", retries)


def fetch_page(publisher: str, year: int, offset: int, limit: int) -> list[dict]:
    """Część odpowiedzi API bywa ucinana deterministycznie: nieudaną stronę dzielimy na mniejsze."""
    try:
        return search(publisher, year, offset, limit, retries=3 if limit > 1 else 10)["items"]
    except FetchError:
        if limit == 1:
            raise
        half = limit // 2
        return fetch_page(publisher, year, offset, half) + fetch_page(publisher, year, offset + half, limit - half)


def index_year(publisher: str, year: int, out_path: Path) -> int:
    total = search(publisher, year, 0, 1, retries=10)["totalCount"]
    items: list[dict] = []
    while len(items) < total:
        items += fetch_page(publisher, year, len(items), min(PAGE, total - len(items)))
    if len({i["ELI"] for i in items}) != total:
        raise RuntimeError(f"{publisher}/{year}: {len(items)} rekordów, oczekiwano {total}")
    tmp = out_path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    tmp.rename(out_path)
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("publishers", nargs="+", help="DU, MP")
    parser.add_argument("--years", nargs="*", type=int)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--reverse", action="store_true", help="od najnowszych lat (do równoległej pracy z drugim procesem)")
    parser.add_argument("--out", default="data/inforce", type=Path)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    current_year = datetime.date.today().year
    meta = {p["code"]: p for p in get_json(BASE, retries=10)}

    jobs = []
    for publisher in args.publishers:
        for year in args.years or meta[publisher]["years"]:
            out_path = args.out / f"{publisher}_{year}.jsonl"
            if out_path.exists() and not args.refresh and year != current_year:
                continue
            jobs.append((publisher, year, out_path))

    if args.reverse:
        jobs.reverse()

    def run(job):
        publisher, year, out_path = job
        if out_path.exists() and not args.refresh and year != current_year:
            return 0  # drugi proces zdążył wcześniej
        count = index_year(publisher, year, out_path)
        print(f"{publisher}/{year}: {count}", flush=True)
        return count

    failed = []

    def safe_run(job):
        try:
            return run(job)
        except (FetchError, RuntimeError) as e:
            failed.append(job[:2])
            print(f"BŁĄD {job[0]}/{job[1]}: {e}", flush=True)
            return 0

    with ThreadPoolExecutor(WORKERS) as pool:
        total = sum(pool.map(safe_run, jobs))
    print(f"razem: {total}; nieudane lata: {sorted(failed)}")


if __name__ == "__main__":
    main()
