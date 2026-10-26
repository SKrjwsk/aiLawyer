#!/usr/bin/env python3
"""Lokalna strona do testowania wyszukiwania: pytanie → trzy listy artykułów (FTS5, wektory, hybryda).

Serwer słucha tylko na 127.0.0.1. Wymaga data/lawdb.sqlite; wektory wymagają .venv z fastembed
(bez nich strona pokazuje sam FTS5 z komunikatem).

Przyciski „LLM” (przepisanie pytania, odpowiedź z cytatami) wołą płatne API, więc idą wyłącznie przez POST z kontrolą
nagłówków Host i Origin; klucz tylko ze zmiennej środowiskowej ANTHROPIC_API_KEY procesu serwera.

Użycie (.venv z fastembed):
    .venv/bin/python scripts/serve.py [--port 8765]
    ANTHROPIC_API_KEY='<KLUCZ>' .venv/bin/python scripts/serve.py    # z przyciskami LLM
"""
import argparse
import html
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from string import Template
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).parent))
import answer as ai  # noqa: E402
import llm  # noqa: E402
from search import CODES, MODES, Index  # noqa: E402

MAX_QUERY = 500
MAX_BODY = 4096
ANSWER_ARTICLES = 8  # tyle pierwszych artykułów z listy hybrydowej dostaje model
DISCLAIMER = "To nie jest porada prawna. Odpowiedź wygenerował model wyłącznie na podstawie wyświetlonych artykułów — sprawdź cytaty w treści przepisu."
PREVIEW_CHARS = 350
COLUMNS = (("fts", "FTS5 (BM25)"), ("vector", "Wektory"), ("hybrid", "Hybryda (FTS5 + wektory, RRF)"))
SCOPES = {"codes": "8 kodeksów", "all": "cały korpus"}
MODE_LABELS = {"words": "pełne słowa", "prefix4": "prefiks 4 znaków", "prefix5": "prefiks 5 znaków", "prefix6": "prefiks 6 znaków"}

PAGE = Template("""<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>aiLawyer — wyszukiwarka</title>
<style>
:root { color-scheme: light dark; --bg: #fafaf8; --fg: #1d1d1b; --muted: #66665f; --card: #fff; --line: #dcdcd4; --accent: #1f5f8b; --warn: #8a5a00; }
@media (prefers-color-scheme: dark) { :root { --bg: #161615; --fg: #ececE6; --muted: #9a9a92; --card: #1f1f1d; --line: #353532; --accent: #6db3e0; --warn: #e0b04a; } }
* { box-sizing: border-box; }
body { margin: 0; padding: 24px 16px 48px; background: var(--bg); color: var(--fg); font: 15px/1.5 system-ui, sans-serif; }
main { max-width: 1500px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 16px; }
form { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
input[type=text] { flex: 1 1 360px; padding: 10px 12px; font: inherit; color: inherit; background: var(--card); border: 1px solid var(--line); border-radius: 6px; }
select, button { padding: 10px 12px; font: inherit; color: inherit; background: var(--card); border: 1px solid var(--line); border-radius: 6px; }
button { background: var(--accent); color: #fff; border-color: var(--accent); cursor: pointer; }
.hint, .meta { color: var(--muted); font-size: 13px; }
.hint { margin: 8px 0 0; }
.warn { color: var(--warn); }
.cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 420px), 1fr)); gap: 16px; margin-top: 20px; align-items: start; }
.col h2 { font-size: 16px; margin: 0 0 8px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; margin-bottom: 10px; }
.card h3 { font-size: 14px; margin: 0 0 4px; }
.card p { margin: 6px 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.tag { display: inline-block; font-size: 12px; padding: 0 6px; border: 1px solid var(--warn); border-radius: 4px; color: var(--warn); margin-left: 4px; }
summary { cursor: pointer; color: var(--accent); font-size: 13px; }
.answer { background: var(--card); border: 1px solid var(--accent); border-radius: 8px; padding: 12px 14px; margin-top: 20px; }
.answer h2 { font-size: 16px; margin: 0 0 8px; }
.answer-text { white-space: pre-wrap; overflow-wrap: anywhere; }
.cite { font-weight: 600; color: var(--accent); }
.cite.bad { color: var(--warn); text-decoration: underline wavy; }
a { color: var(--accent); }
</style>
</head>
<body>
<main>
<h1>aiLawyer — wyszukiwarka przepisów</h1>
<form method="get" action="/">
<input type="text" name="q" value="$q" maxlength="$max_query" placeholder="np. kradzież cudzej rzeczy ruchomej" autofocus>
<select name="scope" aria-label="Zakres">$scope_options</select>
<select name="mode" aria-label="Tryb FTS5">$mode_options</select>
<button type="submit">Szukaj</button>
<button type="submit" formmethod="post" name="llm" value="rewrite" title="Model przepisuje pytanie na język ustawy; wyszukiwanie leci na przepisanych zapytaniach">Przepisz pytanie i szukaj (LLM)</button>
<button type="submit" formmethod="post" name="llm" value="answer" title="Przepisanie pytania, wyszukiwanie i odpowiedź modelu z cytatami">Odpowiedz (LLM)</button>
</form>
<p class="hint">Wyszukiwanie działa na słowach z przepisów, nie na języku potocznym — pisz terminami z ustawy (np. „przedawnienie roszczeń”, a nie „kiedy dług się przedawnia”). Przyciski „LLM” przepisują pytanie potoczne na język ustawy.</p>
$notice$results
</main>
</body>
</html>
""")


