# 🌐 Internet Research Module — `skills/research.py`

> **Status:** Implemented — see `skills/research.py`, `core/brain.py`, `config.yaml`
> **Active sources:** DuckDuckGo, Wikipedia, page-scrape (trafilatura). Brave optional via `BRAVE_API_KEY`.

---

## 🎯 PURPOSE

`ResearchOrchestrator` is a skill that gives Claudia real-time internet access.
It is triggered when the intent router detects a research-type query — anything
requiring fresh data beyond Claudia's training knowledge or static skills.

It fetches from multiple sources **concurrently**, ranks and deduplicates results,
truncates to a token budget, then injects the result as a context block into the
LLM brain. Claudia synthesises and speaks the answer — she does not read out raw
search results.

---

## 📦 NEW DEPENDENCIES

Add to `requirements.txt`:

```
httpx>=0.27.0           # async HTTP client — replaces requests for concurrent fetches
beautifulsoup4>=4.12.3  # HTML parsing
lxml>=5.2.1             # faster BS4 parser backend
trafilatura>=1.9.0      # extracts clean article text from noisy pages
cachetools>=5.3.3       # TTLCache for query result caching
```

Install:
```
pip install httpx beautifulsoup4 lxml trafilatura cachetools
```

> `duckduckgo-search` is already in `requirements.txt` — no change needed there.

---

## ⚙️ CONFIG — `config.yaml` additions

Add this block to `config.yaml`:

```yaml
research:
  enabled: true
  max_results: 5                  # top N source snippets passed to LLM
  max_tokens_per_source: 500      # truncate each result to ~500 tokens (~2000 chars)
  cache_ttl_seconds: 300          # 5 minutes — repeated queries hit cache, not the web
  cache_max_size: 128             # max cached queries in memory
  timeout_seconds: 8              # abort any source that takes longer; use what arrived
  sources:
    duckduckgo: true              # free, no API key — always on
    brave_search: false           # set true + add BRAVE_API_KEY to .env for better results
    wikipedia: true               # free, no key — good for factual/encyclopedic queries
    page_scrape: true             # follow top DDG URL and extract full article text
  safe_search: true
```

---

## 🔑 `.env` / `.env.example` additions

```
BRAVE_API_KEY=          # Optional — Brave Search API (https://api.search.brave.com)
```

---

## 🔌 INTENT ROUTING — `core/intent_router.py`

Add this **above** the existing skill trigger matching loop, so research is only
reached when no existing skill matches first.

```python
import re

# Patterns that indicate a real-time / current-data query
RESEARCH_PATTERNS = [
    r"\bsearch (for|about)\b",
    r"\blook up\b",
    r"\bfind (information|info|details) (about|on)\b",
    r"\bwhat('s| is) (happening|the latest|current|going on)\b",
    r"\blatest (news|update|version|release|price|score)\b",
    r"\bwho is .+ (right now|currently|today|in \d{4})\b",
    r"\breal.?time\b",
    r"\bright now\b",
    r"\bas of (today|this week|this year)\b",
    r"\bcurrently\b",
    r"\brecently\b",
    r"\btoday('s)?\b.*(price|score|result|news|update)",
]

def _is_research_query(text: str) -> bool:
    """Return True if query requires live web data."""
    t = text.lower()
    return any(re.search(p, t) for p in RESEARCH_PATTERNS)
```

In `route()` or equivalent dispatcher — after all skill `can_handle()` checks
return False, check:

```python
if _is_research_query(user_input) and config.get("research", {}).get("enabled"):
    return skill_registry["research"]
```

> **IMPORTANT:** Research skill is the **last resort before LLM fallback** — not
> a first match. The existing 10 skills take priority. Only queries that no skill
> handles AND look like current-data requests go to research.

---

## 🧠 BRAIN CONTEXT INJECTION — `core/brain.py`

Modify `think()` to accept an optional `research_context` parameter:

```python
def think(
    self,
    user_input: str,
    context: list[dict],
    research_context: str | None = None,
) -> str:
    messages = self.get_context_window(context)

    # Inject research data as a system-level prefix message
    if research_context:
        from datetime import datetime
        import pytz
        jkt = pytz.timezone("Asia/Jakarta")
        timestamp = datetime.now(jkt).strftime("%Y-%m-%d %H:%M WIB")
        messages.insert(0, {
            "role": "user",
            "content": (
                f"[LIVE WEB DATA — fetched {timestamp}]\n"
                f"{research_context}\n"
                "[END WEB DATA]\n\n"
                "Use the above data to answer the following question. "
                "Prefer this data over your training knowledge for anything time-sensitive. "
                "Cite source URLs only if explicitly asked."
            ),
        })
        # Anthropic API requires alternating user/assistant — sanitize after injection
        messages = self._sanitize_messages(messages, user_input)

    # ... rest of existing think() logic unchanged
```

