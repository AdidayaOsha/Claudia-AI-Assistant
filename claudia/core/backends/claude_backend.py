"""
core/backends/claude_backend.py — Anthropic Claude API backend.

Wraps the existing Claude integration from brain.py's single-provider
implementation, plus OpenAI as an internal fallback. Behavior is unchanged
from before this refactor — only the call surface moved.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Callable, Generator

from core.backends.base import BrainBackend

logger = logging.getLogger(__name__)

# Tool schema exposed to Claude — enables autonomous web search decisions
RESEARCH_TOOL = {
    "name": "web_research",
    "description": (
        "Search the internet for real-time information. Use when:\n"
        "- The topic requires data from after your training cutoff\n"
        "- The user asks about current events, live scores, recent news, or prices\n"
        "- You are uncertain about a time-sensitive fact\n"
        "- The user challenges your knowledge and you need to verify\n"
        "- Answering well requires up-to-date facts\n"
        "Do NOT use for general knowledge you can answer confidently from training."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Precise search query"},
        },
        "required": ["query"],
    },
}


class ClaudeBackend(BrainBackend):
    name = "claude"

    def __init__(self, config: dict, research_fn: Callable[[str], str] | None = None, searching_callback: Callable[[], None] | None = None):
        llm_cfg = config.get("llm", {})
        brain_claude_cfg = config.get("brain", {}).get("claude", {})
        # brain.claude.model takes precedence over llm.primary_model
        self.primary_model = (
            brain_claude_cfg.get("model") or llm_cfg.get("primary_model", "claude-sonnet-4-6")
        )
        self.fallback_model = llm_cfg.get("fallback_model", "gpt-4o")
        self.max_tokens = brain_claude_cfg.get("max_tokens") or llm_cfg.get("max_tokens", 1024)
        self.max_retries = llm_cfg.get("max_retries", 3)
        self._research_fn = research_fn
        self._searching_callback = searching_callback
        self._anthropic = None
        self._openai = None
        self._last_error: str = ""
        self._init_clients()

    def _init_clients(self) -> None:
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if anthropic_key:
            try:
                import anthropic
                self._anthropic = anthropic.Anthropic(api_key=anthropic_key)
                logger.info("Anthropic client initialized")
            except Exception as e:
                logger.error("Anthropic client init failed: %s", e, exc_info=True)
        else:
            logger.warning("ANTHROPIC_API_KEY not set — Anthropic unavailable")

        openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if openai_key:
            try:
                import openai
                self._openai = openai.OpenAI(api_key=openai_key)
                logger.info("OpenAI fallback client initialized")
            except Exception as e:
                logger.error("OpenAI client init failed: %s", e, exc_info=True)
        else:
            logger.info("OPENAI_API_KEY not set — OpenAI fallback unavailable")

    def is_available(self) -> bool:
        return self._anthropic is not None or self._openai is not None

    def think(self, messages: list[dict]) -> str:
        system, chat_messages = self._split_system(messages)
        # If research was pre-fetched (injected by Brain._build_messages), skip
        # the tool-use loop — we already have the data.
        has_prefetched = any(
            "[LIVE WEB DATA]" in str(m.get("content", "")) for m in chat_messages
        )
        self._last_error = ""

        if self._anthropic:
            for attempt in range(self.max_retries):
                try:
                    if self._research_fn and not has_prefetched:
                        return self._think_with_tools(chat_messages, system)
                    return self._think_anthropic(chat_messages, system)
                except Exception as e:
                    self._last_error = str(e)
                    logger.error(
                        "Anthropic attempt %d failed: %s", attempt + 1, e, exc_info=True
                    )
                    if attempt < self.max_retries - 1:
                        time.sleep(2 ** attempt)

        if self._openai:
            for attempt in range(self.max_retries):
                try:
                    return self._think_openai(chat_messages, system)
                except Exception as e:
                    self._last_error = str(e)
                    logger.error(
                        "OpenAI attempt %d failed: %s", attempt + 1, e, exc_info=True
                    )
                    if attempt < self.max_retries - 1:
                        time.sleep(2 ** attempt)

        raise RuntimeError(
            f"All Claude/OpenAI backends failed. Last error: {self._last_error}"
            if self._last_error
            else "No AI clients configured. Set ANTHROPIC_API_KEY in your .env file."
        )

    def think_stream(self, messages: list[dict]) -> Generator[str, None, None]:
        system, chat_messages = self._split_system(messages)

        if self._anthropic:
            try:
                yield from self._stream_anthropic(chat_messages, system)
                return
            except Exception as e:
                logger.error("Anthropic streaming failed: %s", e, exc_info=True)

        if self._openai:
            try:
                yield from self._stream_openai(chat_messages, system)
                return
            except Exception as e:
                logger.error("OpenAI streaming failed: %s", e, exc_info=True)

        raise RuntimeError("All streaming backends failed.")

    # ------------------------------------------------------------------ #
    #  Tool-use loop (autonomous web research — Claude only)             #
    # ------------------------------------------------------------------ #

    def _serialize_content(self, content) -> list[dict]:
        """Convert Anthropic SDK content blocks to plain dicts for re-use in messages."""
        out = []
        for block in content:
            if block.type == "text":
                out.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                out.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
        return out

    def _think_with_tools(self, messages: list[dict], system: str) -> str:
        current_messages = list(messages)

        for _iteration in range(5):
            response = self._anthropic.messages.create(
                model=self.primary_model,
                max_tokens=1024,
                system=system,
                tools=[RESEARCH_TOOL],
                tool_choice={"type": "auto"},
                messages=current_messages,
            )

            if response.stop_reason == "tool_use":
                tool_block = next(
                    (b for b in response.content if b.type == "tool_use"), None
                )
                if tool_block is None:
                    break

                query = tool_block.input.get("query", "")
                logger.info("Tool call: web_research(query=%r)", query)

                if self._searching_callback:
                    try:
                        self._searching_callback()
                    except Exception:
                        pass

                try:
                    result = self._research_fn(query)
                except Exception as e:
                    logger.warning("Research tool error: %s", e)
                    result = f"Search failed: {e}"

                current_messages.append({
                    "role": "assistant",
                    "content": self._serialize_content(response.content),
                })
                current_messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_block.id,
                            "content": result or "No results found.",
                        }
                    ],
                })
            else:
                return "".join(
                    b.text for b in response.content
                    if hasattr(b, "text") and b.text
                )

        return "I searched but couldn't complete the response. Please try again."

    # ------------------------------------------------------------------ #
    #  Memory helpers — delegated from Brain (called by assistant.py)    #
    # ------------------------------------------------------------------ #

    def extract_facts(self, user_input: str, existing_facts: dict) -> dict:
        if not self._anthropic:
            return {}
        prompt = (
            "Extract ONLY facts the user explicitly stated about themselves in first person.\n"
            "Look for clear statements like 'I am', 'my name is', 'I live in', 'I prefer', "
            "'I like', 'I work at', 'I wake up at', 'my wife/husband/partner is'.\n"
            "Do NOT infer, guess, or extract anything not directly stated by the user.\n"
            "Do NOT extract facts already known.\n"
            "Return ONLY a JSON object {\"key\": \"value\"} or {} if nothing qualifies.\n"
            "Keys: short snake_case labels. Max 2 facts per call.\n\n"
            f"User said: \"{user_input[:400]}\"\n"
            f"Already known: {json.dumps(existing_facts)[:300]}"
        )
        try:
            result = self._anthropic.messages.create(
                model=self.primary_model,
                max_tokens=120,
                system="You are a fact extractor. Return ONLY valid JSON, nothing else. No markdown.",
                messages=[{"role": "user", "content": prompt}],
            )
            text = result.content[0].text.strip()
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except Exception as e:
            logger.debug("Fact extraction failed: %s", e)
            return {}

    def reflect(self, session_history: list[dict]) -> str:
        if not self._anthropic or not session_history:
            return ""
        turns = session_history[-20:]
        dialogue = "\n".join(
            f"{m['role'].upper()}: {m['content'][:120]}" for m in turns
        )
        try:
            result = self._anthropic.messages.create(
                model=self.primary_model,
                max_tokens=60,
                system=(
                    "Summarise this conversation in ONE sentence, max 20 words. "
                    "Focus on what was done or discussed. Be specific. No filler."
                ),
                messages=[{"role": "user", "content": dialogue}],
            )
            return result.content[0].text.strip()
        except Exception as e:
            logger.debug("Reflection failed: %s", e)
            return ""

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
        """Anthropic API takes system prompt as a separate top-level param."""
        system = ""
        chat = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                chat.append(m)
        return system, chat

    def _sanitize_messages(self, messages: list[dict], user_input: str = "") -> list[dict]:
        """Merge consecutive same-role messages; ensure last message is user."""
        if not messages:
            return [{"role": "user", "content": user_input}] if user_input else []
        sanitized = [messages[0]]
        for msg in messages[1:]:
            if msg["role"] != sanitized[-1]["role"]:
                sanitized.append(msg)
            else:
                sanitized[-1] = {
                    "role": msg["role"],
                    "content": sanitized[-1]["content"] + "\n" + msg["content"],
                }
        if sanitized and sanitized[-1]["role"] != "user":
            sanitized.append({"role": "user", "content": user_input})
        return sanitized

    def _think_anthropic(self, messages: list[dict], system: str) -> str:
        logger.debug("Sending %d messages to Anthropic", len(messages))
        response = self._anthropic.messages.create(
            model=self.primary_model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
        )
        return response.content[0].text

    def _think_openai(self, messages: list[dict], system: str) -> str:
        full_messages = [{"role": "system", "content": system}] + messages
        response = self._openai.chat.completions.create(
            model=self.fallback_model,
            messages=full_messages,
            max_tokens=self.max_tokens,
        )
        return response.choices[0].message.content

    def _stream_anthropic(self, messages: list[dict], system: str) -> Generator[str, None, None]:
        with self._anthropic.messages.stream(
            model=self.primary_model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                yield text

    def _stream_openai(self, messages: list[dict], system: str) -> Generator[str, None, None]:
        full_messages = [{"role": "system", "content": system}] + messages
        stream = self._openai.chat.completions.create(
            model=self.fallback_model,
            messages=full_messages,
            max_tokens=self.max_tokens,
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
