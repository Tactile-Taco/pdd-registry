"""Append-only cost ledger for LLM calls made by the pipeline passes."""

from __future__ import annotations

import json
import os
from typing import Any

from common import append_jsonl, read_jsonl


class CostLedger:
    def __init__(self, path: str) -> None:
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def record(self, pass_id: str, model: str, tokens_in: int, tokens_out: int,
               cost_usd: float, **extra: Any) -> None:
        entry = {
            "pass_id": pass_id,
            "model": model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": round(float(cost_usd), 6),
        }
        entry.update(extra)
        append_jsonl(self.path, entry)

    def entries(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        return list(read_jsonl(self.path))

    def totals(self) -> dict:
        calls = 0
        tokens_in = tokens_out = 0
        cost = 0.0
        for e in self.entries():
            calls += 1
            tokens_in += int(e.get("tokens_in", 0))
            tokens_out += int(e.get("tokens_out", 0))
            cost += float(e.get("cost_usd", 0.0))
        return {"calls": calls, "tokens_in": tokens_in, "tokens_out": tokens_out,
                "cost_usd": round(cost, 6)}
