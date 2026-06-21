# 🔀 Brain Backend Switch — Claude API ⇄ Local Model (Ollama)

> **Drop this file at:** `C:\Projects\Claudia\docs\brain_backend_switch.md`
> **Add to `CLAUDE.md`:** under the architecture/skill table, add a line pointing here.
> **Status:** Not yet implemented — implement in this order:
> 1. Install Ollama + pull model (one-time, manual — see Setup section)
> 2. Update `config.yaml`
> 3. Create `core/backends/` package (base + claude + local)
> 4. Refactor `core/brain.py` into a thin dispatcher
> 5. Update `core/assistant.py` (pre-router switch check)
> 6. Update `requirements.txt`
> 7. Update boot sequence in `main.py`

---

## 🎯 PURPOSE

Today, `Brain` talks to one provider. This feature makes the provider **swappable
at runtime** — a voice/text command ("switch to local" / "switch to claude") flips
which backend answers, with no restart, so Osha can A/B test response latency and
quality directly against each other in the same session.

This is **not** a fallback chain (Claude → local on failure). It's a deliberate,
explicit, one-at-a-time switch. Exactly one backend is "active" at any moment;
the inactive one is simply not called.

Local inference runs via **Ollama**, which exposes an OpenAI-compatible-ish REST
API on `http://localhost:11434`. No cloud calls, no API key, no internet required
once the model is pulled.

---

## 🧠 WHY A DISPATCHER, NOT A REWRITE

`Brain`'s public interface (already specced in `JARVIS_AI_Assistant_Prompt.md`)
does not change:

```python
class Brain:
    def think(self, user_input: str, context: list) -> str: ...
    def think_stream(self, user_input: str, context: list) -> Generator[str]: ...
    def reset_context(self): ...
```

Everything downstream — `assistant.py`, `memory.get_context_window()`, the
`research_context` injection from `internet_research.md` — calls `brain.think()`
exactly as before. They never know or care which backend answered. `Brain`
becomes a thin router that holds a reference to one of two `BrainBackend`
implementations and delegates to it.

```
                    ┌─────────────────────┐
 assistant.py  ───▶ │   Brain (router)    │
                    │  .active_backend    │
                    └──────────┬──────────┘
                               │
                 ┌─────────────┴─────────────┐
                 ▼                            ▼
        ┌─────────────────┐         ┌──────────────────┐
        │ ClaudeBackend    │         │ LocalBackend      │
        │ (Anthropic API)  │         │ (Ollama, local)   │
        └─────────────────┘         └──────────────────┘
```

---

## 📦 NEW DEPENDENCIES

No new Python packages required — `httpx` is already being added per
`internet_research.md`, and Ollama's REST API is plain JSON over HTTP.

Add a comment to `requirements.txt` noting the local-inference dependency is
external (not pip-installed):

```
# Local LLM backend requires Ollama running separately — https://ollama.com
# Not a pip package. Install Ollama, then: ollama pull qwen2.5:7b-instruct
```

---

## 🛠️ ONE-TIME SETUP (manual, outside the codebase)

1. Install Ollama for Windows: https://ollama.com/download
2. Pull the default model:
   ```
   ollama pull qwen2.5:7b-instruct
   ```
3. Verify it's serving:
   ```
   curl http://localhost:11434/api/tags
   ```
   Should list `qwen2.5:7b-instruct` in the response.
4. Ollama runs as a background service after install — Claudia does not start
   or manage the Ollama process. If `localhost:11434` isn't reachable when
   `LocalBackend` is selected, fail gracefully (see Error Handling below).

**Why `qwen2.5:7b-instruct` as the default:** at the 7-8B size tier it follows
system-prompt constraints (tone rules, length limits, persona) more reliably
than Llama 3.1 8B in side-by-side testing, which matters directly for Claudia's
JARVIS-style personality prompt with its strict communication rules. Similar
speed and VRAM footprint to Llama 3.1 8B, so there's no latency cost for the
improvement. This is a config default, not a hardcoded value — swap it any time.

---

## ⚙️ CONFIG — `config.yaml` additions

