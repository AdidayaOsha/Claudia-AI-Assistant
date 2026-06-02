import logging

from skills import Skill

logger = logging.getLogger(__name__)


class WebSearchSkill(Skill):
    name = "web_search"
    triggers = ["search for", "look up", "google", "look for", "search the web", "search online", "find information", "find out about"]
    description = "Searches the web using DuckDuckGo and returns a summary of top results."

    def __init__(self, config: dict):
        self.max_results: int = 3

    def execute(self, params: dict) -> str:
        query = self._extract_query(params.get("raw_input", ""))
        if not query:
            return "What would you like me to search for?"
        try:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=self.max_results))
            if not results:
                return f"No results found for '{query}'."
            return self._format(query, results)
        except ImportError:
            return "DuckDuckGo search library not installed. Run: pip install duckduckgo-search"
        except Exception as e:
            logger.error("Web search error: %s", e)
            return f"Search failed for '{query}'. Check your connection."

    def _format(self, query: str, results: list[dict]) -> str:
        lines = [f"Top results for '{query}':"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "Untitled")
            body = r.get("body", "")
            snippet = body[:120].rstrip() + ("…" if len(body) > 120 else "")
            lines.append(f"{i}. {title}: {snippet}")
        return " | ".join(lines)

    def _extract_query(self, text: str) -> str:
        lower = text.lower()
        for keyword in ("search for", "look up", "find", "search", "google", "who is", "what is", "look for"):
            if keyword in lower:
                idx = lower.index(keyword) + len(keyword)
                return text[idx:].strip().rstrip("?.,!")
        return text.strip()


if __name__ == "__main__":
    skill = WebSearchSkill({})
    print(skill.execute({"raw_input": "search for Python asyncio tutorial"}))
