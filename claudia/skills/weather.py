import logging
import os

import requests

from skills import Skill

logger = logging.getLogger(__name__)

OWM_URL = "https://api.openweathermap.org/data/2.5/weather"


class WeatherSkill(Skill):
    name = "weather"
    triggers = ["weather", "forecast", "temperature", "what's the weather", "how's the weather", "is it raining", "weather today", "weather in"]
    description = "Fetches current weather conditions for Jakarta (or a specified city)."

    def __init__(self, config: dict):
        weather_cfg = config.get("weather", {})
        self.default_city: str = weather_cfg.get("default_city", "Jakarta")
        self.units: str = weather_cfg.get("units", "metric")
        self.api_key: str = os.environ.get("OPENWEATHERMAP_API_KEY", "")

    def execute(self, params: dict) -> str:
        city = self._extract_city(params.get("raw_input", "")) or self.default_city
        if not self.api_key:
            return f"Weather API key not configured. Set OPENWEATHERMAP_API_KEY in your .env file."
        try:
            data = self._fetch(city)
            return self._format(data, city)
        except requests.exceptions.ConnectionError:
            return "Can't reach the weather service right now."
        except Exception as e:
            logger.error("Weather fetch error: %s", e)
            return "Weather data unavailable at the moment."

    def _fetch(self, city: str) -> dict:
        resp = requests.get(
            OWM_URL,
            params={"q": city, "appid": self.api_key, "units": self.units},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json()

    def _format(self, data: dict, city: str) -> str:
        temp = round(data["main"]["temp"])
        feels = round(data["main"]["feels_like"])
        humidity = data["main"]["humidity"]
        description = data["weather"][0]["description"].capitalize()
        unit_sym = "°C" if self.units == "metric" else "°F"
        return (
            f"{city}: {description}, {temp}{unit_sym} "
            f"(feels like {feels}{unit_sym}), humidity {humidity}%."
        )

    def _extract_city(self, text: str) -> str | None:
        lower = text.lower()
        for keyword in ("in ", "for ", "at "):
            idx = lower.find(keyword)
            if idx != -1:
                import re
                rest = text[idx + len(keyword):].strip()
                match = re.match(r"([A-Za-z\s]+?)(?:[?.,!]|$)", rest)
                if match:
                    candidate = match.group(1).strip()
                    if len(candidate) > 2:
                        return candidate.title()
        return None


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    skill = WeatherSkill({"weather": {"default_city": "Jakarta", "units": "metric"}})
    print(skill.execute({"raw_input": "What's the weather like?"}))
