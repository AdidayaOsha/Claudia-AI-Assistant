import concurrent.futures
import logging
import queue
import threading
from datetime import datetime
from typing import Callable

logger = logging.getLogger(__name__)


class Assistant:
    """Orchestrates all CLAUDIA subsystems in the main conversation loop."""

    def __init__(self, config: dict):
        self.config = config
        self.name: str = config.get("assistant", {}).get("name", "Claudia")
        self._running = threading.Event()
        self._socket_emit: Callable | None = None
        self._command_queue: queue.Queue = queue.Queue()

        from core.memory import Memory
        from core.brain import Brain
        from core.listener import Listener
        from core.speaker import Speaker
        from core.intent_router import IntentRouter
        from skills import load_all_skills

        mem_cfg = config.get("memory", {})
        self.memory = Memory(
            mem_cfg.get("file", "memory.json"),
            mem_cfg.get("max_session_history", 50),
            mem_cfg.get("max_reflections", 10),
            mem_cfg.get("max_facts", 50),
        )
        self._enable_fact_extraction: bool = mem_cfg.get("enable_fact_extraction", True)
        self._enable_reflection: bool = mem_cfg.get("enable_reflection", True)
        self._min_turns_for_reflection: int = mem_cfg.get("min_turns_for_reflection", 4)

        # Skills must load before Brain so research_fn can be injected
        self.skills = load_all_skills(config)
        research_skill = next((s for s in self.skills if s.name == "research"), None)
        research_fn = (
            (lambda q: research_skill.execute({"query": q, "raw_input": q}))
            if research_skill else None
        )
        def _on_searching():
            self.speaker.speak("Searching the web now. Stand by.")
            self._emit("transcript", {"role": "assistant", "text": "Searching the web..."})

        self.brain = Brain(config, research_fn=research_fn, searching_callback=_on_searching)
        self.listener = Listener(config)
        self.speaker = Speaker(config)
        self.router = IntentRouter(self.skills, config)
        self._research_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="research"
        )
        self._memory_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="memory"
        )
        # Set by _think_stream_and_collect(); checked in run() to skip double-speak
        self._streamed_response: bool = False

    def set_socket_emit(self, emit_fn: Callable) -> None:
        self._socket_emit = emit_fn

    def submit_text(self, text: str) -> None:
        if text:
            self._command_queue.put(text)

    def _emit(self, event: str, data: dict) -> None:
        if self._socket_emit:
            try:
                self._socket_emit(event, data)
            except Exception as e:
                logger.debug("Socket emit failed: %s", e)

    def greet(self) -> None:
        from skills.time_date import TimeDateSkill
        from skills.weather import WeatherSkill

        time_skill = next((s for s in self.skills if isinstance(s, TimeDateSkill)), None)
        weather_skill = next((s for s in self.skills if isinstance(s, WeatherSkill)), None)

        now_str = time_skill.execute({"raw_input": "time and date"}) if time_skill else datetime.now().strftime("%A, %d %B %Y")
        now_str = now_str.rstrip(".")
        weather_str = ""
        if weather_skill:
            try:
                _wxpool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                wx_future = _wxpool.submit(weather_skill.execute, {})
                wx = wx_future.result(timeout=3)
                if "unavailable" not in wx and "not configured" not in wx:
                    weather_str = " " + wx
                _wxpool.shutdown(wait=False)
            except concurrent.futures.TimeoutError:
                pass
            except Exception:
                pass

        greeting = f"Good morning, Boss. {now_str}.{weather_str} Ready when you are."
        logger.info("[CLAUDIA] %s", greeting)
        self.speaker.speak(greeting)
        self._emit("transcript", {"role": "assistant", "text": greeting})

    def run(self) -> None:
        self._running.set()
        self._start_listener_thread()
        self.greet()

        while self._running.is_set():
            try:
                try:
                    user_input = self._command_queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                if not user_input:
                    continue

                if self.speaker.is_speaking():
                    self.speaker.interrupt()

                # Goodbye path — reflect on the session before saying farewell
                if user_input == "__goodbye__" or self._is_goodbye(user_input):
                    self.listener.exit_conversation_mode()
                    self._run_reflection()
                    farewell = "Understood. I'll be here when you need me."
                    logger.info("[CLAUDIA] %s", farewell)
                    self._emit("transcript", {"role": "user", "text": user_input if user_input != "__goodbye__" else ""})
                    self._emit("transcript", {"role": "assistant", "text": farewell})
                    self.memory.add_to_history("assistant", farewell)
                    self.speaker.speak(farewell)
                    continue

                logger.info("[USER] %s", user_input)
                self._emit("transcript", {"role": "user", "text": user_input})

                context_snapshot = self.memory.get_context_window(
                    self.config.get("llm", {}).get("context_window", 10)
                )
                self.memory.add_to_history("user", user_input)

                # Memory correction — intercept before normal routing
                correction = self._handle_memory_correction(user_input)
                if correction:
                    logger.info("[CLAUDIA] %s", correction)
                    self._emit("transcript", {"role": "assistant", "text": correction})
                    self.memory.add_to_history("user", user_input)
                    self.memory.add_to_history("assistant", correction)
                    self.speaker.speak(correction)
                    self.listener.enter_conversation_mode()
                    continue

                response = self._process(user_input, context_snapshot)

                logger.info("[CLAUDIA] %s", response)
                self._emit("transcript", {"role": "assistant", "text": response})
                self.memory.add_to_history("assistant", response)
                self.memory.increment_command((user_input.split() or ["unknown"])[0])

                # Background: extract explicit personal facts from user input only
                if self._enable_fact_extraction:
                    self._memory_executor.submit(
                        self._extract_and_save_facts, user_input
                    )

                # Streaming path (local backend) already spoke sentence-by-sentence;
                # non-streaming path (Claude / skills) needs the full speak call here.
                if not self._streamed_response:
                    self.speaker.speak(response)
                self._streamed_response = False  # reset every turn
                self.listener.enter_conversation_mode()

            except KeyboardInterrupt:
                logger.info("Shutting down.")
                break
            except Exception as e:
                logger.exception("Unhandled error in main loop: %s", e)

        self.shutdown()

    def _start_listener_thread(self) -> None:
        def _listen_loop():
            while self._running.is_set():
                try:
                    text = self.listener.listen_for_wake_word()
                    if text:
                        self._command_queue.put(text)
                except Exception as e:
                    logger.debug("Listener thread error: %s", e)

        t = threading.Thread(target=_listen_loop, daemon=True, name="Listener")
        t.start()

    def _is_goodbye(self, text: str) -> bool:
        lower = text.lower()
        phrases = self.config.get("assistant", {}).get("goodbye_phrases", [])
        return any(phrase in lower for phrase in phrases)

    def _process(self, user_input: str, context: list[dict] | None = None) -> str:
        # Meta-command check — must run before skill/intent routing so phrases
        # like "switch to local" are never accidentally matched by a skill.
        switch_result = self._check_provider_switch(user_input)
        if switch_result is not None:
            return switch_result

        skill, params = self.router.route(user_input)
        if skill:
            return self._process_skill(skill, params, user_input, context)

        if context is None:
            context = []
        memory_ctx = self.memory.get_memory_context()

        # Local backend: stream tokens → sentence-chunked pyttsx3 TTS → low latency
        # Claude backend: full response → ElevenLabs → preserves tool-use loop
        if self.brain.active_provider == "local":
            return self._think_stream_and_collect(user_input, context, memory_ctx)
        return self.brain.think(user_input, context, memory_context=memory_ctx)

    def _process_skill(self, skill, params, user_input, context):
        """Execute a skill; skills with research_output=True inject results into the LLM."""
        self._emit("skill_active", {"skill": skill.name})
        is_research = getattr(skill, "research_output", False)

        if is_research:
            searching_msg = "Searching the web now. Stand by."
            self.speaker.speak(searching_msg)
            self._emit("transcript", {"role": "assistant", "text": "Searching the web..."})

        try:
            if is_research:
                future = self._research_executor.submit(skill.execute, params)
                result = future.result(timeout=60)
            else:
                result = skill.execute(params)
        except concurrent.futures.TimeoutError:
            logger.error("Skill '%s' timed out", skill.name)
            return "I wasn't able to pull live data on that — the search timed out."
        except Exception as e:
            logger.error("Skill '%s' failed: %s", skill.name, e)
            return f"I ran into an issue with {skill.name}. Standing by."

        if is_research and result:
            memory_ctx = self.memory.get_memory_context()
            return self.brain.think(
                user_input=user_input,
                context=context or [],
                research_context=result,
                memory_context=memory_ctx,
            )
        return result

    # ------------------------------------------------------------------ #
    #  Local backend streaming                                            #
    # ------------------------------------------------------------------ #

    def _think_stream_and_collect(
        self,
        user_input: str,
        context: list[dict],
        memory_ctx: str | None,
    ) -> str:
        """
        Stream tokens from the local backend, flushing complete sentences to
        ElevenLabs TTS as they arrive (sentence-level pipelining).

        While ElevenLabs plays sentence N, the LLM is already generating sentence N+1.
        Time-to-first-sound = time to generate first sentence (~2-5 s) instead of
        waiting for the full response (~10-20 s). Voice quality is identical to Claude.
        """
        import re
        SENT_END = re.compile(r'(?<=[.!?])\s+')
        MIN_CHARS = 25  # flush when sentence text before punctuation meets this length

        # Fail fast: 2-second ping before committing to a 30-second stream attempt
        if not self.brain.active_backend.is_available():
            msg = "Local model isn't reachable right now. Say 'switch to claude' to continue."
            self.speaker.speak(msg)
            self._streamed_response = True
            return msg

        buf = ""
        full_text = ""

        try:
            for token in self.brain.think_stream(
                user_input, context, memory_context=memory_ctx
            ):
                buf += token
                full_text += token

                match = SENT_END.search(buf)
                if match and len(buf[: match.start()].strip()) >= MIN_CHARS:
                    sentence = buf[: match.end()].strip()
                    buf = buf[match.end():]
                    self.speaker.speak(sentence)  # ElevenLabs, queued non-blocking

            if buf.strip():
                self.speaker.speak(buf.strip())  # flush final fragment

        except Exception as e:
            # Do NOT retry brain.think() — same local backend, second 30-second timeout
            logger.warning("Local stream failed: %s", e)
            msg = (
                "Local model timed out. Say 'switch to claude' to continue, "
                f"or wait for Ollama to finish loading. Error: {str(e)[:60]}"
            )
            self.speaker.speak(msg)
            self._streamed_response = True
            return msg

        self._streamed_response = True
        return full_text.strip() or "(no response)"

    # ------------------------------------------------------------------ #
    #  Provider switching                                                  #
    # ------------------------------------------------------------------ #

    def _check_provider_switch(self, user_input: str) -> str | None:
        """
        Detect brain-provider switch commands before intent routing.
        Returns a spoken confirmation string if matched, None otherwise.
        Phrases are configured in config.yaml under brain.switch_phrases.
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

    # ------------------------------------------------------------------ #
    #  Memory helpers                                                      #
    # ------------------------------------------------------------------ #

    def _extract_and_save_facts(self, user_input: str) -> None:
        """Background worker — extract explicit personal facts from user input only."""
        try:
            existing = self.memory.long_term.get("user_preferences", {})
            facts = self.brain.extract_facts(user_input, existing)
            for key, value in facts.items():
                if key and value is not None:
                    self.memory.save_fact(key, str(value))
                    logger.info("Fact learned: %s = %s", key, value)
        except Exception as e:
            logger.debug("Fact extraction worker failed: %s", e)

    def _handle_memory_correction(self, user_input: str) -> str | None:
        """If the user is correcting memory, remove the relevant fact and confirm.
        Returns a response string if handled, None otherwise."""
        lower = user_input.lower()
        correction_phrases = [
            "forget that", "that's wrong", "that is wrong", "remove from memory",
            "correct that", "delete that fact", "remove that", "erase that",
            "forget my", "remove my", "delete my", "that's not right", "that is not right",
        ]
        if not any(p in lower for p in correction_phrases):
            return None

        prefs = self.memory.long_term.get("user_preferences", {})
        if not prefs:
            return "Nothing stored to remove."

        # Ask the LLM which key to remove based on the user's phrasing
        facts_list = "\n".join(f"  {k}: {v}" for k, v in prefs.items())
        prompt = (
            f"The user said: \"{user_input}\"\n\n"
            f"Stored facts:\n{facts_list}\n\n"
            "Which key should be removed? Reply with ONLY the exact key name, or 'none' if unclear."
        )
        try:
            result = self.brain._anthropic.messages.create(
                model=self.brain.primary_model,
                max_tokens=30,
                system="You identify which memory key to delete. Reply with the exact key name only.",
                messages=[{"role": "user", "content": prompt}],
            ) if self.brain._anthropic else None

            key = result.content[0].text.strip().strip('"') if result else "none"

            if key and key != "none" and key in prefs:
                del prefs[key]
                self.memory.save(force=True)
                logger.info("Fact removed from memory: %s", key)
                return f"Done. {key.replace('_', ' ').capitalize()} is off the record."
            return "I'm not sure which fact you want removed. Be more specific."
        except Exception as e:
            logger.debug("Memory correction failed: %s", e)
            return None

    def _run_reflection(self) -> None:
        """Synchronously summarise the current session and persist it."""
        if not self._enable_reflection:
            return
        if len(self.memory.session_history) < self._min_turns_for_reflection:
            return
        try:
            summary = self.brain.reflect(self.memory.session_history)
            if summary:
                self.memory.save_reflection(summary)
                logger.info("Session reflection saved: %s", summary)
        except Exception as e:
            logger.debug("Session reflection failed: %s", e)

    # ------------------------------------------------------------------ #
    #  Lifecycle                                                           #
    # ------------------------------------------------------------------ #

    def stop(self) -> None:
        self._running.clear()

    def shutdown(self) -> None:
        self._run_reflection()
        self.memory.save(force=True)
        self._research_executor.shutdown(wait=False)
        self._memory_executor.shutdown(wait=False)
        self.speaker.stop()
        logger.info("CLAUDIA offline.")