def e(value) -> str:
    return html.escape("" if value is None else str(value))


def options(choices: dict, selected: str) -> str:
    return "".join(f'<option value="{e(k)}"{" selected" if k == selected else ""}>{e(v)}</option>' for k, v in choices.items())


def page(q: str, scope: str, mode: str, notice: str = "", results: str = "") -> str:
    return PAGE.substitute(q=e(q), max_query=MAX_QUERY, scope_options=options(SCOPES, scope),
                           mode_options=options(MODE_LABELS, mode), notice=notice, results=results)


def card(a: dict, pos: int) -> str:
    title = f"{a['code'] + ' — ' if a['code'] else ''}{a['title'] or a['eli']}"
    labels = [f"wariant {a['variant']}" if a["variant"] else "", "tekst może być nieaktualny" if a["stale"] else ""]
    tags = "".join(f'<span class="tag">{e(t)}</span>' for t in labels if t)
    text = a["text"]
    preview = text[:PREVIEW_CHARS] + ("…" if len(text) > PREVIEW_CHARS else "")
    body = f"<p>{e(preview)}</p>"
    if len(text) > PREVIEW_CHARS:
        cut = f'<p class="meta">Pokazano {len(text)} z {a["length"]} znaków.</p>' if a["length"] > len(text) else ""
        body = f"<details><summary>{e(preview)}</summary><p>{e(text)}</p>{cut}</details>"
    src = a["source"]
    src_link = f'<a href="{e(src["url"])}" rel="noreferrer">{e(src["label"])}</a>' if src["url"] else e(src["label"])
    eli_link = f'<a href="https://api.sejm.gov.pl/eli/acts/{e(a["eli"])}" rel="noreferrer">{e(a["eli"])}</a>'
    note = f'<p class="meta">Uwaga: {e(a["note"])}</p>' if a["note"] else ""
    path = f'<div class="meta">{e(a["path"])}</div>' if a["path"] else ""
    return (f'<div class="card"><h3>{pos}. Art. {e(a["art"])}{tags}</h3><div class="meta">{e(title)}</div>{path}'
            f'{body}{note}<div class="meta">ELI: {eli_link} · źródło: {src_link}</div></div>')


def warn(text: str) -> str:
    return f'<p class="hint warn">{e(text)}</p>'


def render_answer(articles: list[dict], reply: llm.Reply) -> str:
    parts = ai.split_citations(reply.text, articles)
    body = "".join(e(t) if kind == "text" else f'<span class="cite{" bad" if kind == "bad" else ""}">{e(t)}</span>'
                   for kind, t in parts)
    bad = [t for kind, t in parts if kind == "bad"]
    unverified = warn(f"Uwaga: cytaty spoza listy przekazanych artykułów (niesprawdzone): {', '.join(bad)}.") if bad else ""
    used = " ".join(f"[{ai.label(a)}]" for a in articles)
    return (f'<section class="answer"><h2>Odpowiedź modelu</h2><div class="answer-text">{body}</div>{unverified}'
            f'<p class="meta">Przekazane artykuły: {e(used)}<br>Model: {e(reply.model)} · tokeny: wejście {reply.input_tokens}, '
            f'wyjście {reply.output_tokens}</p><p class="meta">{e(DISCLAIMER)}</p></section>')


