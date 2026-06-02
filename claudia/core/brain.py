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
- Operating system: Windows"""


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

    def think(self, user_input: str, context: list[dict]) -> str:
        messages = self._build_messages(user_input, context)
        self._last_error = ""

        if self._anthropic:
            for attempt in range(self.max_retries):
                try:
                    return self._think_anthropic(messages)
                except Exception as e:
                    self._last_error = str(e)
                    logger.error("Anthropic attempt %d failed: %s", attempt + 1, e, exc_info=True)
                    if attempt < self.max_retries - 1:
                        time.sleep(2 ** attempt)

        if self._openai:
            for attempt in range(self.max_retries):
                try:
                    return self._think_openai(messages)
                except Exception as e:
                    self._last_error = str(e)
                    logger.error("OpenAI attempt %d failed: %s", attempt + 1, e, exc_info=True)
                    if attempt < self.max_retries - 1:
                        time.sleep(2 ** attempt)

        return self._local_fallback(user_input)

    def think_stream(self, user_input: str, context: list[dict]) -> Generator[str, None, None]:
        messages = self._build_messages(user_input, context)
        if self._anthropic:
            try:
                yield from self._stream_anthropic(messages)
                return
            except Exception as e:
                logger.error("Anthropic streaming failed: %s", e, exc_info=True)

        if self._openai:
            try:
                yield from self._stream_openai(messages)
                return
            except Exception as e:
                logger.error("OpenAI streaming failed: %s", e, exc_info=True)

        yield self._local_fallback(user_input)

    def reset_context(self) -> None:
        pass

    def _build_messages(self, user_input: str, context: list[dict]) -> list[dict]:
        """Build a valid alternating-role message list for the Anthropic API."""
        raw = list(context[-self.context_window:])
        raw.append({"role": "user", "content": user_input})
        return self._sanitize_messages(raw, user_input)

    def _sanitize_messages(self, messages: list[dict], user_input: str = "") -> list[dict]:
        """Ensure messages alternate roles correctly for the Anthropic API."""
        if not messages:
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

    def _think_anthropic(self, messages: list[dict]) -> str:
        logger.debug("Sending %d messages to Anthropic: %s", len(messages), [m["role"] for m in messages])
        response = self._anthropic.messages.create(
            model=self.primary_model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        return response.content[0].text

    def _think_openai(self, messages: list[dict]) -> str:
        full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages
        response = self._openai.chat.completions.create(
            model=self.fallback_model,
            messages=full_messages,
            max_tokens=1024,
        )
        return response.choices[0].message.content

    def _stream_anthropic(self, messages: list[dict]) -> Generator[str, None, None]:
        with self._anthropic.messages.stream(
            model=self.primary_model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                yield text

    def _stream_openai(self, messages: list[dict]) -> Generator[str, None, None]:
        full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages
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
