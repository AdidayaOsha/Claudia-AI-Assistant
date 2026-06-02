import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestTimeDateSkill:
    def _skill(self):
        from skills.time_date import TimeDateSkill
        return TimeDateSkill({"user": {"timezone": "Asia/Jakarta"}})

    def test_returns_string(self):
        skill = self._skill()
        result = skill.execute({"raw_input": "What time is it?"})
        assert isinstance(result, str)
        assert len(result) > 0

    def test_time_query(self):
        skill = self._skill()
        result = skill.execute({"raw_input": "What time is it?"})
        assert ":" in result  # time format HH:MM

    def test_date_query(self):
        skill = self._skill()
        result = skill.execute({"raw_input": "What is today's date?"})
        assert "202" in result  # year present

    def test_day_query(self):
        skill = self._skill()
        result = skill.execute({"raw_input": "what day is it"})
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        assert any(d in result for d in days)


class TestJokesFactsSkill:
    def _skill(self):
        from skills.jokes_facts import JokesFactsSkill
        return JokesFactsSkill({})

    def test_returns_joke(self):
        skill = self._skill()
        result = skill.execute({"raw_input": "tell me a joke"})
        assert isinstance(result, str)
        assert len(result) > 10

    def test_returns_fact(self):
        skill = self._skill()
        result = skill.execute({"raw_input": "give me a fact"})
        assert isinstance(result, str)
        assert len(result) > 10

    def test_randomness(self):
        skill = self._skill()
        results = {skill.execute({"raw_input": "joke"}) for _ in range(20)}
        assert len(results) > 1  # not always the same


class TestWeatherSkill:
    def _skill(self, api_key="test_key"):
        from skills.weather import WeatherSkill
        with patch.dict("os.environ", {"OPENWEATHERMAP_API_KEY": api_key}):
            return WeatherSkill({"weather": {"default_city": "Jakarta", "units": "metric"}})

    def test_no_api_key_returns_message(self):
        from skills.weather import WeatherSkill
        with patch.dict("os.environ", {}, clear=True):
            skill = WeatherSkill({"weather": {"default_city": "Jakarta", "units": "metric"}})
            result = skill.execute({"raw_input": "weather"})
            assert "API key" in result or "not configured" in result

    def test_api_success(self):
        from skills.weather import WeatherSkill
        mock_data = {
            "main": {"temp": 30, "feels_like": 35, "humidity": 80},
            "weather": [{"description": "scattered clouds"}],
        }
        with patch.dict("os.environ", {"OPENWEATHERMAP_API_KEY": "key123"}):
            skill = WeatherSkill({"weather": {"default_city": "Jakarta", "units": "metric"}})
            with patch("requests.get") as mock_get:
                mock_resp = MagicMock()
                mock_resp.json.return_value = mock_data
                mock_resp.raise_for_status.return_value = None
                mock_get.return_value = mock_resp
                result = skill.execute({"raw_input": "weather"})
        assert "30" in result
        assert "°C" in result

    def test_extract_city(self):
        from skills.weather import WeatherSkill
        with patch.dict("os.environ", {"OPENWEATHERMAP_API_KEY": "key"}):
            skill = WeatherSkill({"weather": {"default_city": "Jakarta", "units": "metric"}})
            assert skill._extract_city("weather in Bandung") == "Bandung"
            assert skill._extract_city("weather forecast") is None


class TestWebSearchSkill:
    def _skill(self):
        from skills.web_search import WebSearchSkill
        return WebSearchSkill({})

    def test_no_query_returns_prompt(self):
        skill = self._skill()
        result = skill.execute({"raw_input": "search"})
        assert "search" in result.lower() or "what" in result.lower()

    def test_search_returns_results(self):
        skill = self._skill()
        mock_results = [
            {"title": "Python Docs", "body": "The Python documentation."},
            {"title": "Real Python", "body": "Real Python tutorials."},
        ]
        with patch("duckduckgo_search.DDGS") as mock_ddgs:
            mock_ddgs.return_value.__enter__.return_value.text.return_value = mock_results
            result = skill.execute({"raw_input": "search Python tutorials"})
        assert "Python" in result

    def test_extract_query(self):
        skill = self._skill()
        assert "Python tutorials" in skill._extract_query("search for Python tutorials")
        assert "dinosaurs" in skill._extract_query("look up dinosaurs")
