import logging
import os

import requests

from skills import Skill

logger = logging.getLogger(__name__)

NEWSAPI_URL = "https://newsapi.org/v2/top-headlines"


class NewsBriefingSkill(Skill):
    name = "news_briefing"
    triggers = ["news briefing", "morning briefing", "latest news", "top headlines", "what's in the news", "news today", "give me the news", "read the news"]
    description = "Fetches top news headlines using NewsAPI."

    def __init__(self, config: dict):
        self.api_key: str = os.environ.get("NEWSAPI_KEY", "")
        self.country: str = "id"  # Indonesia
        self.max_headlines: int = 5

    def execute(self, params: dict) -> str:
        if not self.api_key:
            return self._rss_fallback()
        try:
            headlines = self._fetch()
            if not headlines:
                return "No headlines available right now."
            return self._format(headlines)
        except requests.exceptions.ConnectionError:
            return "Can't reach the news service."
        except Exception as e:
            logger.error("News briefing error: %s", e)
            return "News briefing unavailable."

    def _fetch(self) -> list[str]:
        resp = requests.get(
            NEWSAPI_URL,
            params={
                "country": self.country,
                "apiKey": self.api_key,
                "pageSize": self.max_headlines,
            },
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        return [a["title"] for a in data.get("articles", []) if a.get("title")]

    def _format(self, headlines: list[str]) -> str:
        intro = f"Top {len(headlines)} headlines: "
        items = ". ".join(f"{i}. {h}" for i, h in enumerate(headlines, 1))
        return intro + items

    def _rss_fallback(self) -> str:
        try:
            import xml.etree.ElementTree as ET
            resp = requests.get(
                "https://feeds.bbci.co.uk/news/world/rss.xml",
                timeout=5,
                headers={"User-Agent": "Claudia/1.0"},
            )
            root = ET.fromstring(resp.content)
            titles = [item.findtext("title") for item in root.iter("item")][:self.max_headlines]
            titles = [t for t in titles if t]
            if titles:
                return "BBC headlines: " + ". ".join(f"{i}. {t}" for i, t in enumerate(titles, 1))
        except Exception as e:
            logger.debug("RSS fallback failed: %s", e)
        return "NewsAPI key not configured. Set NEWSAPI_KEY in your .env file."


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    skill = NewsBriefingSkill({})
    print(skill.execute({}))