```yaml
brain:
  active_provider: "claude"        # "claude" | "local" — which backend answers right now
  switch_phrases:
    local: ["switch to local", "use local model", "switch to ollama"]
    claude: ["switch to claude", "use claude", "switch to cloud"]

  claude:
    model: "claude-sonnet-4-6"
    max_tokens: 1024
    temperature: 0.7

  local:
    provider: "ollama"
    base_url: "http://localhost:11434"
    model: "qwen2.5:7b-instruct"     # change to test other pulled models
    max_tokens: 512                  # smaller than Claude's — keep local responses snappy
    temperature: 0.7
    timeout_seconds: 30              # local generation can be slow on first load (model warm-up)
    max_context_chars: 6000          # ~1500 tokens — local models degrade with long stuffed context
                                      # (see Context Budgeting section — this caps research injection too)
```

`brain.active_provider` is the **startup default**. The runtime switch command
changes the in-memory value for the session; it does not persist back to
`config.yaml` unless you explicitly want that (see Persistence note at the end).

---

## 🔌 PRE-ROUTER SWITCH CHECK — `core/assistant.py`

This is a **meta-command about Claudia herself**, not a skill and not a research
query. It must be checked before intent routing, before skill matching, before
anything else — otherwise "switch to local" could get misrouted (e.g. "local"
might fuzzy-match a file_manager or app_launcher trigger in future skills).

Add near the top of the main input-handling method in `assistant.py`:

```python
import re

def _check_provider_switch(self, user_input: str) -> str | None:
    """
    Check if user_input is a brain-provider switch command.
    Returns a confirmation string if matched, None otherwise.
    Checked BEFORE intent routing — this is a meta-command, not a skill.
    """
    t = user_input.lower().strip()
    switch_cfg = self.config.get("brain", {}).get("switch_phrases", {})

    for phrase in switch_cfg.get("local", []):
        if phrase in t:
            return self.brain.switch_provider("local")

    for phrase in switch_cfg.get("claude", []):
        if phrase in t:
            return self.brain.switch_provider("claude")

    return None
```

In the main orchestration method (wherever `assistant.py` currently does
`intent_router.route(user_input)` first), insert the check above it:

```python
def handle_input(self, user_input: str) -> str:
    # Meta-command check — must run before skill/intent routing
    switch_result = self._check_provider_switch(user_input)
    if switch_result is not None:
        return switch_result

    # ... existing flow: intent_router.route(), skill.execute(), brain.think(), etc.
```

`brain.switch_provider()` returns a short spoken confirmation directly — this
is one of the rare responses that bypasses `brain.think()` entirely, since it's
a statement about Claudia's own state, not a question for the LLM to answer.

---

## 📄 FULL IMPLEMENTATION — `core/backends/base.py`

```python
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
```

---

## 📄 FULL IMPLEMENTATION — `core/backends/claude_backend.py`

```python
"""
core/backends/claude_backend.py — Anthropic Claude API backend.

This wraps the existing Claude integration logic from brain.py's original
single-provider implementation. Behavior is unchanged from before this
refactor — only the call surface moved.
"""

from __future__ import annotations

import logging
import os
from typing import Generator

import anthropic

from core.backends.base import BrainBackend

logger = logging.getLogger("claudia.brain.claude")


class ClaudeBackend(BrainBackend):
    name = "claude"

    def __init__(self, config: dict):
        cfg = config.get("brain", {}).get("claude", {})
        self.model = cfg.get("model", "claude-sonnet-4-6")
        self.max_tokens = cfg.get("max_tokens", 1024)
        self.temperature = cfg.get("temperature", 0.7)
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else None

    def is_available(self) -> bool:
        return self.client is not None

    def think(self, messages: list[dict]) -> str:
        if not self.is_available():
            raise RuntimeError("Claude backend unavailable: ANTHROPIC_API_KEY not set.")

        system, chat_messages = self._split_system(messages)
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=system,
            messages=chat_messages,
        )
        return "".join(
            block.text for block in response.content if block.type == "text"
        )

    def think_stream(self, messages: list[dict]) -> Generator[str, None, None]:
        if not self.is_available():
            raise RuntimeError("Claude backend unavailable: ANTHROPIC_API_KEY not set.")

        system, chat_messages = self._split_system(messages)
        with self.client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=system,
            messages=chat_messages,
        ) as stream:
            for text in stream.text_stream:
                yield text

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
```

---

## 📄 FULL IMPLEMENTATION — `core/backends/local_backend.py`

