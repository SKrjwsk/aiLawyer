#!/usr/bin/env python3
"""Pobiera akty prawne z Sejm ELI API do data/raw/<publisher>_<rok>_<poz>/.

Użycie:
    python3 scripts/fetch_acts.py DU/1964/16 DU/1997/88
    python3 scripts/fetch_acts.py --from-file adresy.txt

Adres = <wydawca>/<rok>/<pozycja> (DU = Dziennik Ustaw, MP = Monitor Polski).
Na akt zapisuje meta.json oraz text.html; istniejące pliki pomija.
"""
import argparse
import http.client
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://api.sejm.gov.pl/eli/acts"
USER_AGENT = "aiLawyer-fetch/0.1"


def get(url: str, retries: int = 5) -> bytes | None:
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            print(f"  HTTP {e.code} {url} (próba {attempt}/{retries})", file=sys.stderr)
        except (urllib.error.URLError, http.client.HTTPException, ConnectionError) as e:
            print(f"  {getattr(e, 'reason', e)} {url} (próba {attempt}/{retries})", file=sys.stderr)
        time.sleep(2 * attempt)
    raise RuntimeError(f"nie udało się pobrać {url}")


def latest_consolidated(code_name: str) -> str:
    """Adres najnowszego obwieszczenia o tekście jednolitym, np. 'Kodeks karny'."""
    query = urllib.parse.urlencode({"title": f"jednolitego tekstu ustawy - {code_name}", "publisher": "DU", "type": "Obwieszczenie", "limit": 10})
    items = json.loads(get(f"{BASE}/search?{query}") or b"{}").get("items", [])
    exact = [i for i in items if i["title"].rstrip(". ").endswith(f"ustawy - {code_name}")]
    if not exact:
        raise RuntimeError(f"brak tekstu jednolitego dla: {code_name}")
    return max(exact, key=lambda i: i["announcementDate"])["ELI"]


def fetch_act(address: str, out_dir: Path, delay: float) -> None:
    publisher, year, pos = address.strip("/").split("/")
    target = out_dir / f"{publisher}_{year}_{pos}"
    target.mkdir(parents=True, exist_ok=True)

    url = f"{BASE}/{publisher}/{year}/{pos}"
    meta_path = target / "meta.json"
    if not meta_path.exists():
        body = get(url)
        time.sleep(delay)
        if body is None:
            print(f"{address}: brak aktu (404)", file=sys.stderr)
            return
        meta_path.write_bytes(body)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    text_path = target / "text.html"
    if meta.get("textHTML") and not (text_path.exists() and text_path.stat().st_size):
        body = get(f"{url}/text.html")
        time.sleep(delay)
        if body:
            text_path.write_bytes(body)

    # T = tekst jednolity (obwieszczenie), U = tekst ujednolicony (akt z naniesionymi zmianami)
    for entry in meta.get("texts", []):
        if entry.get("type") not in ("T", "U"):
            continue
        pdf_path = target / entry["fileName"]
        if pdf_path.exists() and pdf_path.stat().st_size:
            continue
        body = get(f"{url}/text/{entry['type']}/{entry['fileName']}")
        time.sleep(delay)
        if body:
            pdf_path.write_bytes(body)
        else:
            print(f"{address}: brak {entry['fileName']} (404)", file=sys.stderr)

    html = "html" if text_path.exists() and text_path.stat().st_size else "BEZ HTML"
    print(f"{address} [{meta.get('status')}, {html}]: {meta.get('title', '?')}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("addresses", nargs="*", help="np. DU/1964/16")
    parser.add_argument("--code", action="append", default=[], help='np. "Kodeks karny" (najnowszy tekst jednolity)')
    parser.add_argument("--from-file", help="plik z adresami, jeden na linię")
    parser.add_argument("--out", default="data/raw", type=Path)
    parser.add_argument("--delay", default=0.5, type=float, help="przerwa między requestami [s]")
    args = parser.parse_args()

    addresses = list(args.addresses)
    if args.from_file:
        lines = Path(args.from_file).read_text(encoding="utf-8").splitlines()
        addresses += [l.strip() for l in lines if l.strip() and not l.startswith("#")]
    addresses += [latest_consolidated(name) for name in args.code]
    if not addresses:
        parser.error("podaj adresy aktów, --code albo --from-file")

    for address in addresses:
        fetch_act(address, args.out, args.delay)


if __name__ == "__main__":
    main()