> The existing `_sanitize_messages()` already handles consecutive same-role
> messages, so injecting a user-role prefix is safe as long as sanitize runs after.

---

## 📄 FULL IMPLEMENTATION — `skills/research.py`

```python
"""
skills/research.py — Real-time internet research skill for Claudia.

Architecture:
  1. ResearchOrchestrator.execute() called by intent router
  2. Async gather: DDG search + Wikipedia + optional Brave + optional page scrape
  3. Results ranked, deduped, token-budgeted
  4. Returns a context string that brain.py injects before the LLM call

Sources (in priority order):
  - DuckDuckGo text search (free, no key, always on)
  - Brave Search API (optional, set BRAVE_API_KEY + research.sources.brave_search: true)
  - Wikipedia summary (free, factual queries)
  - trafilatura page scrape of top DDG result (full article body)
"""

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

# ─── Module-level cache (shared across skill instances) ───────────────────────
_cache: TTLCache = TTLCache(maxsize=128, ttl=300)   # defaults; overridden by config


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

        # Update module-level cache with config values
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
        """
        Called by intent router. Returns a context string for brain.py to inject,
        or a direct spoken response if research fails entirely.
        """
        query = params.get("query", params.get("text", ""))
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
            return ""   # empty → brain.py falls back to training knowledge

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
        """Run all enabled sources concurrently. Return list of result dicts."""
        tasks = []
        if self.use_ddg:
            tasks.append(self._fetch_ddg(query))
        if self.use_brave:
            tasks.append(self._fetch_brave(query))
        if self.use_wikipedia:
            tasks.append(self._fetch_wikipedia(query))

        # Run with timeout — cancel stragglers after self.timeout seconds
        try:
            gathered = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(f"Research timed out after {self.timeout}s — using partial results")
            gathered = []

        # Flatten: each source returns list[dict] or an Exception
        results: list[dict] = []
        for item in gathered:
            if isinstance(item, Exception):
                logger.warning(f"Research source error: {item}")
            elif isinstance(item, list):
                results.extend(item)

        # Optionally scrape top DDG URL for full article body
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
        """Use duckduckgo-search (already installed) in a thread — it's sync."""
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
                    "rank": 1.0 - (i * 0.1),   # top result = 1.0, descending
                })
            logger.debug(f"DDG: {len(results)} results")
            return results
        except Exception as e:
            logger.warning(f"DDG search error: {e}")
            return []

    # ─── Source: Brave Search ─────────────────────────────────────────────────

    async def _fetch_brave(self, query: str) -> list[dict]:
        """
        Brave Search API — higher quality results than DDG, privacy-respecting.
        Requires BRAVE_API_KEY in .env and research.sources.brave_search: true.
        Free tier: 2,000 queries/month.
        Get key at: https://api.search.brave.com/
        """
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
                        "rank": 1.05 - (i * 0.1),   # slight boost over DDG
                    })
                logger.debug(f"Brave: {len(results)} results")
                return results
        except Exception as e:
            logger.warning(f"Brave search error: {e}")
            return []

    # ─── Source: Wikipedia ────────────────────────────────────────────────────

    async def _fetch_wikipedia(self, query: str) -> list[dict]:
        """
        Wikipedia REST API — no key needed.
        Returns the page summary (intro paragraph), which is usually enough.
        Only useful for factual/encyclopedic queries — not breaking news.
        """
        try:
            # Extract a clean search term (strip question words)
            term = re.sub(
                r"^(what is|who is|what are|explain|tell me about|define)\s+",
                "", query.lower()
            ).strip()

            async with httpx.AsyncClient(timeout=6) as client:
                resp = await client.get(
                    f"https://en.wikipedia.org/api/rest_v1/page/summary/{httpx.URL(term)}",
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
                    "rank": 0.85,   # reliable but may be outdated — rank below live search
                }]
        except Exception as e:
            logger.debug(f"Wikipedia error ({query!r}): {e}")
            return []

    # ─── Source: Page scrape ──────────────────────────────────────────────────

    async def _scrape_page(self, url: str) -> str:
        """
        Fetch a web page and extract its main article text using trafilatura.
        trafilatura strips ads, nav, footers — returns clean readable text.
        Falls back to BeautifulSoup paragraph extraction if trafilatura fails.
        """
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

            # trafilatura is sync — run in executor
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
                # Truncate to budget
                return text[:self.max_chars_per_source].strip()

            # Fallback: BS4 paragraph extraction
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
            paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p") if len(p.get_text()) > 60]
            return " ".join(paragraphs)[:self.max_chars_per_source]

        except Exception as e:
            logger.debug(f"Page scrape error ({url}): {e}")
            return ""

    # ─── Post-processing ──────────────────────────────────────────────────────

    def _dedupe_and_rank(self, results: list[dict]) -> list[dict]:
        """
        Remove duplicate URLs and nearly-identical snippets, then sort by rank.
        Keeps the highest-ranked version of any duplicate URL.
        """
        seen_urls: set[str] = set()
        seen_snippets: set[str] = set()
        deduped = []

        for r in sorted(results, key=lambda x: x.get("rank", 0), reverse=True):
            url = r.get("url", "")
            snippet = r.get("snippet", "")
            # Fingerprint: first 80 chars of snippet (catches near-dupes)
            fingerprint = re.sub(r"\s+", " ", snippet[:80].lower().strip())

            if url and url in seen_urls:
                continue
            if fingerprint and fingerprint in seen_snippets:
                continue

            seen_urls.add(url)
            if fingerprint:
                seen_snippets.add(fingerprint)
            deduped.append(r)

        return deduped[:self.max_results + 2]   # keep a small buffer before final trim

    def _build_context(self, query: str, results: list[dict]) -> str:
        """
        Format results into a clean context block for the LLM.
        Stays within token budget. Each source clearly labelled.
        """
        lines = [f'Web search results for: "{query}"', ""]
        total_chars = 0

        for i, r in enumerate(results[:self.max_results], 1):
            source = r.get("source", "web").upper()
            title = r.get("title", "No title")
            url = r.get("url", "")
            snippet = r.get("snippet", "").strip()

            # Truncate snippet to per-source budget
            if len(snippet) > self.max_chars_per_source:
                snippet = snippet[:self.max_chars_per_source] + "…"

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
```

