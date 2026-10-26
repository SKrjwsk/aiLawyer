# aiLawyer

Hybrid search and citation-grounded question answering over the **Polish legal corpus** (statutes and codes in force), built on the official [Sejm ELI API](https://api.sejm.gov.pl/eli/openapi/).

aiLawyer downloads the current consolidated text of Polish legal acts, parses it into articles and paragraphs, indexes it in SQLite (FTS5) and a local multilingual embedding store, and exposes a local web UI to compare three retrieval strategies side by side. An optional LLM step rewrites a colloquial question into statute-style queries and produces an answer in which every claim is cited and verified against the retrieved articles.

> **Disclaimer:** This is a research and engineering project. It is **not legal advice** and must not be used as a substitute for a qualified lawyer or for the official texts published in the *Dziennik Ustaw*.

## Table of contents

- [Features](#features)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [Keeping the corpus up to date](#keeping-the-corpus-up-to-date)
- [Evaluation](#evaluation)
- [Project structure](#project-structure)
- [Configuration](#configuration)
- [Data and disk usage](#data-and-disk-usage)
- [Security notes](#security-notes)
- [Roadmap](#roadmap)
- [License](#license)

## Features

- **Official, current sources** – acts are fetched from the Sejm ELI API (Dziennik Ustaw and Monitor Polski). For amended acts, the text comes from the latest consolidated-text announcement (*tekst jednolity*) whenever one exists.
- **Structure-aware parsing** – HTML and PDF acts are parsed into articles, paragraphs and footnotes. PDF parsing uses line coordinates to detect footnotes and handles parallel (future-dated) article variants.
- **Three retrieval modes** – lexical (SQLite FTS5 / BM25), semantic (multilingual embeddings) and hybrid fusion via Reciprocal Rank Fusion (RRF).
- **Polish-aware lexical search** – diacritics-insensitive tokenization with optional prefix matching (`prefix4`–`prefix6`) as a cheap stemming substitute.
- **Citation-grounded answers (optional)** – an LLM answers *only* from the retrieved articles; each `[KC art. 118]`-style citation is validated against the list of articles supplied in the prompt.
- **Incremental updates** – a change feed from ELI refreshes only modified acts; embeddings are keyed by text hash, so only new or changed units are re-embedded.
- **Local-first** – everything runs on your machine. The web server binds to `127.0.0.1` only.
- **Minimal dependencies** – ingestion and parsing use the Python standard library; only retrieval needs `fastembed` and `numpy`.

## How it works

```
Sejm ELI API
    │  fetch_acts.py / fetch_corpus.py / update.py
    ▼
data/texts/<ELI>/            raw HTML or PDF + source.json
    │  parse_html.py / parse_pdf.py
    ▼
data/parsed_corpus/*.json    articles, paragraphs, footnotes
    │  build_db.py
    ▼
data/lawdb.sqlite            acts · articles · units · footnotes · FTS5 index
    │  embed_store.py sync
    ▼
data/emb/<model>/            hash-keyed float16 vector store
    │  search.py
    ▼
BM25 ─┐
      ├─ RRF fusion ─► ranked articles ─► (optional) answer.py + llm.py ─► cited answer
Vector┘                                                         ▲
                                                 serve.py (local web UI)
```

**Source selection per act.** Acts are grouped by how their current text can be obtained:

| Group | Description | Text source |
|-------|-------------|-------------|
| D | Never amended | Original HTML or PDF |
| A | Amended, consolidated text exists | PDF of the latest consolidated-text announcement |
| B | Amended, no consolidated text, unified text available | Unified-text PDF |
| C | Amended, no consolidated or unified text | Original text, flagged as possibly outdated (`stale`) |

**Question answering pipeline.** (1) The LLM rewrites a colloquial question into 2–3 short queries in statutory language. (2) Each query goes through hybrid retrieval. (3) The top articles are passed to the LLM, which must answer only from them, cite every claim, and say so explicitly when the articles are insufficient. (4) Citations are checked against the supplied articles. Article text is treated as data, never as instructions.

## Requirements

- Python **3.12+** (developed on 3.14)
- [`poppler`](https://poppler.freedesktop.org/) – provides `pdftotext`, required to parse PDF acts
  - macOS: `brew install poppler`
  - Debian/Ubuntu: `sudo apt install poppler-utils`
- Python packages for retrieval: `fastembed`, `numpy` (installed in a virtual environment, see below)
- An `ANTHROPIC_API_KEY` – **optional**, only for the LLM question-rewriting and answering features
- Disk space – see [Data and disk usage](#data-and-disk-usage)

## Installation

```bash
git clone <repository-url> aiLawyer
cd aiLawyer

python3 -m venv .venv
.venv/bin/pip install fastembed numpy
```

The ingestion scripts need no third-party packages and can be run with the system `python3`.

## Usage

### 1. Fetch and build the corpus

Fetch a few specific acts (address format: `<publisher>/<year>/<position>`, where `DU` = Dziennik Ustaw and `MP` = Monitor Polski):

```bash
python3 scripts/fetch_acts.py DU/1964/93 DU/1997/553
```

Fetch the core corpus (acts in force with an available current text). Use `--sample` first to estimate size:

```bash
python3 scripts/fetch_corpus.py --sample 100
python3 scripts/fetch_corpus.py
```

Index catalogs and in-force records, then parse the downloaded texts:

```bash
python3 scripts/index_catalog.py
python3 scripts/index_inforce.py
python3 scripts/parse_html.py
python3 scripts/parse_pdf.py
```

> Each script documents its own flags in its module docstring and via `--help`.

### 2. Build the database and embeddings

```bash
python3 scripts/build_db.py
HF_HUB_DISABLE_XET=1 .venv/bin/python scripts/embed_store.py sync \
    sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
```

`build_db.py` accepts `--parsed`, `--db` and `--trigram` (adds a large trigram index) to build a separate database, for example for the eight main codes only.

### 3. Search from the command line

```bash
.venv/bin/python scripts/search.py "kradzież cudzej rzeczy ruchomej" --scope all --mode prefix5 -n 10
```

| Option | Description |
|--------|-------------|
| `--scope` | `codes` (eight main codes: KC, KK, KPC, KP, KPA, KPK, KRO, KSH) or `all` (entire corpus) |
| `--mode` | Lexical matching: `words`, `prefix4`, `prefix5`, `prefix6` |
| `-n` | Number of results |

Without `fastembed` or a vector store, search falls back to FTS5 only.

### 4. Run the local web UI

```bash
.venv/bin/python scripts/serve.py                      # retrieval only
ANTHROPIC_API_KEY='<KEY>' .venv/bin/python scripts/serve.py   # + LLM buttons
```

Open <http://127.0.0.1:8765/> (change the port with `--port`). The page shows three result lists for each query – FTS5, vectors and hybrid – so retrieval quality can be compared directly.

## Keeping the corpus up to date

A single command pulls changes from the ELI change feed, refreshes the affected texts, rebuilds the database and embeds only the new units:

```bash
sh scripts/update_all.sh
```

Individual steps:

```bash
python3 scripts/update.py --dry-run                      # list changes only
python3 scripts/update.py                                # since the last sync (data/state.json)
python3 scripts/update.py --since 2026-10-20T00:00:00    # since a specific moment
```

Set `EMBED_MODEL` to use a different embedding model during `update_all.sh`.

## Evaluation

Retrieval experiments and benchmarks live in `scripts/`:

| Script | Purpose |
|--------|---------|
| `embed_experiment.py` | Compare embedding models |
| `eval_search.py` | Evaluate lexical and vector search |
| `eval_hybrid.py` | Evaluate hybrid (RRF) fusion |
| `eval_split.py` | Evaluate unit-splitting strategies |

Two embedding models have been evaluated: `paraphrase-multilingual-MiniLM-L12-v2` (default, small and fast) and `intfloat/multilingual-e5-large` (larger).

## Project structure

```
aiLawyer/
├── scripts/
│   ├── fetch_acts.py        # fetch selected acts from ELI
│   ├── fetch_corpus.py      # fetch the whole in-force core corpus
│   ├── index_catalog.py     # index the full act catalog
│   ├── index_inforce.py     # index acts currently in force
│   ├── parse_html.py        # HTML → structured JSON
│   ├── parse_pdf.py         # PDF (pdftotext -bbox-layout) → structured JSON
│   ├── units.py             # split articles into embedding units
│   ├── build_db.py          # SQLite + FTS5 database
│   ├── embed_corpus.py      # bulk embedding
│   ├── embed_store.py       # hash-keyed vector store (import-chunks, sync)
│   ├── search.py            # BM25, vector and RRF hybrid search
│   ├── answer.py            # query rewriting and cited answers
│   ├── llm.py               # Anthropic Messages API client (stdlib only)
│   ├── serve.py             # local web UI
│   ├── update.py            # incremental update from the ELI change feed
│   ├── update_all.sh        # full update pipeline
│   └── eval_*.py, embed_experiment.py
└── data/                    # generated; git-ignored
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | – | Enables the LLM features in `serve.py`. Read only from the server process environment. |
| `EMBED_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | Embedding model used by `update_all.sh`. |
| `HF_HUB_DISABLE_XET` | – | Set to `1` when downloading models if the Xet transport causes problems. |

The LLM model, API version and timeouts are constants at the top of `scripts/llm.py`.

## Data and disk usage

The `data/` directory is generated and **git-ignored**. A full corpus is large – on the order of tens of gigabytes:

| Path | Approx. size | Contents |
|------|--------------|----------|
| `data/texts/` | ~13 GB | Downloaded raw acts |
| `data/parsed_corpus/` | ~0.6 GB | Parsed JSON |
| `data/lawdb.sqlite` | ~1 GB | Database with FTS5 index |
| `data/emb/` | ~0.4 GB | Vector stores |
| `data/models/` | ~2.3 GB | Cached embedding models |

Use `fetch_corpus.py --sample` to try the pipeline on a small subset first.

## Security notes

- The web server listens on `127.0.0.1` only and is not intended to be exposed to a network.
- Endpoints that call the paid LLM API accept only `POST` and verify the `Host` and `Origin` headers.
- The API key is read exclusively from the process environment, sent only in the `x-api-key` header, and never written to logs or error messages.
- Retrieved article text is passed to the LLM as data; instructions embedded in it are ignored by design.

## Roadmap

- Wider coverage of historical and non-core act groups
- Retrieval evaluation set with recall/MRR metrics
- Case-law (judicial decisions) as an additional source
- Packaging with pinned dependencies (`pyproject.toml`) and automated tests

## License

No license has been specified yet. Until one is added, all rights are reserved by the author. Legal texts are sourced from the Sejm ELI API; consult its terms of use before redistributing the data.
