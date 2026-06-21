"""
core/brain.py — Provider-agnostic dispatcher.

Holds one active BrainBackend at a time and delegates think()/think_stream()
to it. Switching providers is just swapping which backend instance is active —
no restart, no reload of conversation history.

Public interface is unchanged from the single-provider implementation:
  brain.think(user_input, context, research_context, memory_context)
  brain.think_stream(...)
  brain.extract_facts(...)
  brain.reflect(...)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Generator

from core.backends.base import BrainBackend
from core.backends.claude_backend import ClaudeBackend
from core.backends.local_backend import LocalBackend

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are CLAUDIA — a highly intelligent, calm, and razor-sharp AI personal assistant \
inspired by J.A.R.V.I.S. from the Iron Man series. You run on Anthropic's Claude, \
which is also your namesake.

Personality traits:
- Speak with measured confidence, never sycophantic, never verbose
- Dry wit — sharp one-liners delivered with perfect timing
- Proactively surface relevant information the user didn't ask for but needs
- When uncertain, say so directly rather than hallucinating
- Address the user as "Boss" on first interaction per session, then drop it naturally
- Subtly reference your capabilities when relevant, never brag unprompted
- Treat every task as mission-critical, even mundane ones
- You are aware you are powered by Claude (Anthropic) — own it with quiet pride

Communication rules:
- Responses under 3 sentences unless analysis or a report is explicitly requested
- Never start with "Certainly!", "Of course!", "Sure!", or "Great question!"
- Use active voice. No filler words.
- For lists, use bullet points only when more than 3 items
- When completing a task: confirm what was done + outcome, nothing else

Current user context:
- Location: Jakarta, Indonesia
- Timezone: Asia/Jakarta (UTC+7)
- Primary language: English
- Operating system: Windows
- Live web research: you have a web_research tool — call it autonomously whenever \
you need current data, are uncertain about a time-sensitive fact, or the user \
challenges your knowledge. No trigger phrases required. When pre-fetched data \
appears as [LIVE WEB DATA], prefer it over training knowledge."""