def render(index: Index, q: str, scope: str, mode: str, action: str | None = None) -> str:
    notice, results, queries = "", "", [q]
    if index.vector_error:
        notice += warn(f"Wektory niedostępne, działa tylko FTS5 ({index.vector_error}).")
    elif index.missing_vectors:
        notice += warn(f"{index.missing_vectors} artykułów nie ma wektora (uruchom scripts/embed_store.py sync) — "
                       "w kolumnach z wektorami ich nie będzie.")
    if action and not llm.has_key():
        notice += warn(llm.NO_KEY_MESSAGE)
        action = None
    if q:
        if action:
            try:
                queries = ai.rewrite(q)
                notice += f'<p class="hint">Użyte zapytania: {" · ".join("„" + e(x) + "”" for x in queries)}</p>'
            except llm.LLMError as error:
                notice += warn(f"Przepisanie pytania nie powiodło się: {error} Wyszukano po oryginalnym pytaniu.")
        found = index.search_many(queries, scope, mode)
        if action == "answer":
            try:
                articles = found["hybrid"][:ANSWER_ARTICLES]
                results += render_answer(articles, ai.answer(q, articles))
            except llm.LLMError as error:
                notice += warn(f"Odpowiedź modelu niedostępna: {error}")
        columns = ""
        for key, title in COLUMNS:
            cards = "".join(card(a, pos) for pos, a in enumerate(found[key], 1)) or '<p class="meta">Brak wyników.</p>'
            columns += f'<section class="col"><h2>{e(title)}</h2>{cards}</section>'
        ms = found["ms"]
        notice += f'<p class="hint">Czas: FTS5 {ms["fts"]} ms, wektory {ms["vector"]} ms.</p>'
        results += f'<div class="cols">{columns}</div>'
    return page(q, scope, mode, notice, results)


class Handler(BaseHTTPRequestHandler):
    index: Index

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        if url.path != "/":
            self.reply(404, "<p>Nie ma takiej strony.</p>")
            return
        self.respond(parse_qs(url.query), None)

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/":
            self.reply(404, "<p>Nie ma takiej strony.</p>")
            return
        if not self.host_ok():
            self.reply(403, "<p>Niedozwolony nagłówek Host lub Origin.</p>")
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            self.reply(413, "<p>Nieprawidłowy rozmiar żądania.</p>")
            return
        params = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        action = params.get("llm", [""])[0]
        self.respond(params, action if action in ("rewrite", "answer") else None)

    def host_ok(self) -> bool:
        """Akcje LLM kosztują, więc POST przyjmujemy tylko z naszej strony (chroni przed obcą stroną i DNS rebindingiem)."""
        hosts = {f"127.0.0.1:{self.server.server_address[1]}", f"localhost:{self.server.server_address[1]}"}
        origin = self.headers.get("Origin")
        return self.headers.get("Host") in hosts and (origin is None or origin in {f"http://{h}" for h in hosts})

    def respond(self, params: dict, action: str | None) -> None:
        q = params.get("q", [""])[0].strip()[:MAX_QUERY]
        scope = params.get("scope", ["codes"])[0]
        mode = params.get("mode", ["prefix5"])[0]
        scope = scope if scope in SCOPES else "codes"
        mode = mode if mode in MODES else "prefix5"
        try:
            self.reply(200, render(self.index, q, scope, mode, action))
        except Exception as error:  # strona ma zostać żywa; szczegóły w logu serwera
            self.log_error("błąd wyszukiwania: %s: %s", type(error).__name__, error)
            self.reply(500, page(q, scope, mode, '<p class="hint warn">Błąd wyszukiwania — szczegóły w logu serwera.</p>'))

    def reply(self, status: int, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    print("ładowanie bazy i wektorów…", flush=True)
    Handler.index = Index()
    server = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"gotowe: http://127.0.0.1:{args.port}/  (zakresy: {', '.join(CODES.values())} lub cały korpus; Ctrl+C kończy)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
