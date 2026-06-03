import json
import logging
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class Memory:
    def __init__(
        self,
        memory_file: str = "memory.json",
        max_session_history: int = 50,
        max_reflections: int = 10,
        max_facts: int = 50,
    ):
        self.memory_path = Path(memory_file)
        self.max_session_history = max_session_history
        self.max_reflections = max_reflections
        self.max_facts = max_facts
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

    # ------------------------------------------------------------------ #
    #  Session history                                                     #
    # ------------------------------------------------------------------ #

    def add_to_history(self, role: str, content: str) -> None:
        self.session_history.append({"role": role, "content": content})
        if len(self.session_history) > self.max_session_history:
            self.session_history = self.session_history[-self.max_session_history:]

    def get_context_window(self, n: int = 10) -> list[dict]:
        return self.session_history[-n:]

    # ------------------------------------------------------------------ #
    #  Factual memory                                                      #
    # ------------------------------------------------------------------ #

    def save_fact(self, key: str, value) -> None:
        """Persist a user fact; prunes oldest entries when over max_facts."""
        prefs = self.long_term["user_preferences"]
        prefs[key] = value
        if len(prefs) > self.max_facts:
            excess = len(prefs) - self.max_facts
            for k in list(prefs.keys())[:excess]:
                del prefs[k]
        self.save()

    def recall(self, key: str):
        return self.long_term["user_preferences"].get(key)

    # ------------------------------------------------------------------ #
    #  Reflective memory                                                   #
    # ------------------------------------------------------------------ #

    def save_reflection(self, summary: str) -> None:
        """Append a session summary; trims to max_reflections (oldest dropped)."""
        notes = self.long_term["notes"]
        notes.append({"text": summary, "timestamp": datetime.now().isoformat()})
        if len(notes) > self.max_reflections:
            self.long_term["notes"] = notes[-self.max_reflections:]
        self.save(force=True)

    # ------------------------------------------------------------------ #
    #  Context injection                                                   #
    # ------------------------------------------------------------------ #

    def get_memory_context(self) -> str:
        """Return a compact memory block for injection into the system prompt."""
        prefs = self.long_term.get("user_preferences", {})
        notes = self.long_term.get("notes", [])
        if not prefs and not notes:
            return ""

        parts = ["[MEMORY — use this to personalise responses]"]
        if prefs:
            facts_str = " | ".join(f"{k}={v}" for k, v in list(prefs.items())[:20])
            parts.append(f"Known facts about Boss: {facts_str}")
        if notes:
            parts.append("Recent session summaries:")
            for n in notes[-5:]:
                date = (n.get("timestamp") or "")[:10]
                text = n.get("text", "").strip()
                if text:
                    parts.append(f"  - {date}: {text}")
        parts.append("[/MEMORY]")
        return "\n".join(parts)

    # ------------------------------------------------------------------ #
    #  Legacy helpers (kept for compatibility)                             #
    # ------------------------------------------------------------------ #

    def remember(self, key: str, value) -> None:
        self.save_fact(key, value)

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

    # ------------------------------------------------------------------ #
    #  Persistence                                                         #
    # ------------------------------------------------------------------ #

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
            logger.info(
                "Memory loaded: %d facts, %d reflections",
                len(self.long_term.get("user_preferences", {})),
                len(self.long_term.get("notes", [])),
            )
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
    mem.save_fact("favorite_city", "Jakarta")
    mem.save_reflection("Discussed weather and opened Service Studio.")
    mem.add_to_history("user", "What time is it?")
    mem.add_to_history("assistant", "It's 9:00 AM.")
    print("Recalled:", mem.recall("favorite_city"))
    print("Context:\n", mem.get_memory_context())
    mem.save()
    print("Saved. Entries:", mem.entry_count)
    Path("test_memory.json").unlink(missing_ok=True)
