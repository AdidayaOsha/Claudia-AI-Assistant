import logging
import time
import queue
import threading
from typing import Callable

logger = logging.getLogger(__name__)


class Listener:
    def __init__(self, config: dict):
        cfg = config.get("listener", {})
        self.wake_word: str = config.get("assistant", {}).get("wake_word", "hey claudia").lower()
        self.goodbye_phrases: list[str] = config.get("assistant", {}).get(
            "goodbye_phrases",
            ["talk to you later", "see you later", "never mind", "that's enough",
             "stop listening", "stand by", "goodbye claudia", "bye claudia", "that's all claudia"]
        )
        self.energy_threshold: int = cfg.get("energy_threshold", 300)
        self.silence_timeout: int = cfg.get("silence_timeout", 3)
        self.phrase_time_limit: int = cfg.get("phrase_time_limit", 10)
        self.auto_text_fallback: bool = cfg.get("auto_text_fallback", True)
        # How long to stay in conversation mode after last interaction (seconds)
        self.conversation_timeout: int = cfg.get("conversation_timeout", 60)
        self._text_mode: bool = False
        self._recognizer = None
        self._microphone = None
        self._conversation_active: bool = False
        self._last_interaction: float = 0.0
        self._init_speech()

    def _init_speech(self) -> None:
        try:
            import speech_recognition as sr
            self._recognizer = sr.Recognizer()
            self._recognizer.energy_threshold = self.energy_threshold
            self._recognizer.dynamic_energy_threshold = True
            self._microphone = sr.Microphone()
            self._calibrate()
            logger.info("Microphone initialized")
        except Exception as e:
            logger.warning("Microphone unavailable (%s) -- switching to text input mode", e)
            self._text_mode = True

    def _calibrate(self) -> None:
        try:
            with self._microphone as source:
                logger.info("Calibrating for ambient noise...")
                self._recognizer.adjust_for_ambient_noise(source, duration=1)
            logger.info("Calibration complete. Energy threshold: %.0f", self._recognizer.energy_threshold)
        except Exception as e:
            logger.warning("Calibration failed: %s", e)

    def enter_conversation_mode(self) -> None:
        """Call this after each CLAUDIA response to keep mic open for follow-ups."""
        self._conversation_active = True
        self._last_interaction = time.time()
        logger.debug("Conversation mode active (timeout: %ds)", self.conversation_timeout)

    def exit_conversation_mode(self) -> None:
        self._conversation_active = False
        logger.debug("Conversation mode ended — back to wake-word standby")

    def _is_conversation_active(self) -> bool:
        if not self._conversation_active:
            return False
        if time.time() - self._last_interaction > self.conversation_timeout:
            self.exit_conversation_mode()
            logger.info("Conversation timed out — listening for wake word")
            return False
        return True

    def listen_once(self) -> str | None:
        """Block until one utterance is captured; return transcribed text or None."""
        if self._text_mode:
            return self._text_input()
        try:
            with self._microphone as source:
                audio = self._recognizer.listen(
                    source,
                    timeout=self.silence_timeout,
                    phrase_time_limit=self.phrase_time_limit,
                )
            return self._transcribe(audio)
        except Exception as e:
            logger.debug("Listen error: %s", e)
            return None

    def listen_for_wake_word(self) -> str | None:
        """Return speech text when triggered by wake word or active conversation mode."""
        if self._text_mode:
            text = self._text_input()
            if not text:
                return None
            # In text mode, strip wake word if present but don't require it
            lower = text.lower()
            if self.wake_word in lower:
                return lower.replace(self.wake_word, "").strip() or text
            return text

        import speech_recognition as sr
        try:
            with self._microphone as source:
                audio = self._recognizer.listen(
                    source,
                    timeout=self.silence_timeout,
                    phrase_time_limit=self.phrase_time_limit,
                )
            text = self._transcribe(audio)
            if not text:
                return None

            lower = text.lower()

            # Goodbye phrase — exit conversation mode and return a sentinel
            if self._conversation_active and any(phrase in lower for phrase in self.goodbye_phrases):
                self.exit_conversation_mode()
                return "__goodbye__"

            # Wake word detected — activate conversation mode and return command
            if self.wake_word in lower:
                self.enter_conversation_mode()
                command = lower.replace(self.wake_word, "").strip()
                return command or self._prompt_follow_up()

            # Already in conversation mode — accept without wake word
            if self._is_conversation_active():
                self._last_interaction = time.time()  # reset timeout
                return text

            return None

        except sr.WaitTimeoutError:
            # Silence during conversation mode — let the timeout check handle it
            if self._conversation_active:
                self._is_conversation_active()
            return None
        except Exception as e:
            logger.debug("Wake word listen error: %s", e)
            return None

    def _prompt_follow_up(self) -> str | None:
        """After bare wake word, listen for the actual command."""
        import speech_recognition as sr
        try:
            with self._microphone as source:
                audio = self._recognizer.listen(source, phrase_time_limit=self.phrase_time_limit)
            return self._transcribe(audio)
        except Exception:
            return None

    def _transcribe(self, audio) -> str | None:
        try:
            import speech_recognition as sr
            return self._recognizer.recognize_google(audio)
        except Exception as e:
            logger.debug("Transcription failed: %s", e)
            return None

    def _text_input(self) -> str | None:
        try:
            text = input("[CLAUDIA] Type command: ").strip()
            return text if text else None
        except (EOFError, KeyboardInterrupt):
            return None

    @property
    def is_text_mode(self) -> bool:
        return self._text_mode


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    listener = Listener({
        "assistant": {"wake_word": "hey claudia"},
        "listener": {"silence_timeout": 5, "conversation_timeout": 15},
    })
    print("Listening for 'Hey Claudia'...")
    try:
        while True:
            cmd = listener.listen_for_wake_word()
            if cmd:
                print("Command:", cmd)
                listener.enter_conversation_mode()
    except KeyboardInterrupt:
        print("Stopped.")