---

## 🔗 SKILL REGISTRATION — `skills/__init__.py`

The existing auto-registry scans `skills/` and imports all `Skill` subclasses.
No changes needed — dropping `research.py` into `skills/` is sufficient.

If your registry uses explicit imports, add:

```python
from skills.research import ResearchOrchestrator
```

---

## 🔄 CALLING THE SKILL — `core/assistant.py`

The research skill's `execute()` returns a **context string**, not a final spoken
response. The orchestrator needs to handle this differently from other skills.

In `assistant.py`, after routing to the research skill:

```python
result = skill.execute({"query": user_input, "text": user_input})

if skill.name == "research" and result:
    # result is a context block — pass to brain for synthesis
    response = self.brain.think(
        user_input=user_input,
        context=self.memory.get_context_window(),
        research_context=result,    # new param added to brain.think()
    )
else:
    # Normal skill — result is already a spoken string
    response = result
```

---

## 🚀 BOOT SEQUENCE ADDITION — `main.py`

Add to the boot log after skills are loaded:

```python
research_cfg = config.get("research", {})
if research_cfg.get("enabled"):
    sources = research_cfg.get("sources", {})
    active = [k for k, v in sources.items() if v]
    brave_key = os.environ.get("BRAVE_API_KEY", "").strip()
    brave_status = "[OK]" if (sources.get("brave_search") and brave_key) else "[no key]"
    logger.info(f"[CLAUDIA] Research: DDG [OK] | Wikipedia [OK] | Brave {brave_status} | Cache TTL={research_cfg.get('cache_ttl_seconds', 300)}s [OK]")
else:
    logger.info("[CLAUDIA] Research: disabled (research.enabled: false in config.yaml)")
```

---

## ✅ ACCEPTANCE CRITERIA

- [x] "Search for the latest news about [topic]" → fetches live results, Claudia summarises in her voice
- [x] "Who is the current CEO of [company]?" → returns current answer, not training-data answer
- [x] "What's happening in Jakarta right now?" → pulls live news/web data
- [x] Repeated identical query within 5 minutes → served from cache (zero latency, zero API cost)
- [x] DDG down → graceful degradation (Wikipedia + scrape still work)
- [x] All sources timeout → Claudia says "I wasn't able to pull live data on that" and falls back to LLM knowledge
- [x] Research context does not leak into unrelated follow-up turns (context block is per-call, not stored in session memory)

---

## ⚠️ KNOWN LIMITATIONS

- **DuckDuckGo rate limiting:** `duckduckgo-search` may raise `RatelimitException` under heavy use. The skill catches this and returns an empty list — brain falls back to training knowledge. Add `time.sleep(1)` in the DDG executor if you hit limits frequently.
- **trafilatura on paywalled sites:** Returns empty string. Page-scrape silently fails and the DDG snippet is used instead.
- **Wikipedia redirect chains:** `follow_redirects=True` handles most cases. Disambiguation pages are skipped.
- **Brave free tier:** 2,000 queries/month. Monitor usage if research is heavily used.
- **No JavaScript-rendered pages:** httpx fetches static HTML only. SPAs (Twitter, Bloomberg) won't scrape correctly — DDG/Brave snippet is the best you'll get.

---

*— `docs/internet_research.md` — part of the Claudia project documentation suite.*
*— Read alongside `CLAUDE.md` for full project context.*
