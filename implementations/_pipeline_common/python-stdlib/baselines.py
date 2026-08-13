"""Per-model baseline statistics for deviation-based heatmap normalization.

Baselines are accumulated per model (per the model-dialect finding: marker
densities differ per model family, so deviations must be computed against the
same model's own baseline).
"""

from __future__ import annotations

import json
import math
import os
import re

# Kimi Code CLI transcripts carry no explicit model key, but their system prompt
# embeds the session's "current date". Kimi traces are kimi-k2.6 by default;
# after Kimi K3's open-source release (2026-07-27) they are assumed kimi-k3.
# Overridable via env in case the release date needs adjusting.
KIMI_K3_RELEASE = os.environ.get("KIMI_K3_RELEASE_DATE", "2026-07-27")
_KIMI_DATE_RE = re.compile(r"current date and time in ISO format is `(20\d\d-\d\d-\d\d)")
_SYNTHETIC_RE = re.compile(r"^\s*<")


def normalize_model(raw) -> str:
    """Collapse provider-qualified / dash-joined model ids to a stable slug.

    Handles: 'deepseek/deepseek-v4-flash' -> 'deepseek-v4-flash',
    'deepseek-deepseek-v4-flash' -> 'deepseek-v4-flash' (dash-joined provider),
    'bifrost-deepseek/deepseek/deepseek-v4-flash' -> 'deepseek-v4-flash',
    'kimi-k2.6' -> 'kimi-k2.6'. Returns 'unknown' for empty/unparseable.
    """
    m = str(raw or "").strip()
    if not m:
        return "unknown"
    m = m.rsplit("/", 1)[-1]  # provider/model paths -> model
    if m.startswith("deepseek-deepseek-"):
        m = "deepseek-" + m[len("deepseek-deepseek-"):]
    for prefix in ("bifrost-", "opencode-go-", "nousresearch-"):
        if m.startswith(prefix) and len(m) > len(prefix) + 2:
            m = m[len(prefix):]
            break
    return m if m else "unknown"


def detect_model(source: str, filename: str, lines: list[str]) -> str:
    """Best-effort model id for a transcript, keyed for baseline accumulation."""
    if source == "reasonix":
        base = os.path.basename(filename)
        if base.startswith("sa_"):
            return "unknown"
        m = re.sub(r"\.events\.jsonl$", "", base)
        m = re.sub(r"^\d{8}-\d{6}\.\d+-", "", m)
        m = re.sub(r"-recovery-[a-f0-9-]+$", "", m)
        m = re.sub(r"(-[a-f0-9]{8,}){1,}$", "", m)  # trailing uuid-ish suffixes
        if not m or m in ("session",):
            return "unknown"
        return normalize_model(m)
    if source == "hermes":
        for line in lines:
            try:
                o = json.loads(line)
            except Exception:
                continue
            if not isinstance(o, dict):
                continue
            mdl = o.get("model") or (o.get("message") or {}).get("model")
            if mdl:
                return normalize_model(mdl)
        return "unknown"
    if source == "omp":
        last = None
        for line in lines:
            try:
                o = json.loads(line)
            except Exception:
                continue
            if isinstance(o, dict) and o.get("type") == "model_change" and o.get("model"):
                last = o["model"]
        return normalize_model(last) if last else "unknown"
    if source == "claude":
        for line in lines:
            try:
                o = json.loads(line)
            except Exception:
                continue
            if isinstance(o, dict):
                msg = o.get("message") or {}
                if isinstance(msg, dict) and msg.get("model"):
                    return normalize_model(msg["model"])
        return "unknown"
    if source == "kimi":
        return kimi_model(lines)
    return "unknown"


def is_synthetic(model: str) -> bool:
    """True for synthetic/test fixtures (e.g. a claude file keyed '<synthetic>')."""
    return bool(model) and bool(_SYNTHETIC_RE.match(model))


def kimi_model(lines: list[str]) -> str:
    """Kimi traces: kimi-k2.6 by default; kimi-k3 once the session's current
    date is at/after the K3 release. Uses the latest date found across the
    session's system-prompt records."""
    latest: str | None = None
    for line in lines:
        try:
            o = json.loads(line)
        except Exception:
            continue
        if not isinstance(o, dict) or o.get("role") != "_system_prompt":
            continue
        m = _KIMI_DATE_RE.search(str(o.get("content", "")))
        if m and (latest is None or m.group(1) > latest):
            latest = m.group(1)
    if latest is None:
        return "kimi-k2.6"  # assume k2.6 unless provably after K3
    return "kimi-k3" if latest >= KIMI_K3_RELEASE else "kimi-k2.6"
    return "unknown"


class BaselineStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self._data: dict = {"models": {}}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                self._data = json.load(f)

    def _save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, sort_keys=True, indent=1)

    def add_sample(self, model: str, metric: str, value: float) -> None:
        m = self._data["models"].setdefault(model, {})
        samples = m.setdefault(metric, {"n": 0, "sum": 0.0, "sum_sq": 0.0})
        samples["n"] += 1
        samples["sum"] += value
        samples["sum_sq"] += value * value
        self._save()

    def merge(self, model: str, metric: str, n: int, sum_: float, sum_sq: float,
              sources: dict | None = None) -> None:
        """Batch-seed precomputed aggregate samples (one save)."""
        m = self._data["models"].setdefault(model, {})
        s = m.setdefault(metric, {"n": 0, "sum": 0.0, "sum_sq": 0.0})
        s["n"] += int(n)
        s["sum"] += float(sum_)
        s["sum_sq"] += float(sum_sq)
        if sources:
            srcs = m.setdefault("sources", {})
            for src, c in sources.items():
                srcs[src] = srcs.get(src, 0) + int(c)
        self._save()

    def sources_for(self, model: str) -> dict:
        m = self._data["models"].get(model) or {}
        return dict(m.get("sources") or {})

    def stats(self, model: str, metric: str) -> dict | None:
        m = self._data["models"].get(model)
        if not m or metric not in m:
            return None
        s = m[metric]
        n = s["n"]
        if n == 0:
            return None
        mean = s["sum"] / n
        var = max(0.0, s["sum_sq"] / n - mean * mean)
        return {"n": n, "mean": mean, "std": math.sqrt(var)}

    def deviation(self, model: str, metric: str, value: float) -> float | None:
        """z-score of value against the model's baseline, or None if none."""
        st = self.stats(model, metric)
        if st is None or st["std"] == 0:
            return None
        return round((value - st["mean"]) / st["std"], 4)
