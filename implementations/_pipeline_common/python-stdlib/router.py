"""Model router for LLM-backed passes (topic-transition, topic-flow-review).

Real router: Bifrost OpenAI-compatible endpoint with a free-first failover
chain (per the user's model-selection rule: free models with failover, deepseek
as final backup). Stub router: canned deterministic responses for offline
tests — passes must NEVER open their own network connections.

Stdlib only (urllib). All calls are recorded in the cost ledger when one is
attached.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Optional

DEFAULT_BASE_URL = "https://agent-workstation.tail4904d2.ts.net:10000/v1"
DEFAULT_MODEL = os.environ.get(
    "REFLECTION_MODEL", "nousresearch/google/gemini-3.1-flash-lite"
)

# USD per 1M tokens (input, output). Free tiers are $0.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "nousresearch/google/gemini-3.1-flash-lite": (0.0, 0.0),
    "nousresearch/deepseek-v4-flash": (0.0, 0.0),
    "deepseek-v4-flash": (0.14, 0.28),
    "deepseek/deepseek-v4-flash-0731": (0.08, 0.18),
    "opencode-go/deepseek-v4-flash": (0.14, 0.28),
}


def _price_for(model: str) -> tuple[float, float]:
    for prefix in ("nousresearch/", "openrouter/"):
        if model.startswith(prefix) or model.startswith(("gemini-", "claude-", "gpt-")):
            return (0.0, 0.0)
    return MODEL_PRICING.get(model, (0.14, 0.28))


class RouterError(RuntimeError):
    pass


class ModelRouter:
    """Bifrost chat-completions client with free-first failover + ledger."""

    def __init__(
        self,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        fallbacks: Optional[list[str]] = None,
        ledger: Optional[Any] = None,
        timeout: float = 60.0,
    ) -> None:
        self.model = model or DEFAULT_MODEL
        self.base_url = (base_url or os.environ.get("BIFROST_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("BIFROST_KEY", "")
        env_fb = os.environ.get("REFLECTION_MODEL_FALLBACKS", "")
        self.fallbacks = list(fallbacks) if fallbacks is not None else (
            [m.strip() for m in env_fb.split(",") if m.strip()] if env_fb else []
        )
        self.ledger = ledger
        self.timeout = timeout

    # -- public API ----------------------------------------------------------

    def complete(self, messages: list[dict], max_tokens: int = 4096) -> dict:
        """Return {'text', 'model', 'tokens_in', 'tokens_out', 'cost_usd'}."""
        chain = [self.model] + self.fallbacks
        last_err: Optional[Exception] = None
        for model in chain:
            try:
                return self._call(model, messages, max_tokens)
            except Exception as e:  # noqa: BLE001 — try next in chain
                last_err = e
        raise RouterError(f"all models failed: {last_err}") from last_err

    def complete_json(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> dict:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        out = self.complete(messages, max_tokens=max_tokens)
        self.last_usage = {"tokens_in": out["tokens_in"], "tokens_out": out["tokens_out"]}
        return json.loads(_extract_json(out["text"]))

    # -- internals ------------------------------------------------------------

    def _call(self, model: str, messages: list[dict], max_tokens: int) -> dict:
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 — configured endpoint
            data = json.loads(resp.read().decode("utf-8"))
        choice = data["choices"][0]
        text = choice["message"].get("content") or ""
        usage = data.get("usage") or {}
        tokens_in = int(usage.get("prompt_tokens", 0))
        tokens_out = int(usage.get("completion_tokens", 0))
        cost = self._cost(model, tokens_in, tokens_out)
        if self.ledger is not None:
            self.ledger.record(
                pass_id="router", model=model, tokens_in=tokens_in,
                tokens_out=tokens_out, cost_usd=cost,
            )
        return {
            "text": text,
            "model": model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost,
        }

    @staticmethod
    def _cost(model: str, tokens_in: int, tokens_out: int) -> float:
        p_in, p_out = _price_for(model)
        return round((tokens_in * p_in + tokens_out * p_out) / 1_000_000, 6)


class StubRouter:
    """Deterministic canned router for offline tests.

    `replies` maps a regex (searched in the prompt) to a JSON object. Unmatched
    prompts raise RouterError so tests notice unexpected model calls.
    """

    def __init__(self, replies: Optional[dict[str, Any]] = None, default: Optional[Any] = None) -> None:
        self.replies = replies or {}
        self.default = default
        self.calls: list[str] = []

    def complete(self, messages: list[dict], max_tokens: int = 4096) -> dict:
        prompt = "\n".join(m.get("content", "") for m in messages)
        self.calls.append(prompt)
        for pattern, reply in self.replies.items():
            if re.search(pattern, prompt):
                text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
                return {"text": text, "model": "stub", "tokens_in": len(prompt) // 4,
                        "tokens_out": len(text) // 4, "cost_usd": 0.0}
        if self.default is not None:
            text = self.default if isinstance(self.default, str) else json.dumps(self.default, ensure_ascii=False)
            return {"text": text, "model": "stub", "tokens_in": len(prompt) // 4,
                    "tokens_out": len(text) // 4, "cost_usd": 0.0}
        raise RouterError(f"stub router: no reply for prompt starting {prompt[:80]!r}")

    def complete_json(self, prompt: str, system: str | None = None, max_tokens: int = 4096) -> dict:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        out = self.complete(messages, max_tokens=max_tokens)
        self.last_usage = {"tokens_in": out["tokens_in"], "tokens_out": out["tokens_out"]}
        return json.loads(_extract_json(out["text"]))


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text
