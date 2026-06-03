from __future__ import annotations

import logging
import re
import time

from cachetools import TTLCache

from skills import Skill

logger = logging.getLogger("claudia.movies")

_cache: TTLCache = TTLCache(maxsize=64, ttl=600)


class MovieSkill(Skill):
    name = "movies"
    research_output = True
    triggers = [
        "trending movie", "top movie", "what movies are", "popular movie",
        "movie recommendation", "recommend a movie", "what should i watch",
        "what to watch tonight", "movie review", "review this movie",
        "is this movie good", "how good is", "movie rating",
        "box office", "new movie", "latest movie", "currently showing",
        "best movie", "worst movie", "movie cast", "who stars in",
    ]
    description = "Live movie search — trending films, reviews, ratings, and recommendations."

    def __init__(self, config: dict):
        cfg = config.get("movies", {})
        self.enabled: bool = cfg.get("enabled", True)
        self.max_results: int = cfg.get("max_results", 5)
        self.scrape_top: bool = cfg.get("scrape_top_result", True)

    def execute(self, params: dict) -> str:
        if not self.enabled:
            return ""
        raw = params.get("raw_input", "").strip()
        if not raw:
            return ""

        cache_key = raw.lower()
        if cache_key in _cache:
            logger.debug("Movie cache hit: %s", cache_key)
            return _cache[cache_key]

        query = self._build_query(raw)
        logger.info("Movie search query: %s", query)

        results = self._search_ddg(query)
        scraped = ""
        if self.scrape_top and results:
            top_url = results[0].get("href", "")
            if top_url:
                scraped = self._scrape(top_url)

        context = self._build_context(raw, query, results, scraped)
        _cache[cache_key] = context
        return context

    # ------------------------------------------------------------------ #
    #  Query builder                                                       #
    # ------------------------------------------------------------------ #

    def _build_query(self, raw: str) -> str:
        lower = raw.lower()
        year = time.strftime("%Y")

        if any(k in lower for k in ("trending", "popular", "top", "best", "box office", "currently showing", "what movies are")):
            return f"trending movies {year} box office"

        if any(k in lower for k in ("recommend", "what should i watch", "what to watch")):
            genre = self._extract_genre(lower)
            return f"best movies to watch {year} {genre}".strip()

        if any(k in lower for k in ("review", "rating", "how good is", "is this movie good", "is", "cast", "who stars")):
            title = self._extract_title(raw)
            if title:
                return f"{title} movie review rating Rotten Tomatoes IMDb {year}"
            return f"best movie reviews {year}"

        # Generic fallback — use the raw input + movie context
        return f"{raw} movie {year}"

    def _extract_title(self, text: str) -> str:
        """Pull a movie title from phrases like 'review for Inception' or 'is Dune good'."""
        patterns = [
            r"review (?:for |of )?(.+?)(?:\?|$)",
            r"(?:how good is|is) (.+?) (?:good|bad|worth)",
            r"rating (?:for |of )?(.+?)(?:\?|$)",
            r"(?:cast|who stars in) (.+?)(?:\?|$)",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                return m.group(1).strip().rstrip("?.,!")
        # Last resort: strip common lead-in words
        cleaned = re.sub(
            r"^(movie review|review this movie|how good is|is this movie good|"
            r"movie rating|who stars in|cast of|review for|review of)\s*",
            "", text, flags=re.IGNORECASE,
        ).strip()
        return cleaned if len(cleaned) > 2 else ""

    def _extract_genre(self, text: str) -> str:
        genres = ["action", "comedy", "horror", "drama", "thriller", "romance",
                  "sci-fi", "animation", "documentary", "fantasy", "mystery"]
        for g in genres:
            if g in text:
                return g
        return ""

    # ------------------------------------------------------------------ #
    #  Data fetching                                                       #
    # ------------------------------------------------------------------ #

    def _search_ddg(self, query: str) -> list[dict]:
        try:
            from ddgs import DDGS
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=self.max_results))
        except Exception as e:
            logger.warning("DDG movie search failed: %s", e)
            return []

    def _scrape(self, url: str) -> str:
        try:
            import trafilatura
            downloaded = trafilatura.fetch_url(url)
            if not downloaded:
                return ""
            text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
            return (text or "")[:2000]
        except Exception as e:
            logger.debug("Movie page scrape failed (%s): %s", url, e)
            return ""

    # ------------------------------------------------------------------ #
    #  Context formatter                                                   #
    # ------------------------------------------------------------------ #

    def _build_context(self, raw: str, query: str, results: list[dict], scraped: str) -> str:
        if not results and not scraped:
            return ""

        timestamp = time.strftime("%Y-%m-%d %H:%M")
        lines = [
            f"[LIVE MOVIE DATA — fetched {timestamp}]",
            f"Query: {query}",
            "",
        ]

        for i, r in enumerate(results, 1):
            title = r.get("title", "").strip()
            body = (r.get("body") or r.get("snippet") or "").strip()
            url = r.get("href", "")
            snippet = body[:300].rstrip() + ("…" if len(body) > 300 else "")
            lines.append(f"[{i}] {title}")
            if snippet:
                lines.append(f"    {snippet}")
            if url:
                lines.append(f"    Source: {url}")
            lines.append("")

        if scraped:
            lines.append("--- Full article from top result ---")
            lines.append(scraped[:1500])
            lines.append("")

        lines.append("[END MOVIE DATA]")
        lines.append("")
        lines.append(
            f"Answer the user's question: \"{raw}\"\n"
            "Use the above live data. Be direct and opinionated — recommend or rate clearly. "
            "Cite sources only if explicitly asked."
        )
        return "\n".join(lines)
