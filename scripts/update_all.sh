#!/bin/sh
# Pełna aktualizacja: zmiany z ELI → przebudowa bazy → doliczenie nowych wektorów.
# Użycie: sh scripts/update_all.sh
set -e
cd "$(dirname "$0")/.."
MODEL="${EMBED_MODEL:-sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2}"
python3 -I scripts/update.py
python3 -I scripts/build_db.py
HF_HUB_DISABLE_XET=1 .venv/bin/python -I scripts/embed_store.py sync "$MODEL"
