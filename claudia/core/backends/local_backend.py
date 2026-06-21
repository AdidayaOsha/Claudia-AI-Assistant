"""
core/backends/local_backend.py — Local model backend via Ollama.

Ollama exposes a REST API on localhost (default port 11434) that accepts an
OpenAI-style chat payload. No API key, no network egress, runs entirely on
the local machine. Claudia does not start/stop the Ollama process — it must
already be running (it installs as a background service on Windows).
"""

from __future__ import annotations

import json
import logging
from typing import Generator

import httpx

from core.backends.base import BrainBackend

logger = logging.getLogger(__name__)


class LocalBackend(BrainBackend):
    name = "local"

    def __init__(self, config: dict):
        cfg = config.get("brain", {}).get("local", {})
        self.base_url = cfg.get("base_url", "http://localhost:11434").rstrip("/")
        self.model = cfg.get("model", "qwen2.5:7b-instruct")
        self.max_tokens = cfg.get("max_tokens", 512)
        self.temperature = cfg.get("temperature", 0.7)
        self.timeout = cfg.get("timeout_seconds", 30)
        self.max_context_chars = cfg.get("max_context_chars", 6000)

    def is_available(self) -> bool:
        """Cheap reachability check — pings Ollama's tag list, no generation."""
        try:
            resp = httpx.get(f"{self.base_url}/api/tags", timeout=2.0)
            return resp.status_code == 200
        except Exception as e:
            logger.debug("Ollama not reachable: %s", e)
            return False

    def think(self, messages: list[dict]) -> str:
        budgeted = self._budget_context(messages)
        payload = {
            "model": self.model,
            "messages": budgeted,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }
        try:
            resp = httpx.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json().get("message", {}).get("content", "").strip()
        except httpx.ConnectError as e:
            raise RuntimeError(
                f"Local backend unavailable: Ollama isn't reachable at {self.base_url}. "
                f"Is it running? ({e})"
            ) from e
        except httpx.TimeoutException as e:
            raise RuntimeError(
                f"Local model timed out after {self.timeout}s. "
                "Model may still be loading into memory on first call — try again."
            ) from e

    def think_stream(self, messages: list[dict]) -> Generator[str, None, None]:
        budgeted = self._budget_context(messages)
        payload = {
            "model": self.model,
            "messages": budgeted,
            "stream": True,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }
        try:
            with httpx.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout,
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    content = chunk.get("message", {}).get("content", "")
                    if content:
                        yield content
                    if chunk.get("done"):
                        break
        except httpx.ConnectError as e:
            raise RuntimeError(
                f"Local backend unavailable: Ollama isn't reachable at {self.base_url}."
            ) from e

    def _budget_context(self, messages: list[dict]) -> list[dict]:
        """
        Local models (7-8B) degrade noticeably with long stuffed context —
        unlike Claude, which handles large injected blocks gracefully. Trim
        from the oldest non-system messages first if total chars exceed budget.

        System message and the most recent user turn are always preserved.
        This is the key behavioral difference from ClaudeBackend: the dispatcher
        sends the SAME messages list to both backends, but only LocalBackend
        applies this extra trim.
        """
        total = sum(len(str(m.get("content", ""))) for m in messages)
        if total <= self.max_context_chars:
            return messages

        system_msgs = [m for m in messages if m["role"] == "system"]
        other_msgs = [m for m in messages if m["role"] != "system"]

        # Drop oldest non-system turns until under budget.
        # Always keep the last message (the current question).
        while len(other_msgs) > 1 and total > self.max_context_chars:
            removed = other_msgs.pop(0)
            total -= len(str(removed.get("content", "")))
            logger.debug(
                "Local context budget: dropped oldest turn (remaining: %d msgs, ~%d chars)",
                len(other_msgs),
                total,
            )

        return system_msgs + other_msgs
