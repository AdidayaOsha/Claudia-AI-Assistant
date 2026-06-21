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


# Use multi-word triggers matching the real skill conventions
SKILLS = [
    MockSkill("time_date", ["what time", "what day", "current time", "what's the time", "what day is it", "what's the date"]),
    MockSkill("weather", ["weather", "forecast", "temperature", "what's the weather", "weather in"]),
    MockSkill("web_search", ["search for", "look up", "google", "find information", "find out about"]),
    MockSkill("jokes_facts", ["tell me a joke", "fun fact", "did you know", "tell a joke"]),
    MockSkill("app_launcher", ["open app", "open chrome", "launch app", "start app", "open notepad", "browse to"]),
    MockSkill("news_briefing", ["latest news", "news briefing", "top headlines", "morning briefing"]),
    MockSkill("research", ["search for", "look up", "find information about", "latest news about", "right now", "as of today"]),
]
CONFIG = {"research": {"enabled": True}}


class TestIntentRouter:
    def _router(self, config=None):
        return IntentRouter(SKILLS, config or CONFIG)

    def test_routes_time_query(self):
        router = self._router()
        skill, _ = router.route("what time is it?")
        assert skill is not None
        assert skill.name == "time_date"

    def test_routes_weather_query(self):
        router = self._router()
        skill, _ = router.route("what's the weather like today?")
        assert skill is not None
        assert skill.name == "weather"

    def test_routes_search_query_to_web_search(self):
        router = self._router()
        skill, _ = router.route("search for Python tutorials")
        assert skill is not None
        assert skill.name == "web_search", f"Expected web_search, got {skill.name}"

    def test_routes_joke_query(self):
        router = self._router()
        skill, _ = router.route("tell me a joke")
        assert skill is not None
        assert skill.name == "jokes_facts"

    def test_routes_app_launch(self):
        router = self._router()
        skill, _ = router.route("open chrome")
        assert skill is not None
        assert skill.name == "app_launcher"

    def test_routes_news(self):
        router = self._router()
        skill, _ = router.route("latest news headlines")
        assert skill is not None
        assert skill.name == "news_briefing"

    def test_unmatched_research_query_falls_through_to_brain(self):
        # Research routing was removed from the router — the Brain handles it
        # autonomously via tool use. Unmatched queries return None.
        router = self._router()
        skill, _ = router.route("what is happening in Jakarta right now")
        assert skill is None

    def test_unmatched_as_of_today_falls_through_to_brain(self):
        router = self._router()
        skill, _ = router.route("as of today what is the price of gold")
        assert skill is None

    def test_research_skip_when_existing_skill_matches(self):
        router = self._router()
        skill, _ = router.route("search for python in the morning")
        # web_search triggers match "search for" — research is not checked
        assert skill.name == "web_search"

    def test_research_disabled_when_config_disabled(self):
        router = self._router({"research": {"enabled": False}})
        skill, _ = router.route("what is happening in Jakarta right now")
        assert skill is None

    def test_no_match_returns_none(self):
        router = self._router()
        skill, params = router.route("how are you doing today?")
        assert skill is None
        assert params == {}

    def test_params_contain_raw_input(self):
        router = self._router()
        skill, params = router.route("what time is it in Tokyo?")
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