```python
"""
core/backends/local_backend.py — Local model backend via Ollama.

Ollama exposes a REST API on localhost (default port 11434) that accepts an
OpenAI-style chat payload. No API key, no network egress, runs entirely on
Osha's machine. Claudia does not start/stop the Ollama process — it must
already be running (it installs as a background service).
"""

from __future__ import annotations

import json
import logging
from typing import Generator

import httpx

from core.backends.base import BrainBackend

logger = logging.getLogger("claudia.brain.local")


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
        """Cheap reachability check — pings Ollama, does not generate."""
        try:
            resp = httpx.get(f"{self.base_url}/api/tags", timeout=2)
            return resp.status_code == 200
        except Exception as e:
            logger.debug(f"Ollama not reachable: {e}")
            return False

    def think(self, messages: list[dict]) -> str:
        messages = self._budget_context(messages)
        payload = {
            "model": self.model,
            "messages": messages,
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
            data = resp.json()
            return data.get("message", {}).get("content", "").strip()
        except httpx.ConnectError as e:
            raise RuntimeError(
                "Local backend unavailable: Ollama isn't reachable at "
                f"{self.base_url}. Is it running? ({e})"
            ) from e
        except httpx.TimeoutException as e:
            raise RuntimeError(
                f"Local model timed out after {self.timeout}s. "
                "Model may still be loading into memory (first call after "
                "Ollama start is slower) — try again."
            ) from e

    def think_stream(self, messages: list[dict]) -> Generator[str, None, None]:
        messages = self._budget_context(messages)
        payload = {
            "model": self.model,
            "messages": messages,
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
        Local models (esp. 7-8B) degrade noticeably with long stuffed context —
        unlike Claude, which handles large injected blocks (e.g. research
        results) gracefully. Truncate from the oldest non-system messages
        first if total context exceeds max_context_chars.

        This is the key behavioral difference from ClaudeBackend: the dispatcher
        sends the SAME messages list to both backends, but only LocalBackend
        applies this extra trim.
        """
        total = sum(len(m.get("content", "")) for m in messages)
        if total <= self.max_context_chars:
            return messages

        system_msgs = [m for m in messages if m["role"] == "system"]
        other_msgs = [m for m in messages if m["role"] != "system"]

        # Drop oldest non-system messages until under budget, always keep the
        # most recent user turn (it's the actual question being asked).
        while other_msgs and total > self.max_context_chars:
            removed = other_msgs.pop(0)
            total -= len(removed.get("content", ""))
            logger.debug("Local context budget: dropped oldest message to fit window")

        return system_msgs + other_msgs
```

---

## 📄 REFACTORED — `core/brain.py` (now a thin dispatcher)

```python
"""
core/brain.py — Provider-agnostic dispatcher.

Holds one active BrainBackend at a time and delegates think()/think_stream()
to it. Switching providers is just swapping which backend instance is active —
no restart, no reload of conversation history.
"""

from __future__ import annotations

import logging
from typing import Generator

from core.backends.base import BrainBackend
from core.backends.claude_backend import ClaudeBackend
from core.backends.local_backend import LocalBackend

logger = logging.getLogger("claudia.brain")

SYSTEM_PROMPT = """You are Claudia — a highly intelligent, calm, and razor-sharp
AI personal assistant..."""  # existing JARVIS-style system prompt, unchanged


class Brain:
    def __init__(self, config: dict):
        self.config = config
        self._backends: dict[str, BrainBackend] = {
            "claude": ClaudeBackend(config),
            "local": LocalBackend(config),
        }
        self.active_provider = config.get("brain", {}).get("active_provider", "claude")
        logger.info(f"Brain initialised. Active provider: {self.active_provider}")

    @property
    def active_backend(self) -> BrainBackend:
        return self._backends[self.active_provider]

    def switch_provider(self, provider: str) -> str:
        """
        Called by assistant.py's pre-router check. Returns a short spoken
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
        logger.info(f"Provider switched to: {provider}")
        return f"Switched to {provider}." if provider == "claude" else "Switched to local model."

    def think(self, user_input: str, context: list, research_context: str | None = None) -> str:
        messages = self._build_messages(user_input, context, research_context)
        try:
            return self.active_backend.think(messages)
        except RuntimeError as e:
            logger.error(f"{self.active_provider} backend failed: {e}")
            return (
                f"I'm having trouble reaching the {self.active_provider} backend. "
                f"Try 'switch to {'claude' if self.active_provider == 'local' else 'local'}'."
            )

    def think_stream(self, user_input: str, context: list, research_context: str | None = None) -> Generator[str, None, None]:
        messages = self._build_messages(user_input, context, research_context)
        try:
            yield from self.active_backend.think_stream(messages)
        except RuntimeError as e:
            logger.error(f"{self.active_provider} backend failed: {e}")
            yield (
                f"I'm having trouble reaching the {self.active_provider} backend. "
                f"Try 'switch to {'claude' if self.active_provider == 'local' else 'local'}'."
            )

    def reset_context(self):
        # Unchanged from original spec — clears session history in memory.py,
        # not backend-specific.
        pass

    def _build_messages(self, user_input: str, context: list, research_context: str | None) -> list[dict]:
        """
        Builds the full messages list — system prompt + context window +
        optional research injection + current input. Identical construction
        regardless of active provider; per-backend trimming (see LocalBackend
        ._budget_context) happens AFTER this, inside the backend itself.
        """
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(context)

        if research_context:
            from datetime import datetime
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
                    "Prefer this data over your training knowledge for anything "
                    "time-sensitive. Cite source URLs only if explicitly asked."
                ),
            })

        messages.append({"role": "user", "content": user_input})
        return messages
```

