"""Wywołanie Anthropic Messages API przez urllib (stdlib, bez SDK).

Klucz tylko ze zmiennej środowiskowej procesu ANTHROPIC_API_KEY (żadnych plików z kluczem); trafia wyłącznie
do nagłówka `x-api-key`, nigdy do logów ani komunikatów. Jeden model (MODEL) do przepisywania pytań i odpowiedzi.
"""
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2026-10-21"  # od tej wersji Messages API odrzuca temperature/top_p/top_k — nie wysyłamy ich
FALLBACK_BETA = "server-side-fallback-2026-07-01"  # z fallbacks: "default" (Claude API)
MODEL = "claude-sonnet-5-5"
KEY_VAR = "ANTHROPIC_API_KEY"
TIMEOUT = 120
ATTEMPTS = 3  # jak SDK: ponawiamy 408/409/429/5xx i błędy połączenia
NO_KEY_MESSAGE = f"Brak zmiennej {KEY_VAR} w procesie serwera. Uruchom serwer z kluczem: {KEY_VAR}='<KLUCZ>' .venv/bin/python scripts/serve.py"


class LLMError(Exception):
    """Błąd z komunikatem czytelnym dla użytkownika strony."""


class NoKeyError(LLMError):
    pass


class Refusal(LLMError):
    pass


@dataclass
class Reply:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    request_id: str | None


def has_key() -> bool:
    return bool(os.environ.get(KEY_VAR, "").strip())


def _send(body: dict, key: str) -> tuple[dict, str | None]:
    request = urllib.request.Request(
        API_URL, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": API_VERSION, "anthropic-beta": FALLBACK_BETA})
    for attempt in range(ATTEMPTS):
        last = attempt == ATTEMPTS - 1
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return json.loads(response.read()), response.headers.get("request-id")
        except urllib.error.HTTPError as error:
            retryable = error.code in (408, 409, 429) or error.code >= 500
            if retryable and not last:
                time.sleep(min(float(error.headers.get("retry-after") or 2 ** attempt), 10))
                continue
            raise LLMError(_describe(error)) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            # timeout nie jest ponawiany: serwer strony jest jednowątkowy, więc zawieszone API blokowałoby całą stronę
            if isinstance(error, TimeoutError) or isinstance(getattr(error, "reason", None), TimeoutError):
                raise LLMError(f"API nie odpowiedziało w ciągu {TIMEOUT} s.") from None
            if last:
                raise LLMError(f"Brak połączenia z API ({type(error).__name__}: {getattr(error, 'reason', error)}).") from None
            time.sleep(2 ** attempt)
    raise LLMError("Nie udało się wywołać API.")  # nieosiągalne: ostatnia próba zwraca lub zgłasza błąd


def _describe(error: urllib.error.HTTPError) -> str:
    try:
        detail = json.loads(error.read())["error"]
        text = f"{detail.get('type')}: {detail.get('message')}"
    except Exception:  # odpowiedź nie jest JSON-em w formacie API
        text = error.reason
    hint = {401: " Sprawdź wartość ANTHROPIC_API_KEY.", 429: " Limit zapytań — spróbuj za chwilę."}.get(error.code, "")
    return f"API zwróciło {error.code} ({text}).{hint}"


def complete(system: str, user: str, *, effort: str, max_tokens: int, schema: dict | None = None) -> Reply:
    key = os.environ.get(KEY_VAR, "").strip()
    if not key:
        raise NoKeyError(NO_KEY_MESSAGE)
    output_config = {"effort": effort}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    body = {"model": MODEL, "max_tokens": max_tokens, "system": system, "output_config": output_config,
            "messages": [{"role": "user", "content": user}], "fallbacks": "default"}
    data, request_id = _send(body, key)
    if data.get("stop_reason") == "refusal":
        category = (data.get("stop_details") or {}).get("category")
        raise Refusal(f"Model odmówił odpowiedzi{f' (kategoria: {category})' if category else ''}.")
    if data.get("stop_reason") == "max_tokens":
        raise LLMError("Odpowiedź modelu została ucięta (limit max_tokens).")
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    if not text.strip():
        raise LLMError("Model zwrócił pustą odpowiedź.")
    usage = data.get("usage") or {}
    return Reply(text, data.get("model", MODEL), usage.get("input_tokens", 0), usage.get("output_tokens", 0), request_id)
