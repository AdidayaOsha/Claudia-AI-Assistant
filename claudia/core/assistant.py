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
        self._socket_emit: Callable | None = None  # injected by dashboard
        self._command_queue: queue.Queue = queue.Queue()  # dashboard text input

        # Lazy imports keep boot fast if a module fails
        from core.memory import Memory
        from core.brain import Brain
        from core.listener import Listener
        from core.speaker import Speaker
        from core.intent_router import IntentRouter
        from skills import load_all_skills

        self.memory = Memory(config.get("memory", {}).get("file", "memory.json"))
        self.brain = Brain(config)
        self.listener = Listener(config)
        self.speaker = Speaker(config)
        self.skills = load_all_skills(config)
        self.router = IntentRouter(self.skills)
        self._first_interaction = True

    def set_socket_emit(self, emit_fn: Callable) -> None:
        self._socket_emit = emit_fn

    def submit_text(self, text: str) -> None:
        """Inject a command from the dashboard (or any external source) into the main loop."""
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
        # Strip trailing period so it flows into the greeting naturally
        now_str = now_str.rstrip(".")
        weather_str = ""
        if weather_skill:
            try:
                wx = weather_skill.execute({})
                if "unavailable" not in wx and "not configured" not in wx:
                    weather_str = " " + wx
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
                # Block until either mic or dashboard delivers a command
                try:
                    user_input = self._command_queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                if not user_input:
                    continue

                # Stop Claudia mid-sentence if she's still talking (safety net / text-input path)
                if self.speaker.is_speaking():
                    self.speaker.interrupt()

                # Goodbye detected by listener (fast path) OR by assistant (safety net)
                if user_input == "__goodbye__" or self._is_goodbye(user_input):
                    self.listener.exit_conversation_mode()
                    farewell = "Understood. I'll be here when you need me."
                    logger.info("[CLAUDIA] %s", farewell)
                    self._emit("transcript", {"role": "user", "text": user_input if user_input != "__goodbye__" else ""})
                    self._emit("transcript", {"role": "assistant", "text": farewell})
                    self.memory.add_to_history("assistant", farewell)
                    self.speaker.speak(farewell)
                    continue  # skip enter_conversation_mode()

                logger.info("[USER] %s", user_input)
                self._emit("transcript", {"role": "user", "text": user_input})

                # Snapshot context BEFORE adding current turn so brain gets clean history
                context_snapshot = self.memory.get_context_window(
                    self.config.get("llm", {}).get("context_window", 10)
                )
                self.memory.add_to_history("user", user_input)

                response = self._process(user_input, context_snapshot)

                logger.info("[CLAUDIA] %s", response)
                self._emit("transcript", {"role": "assistant", "text": response})
                self.memory.add_to_history("assistant", response)
                self.memory.increment_command(user_input.split()[0] if user_input else "unknown")
                self.speaker.speak(response)
                self.listener.enter_conversation_mode()  # only reached for non-goodbye responses

            except KeyboardInterrupt:
                logger.info("Shutting down.")
                break
            except Exception as e:
                logger.exception("Unhandled error in main loop: %s", e)

        self.shutdown()

    def _start_listener_thread(self) -> None:
        """Push microphone input into the shared command queue."""
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
            self._emit("skill_active", {"skill": skill.name})
            try:
                return skill.execute(params)
            except Exception as e:
                logger.error("Skill '%s' failed: %s", skill.name, e)
                return f"I ran into an issue with {skill.name}. Standing by."

        # context snapshot was taken before the current user turn was added to history
        if context is None:
            context = []
        return self.brain.think(user_input, context)

    def stop(self) -> None:
        self._running.clear()

    def shutdown(self) -> None:
        self.memory.save()
        self.speaker.stop()
        logger.info("CLAUDIA offline.")
