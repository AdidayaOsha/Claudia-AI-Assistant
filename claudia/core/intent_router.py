import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from skills import Skill

logger = logging.getLogger(__name__)


class IntentRouter:
    def __init__(self, skills: list["Skill"], config: dict | None = None):
        self.skills = skills
        self.config = config or {}
        self._trigger_map: dict[str, "Skill"] = {}
        self._build_trigger_map()

    def _build_trigger_map(self) -> None:
        for skill in self.skills:
            if skill.name == "research":
                continue  # research is only reached via regex fallback, not exact triggers
            for trigger in skill.triggers:
                self._trigger_map[trigger.lower()] = skill
        logger.info("IntentRouter loaded %d skills, %d triggers", len(self.skills), len(self._trigger_map))

    def route(self, user_input: str) -> tuple["Skill | None", dict]:
        """Return (skill, params) or (None, {}) if no skill matches."""
        normalized = user_input.lower().strip()
        normalized = re.sub(r"[^\w\s]", " ", normalized)
        words = normalized.split()

        # Exact trigger match — longest trigger wins
        best_skill = None
        best_len = 0
        for trigger, skill in self._trigger_map.items():
            trigger_words = trigger.split()
            trigger_len = len(trigger_words)
            if trigger_len > best_len and self._contains_phrase(words, trigger_words):
                best_skill = skill
                best_len = trigger_len

        if best_skill:
            params = self._extract_params(user_input, best_skill)
            logger.debug("Routed '%s' → %s", user_input, best_skill.name)
            return best_skill, params

        logger.debug("No skill matched '%s' — routing to Brain", user_input)
        return None, {}

    def _contains_phrase(self, words: list[str], phrase_words: list[str]) -> bool:
        if len(phrase_words) == 1:
            return phrase_words[0] in words
        phrase_str = " ".join(phrase_words)
        text_str = " ".join(words)
        return phrase_str in text_str

    def _extract_params(self, user_input: str, skill: "Skill") -> dict:
        return {"raw_input": user_input}

    def list_skills(self) -> list[str]:
        return [s.name for s in self.skills]


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    from skills import load_all_skills
    import yaml
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    skills = load_all_skills(cfg)
    router = IntentRouter(skills)
    tests = [
        "What time is it?",
        "Search for Python tutorials",
        "What's the weather like today?",
        "Tell me a joke",
        "Open Chrome",
    ]
    for q in tests:
        skill, params = router.route(q)
        print(f"  '{q}' → {skill.name if skill else 'Brain'}")
