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

        self.brain = Brain(config)
        self.listener = Listener(config)
        self.speaker = Speaker(config)
        self.skills = load_all_skills(config)
        self.router = IntentRouter(self.skills, config)
        self._research_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="research"
        )
        self._memory_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="memory"
        )

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

                response = self._process(user_input, context_snapshot)

                logger.info("[CLAUDIA] %s", response)
                self._emit("transcript", {"role": "assistant", "text": response})
                self.memory.add_to_history("assistant", response)
                self.memory.increment_command((user_input.split() or ["unknown"])[0])

                # Background: extract personal facts from this exchange
                if self._enable_fact_extraction:
                    self._memory_executor.submit(
                        self._extract_and_save_facts, user_input, response
                    )

                self.speaker.speak(response)
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
        skill, params = self.router.route(user_input)
        if skill:
            return self._process_skill(skill, params, user_input, context)

        if context is None:
            context = []
        memory_ctx = self.memory.get_memory_context()
        return self.brain.think(user_input, context, memory_context=memory_ctx)

    def _process_skill(self, skill, params, user_input, context):
        """Execute a skill; skills with research_output=True inject results into the LLM."""
        self._emit("skill_active", {"skill": skill.name})
        is_research = getattr(skill, "research_output", False)
        try:
            if is_research:
                future = self._research_executor.submit(skill.execute, params)
                result = future.result(timeout=30)
            else:
                result = skill.execute(params)
        except concurrent.futures.TimeoutError:
            logger.error("Skill '%s' timed out after 30s", skill.name)
            return "I wasn't able to pull live data on that."
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
    #  Memory helpers                                                      #
    # ------------------------------------------------------------------ #

    def _extract_and_save_facts(self, user_input: str, response: str) -> None:
        """Background worker — extract personal facts and persist them."""
        try:
            existing = self.memory.long_term.get("user_preferences", {})
            facts = self.brain.extract_facts(user_input, response, existing)
            for key, value in facts.items():
                if key and value is not None:
                    self.memory.save_fact(key, str(value))
                    logger.info("Fact learned: %s = %s", key, value)
        except Exception as e:
            logger.debug("Fact extraction worker failed: %s", e)

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
