"""Model client for the fleet agents.

Backends:
  letta   — OpenAI-compat surface of the Letta App Server on M6
            (https://$M6_TAILSCALE_DNS:4500/v1/chat/completions, model =
            <agent-id>; Bearer LETTA_APP_SERVER_TOKEN). Each Letta agent
            appears as a model; the agent's standing process lives in its
            system prompt (provisioned by bootstrap.py).
  direct  — straight to the Bifrost gateway with the free-first failover
            chain (nousresearch → deepseek backup) per the model-selection
            rule; used as a fallback when the Letta server is unavailable.
  stub    — scripted responses (tests only).

The client is deliberately thin: agents are stochastic, and their contract is
the schema-shaped output validated by memory_plane.fleet (shape + one retry),
not deep invariants. See README "PDD boundary".
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request

# Free-first failover chain; the last entry is the paid backup. Override with
# MODEL_CHAIN (comma-separated). Per the model-selection rule, synthesis is
# hard reasoning, so the chain ends on deepseek.
DEFAULT_MODEL_CHAIN = [
    "nousresearch/deepseek/deepseek-v4-flash-0731",
    "deepseek/deepseek-v4-flash",
]

DEFAULT_BIFROST_URL = "https://agent-workstation.tail4904d2.ts.net:10000"


def _resolve_bifrost_key() -> str:
    """Env first, else the Infisical credential used by the skill-sync system
    (BIFROST_AGENT_VIRTUAL_KEY). Never hardcoded."""
    key = os.environ.get("BIFROST_KEY", "")
    if key:
        return key
    try:
        out = subprocess.run(
            ["infisical", "secrets", "get", "BIFROST_AGENT_VIRTUAL_KEY",
             "--projectId", "5598630f-4109-47d9-bbfb-91bac16ac92c",
             "--env", "prod", "--plain", "--silent"],
            capture_output=True, text=True, timeout=15)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


def _post_json(url: str, payload: dict, headers: dict | None = None,
               timeout: float = 120.0) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (internal)
        return json.loads(resp.read().decode())


class LettaClient:
    """Drives Letta agents on the M6 App Server (OpenAI-compat /v1)."""

    def __init__(self, base_url: str | None = None, token: str | None = None,
                 timeout: float = 180.0) -> None:
        self.base_url = (base_url or os.environ.get(
            "LETTA_BASE_URL", "https://agent-workstation.tail4904d2.ts.net:4500")).rstrip("/")
        self.token = token or os.environ.get("LETTA_APP_SERVER_TOKEN", "")
        self.timeout = timeout

    def chat(self, agent_id: str, task: str, system: str | None = None) -> str:
        if not self.token:
            raise RuntimeError("LETTA_APP_SERVER_TOKEN is required for the "
                               "letta backend (set it from Infisical)")
        # The Letta server exposes agents by NAME as the model handle; the
        # standing process lives in the agent's system prompt (provisioned), so
        # `system` is intentionally ignored here.
        resp = _post_json(
            f"{self.base_url}/v1/chat/completions",
            {"model": agent_id,
             "messages": [{"role": "user", "content": task}]},
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=self.timeout)
        return resp["choices"][0]["message"]["content"]


class DirectClient:
    """Straight-to-Bifrost with the free-first failover chain."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model_chain: list[str] | None = None,
                 timeout: float = 180.0) -> None:
        self.base_url = (base_url or os.environ.get("BIFROST_URL",
                                                    DEFAULT_BIFROST_URL)).rstrip("/")
        self.api_key = api_key if api_key is not None else _resolve_bifrost_key()
        chain = model_chain or [
            m.strip() for m in os.environ.get(
                "MODEL_CHAIN", ",".join(DEFAULT_MODEL_CHAIN)).split(",") if m.strip()]
        self.model_chain = chain or DEFAULT_MODEL_CHAIN
        self.timeout = timeout

    def chat(self, agent_id: str, task: str, system: str | None = None) -> str:
        # Direct mode has no provisioned agent: the standing process is the
        # system message (fleet passes agent["system"]), agent_id is a label.
        messages = [{"role": "system", "content": system or agent_id},
                    {"role": "user", "content": task}]
        last_err: Exception | None = None
        for model in self.model_chain:
            try:
                headers = {}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                resp = _post_json(
                    f"{self.base_url}/v1/chat/completions",
                    {"model": model, "messages": messages},
                    headers=headers, timeout=self.timeout)
                return resp["choices"][0]["message"]["content"]
            except (urllib.error.URLError, KeyError, IndexError,
                    json.JSONDecodeError, OSError) as e:
                last_err = e
                time.sleep(1.0)
        raise RuntimeError(f"all models in chain failed: {last_err}")


class StubClient:
    """Scripted responses for tests. Callable per agent or a default."""

    def __init__(self, script: dict[str, str] | None = None,
                 default: str = "{}") -> None:
        self.script = script or {}
        self.default = default
        self.calls: list[tuple[str, str]] = []

    def chat(self, agent_id: str, task: str, system: str | None = None) -> str:
        self.calls.append((agent_id, task))
        return self.script.get(agent_id, self.default)


def make_client(backend: str, **kwargs):
    if backend == "letta":
        return LettaClient(**kwargs)
    if backend == "direct":
        return DirectClient(**kwargs)
    if backend == "stub":
        return StubClient(**kwargs)
    raise ValueError(f"unknown backend: {backend!r} (letta|direct|stub)")
