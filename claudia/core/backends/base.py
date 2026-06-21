"""
core/backends/base.py — Abstract interface all brain backends implement.

Both ClaudeBackend and LocalBackend conform to this so Brain (the dispatcher)
can call either one identically.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generator


class BrainBackend(ABC):
    """Common interface for any LLM backend (cloud or local)."""

    name: str  # "claude" | "local" — used in logs and switch confirmations

    @abstractmethod
    def think(self, messages: list[dict]) -> str:
        """
        messages: fully-prepared list of {"role": ..., "content": ...} dicts,
        already including system prompt, context window, and any research
        injection. The backend's only job is to call its model and return text.
        """
        ...

    @abstractmethod
    def think_stream(self, messages: list[dict]) -> Generator[str, None, None]:
        """Same as think(), but yields tokens/chunks as they arrive."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """
        Cheap reachability check — does NOT make a full generation call.
        ClaudeBackend: checks API key is set.
        LocalBackend: pings Ollama's /api/tags endpoint.
        """
        ...
