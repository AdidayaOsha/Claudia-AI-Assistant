# Claude Code Prompt — Implement Internet Research for Claudia

> **Paste this entire prompt into Claude Code at `C:\Projects\Claudia\`**
> **Prerequisite:** `CLAUDE.md` and `docs/internet_research.md` are both present in the project.

---

You are implementing the internet research module for **Claudia**, as specified in `docs/internet_research.md`. Read that file in full before writing any code. Also read `CLAUDE.md` for the existing project conventions, file structure, and coding patterns — match them exactly.

Implement the feature in this exact order. After each step, confirm what was done and what comes next before proceeding.

---

## STEP 1 — Install dependencies

Run in the project root:

```
pip install httpx>=0.27.0 beautifulsoup4>=4.12.3 lxml>=5.2.1 trafilatura>=1.9.0 cachetools>=5.3.3
```

Then append these lines to `requirements.txt` (preserve existing entries, add after the last line):

```
httpx>=0.27.0
beautifulsoup4>=4.12.3
lxml>=5.2.1
trafilatura>=1.9.0
cachetools>=5.3.3
```

Confirm all five packages install without errors before continuing.

---

## STEP 2 — Update `config.yaml`

Append this block to the end of `config.yaml`. Do not touch any existing keys:

```yaml
research:
  enabled: true
  max_results: 5
  max_tokens_per_source: 500
  cache_ttl_seconds: 300
  cache_max_size: 128
  timeout_seconds: 8
  sources:
    duckduckgo: true
    brave_search: false
    wikipedia: true
    page_scrape: true
  safe_search: true
```

---

## STEP 3 — Update `.env.example`

Add this line to `.env.example` after the existing `NEWSAPI_KEY` line:

```
BRAVE_API_KEY=          # Optional — Brave Search API (https://api.search.brave.com). Free tier: 2000 queries/month.
```

Do NOT touch `.env` — the user's live secrets are in there.

---

## STEP 4 — Create `skills/research.py`

Create the file `claudia/skills/research.py` with the full implementation from `docs/internet_research.md` (the `FULL IMPLEMENTATION` section). Copy the implementation exactly as written.

Verify the following after creating the file:
- The class is named `ResearchOrchestrator`
- It inherits from `Skill` (imported from `skills`)
- `name = "research"` is set as a class attribute
- `execute()` returns a string (the context block), not a spoken response
- The module-level `_cache` TTLCache is present
- All four source methods are present: `_fetch_ddg`, `_fetch_brave`, `_fetch_wikipedia`, `_scrape_page`
- `_dedupe_and_rank` and `_build_context` are present

---

## STEP 5 — Update `core/intent_router.py`

Read the existing `core/intent_router.py` first. Then make two additions:

**Addition A** — Add these imports and the pattern list near the top of the file, after existing imports:

```python
import re

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
    """Return True if the query requires live web data."""
    t = text.lower()
    return any(re.search(p, t) for p in RESEARCH_PATTERNS)
```

**Addition B** — In the main routing function (wherever skills are matched via `can_handle()` or trigger matching), add this block **after** all existing skill checks return no match, and **before** the LLM fallback:

```python
# Research skill — last resort before LLM, only for live-data queries
if _is_research_query(user_input) and self.config.get("research", {}).get("enabled", False):
    research_skill = self.skill_registry.get("research")
    if research_skill:
        return research_skill
```

Adapt the variable names (`self.config`, `self.skill_registry`) to match whatever names the existing router actually uses — read the file first, do not assume.

Do not modify any existing trigger matching logic. Research is additive — it only runs when nothing else matched.

---

## STEP 6 — Update `core/brain.py`

Read the existing `core/brain.py` first. Then modify `think()` to accept and use the optional `research_context` parameter.

Find the existing `think()` signature:

```python
def think(self, user_input: str, context: list[dict]) -> str:
```

Replace it with:

```python
def think(self, user_input: str, context: list[dict], research_context: str | None = None) -> str:
```

Then, inside `think()`, immediately after the line where `messages` is assembled from the context window (before any API call), insert:

```python
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
    messages = self._sanitize_messages(messages, user_input)
