import json
import logging
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class Memory:
    def __init__(self, memory_file: str = "memory.json", max_session_history: int = 50):
        self.memory_path = Path(memory_file)
        self.max_session_history = max_session_history
        self.session_history: list[dict] = []
        self.long_term: dict = {
            "user_preferences": {},
            "frequent_commands": {},
            "notes": [],
            "last_session": "",
        }
        self._last_save_time: float = 0.0
        self._save_interval: float = 2.0
        self.load()

    def remember(self, key: str, value) -> None:
        self.long_term["user_preferences"][key] = value
        self.save()

    def recall(self, key: str):
        return self.long_term["user_preferences"].get(key)

    def increment_command(self, command: str) -> None:
        freq = self.long_term["frequent_commands"]
        freq[command] = freq.get(command, 0) + 1
        self.save()

    def add_note(self, note: str) -> None:
        self.long_term["notes"].append({
            "text": note,
            "timestamp": datetime.now().isoformat(),
        })
        self.save()

    def add_to_history(self, role: str, content: str) -> None:
        self.session_history.append({"role": role, "content": content})
        if len(self.session_history) > self.max_session_history:
            self.session_history = self.session_history[-self.max_session_history:]

    def get_context_window(self, n: int = 10) -> list[dict]:
        return self.session_history[-n:]

    def save(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_save_time < self._save_interval:
            return
        if not self.memory_path.parent.exists():
            self.memory_path.parent.mkdir(parents=True, exist_ok=True)
        self.long_term["last_session"] = datetime.now().isoformat()
        tmp = self.memory_path.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(self.long_term, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(self.memory_path)
            self._last_save_time = now
        except OSError as e:
            logger.error("Failed to save memory: %s", e)

    def load(self) -> None:
        if not self.memory_path.exists():
            return
        try:
            data = json.loads(self.memory_path.read_text(encoding="utf-8"))
            self.long_term.update(data)
            # Validate types — reset any corrupted values to defaults
            expected = {
                "user_preferences": {},
                "frequent_commands": {},
                "notes": [],
                "last_session": "",
            }
            for key, default in expected.items():
                if not isinstance(self.long_term.get(key), type(default)):
                    logger.warning("Memory entry '%s' has wrong type — resetting", key)
                    self.long_term[key] = default
            logger.info("Memory loaded: %d entries", len(self.long_term.get("user_preferences", {})))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Could not load memory file: %s", e)

    @property
    def entry_count(self) -> int:
        return (
            len(self.long_term.get("user_preferences", {}))
            + len(self.long_term.get("notes", []))
            + len(self.long_term.get("frequent_commands", {}))
        )


if __name__ == "__main__":
    mem = Memory("test_memory.json")
    mem.remember("favorite_city", "Jakarta")
    mem.add_to_history("user", "What time is it?")
    mem.add_to_history("assistant", "It's 9:00 AM.")
    print("Recalled:", mem.recall("favorite_city"))
    print("Context:", mem.get_context_window())
    mem.save()
    print("Saved. Entries:", mem.entry_count)
    Path("test_memory.json").unlink(missing_ok=True)
