#!/usr/bin/env python3
"""Aktualizacja bazy: pobiera zmienione akty z /changes/acts i odświeża korpus.

Dla każdego zmienionego aktu (od czasu ostatniej synchronizacji w data/state.json):
  1. pobiera pełny rekord ELI,
  2. aktualizuje data/inforce/<wydawca>_<rok>.jsonl (rekord wpada, gdy inForce, wypada, gdy nie),
  3. jeśli akt należy do rdzenia (status `obowiązujący` / `akt posiada tekst jednolity`) — pobiera
     aktualny tekst (fetch_corpus.fetch) i parsuje go do data/parsed_corpus/,
  4. jeśli nie należy (już/jeszcze) — usuwa jego teksty i wynik parsowania.
Nowe obwieszczenie o tekście jednolitym odświeża akt bazowy (references["Tekst jednolity dla aktu"]).

Użycie:
    python3 scripts/update.py --dry-run          # tylko lista zmian
    python3 scripts/update.py                    # od ostatniej synchronizacji
    python3 scripts/update.py --since 2026-10-20T00:00:00
"""
import argparse
import datetime
import json
import shutil
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fetch_corpus as F  # noqa: E402
import parse_html  # noqa: E402
import parse_pdf  # noqa: E402

CHANGES_URL = "https://api.sejm.gov.pl/eli/changes/acts"
PAGE = 100


def fetch_changes(since: str) -> list[dict]:
    items, offset = [], 0
    while True:
        query = urllib.parse.urlencode({"since": since, "limit": PAGE, "offset": offset})
        data = json.loads(F.get(f"{CHANGES_URL}?{query}"))
        items += data["items"]
        offset += len(data["items"])
        if not data["items"] or offset >= data["totalCount"]:
            return items


def update_inforce_file(inforce_dir: Path, record: dict) -> None:
    publisher, year, _ = record["ELI"].split("/")
    path = inforce_dir / f"{publisher}_{year}.jsonl"
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    rows = [r for r in rows if r["ELI"] != record["ELI"]]
    if record.get("inForce") == "IN_FORCE":
        rows.append(record)
    rows.sort(key=lambda r: int(r["ELI"].split("/")[2]))
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def parse_dir(text_dir: Path, parsed_dir: Path) -> None:
    if (text_dir / "text.html").exists():
        parse_html.parse_act(text_dir, parsed_dir)
    else:
        parse_pdf.parse_act(text_dir, parsed_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--since", help="ISO, np. 2026-10-20T00:00:00 (domyślnie z data/state.json)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--data", default="data", type=Path)
    args = parser.parse_args()

    state_path = args.data / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    since = args.since or state.get("lastSync")
    if not since:
        parser.error("brak data/state.json — podaj --since (data zakończenia pełnego pobrania)")
    started = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    changes = fetch_changes(since)
    print(f"zmienionych aktów od {since}: {len(changes)}")

    to_refresh = {c["ELI"]: c for c in changes}
    for change in changes:
        for ref in change.get("references", {}).get("Tekst jednolity dla aktu", []):
            to_refresh.setdefault(ref["id"], {"ELI": ref["id"]})

    stats = {"dodane": 0, "usunięte": 0, "odświeżone": 0}
    for eli in sorted(to_refresh):
        record = json.loads(F.get(f"{F.BASE}/{eli}"))
        core = record["status"] in F.CORE_STATUSES and record.get("inForce") == "IN_FORCE"
        text_dir = args.data / "texts" / eli.replace("/", "_")
        was_present = text_dir.exists()
        action = "odśwież" if core and was_present else "dodaj" if core else "usuń" if was_present else "pomiń"
        print(f"  {eli:16} {record['status']:30} → {action}")
        if args.dry_run:
            continue
        update_inforce_file(args.data / "inforce", record)
        parsed = args.data / "parsed_corpus" / f"{eli.replace('/', '_')}.json"
        if core:
            shutil.rmtree(text_dir, ignore_errors=True)
            F.fetch(record, args.data / "texts")
            parse_dir(text_dir, args.data / "parsed_corpus")
            stats["odświeżone" if was_present else "dodane"] += 1
        elif was_present:
            shutil.rmtree(text_dir, ignore_errors=True)
            parsed.unlink(missing_ok=True)
            stats["usunięte"] += 1
        time.sleep(0.3)

    if not args.dry_run:
        state_path.write_text(json.dumps({"lastSync": started, "last": stats}, indent=1), encoding="utf-8")
        print(stats)


if __name__ == "__main__":
    main()
