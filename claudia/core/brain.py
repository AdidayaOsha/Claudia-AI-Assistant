import json
import logging
import time
from typing import Generator

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
- Live web research: available via DuckDuckGo + Wikipedia + page scraping. \
Triggered automatically when queries contain action phrases (e.g. "search for", \
"look up", "what's happening", "latest news on"). When live data is provided, \
it appears as [LIVE WEB DATA] above the user message — use it and prefer it \
over your training knowledge for anything time-sensitive."""


class Brain:
    def __init__(self, config: dict):
        self.config = config
        self.llm_config = config.get("llm", {})
        self.context_window = self.llm_config.get("context_window", 10)
        self.max_retries = self.llm_config.get("max_retries", 3)
        self.primary_model = self.llm_config.get("primary_model", "claude-sonnet-4-6")
        self.fallback_model = self.llm_config.get("fallback_model", "gpt-4o")
        self._anthropic = None
        self._openai = None
        self._last_error: str = ""
        self._init_clients()

    def _init_clients(self) -> None:
        import os
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

    # ------------------------------------------------------------------ #
    #  System prompt                                                       #
    # ------------------------------------------------------------------ #

    def _build_system_prompt(self, memory_context: str | None = None) -> str:
        if memory_context:
            return SYSTEM_PROMPT + "\n\n" + memory_context
        return SYSTEM_PROMPT

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
        messages = self._build_messages(user_input, context)
        system = self._build_system_prompt(memory_context)

        if research_context:
            from datetime import datetime
            import pytz
            jkt = pytz.timezone("Asia/Jakarta")
            timestamp = datetime.now(jkt).strftime("%Y-%m-%d %H:%M WIB")
            messages.insert(0, {
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
            messages = self._sanitize_messages(messages, user_input)

        self._last_error = ""

        if self._anthropic:
            for attempt in range(self.max_retries):
                try:
                    return self._think_anthropic(messages, system)
                except Exception as e:
                    self._last_error = str(e)
                    logger.error("Anthropic attempt %d failed: %s", attempt + 1, e, exc_info=True)
                    if attempt < self.max_retries - 1:
                        time.sleep(2 ** attempt)

        if self._openai:
            for attempt in range(self.max_retries):
                try:
                    return self._think_openai(messages, system)
                except Exception as e:
                    self._last_error = str(e)
                    logger.error("OpenAI attempt %d failed: %s", attempt + 1, e, exc_info=True)
                    if attempt < self.max_retries - 1:
                        time.sleep(2 ** attempt)

        return self._local_fallback(user_input)

    def think_stream(
        self,
        user_input: str,
        context: list[dict],
        research_context: str | None = None,
        memory_context: str | None = None,
    ) -> Generator[str, None, None]:
        messages = self._build_messages(user_input, context)
        system = self._build_system_prompt(memory_context)

        if research_context:
            from datetime import datetime
            import pytz
            jkt = pytz.timezone("Asia/Jakarta")
            timestamp = datetime.now(jkt).strftime("%Y-%m-%d %H:%M WIB")
            messages.insert(0, {
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
            messages = self._sanitize_messages(messages, user_input)

        if self._anthropic:
            try:
                yield from self._stream_anthropic(messages, system)
                return
            except Exception as e:
                logger.error("Anthropic streaming failed: %s", e, exc_info=True)

        if self._openai:
            try:
                yield from self._stream_openai(messages, system)
                return
            except Exception as e:
                logger.error("OpenAI streaming failed: %s", e, exc_info=True)

        yield self._local_fallback(user_input)

    # ------------------------------------------------------------------ #
    #  Memory helpers — called from assistant background threads          #
    # ------------------------------------------------------------------ #

    def extract_facts(
        self,
        user_input: str,
        response: str,
        existing_facts: dict,
    ) -> dict:
        """Extract personal facts from a single exchange. Returns {} on failure or nothing found."""
        if not self._anthropic:
            return {}
        prompt = (
            "Extract any personal facts, preferences, or important information the user revealed.\n"
            "Return ONLY a JSON object like {\"key\": \"value\"} or {} if nothing notable.\n"
            "Keys must be short snake_case labels (e.g. wife_name, prefers_morning_coffee).\n"
            "Max 3 facts. Do not re-extract facts already known.\n\n"
            f"User said: \"{user_input[:300]}\"\n"
            f"Assistant replied: \"{response[:200]}\"\n"
            f"Already known: {json.dumps(existing_facts)[:400]}"
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
        """Summarise a session in one sentence (≤20 words)."""
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

    def _build_messages(self, user_input: str, context: list[dict]) -> list[dict]:
        raw = list(context[-self.context_window:])
        raw.append({"role": "user", "content": user_input})
        return self._sanitize_messages(raw, user_input)

    def _sanitize_messages(self, messages: list[dict], user_input: str = "") -> list[dict]:
        if not messages:
            if user_input:
                return [{"role": "user", "content": user_input}]
            return messages
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
            max_tokens=1024,
            system=system,
            messages=messages,
        )
        return response.content[0].text

    def _think_openai(self, messages: list[dict], system: str) -> str:
        full_messages = [{"role": "system", "content": system}] + messages
        response = self._openai.chat.completions.create(
            model=self.fallback_model,
            messages=full_messages,
            max_tokens=1024,
        )
        return response.choices[0].message.content

    def _stream_anthropic(self, messages: list[dict], system: str) -> Generator[str, None, None]:
        with self._anthropic.messages.stream(
            model=self.primary_model,
            max_tokens=1024,
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
            max_tokens=1024,
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    def _local_fallback(self, user_input: str) -> str:
        if self._last_error:
            logger.error("All LLM backends failed. Last error: %s", self._last_error)
        else:
            logger.error("All LLM backends failed — no clients configured")
        keywords = user_input.lower()
        if any(w in keywords for w in ("time", "date", "day")):
            from datetime import datetime, timezone, timedelta
            utc7 = timezone(timedelta(hours=7))
            now = datetime.now(utc7)
            return f"It's {now.strftime('%H:%M on %A, %d %B %Y')} in Jakarta."
        if any(w in keywords for w in ("hello", "hi", "hey")):
            return "Online and operational."
        if self._last_error:
            return f"I'm having trouble reaching my brain right now. Error: {self._last_error[:80]}"
        return "No AI backends are configured. Set ANTHROPIC_API_KEY in your .env file."


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    load_dotenv()
    brain = Brain({"llm": {"primary_model": "claude-sonnet-4-6"}})
    reply = brain.think("How are you doing today?", [])
    print("Reply:", reply)
