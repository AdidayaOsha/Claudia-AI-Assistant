from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any

import httpx
from cachetools import TTLCache

from skills import Skill

logger = logging.getLogger("claudia.research")

_cache: TTLCache = TTLCache(maxsize=128, ttl=300)


class ResearchOrchestrator(Skill):
    name = "research"
    triggers = [
        "search for", "look up", "find information about",
        "find info about", "what's happening", "what is happening",
        "latest news about", "latest update on", "current status of",
        "right now", "as of today", "recently",
    ]
    description = (
        "Real-time web research. Fetches live data from DuckDuckGo, Wikipedia, "
        "and optional Brave Search when the query requires current information."
    )

    def __init__(self, config: dict):
        self.config = config.get("research", {})
        self.enabled = self.config.get("enabled", True)
        self.max_results = self.config.get("max_results", 5)
        self.max_chars_per_source = self.config.get("max_tokens_per_source", 500) * 4
        self.timeout = self.config.get("timeout_seconds", 8)
        self.safe_search = self.config.get("safe_search", True)
        sources = self.config.get("sources", {})
        self.use_ddg = sources.get("duckduckgo", True)
        self.use_brave = sources.get("brave_search", False) and bool(os.environ.get("BRAVE_API_KEY", "").strip())
        self.use_wikipedia = sources.get("wikipedia", True)
        self.use_scrape = sources.get("page_scrape", True)
        self.brave_api_key = os.environ.get("BRAVE_API_KEY", "").strip()

        global _cache
        _cache = TTLCache(
            maxsize=self.config.get("cache_max_size", 128),
            ttl=self.config.get("cache_ttl_seconds", 300),
        )

        sources_active = []
        if self.use_ddg: sources_active.append("DuckDuckGo")
        if self.use_brave: sources_active.append("Brave")
        if self.use_wikipedia: sources_active.append("Wikipedia")
        if self.use_scrape: sources_active.append("page-scrape")
        logger.info(f"Research skill initialised. Sources: {', '.join(sources_active)}")

    def can_handle(self, intent: str) -> bool:
        if not self.enabled:
            return False
        t = intent.lower()
        return any(trigger in t for trigger in self.triggers)

    def execute(self, params: dict) -> str:
        query = params.get("query", params.get("text", params.get("raw_input", "")))
        if not query:
            return "I need a query to search for."

        cache_key = query.lower().strip()
        if cache_key in _cache:
            logger.debug(f"Research cache hit: {cache_key!r}")
            return _cache[cache_key]

        logger.info(f"Research query: {query!r}")
        start = time.monotonic()

        try:
            results = asyncio.run(self._gather(query))
        except Exception as e:
            logger.error(f"Research gather failed: {e}", exc_info=True)
            return ""

        if not results:
            logger.warning("Research returned no results.")
            return ""

        context = self._build_context(query, results)
        elapsed = time.monotonic() - start
        logger.info(f"Research complete in {elapsed:.2f}s — {len(results)} sources")

        _cache[cache_key] = context
        return context

    # ─── Async orchestration ──────────────────────────────────────────────────

    async def _gather(self, query: str) -> list[dict]:
        tasks = []
        if self.use_ddg:
            tasks.append(self._fetch_ddg(query))
        if self.use_brave:
            tasks.append(self._fetch_brave(query))
        if self.use_wikipedia:
            tasks.append(self._fetch_wikipedia(query))

        done, pending = await asyncio.wait(tasks, timeout=self.timeout)
        if pending:
            for task in pending:
                task.cancel()
                name = getattr(task, "_coro_name", "unknown")
                logger.warning(f"Research source timed out ({name})")

        results: list[dict] = []
        for task in done:
            try:
                item = await task
            except Exception as e:
                logger.warning(f"Research source error: {e}")
                continue
            if isinstance(item, list):
                results.extend(item)

        if self.use_scrape and results:
            top_url = next((r.get("url") for r in results if r.get("url")), None)
            if top_url:
                try:
                    scraped = await asyncio.wait_for(
                        self._scrape_page(top_url), timeout=6
                    )
                    if scraped:
                        results.append({
                            "source": "article",
                            "title": "Full article",
                            "url": top_url,
                            "snippet": scraped,
                            "rank": 0.9,
                        })
                except Exception as e:
                    logger.debug(f"Page scrape failed ({top_url}): {e}")

        return self._dedupe_and_rank(results)

    # ─── Source: DuckDuckGo ───────────────────────────────────────────────────

    async def _fetch_ddg(self, query: str) -> list[dict]:
        try:
            from duckduckgo_search import DDGS
            loop = asyncio.get_event_loop()

            def _search():
                with DDGS() as ddgs:
                    return list(ddgs.text(
                        query,
                        max_results=self.max_results,
                        safesearch="on" if self.safe_search else "off",
                    ))

            raw = await loop.run_in_executor(None, _search)
            results = []
            for i, r in enumerate(raw):
                results.append({
                    "source": "duckduckgo",
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                    "rank": 1.0 - (i * 0.1),
                })
            logger.debug(f"DDG: {len(results)} results")
            return results
        except Exception as e:
            logger.warning(f"DDG search error: {e}")
            return []

    # ─── Source: Brave Search ─────────────────────────────────────────────────

    async def _fetch_brave(self, query: str) -> list[dict]:
        if not self.brave_api_key:
            return []
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": self.max_results, "safesearch": "strict" if self.safe_search else "off"},
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "gzip",
                        "X-Subscription-Token": self.brave_api_key,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                results = []
                for i, r in enumerate(data.get("web", {}).get("results", [])):
                    results.append({
                        "source": "brave",
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "snippet": r.get("description", ""),
                        "rank": 1.05 - (i * 0.1),
                    })
                logger.debug(f"Brave: {len(results)} results")
                return results
        except Exception as e:
            logger.warning(f"Brave search error: {e}")
            return []

    # ─── Source: Wikipedia ────────────────────────────────────────────────────

    async def _fetch_wikipedia(self, query: str) -> list[dict]:
        try:
            term = re.sub(
                r"^(what is|who is|what are|explain|tell me about|define)\s+",
                "", query.lower()
            ).strip()

            async with httpx.AsyncClient(timeout=6) as client:
                import urllib.parse
                resp = await client.get(
                    f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(term, safe='')}",
                    headers={"User-Agent": "Claudia-AI-Assistant/1.0"},
                    follow_redirects=True,
                )
                if resp.status_code != 200:
                    return []
                data = resp.json()
                if data.get("type") == "disambiguation":
                    return []
                extract = data.get("extract", "").strip()
                if not extract:
                    return []
                return [{
                    "source": "wikipedia",
                    "title": data.get("title", term),
                    "url": data.get("content_urls", {}).get("desktop", {}).get("page", ""),
                    "snippet": extract[:self.max_chars_per_source],
                    "rank": 0.85,
                }]
        except Exception as e:
            logger.debug(f"Wikipedia error ({query!r}): {e}")
            return []

    # ─── Source: Page scrape ──────────────────────────────────────────────────

    async def _scrape_page(self, url: str) -> str:
        try:
            import trafilatura

            async with httpx.AsyncClient(
                timeout=6,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; ClaudiaBot/1.0)"},
            ) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return ""
                html = resp.text

            loop = asyncio.get_event_loop()
            text = await loop.run_in_executor(
                None,
                lambda: trafilatura.extract(
                    html,
                    include_comments=False,
                    include_tables=False,
                    no_fallback=False,
                ),
            )

            if text:
                return text[:self.max_chars_per_source].strip()

            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
            paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p") if len(p.get_text()) > 60]
            return " ".join(paragraphs)[:self.max_chars_per_source]

        except Exception as e:
            logger.debug(f"Page scrape error ({url}): {e}")
            return ""

    # ─── Post-processing ──────────────────────────────────────────────────────

    def _dedupe_and_rank(self, results: list[dict]) -> list[dict]:
        seen_urls: set[str] = set()
        seen_snippets: set[str] = set()
        deduped = []

        for r in sorted(results, key=lambda x: x.get("rank", 0), reverse=True):
            url = r.get("url", "")
            snippet = r.get("snippet", "")
            fingerprint = re.sub(r"\s+", " ", snippet[:80].lower().strip())

            if url and url in seen_urls:
                continue
            if fingerprint and fingerprint in seen_snippets:
                continue

            seen_urls.add(url)
            if fingerprint:
                seen_snippets.add(fingerprint)
            deduped.append(r)

        return deduped[:self.max_results + 2]

    def _build_context(self, query: str, results: list[dict]) -> str:
        lines = [f'Web search results for: "{query}"', ""]
        total_chars = 0

        for i, r in enumerate(results[:self.max_results], 1):
            source = r.get("source", "web").upper()
            title = r.get("title", "No title")
            url = r.get("url", "")
            snippet = r.get("snippet", "").strip()

            if len(snippet) > self.max_chars_per_source:
                snippet = snippet[:self.max_chars_per_source] + "\u2026"

            block = f"[{i}] {source}: {title}\n"
            if url:
                block += f"URL: {url}\n"
            block += f"{snippet}\n"

            total_chars += len(block)
            if total_chars > self.max_chars_per_source * self.max_results:
                lines.append(f"[{i}+] (additional results truncated to stay within context budget)")
                break

            lines.append(block)

        return "\n".join(lines)
