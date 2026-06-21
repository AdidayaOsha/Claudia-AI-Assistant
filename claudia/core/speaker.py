import logging
import re
import threading
import time
from queue import Queue, Empty
from typing import Callable

logger = logging.getLogger(__name__)


def _pcm_amplitude_peaks(pcm_bytes: bytes, sample_rate: int = 22050) -> list[float]:
    """
    Detect amplitude peaks in raw 16-bit signed mono PCM.
    Returns timestamps (seconds) that correlate with syllable onsets.
    Pure Python — no numpy required.
    """
    import struct
    if len(pcm_bytes) < 4:
        return []

    window = int(sample_rate * 0.025)   # 25 ms analysis window
    stride = window                      # non-overlapping
    n      = len(pcm_bytes) // 2        # total 16-bit samples

    envelope = []
    for i in range(0, n - window, stride):
        chunk = struct.unpack_from(f'<{window}h', pcm_bytes, i * 2)
        rms = (sum(s * s for s in chunk) / window) ** 0.5
        envelope.append(rms)

    if not envelope:
        return []

    threshold = max(envelope) * 0.38
    min_gap   = max(1, int(0.085 / (window / sample_rate)))  # ≥ 85 ms between peaks

    peaks = []
    last  = -min_gap
    for i, amp in enumerate(envelope):
        if amp >= threshold and (i - last) >= min_gap:
            lo = max(0, i - 2)
            hi = min(len(envelope) - 1, i + 2)
            if amp == max(envelope[lo : hi + 1]):
                peaks.append(round(i * window / sample_rate, 3))
                last = i

    return peaks


def _strip_markdown(text: str) -> str:
    """Remove markdown syntax that TTS engines would read literally."""
    # Bold/italic: ***x***, **x**, *x*, __x__, _x_
    text = re.sub(r'\*{1,3}(.*?)\*{1,3}', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'_{1,3}(.*?)_{1,3}', r'\1', text, flags=re.DOTALL)
    # Headers: ## Title → Title
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Inline code / code blocks
    text = re.sub(r'`{1,3}[^`]*`{1,3}', '', text)
    # Links: [label](url) → label
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    # Bullet / numbered list markers
    text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
    # Collapse newlines to spaces
    text = re.sub(r'\n+', ' ', text)
    return text.strip()