```

> `_sanitize_messages()` already exists in `brain.py` — do not rewrite it. Just call it here after the injection.

Also update `think_stream()` with the same signature change and the same injection block if it exists, keeping all existing streaming logic intact.

---

## STEP 7 — Update `core/assistant.py`

Read the existing `core/assistant.py` first. Find the section where a matched skill's `execute()` is called and its result is passed to the speaker. It will look roughly like:

```python
result = skill.execute(params)
self.speaker.speak(result)
```

Replace that block with:

```python
result = skill.execute({"query": user_input, "text": user_input})

if skill.name == "research" and result:
    # Research returns a context block, not a spoken string — synthesise via brain
    response = self.brain.think(
        user_input=user_input,
        context=self.memory.get_context_window(),
        research_context=result,
    )
else:
    response = result

self.speaker.speak(response)
```

Adapt variable names to match what the existing code uses. Do not touch any other logic in `assistant.py`.

---

## STEP 8 — Update `main.py` boot sequence

Read `main.py`. Find the section that logs skill loading (something like `[CLAUDIA] Skill modules loaded: N skills [OK]`). Immediately after that block, add:

```python
research_cfg = config.get("research", {})
if research_cfg.get("enabled"):
    brave_key = os.environ.get("BRAVE_API_KEY", "").strip()
    brave_status = "[OK]" if (research_cfg.get("sources", {}).get("brave_search") and brave_key) else "[no key]"
    logger.info(f"[CLAUDIA] Research: DDG [OK] | Wikipedia [OK] | Brave {brave_status} | Cache TTL={research_cfg.get('cache_ttl_seconds', 300)}s [OK]")
else:
    logger.info("[CLAUDIA] Research: disabled (research.enabled: false in config.yaml)")
```

---

## STEP 9 — Update `CLAUDE.md`

Add this section to `CLAUDE.md` immediately after the skill table (the `| Skill | Example Triggers | API / Method |` block):

```markdown
### 🌐 Internet Research
See `docs/internet_research.md` for full spec.
Skill file: `skills/research.py` — class `ResearchOrchestrator`.
Triggered **only** after all 10 primary skills fail to match, and only if the query matches `RESEARCH_PATTERNS` in `intent_router.py`.
Returns a context string (not a spoken response) — `assistant.py` passes it to `brain.think(research_context=...)` for synthesis.
New dependencies: `httpx`, `beautifulsoup4`, `lxml`, `trafilatura`, `cachetools`.
```

---

## STEP 10 — Smoke test

Run Claudia normally (`python main.py`) and verify the boot log contains:

```
[CLAUDIA] Research: DDG [OK] | Wikipedia [OK] | Brave [no key] | Cache TTL=300s [OK]
```

Then test via the dashboard text input at `http://127.0.0.1:5000` with these three queries in order:

1. `"What's happening in Jakarta right now?"` — should trigger research, fetch DDG results, Claudia speaks a synthesised summary
2. `"What's happening in Jakarta right now?"` (repeat immediately) — should return in under 100ms (cache hit), log shows "Research cache hit"
3. `"What time is it?"` — should NOT trigger research, routes to `time_date` skill as before

If any step fails, read the error in `logs/claudia_YYYYMMDD.log` and fix before continuing.

---

## CONVENTIONS TO FOLLOW (from `CLAUDE.md`)

- All API keys via `os.environ.get("KEY", "").strip()` — strip whitespace, check for empty string before use
- Log with `logger.info` / `logger.warning` / `logger.error(exc_info=True)` — never `print()`
- Boot log uses `[OK]` / `[!!]` ASCII — no unicode symbols (Windows console)
- Never block the main thread — research runs in `asyncio.run()` which is safe from a non-async caller
- Every exception caught with a specific message — no bare `except:` clauses
- Do not modify any file not listed in these steps

---

*— Prompt written for Claude Code. Run from `C:\Projects\Claudia\` with both `CLAUDE.md` and `docs/internet_research.md` present.*
