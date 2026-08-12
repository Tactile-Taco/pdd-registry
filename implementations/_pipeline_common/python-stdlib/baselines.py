"""Per-model baseline statistics for deviation-based heatmap normalization.

Baselines are accumulated per model (per the model-dialect finding: marker
densities differ per model family, so deviations must be computed against the
same model's own baseline).
"""

from __future__ import annotations

import json
import math
import os


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