> **Note on research context + local model:** the injection mechanism from
> `internet_research.md` is unchanged — same `[LIVE WEB DATA]` block, same
> place in the message list. The only difference is `LocalBackend._budget_context()`
> may trim it (or older turns) harder than Claude would ever need. If you're
> testing research-heavy queries on local, expect occasionally thinner answers
> than Claude gives for the same query — that's the context budget, not a bug.

---

## 🚀 BOOT SEQUENCE ADDITION — `main.py`

```python
brain_cfg = config.get("brain", {})
active = brain_cfg.get("active_provider", "claude")
claude_ok = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
local_ok = brain.active_backend.name == "local" and brain._backends["local"].is_available() \
    if active == "local" else brain._backends["local"].is_available()

logger.info(
    f"[CLAUDIA] Brain backend: {active.upper()} active "
    f"| Claude {'[OK]' if claude_ok else '[no key]'} "
    f"| Local/Ollama {'[OK]' if local_ok else '[unreachable]'}"
)
```

---

## ✅ ACCEPTANCE CRITERIA

- [ ] Saying/typing "switch to local" while Claude is active → Claudia confirms
      and the next response comes from Ollama
- [ ] Saying/typing "switch to claude" while local is active → confirms and
      switches back
- [ ] Switching mid-conversation preserves session memory — Claudia remembers
      what was said before the switch, regardless of which backend answers next
- [ ] If Ollama isn't running and user says "switch to local" → Claudia reports
      it's unreachable and stays on the current provider (no crash, no silent
      failure)
- [ ] If `ANTHROPIC_API_KEY` is missing and user says "switch to claude" →
      same graceful refusal
- [ ] Research context injection (`internet_research.md`) works identically
      on both backends, with local applying tighter truncation
- [ ] Boot log clearly states which provider is active and whether both are
      reachable
- [ ] No restart required to switch — the change is in-memory on the running
      `Brain` instance

---

## ⚠️ KNOWN LIMITATIONS

- **Local model quality ceiling:** a 7-8B model is meaningfully less capable
  than Claude Sonnet at nuanced reasoning, multi-step instructions, and the
  driest end of the JARVIS wit. Expect this — it's the speed/quality tradeoff
  you're testing for, not a bug.
- **First-call latency on local:** Ollama loads the model into memory on first
  use after the service starts (or after it's been idle and unloaded). The
  first "switch to local" response may be slow even though subsequent ones
  are fast — this is expected, not a timeout bug.
- **No streaming token-level TTS gain on local unless you wire it up:** the
  `think_stream()` method exists on `LocalBackend`, but `speaker.py`'s
  streaming-to-TTS integration (per the original JARVIS spec) needs to treat
  both backends' streams identically — no extra work needed if `assistant.py`
  already just iterates `brain.think_stream()` generically.
- **Context budget is approximate:** `max_context_chars` is a character count,
  not a real tokenizer count — it's a cheap proxy. Good enough for trimming
  decisions; don't treat it as exact.
- **Persistence:** runtime switches do not write back to `config.yaml` by
  design — every fresh boot starts from `brain.active_provider` in config.
  If you want the last-used provider to persist across restarts, add a
  `memory.remember("active_provider", provider)` call inside
  `switch_provider()` and read it back on `Brain.__init__` — straightforward
  addition, left out here to keep the switch behavior predictable for testing.

---

*— `docs/brain_backend_switch.md` — part of the Claudia project documentation suite.*
*— Read alongside `CLAUDE.md` and `docs/internet_research.md` for full project context.*