class Speaker:
    def __init__(self, config: dict):
        voice_cfg = config.get("voice", {})
        listener_cfg = config.get("listener", {})
        self.engine_name: str = voice_cfg.get("engine", "pyttsx3")
        self.rate: int = voice_cfg.get("rate", 175)
        self.volume: float = voice_cfg.get("volume", 0.9)
        self.elevenlabs_voice_id: str = voice_cfg.get("elevenlabs_voice_id", "")
        self._queue: Queue = Queue()
        self._stop_event = threading.Event()
        self._interrupted = threading.Event()
        self._speaking: bool = False
        self._barge_in_enabled: bool = listener_cfg.get("enable_barge_in", True)
        self._barge_in_threshold: int = listener_cfg.get("barge_in_threshold", 600)
        self._engine = None
        self._pyttsx3_engine = None  # secondary offline engine for fast/local TTS
        self._worker_thread: threading.Thread | None = None
        self._on_speak_start: Callable | None = None        # fired when TTS begins
        self._on_speak_stop: Callable | None = None         # fired when TTS ends
        self._on_speak_peaks: Callable | None = None        # fired with syllable peak timestamps
        self._init_engine()
        self._start_worker()

    def _init_engine(self) -> None:
        if self.engine_name == "pyttsx3":
            self._init_pyttsx3()
        elif self.engine_name == "elevenlabs":
            self._init_elevenlabs()
        else:
            logger.warning("Unknown voice engine '%s' — defaulting to pyttsx3", self.engine_name)
            self._init_pyttsx3()

    def _init_pyttsx3(self) -> None:
        try:
            import pyttsx3
            self._engine = pyttsx3.init()
            self._engine.setProperty("rate", self.rate)
            self._engine.setProperty("volume", self.volume)
            logger.info("pyttsx3 TTS initialized (rate=%d, volume=%.1f)", self.rate, self.volume)
        except Exception as e:
            logger.error("pyttsx3 init failed: %s", e)
            self._engine = None

    def _try_init_pyttsx3_secondary(self) -> None:
        """Lazily initialise pyttsx3 as a secondary offline engine.
        Called on first fast=True speak, NOT at startup — eager init after
        pygame.mixer.init() breaks the ElevenLabs audio path on Windows."""
        if self._pyttsx3_engine is not None:
            return  # already done
        if self.engine_name == "pyttsx3":
            self._pyttsx3_engine = self._engine
            return
        try:
            import pyttsx3
            eng = pyttsx3.init()
            eng.setProperty("rate", self.rate)
            eng.setProperty("volume", self.volume)
            self._pyttsx3_engine = eng
            logger.info("pyttsx3 secondary engine ready (fast offline TTS)")
        except Exception as e:
            logger.debug("pyttsx3 secondary engine unavailable: %s", e)

    def _init_elevenlabs(self) -> None:
        try:
            import os
            import pygame
            from elevenlabs import ElevenLabs
            self._eleven = ElevenLabs(api_key=os.environ.get("ELEVENLABS_API_KEY", ""))
            pygame.mixer.init(frequency=22050, size=-16, channels=1)
            logger.info("ElevenLabs TTS initialized")
        except Exception as e:
            logger.warning("ElevenLabs init failed (%s) — falling back to pyttsx3", e)
            self._init_pyttsx3()
            self.engine_name = "pyttsx3"

    def _start_worker(self) -> None:
        self._worker_thread = threading.Thread(target=self._worker, daemon=True, name="Speaker")
        self._worker_thread.start()

    def _worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                item = self._queue.get(timeout=0.2)
                if item is None:
                    break
                text, fast = item if isinstance(item, tuple) else (item, False)
                self._speak_now(text, fast=fast)
                self._queue.task_done()
            except Empty:
                continue
            except Exception as e:
                logger.error("Speaker worker error: %s", e)

    def _speak_now(self, text: str, fast: bool = False) -> None:
        self._speaking = True
        self._interrupted.clear()
        if self._on_speak_start:
            try:
                self._on_speak_start()
            except Exception:
                pass
        try:
            if fast:
                # Lazy-init pyttsx3 on first use (avoids breaking pygame mixer at startup)
                if self._pyttsx3_engine is None:
                    self._try_init_pyttsx3_secondary()
                if self._pyttsx3_engine:
                    try:
                        self._pyttsx3_engine.say(text)
                        self._pyttsx3_engine.runAndWait()
                    except Exception as e:
                        logger.error("Fast TTS (pyttsx3) error: %s", e)
                    return  # don't fall through to ElevenLabs path
            if self.engine_name == "pyttsx3" and self._engine:
                try:
                    self._engine.say(text)
                    self._engine.runAndWait()
                except Exception as e:
                    logger.error("pyttsx3 speak error: %s", e)
            elif self.engine_name == "elevenlabs":
                self._speak_elevenlabs(text)
        finally:
            self._speaking = False
            self._interrupted.clear()
            if self._on_speak_stop:
                try:
                    self._on_speak_stop()
                except Exception:
                    pass

    def _speak_elevenlabs(self, text: str) -> None:
        try:
            import io
            import pygame
            audio_iter = self._eleven.text_to_speech.convert(
                voice_id=self.elevenlabs_voice_id or "21m00Tcm4TlvDq8ikWAM",
                text=text,
                model_id="eleven_multilingual_v2",
                output_format="pcm_22050",
            )
            pcm_bytes = b"".join(audio_iter)

            # Analyze amplitude peaks BEFORE playback so frontend can pre-schedule flares
            if self._on_speak_peaks:
                try:
                    peaks = _pcm_amplitude_peaks(pcm_bytes)
                    if peaks:
                        self._on_speak_peaks(peaks)
                except Exception:
                    pass

            import wave
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(22050)
                wf.writeframes(pcm_bytes)
            buf.seek(0)
            sound = pygame.mixer.Sound(buf)
            sound.play()
            self._start_vad_monitor()
            while pygame.mixer.get_busy() and not self._interrupted.is_set():
                pygame.time.wait(50)
            if self._interrupted.is_set():
                pygame.mixer.stop()
        except Exception as e:
            logger.error("ElevenLabs speak error: %s — falling back to pyttsx3", e)
            if self._engine:
                self._engine.say(text)
                self._engine.runAndWait()

    # ------------------------------------------------------------------ #
    #  Barge-in: VAD monitor                                              #
    # ------------------------------------------------------------------ #

    def _start_vad_monitor(self) -> None:
        if not self._barge_in_enabled:
            return
        if getattr(self, '_vad_disabled', False):
            return
        t = threading.Thread(target=self._vad_worker, daemon=True, name="VAD")
        t.start()

    def _vad_worker(self) -> None:
        """Open a raw PyAudio input stream and call interrupt() on voice onset."""
        try:
            import pyaudio
        except ImportError:
            logger.debug("pyaudio not available — barge-in VAD disabled")
            return

        # Compute RMS using audioop (stdlib) or manual fallback for Python 3.13+
        try:
            import audioop
            def _rms(data: bytes) -> float:
                return audioop.rms(data, 2)
        except ImportError:
            import struct
            def _rms(data: bytes) -> float:
                n = len(data) // 2
                if n == 0:
                    return 0.0
                samples = struct.unpack(f"{n}h", data)
                return (sum(s * s for s in samples) / n) ** 0.5

        pa = None
        stream = None
        # Retry opening the mic — the listener thread may hold it briefly after
        # releasing it between listen() calls; wait up to 3 s for it to free up.
        for attempt in range(20):
            try:
                pa = pyaudio.PyAudio()
                stream = pa.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=16000,
                    input=True,
                    frames_per_buffer=1024,
                )
                break
            except OSError:
                try:
                    pa.terminate()
                except Exception:
                    pass
                pa = None
                if attempt < 19:
                    time.sleep(0.15)
            except Exception as e:
                logger.debug("VAD monitor open error: %s", e)
                return
        if stream is None:
            logger.warning("VAD monitor: mic still busy after retries — barge-in skipped this turn")
            return

        # VAD loop — only reached if stream opened successfully
        time.sleep(0.3)
        consecutive_loud = 0
        try:
            while self._speaking and not self._interrupted.is_set():
                try:
                    data = stream.read(1024, exception_on_overflow=False)
                    rms = _rms(data)
                    if rms > self._barge_in_threshold:
                        consecutive_loud += 1
                        if consecutive_loud >= 2:  # ~130 ms of sustained voice
                            logger.info("Barge-in detected (RMS=%.0f) — interrupting speech", rms)
                            self.interrupt()
                            break
                    else:
                        consecutive_loud = 0
                except Exception:
                    break
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            if pa is not None:
                try:
                    pa.terminate()
                except Exception:
                    pass

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def is_speaking(self) -> bool:
        return self._speaking

    def interrupt(self) -> None:
        """Stop current speech immediately (barge-in or safety-net call)."""
        self._interrupted.set()
        try:
            import pygame
            if pygame.mixer.get_init():
                pygame.mixer.stop()
        except Exception:
            pass
        if self.engine_name == "pyttsx3" and self._engine:
            try:
                self._engine.stop()
            except Exception:
                pass
        self._drain_queue()
        logger.info("Speech interrupted")

    def speak(self, text: str, fast: bool = False) -> None:
        """Queue text for non-blocking TTS output."""
        text = _strip_markdown(text)
        if not text:
            return
        self._queue.put((text, fast))

    def speak_sync(self, text: str) -> None:
        """Speak immediately and block until done (for boot messages)."""
        if not text:
            return
        self._speak_now(text)

    def _drain_queue(self) -> None:
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except Empty:
                break

    def stop(self) -> None:
        self._drain_queue()
        self._queue.put(None)
        self._stop_event.set()
        if self._worker_thread:
            self._worker_thread.join(timeout=2)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    speaker = Speaker({"voice": {"engine": "pyttsx3", "rate": 175, "volume": 0.9}})
    speaker.speak("Online and operational. Good morning, Boss.")
    import time
    time.sleep(4)
    speaker.stop()
