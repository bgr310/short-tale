"""Ollama client with explicit VRAM discipline.

A 3080 Ti has 12GB. A 14B model at Q4 takes roughly 9GB of that, which leaves
no room for Whisper. So the pipeline is strictly sequential and this client
can evict the model from VRAM the moment the language stages are finished
(`unload()`), before the caption stage loads Whisper.

Everything here talks to a container on your own machine. No request in this
file ever leaves the host.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..config import get_settings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any:
    """Pull a JSON object out of a model response.

    Local models fenced in markdown, added a preamble, or emitted a trailing
    comma far more often than hosted ones do, so this is deliberately
    forgiving rather than strict.
    """
    text = (text or "").strip()
    if not text:
        raise LLMError("empty response from model")

    for candidate in _candidates(text):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            repaired = _repair(candidate)
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                continue
    raise LLMError(f"could not parse JSON from model output: {text[:400]}")


def _candidates(text: str):
    yield text
    m = _JSON_BLOCK.search(text)
    if m:
        yield m.group(1).strip()
    # Outermost {...} or [...]
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if 0 <= start < end:
            yield text[start : end + 1]


def _repair(s: str) -> str:
    s = re.sub(r",\s*([}\]])", r"\1", s)          # trailing commas
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s)  # stray control chars
    return s


class OllamaClient:
    def __init__(self, host: str | None = None, model: str | None = None) -> None:
        s = get_settings()
        self.host = (host or s.ollama_host).rstrip("/")
        self.model = model or s.ollama_model
        self.fast_model = s.ollama_model_fast
        self.timeout = s.ollama_timeout

    # --- plumbing ----------------------------------------------------------

    def _post(self, path: str, payload: dict) -> dict:
        with httpx.Client(timeout=self.timeout) as client:
            r = client.post(f"{self.host}{path}", json=payload)
            r.raise_for_status()
            return r.json()

    def health(self) -> bool:
        try:
            with httpx.Client(timeout=10) as client:
                return client.get(f"{self.host}/api/tags").status_code == 200
        except Exception:  # noqa: BLE001
            return False

    def available_models(self) -> list[str]:
        try:
            with httpx.Client(timeout=15) as client:
                data = client.get(f"{self.host}/api/tags").json()
            return [m["name"] for m in data.get("models", [])]
        except Exception:  # noqa: BLE001
            return []

    def ensure_model(self, model: str | None = None) -> None:
        """Pull the model if it isn't present. First run only; it is slow."""
        model = model or self.model
        have = self.available_models()
        if any(m == model or m.startswith(model.split(":")[0] + ":") for m in have):
            return
        log.warning("model %s not found locally — pulling (this takes a while)", model)
        with httpx.Client(timeout=None) as client:
            with client.stream("POST", f"{self.host}/api/pull", json={"model": model}) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("status"):
                        log.debug("pull: %s", msg["status"])
        log.info("pulled %s", model)

    def unload(self, model: str | None = None) -> None:
        """Evict the model from VRAM immediately.

        Called between the scripting stage and the caption stage so Whisper
        isn't fighting a 9GB resident model for a 12GB card.
        """
        for m in {model or self.model, self.fast_model}:
            try:
                self._post("/api/generate", {"model": m, "prompt": "", "keep_alive": 0})
                log.debug("unloaded %s from VRAM", m)
            except Exception as exc:  # noqa: BLE001
                log.debug("unload of %s failed (harmless): %s", m, exc)

    # --- generation --------------------------------------------------------

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, LLMError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=20),
        reraise=True,
    )
    def chat_json(
        self,
        system: str,
        user: str,
        *,
        model: str | None = None,
        temperature: float = 0.7,
        num_ctx: int = 8192,
        max_tokens: int = 1400,
    ) -> Any:
        """Chat completion constrained to JSON, parsed and returned."""
        payload = {
            "model": model or self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "format": "json",
            "options": {
                "temperature": temperature,
                "num_ctx": num_ctx,
                "num_predict": max_tokens,
                "top_p": 0.9,
                "repeat_penalty": 1.05,
            },
        }
        data = self._post("/api/chat", payload)
        content = (data.get("message") or {}).get("content", "")
        return extract_json(content)

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=20),
        reraise=True,
    )
    def chat_text(
        self,
        system: str,
        user: str,
        *,
        model: str | None = None,
        temperature: float = 0.7,
        num_ctx: int = 8192,
        max_tokens: int = 800,
    ) -> str:
        payload = {
            "model": model or self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_ctx": num_ctx,
                "num_predict": max_tokens,
            },
        }
        data = self._post("/api/chat", payload)
        return ((data.get("message") or {}).get("content") or "").strip()
