import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.intent_router import IntentRouter
from skills import Skill


class MockSkill(Skill):
    def __init__(self, name, triggers):
        self.name = name
        self.triggers = triggers
        self.description = f"Mock skill: {name}"

    def execute(self, params):
        return f"{self.name} executed"


SKILLS = [
    MockSkill("time_date", ["time", "date", "day", "what time"]),
    MockSkill("weather", ["weather", "forecast", "temperature"]),
    MockSkill("web_search", ["search", "look up", "find"]),
    MockSkill("jokes_facts", ["joke", "funny", "fact", "trivia"]),
    MockSkill("app_launcher", ["open", "launch", "start"]),
    MockSkill("news_briefing", ["news", "headlines", "briefing"]),
]


class TestIntentRouter:
    def _router(self):
        return IntentRouter(SKILLS)

    def test_routes_time_query(self):
        router = self._router()
        skill, _ = router.route("What time is it?")
        assert skill is not None
        assert skill.name == "time_date"

    def test_routes_weather_query(self):
        router = self._router()
        skill, _ = router.route("What's the weather like today?")
        assert skill is not None
        assert skill.name == "weather"

    def test_routes_search_query(self):
        router = self._router()
        skill, _ = router.route("Search for Python tutorials")
        assert skill is not None
        assert skill.name == "web_search"

    def test_routes_joke_query(self):
        router = self._router()
        skill, _ = router.route("Tell me a joke")
        assert skill is not None
        assert skill.name == "jokes_facts"

    def test_routes_app_launch(self):
        router = self._router()
        skill, _ = router.route("Open Chrome")
        assert skill is not None
        assert skill.name == "app_launcher"

    def test_routes_news(self):
        router = self._router()
        skill, _ = router.route("Give me the news headlines")
        assert skill is not None
        assert skill.name == "news_briefing"

    def test_no_match_returns_none(self):
        router = self._router()
        skill, params = router.route("How are you doing today?")
        assert skill is None
        assert params == {}

    def test_params_contain_raw_input(self):
        router = self._router()
        skill, params = router.route("What time is it in Tokyo?")
        assert skill is not None
        assert "raw_input" in params
        assert "time" in params["raw_input"].lower()

    def test_list_skills_returns_all(self):
        router = self._router()
        names = router.list_skills()
        assert len(names) == len(SKILLS)
        assert "time_date" in names

    def test_multi_word_trigger(self):
        router = self._router()
        skill, _ = router.route("what time is it now?")
        assert skill is not None
        assert skill.name == "time_date"