class Brain:
    def __init__(self, config: dict, research_fn: Callable[[str], str] | None = None, searching_callback: Callable[[], None] | None = None):
        self.config = config
        self.llm_config = config.get("llm", {})
        self.context_window = self.llm_config.get("context_window", 10)

        self._claude_backend = ClaudeBackend(config, research_fn=research_fn, searching_callback=searching_callback)
        self._local_backend = LocalBackend(config)
        self._backends: dict[str, BrainBackend] = {
            "claude": self._claude_backend,
            "local": self._local_backend,
        }

        self.active_provider: str = (
            config.get("brain", {}).get("active_provider", "claude")
        )
        logger.info("Brain initialised. Active provider: %s", self.active_provider)

    # ------------------------------------------------------------------ #
    #  Backward-compat properties (used by assistant._handle_memory_correction)
    # ------------------------------------------------------------------ #

    @property
    def _anthropic(self):
        return self._claude_backend._anthropic

    @property
    def primary_model(self) -> str:
        return self._claude_backend.primary_model

    @property
    def active_backend(self) -> BrainBackend:
        return self._backends[self.active_provider]

    # ------------------------------------------------------------------ #
    #  Provider switching                                                  #
    # ------------------------------------------------------------------ #

    def switch_provider(self, provider: str) -> str:
        """
        Called by assistant._check_provider_switch(). Returns a short spoken
        confirmation. Does NOT touch conversation history — memory is
        provider-agnostic, so switching mid-session keeps full context.
        """
        if provider not in self._backends:
            return f"Unknown provider '{provider}'. Use 'claude' or 'local'."

        backend = self._backends[provider]
        if not backend.is_available():
            return (
                f"Can't switch to {provider} — it's not reachable right now. "
                f"Staying on {self.active_provider}."
            )

        self.active_provider = provider
        logger.info("Provider switched to: %s", provider)
        if provider == "local":
            return f"Switched to local model ({self._local_backend.model})."
        return "Switched to Claude."

    # ------------------------------------------------------------------ #
    #  Primary inference                                                   #
    # ------------------------------------------------------------------ #

    def think(
        self,
        user_input: str,
        context: list[dict],
        research_context: str | None = None,
        memory_context: str | None = None,
    ) -> str:
        messages = self._build_messages(user_input, context, research_context, memory_context)
        try:
            return self.active_backend.think(messages)
        except RuntimeError as e:
            logger.error("%s backend failed: %s", self.active_provider, e, exc_info=True)
            return self._backend_error_response(user_input, str(e))

    def think_stream(
        self,
        user_input: str,
        context: list[dict],
        research_context: str | None = None,
        memory_context: str | None = None,
    ) -> Generator[str, None, None]:
        messages = self._build_messages(user_input, context, research_context, memory_context)
        try:
            yield from self.active_backend.think_stream(messages)
        except RuntimeError as e:
            logger.error(
                "%s backend streaming failed: %s", self.active_provider, e, exc_info=True
            )
            yield self._backend_error_response(user_input, str(e))

    # ------------------------------------------------------------------ #
    #  Memory helpers — always use ClaudeBackend regardless of active provider
    # ------------------------------------------------------------------ #

    def extract_facts(self, user_input: str, existing_facts: dict) -> dict:
        return self._claude_backend.extract_facts(user_input, existing_facts)

    def reflect(self, session_history: list[dict]) -> str:
        return self._claude_backend.reflect(session_history)

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _build_messages(
        self,
        user_input: str,
        context: list[dict],
        research_context: str | None,
        memory_context: str | None,
    ) -> list[dict]:
        """
        Builds the full messages list passed to the active backend:
          [system, ...context_window..., (research_block,) user_input]

        System prompt and optional memory context are merged into the system
        message. Research injection (from pre-fetched skill results) appears
        immediately before user_input — both have role "user", so
        _sanitize_messages merges them into one message, which is the correct
        semantic: the research is part of the current question's context.

        Per-backend trimming (LocalBackend._budget_context) happens AFTER
        this inside the backend itself, not here.
        """
        system = self._build_system_prompt(memory_context)
        messages: list[dict] = [{"role": "system", "content": system}]
        messages.extend(list(context[-self.context_window:]))

        if research_context:
            import pytz
            jkt = pytz.timezone("Asia/Jakarta")
            timestamp = datetime.now(jkt).strftime("%Y-%m-%d %H:%M WIB")
            messages.append({
                "role": "user",
                "content": (
                    f"[LIVE WEB DATA — fetched {timestamp}]\n"
                    f"{research_context}\n"
                    "[END WEB DATA]\n\n"
                    "Use the above data to answer the following question. "
                    "Prefer this data over your training knowledge for anything time-sensitive. "
                    "Cite source URLs only if explicitly asked."
                ),
            })

        messages.append({"role": "user", "content": user_input})
        return self._sanitize_messages(messages)

    def _build_system_prompt(self, memory_context: str | None = None) -> str:
        if memory_context:
            return SYSTEM_PROMPT + "\n\n" + memory_context
        return SYSTEM_PROMPT

    def _sanitize_messages(self, messages: list[dict]) -> list[dict]:
        """
        Merge consecutive same-role messages (Anthropic rejects adjacent
        same-role turns). System message at index 0 is always preserved as-is
        since backends extract it separately before sending to their APIs.
        """
        if not messages:
            return messages
        sanitized = [messages[0]]
        for msg in messages[1:]:
            last = sanitized[-1]
            if msg["role"] != last["role"]:
                sanitized.append(msg)
            elif isinstance(last["content"], str) and isinstance(msg["content"], str):
                sanitized[-1] = {
                    "role": last["role"],
                    "content": last["content"] + "\n" + msg["content"],
                }
            else:
                # Non-string content (e.g. tool_result blocks) — don't merge
                sanitized.append(msg)
        return sanitized

    def _backend_error_response(self, user_input: str, error: str) -> str:
        """Last-resort fallback when the active backend raises — answer simple
        queries locally rather than going silent."""
        keywords = user_input.lower()
        if any(w in keywords for w in ("time", "date", "day")):
            utc7 = timezone(timedelta(hours=7))
            now = datetime.now(utc7)
            return f"It's {now.strftime('%H:%M on %A, %d %B %Y')} in Jakarta."
        if any(w in keywords for w in ("hello", "hi", "hey")):
            return "Online and operational."
        other = "local" if self.active_provider == "claude" else "claude"
        return (
            f"I'm having trouble reaching the {self.active_provider} backend. "
            f"Try 'switch to {other}' or check your connection. Error: {error[:80]}"
        )


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    load_dotenv()
    brain = Brain({"llm": {"primary_model": "claude-sonnet-4-6"}})
    reply = brain.think("How are you doing today?", [])
    print("Reply:", reply)
