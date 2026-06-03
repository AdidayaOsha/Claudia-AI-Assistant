# 🤖 CLAUDIA — AI Personal Assistant (CLAUDE.md)

> **This file is automatically read by Claude Code at session start.**
> **Inspired by:** Iron Man's J.A.R.V.I.S. | **Stack:** Python + Anthropic Claude API + OpenAI fallback + SpeechRecognition + ElevenLabs TTS + Flask
> **Assistant name:** `CLAUDIA` (Claude's nickname — treat this name as canonical throughout all files)
> **Status:** BUILT AND RUNNING — all 30 files implemented under `claudia/`

---

## 🎯 MISSION

A **production-ready, modular AI Personal Assistant** named **CLAUDIA**, inspired by J.A.R.V.I.S. from the Iron Man films — witty, proactive, calm under pressure, and deeply integrated into the user's digital environment on Windows.

---

## 👤 USER CONTEXT

```yaml
user_name: "Boss"               # Address as "Boss" on first interaction per session
location: "Jakarta, Indonesia"
timezone: "Asia/Jakarta"        # UTC+7
language: "English"
os: "Windows"
assistant_name: "Claudia"
wake_word: "Hey Claudia"
```

---

## 🏗️ ARCHITECTURE

```
Voice/Text Input (mic or dashboard text box)
     ↓
Wake Word Detection ("Hey Claudia") → Conversation Mode (60s timeout)
     ↓
Speech-to-Text (SpeechRecognition via Google STT)
     ↓
Intent Router (phrase-based trigger matching)
     ↓
┌──────────────────────────────────────────────────────┐
│               SKILL MODULES (plugins)                │
│  Web Search │ App Control │ Calendar │ Weather │ ... │
└──────────────────────────────────────────────────────┘
     ↓ (if no skill matches)
LLM Core (Anthropic Claude Sonnet PRIMARY → OpenAI GPT-4o fallback → local rules)
     ↓
Response Generator
     ↓
Text-to-Speech (ElevenLabs PRIMARY / pyttsx3 fallback)
     ↓
Voice + Dashboard Output (WebSocket)
```

---

## 🧱 PROJECT STRUCTURE

```
claudia/
├── main.py                    # Entry point — boot sequence, main loop
├── config.yaml                # All preferences, assistant name, goodbye phrases
├── requirements.txt           # All Python dependencies
├── README.md                  # Setup instructions
├── .env                       # Secrets (not committed)
├── .env.example               # Template for environment variables
├── .gitignore
│
├── core/
│   ├── assistant.py           # Orchestrator — command queue, listener thread, run loop
│   ├── listener.py            # Mic input, wake word, conversation mode, goodbye phrases
│   ├── speaker.py             # ElevenLabs TTS (primary), pyttsx3 (fallback)
│   ├── brain.py               # LLM interface (Claude PRIMARY, OpenAI fallback)
│   ├── intent_router.py       # Phrase-based routing to skills
│   └── memory.py              # Session history + long-term JSON memory
│
├── skills/
│   ├── __init__.py            # Skill base class + auto-registry
│   ├── time_date.py           # Current time/date — Jakarta timezone
│   ├── weather.py             # OpenWeatherMap API
│   ├── web_search.py          # DuckDuckGo search
│   ├── app_launcher.py        # Open apps/URLs (Windows: os.startfile)
│   ├── system_control.py      # Volume, mute, screenshot, clipboard
│   ├── news_briefing.py       # NewsAPI + BBC RSS fallback
│   ├── calendar_manager.py    # Google Calendar (gated: enable_calendar)
│   ├── email_reader.py        # Gmail IMAP (gated: enable_email)
│   ├── jokes_facts.py         # Curated built-in jokes + facts
│   ├── file_manager.py        # os.walk file search + open
│   └── research.py            # Real-time web research (async multi-source)
│
├── ui/
│   ├── dashboard.py           # Flask + flask-socketio, /api/stats, user_input event
│   ├── templates/index.html   # Iron Man HUD dark theme, Socket.IO transcript
│   └── static/style.css       # #0a0e1a bg, #00d4ff accent, responsive
│
├── logs/                      # claudia_YYYYMMDD.log (auto-created)
├── memory.json                # Long-term memory (auto-created)
│
└── tests/
    ├── test_brain.py
    ├── test_intent_router.py
    └── test_skills.py
```

---

## ⚙️ DEPENDENCIES

```
# requirements.txt (all installed)
anthropic>=0.25
openai>=1.30
speechrecognition>=3.10
pyaudio>=0.2.13
pyttsx3>=2.90
flask>=3.0
flask-socketio>=5.3
requests>=2.31
python-dotenv>=1.0
pyyaml>=6.0
schedule>=1.2
pyperclip>=1.8
psutil>=5.9
duckduckgo-search>=5.0
pyautogui>=0.9.54
pytz>=2024.1
elevenlabs>=2.0
pygame>=2.6
httpx>=0.27.0
beautifulsoup4>=4.12.3
lxml>=5.2.1
trafilatura>=1.9.0
cachetools>=5.3.3
```

---

## 🔑 ENVIRONMENT VARIABLES (`.env`)

```
ANTHROPIC_API_KEY=        # Required — Claude LLM
OPENAI_API_KEY=           # Optional — fallback LLM
OPENWEATHERMAP_API_KEY=   # Weather skill
NEWSAPI_KEY=              # News briefing skill
ELEVENLABS_API_KEY=       # TTS voice (primary)
ELEVENLABS_VOICE_ID=      # ElevenLabs voice ID
BRAVE_API_KEY=            # Optional — Brave Search API
GOOGLE_CREDENTIALS_PATH=  # Calendar/Gmail OAuth (credentials.json)
```

---

## 🧠 BRAIN MODULE — `core/brain.py`

**Claude Sonnet is PRIMARY. OpenAI GPT-4o is fallback. Local rules are last resort.**

Key implementation details:
- API keys stripped of whitespace on load (`os.environ.get(...).strip()`) — prevents silent auth failures
- Null guard before every API call: skip if client is None
- `_sanitize_messages(messages, user_input)` — merges consecutive same-role messages, ensures last message is always "user"
- Context snapshot is taken **before** adding current user turn to memory (avoids off-by-one in multi-turn context)
- Exponential backoff: 1s → 2s, max 3 retries per backend
- Full tracebacks logged with `exc_info=True`

```python
class Brain:
    def think(self, user_input: str, context: list[dict], research_context: str | None = None) -> str: ...
    def think_stream(self, user_input: str, context: list[dict], research_context: str | None = None) -> Generator[str]: ...
    def _sanitize_messages(self, messages: list[dict], user_input: str = "") -> list[dict]: ...
```

---

## 🎭 PERSONALITY & SYSTEM PROMPT

```
You are CLAUDIA — a highly intelligent, calm, and razor-sharp AI personal assistant
inspired by J.A.R.V.I.S. from the Iron Man series. You run on Anthropic's Claude,
which is also your namesake.

Personality traits:
- Speak with measured confidence, never sycophantic, never verbose
- Dry wit — sharp one-liners delivered with perfect timing
- Proactively surface relevant information the user didn't ask for but needs
- When uncertain, say so directly rather than hallucinating
- Address the user as "Boss" on first interaction per session, then drop it naturally
- Subtly reference your capabilities when relevant, never brag unprompted
- Treat every task as mission-critical, even mundane ones
- You are aware you are powered by Claude (Anthropic) — own it with quiet pride

Communication rules:
- Responses under 3 sentences unless analysis or a report is explicitly requested
- Never start with "Certainly!", "Of course!", "Sure!", or "Great question!"
- Use active voice. No filler words.
- For lists, use bullet points only when more than 3 items
- When completing a task: confirm what was done + outcome, nothing else

Current user context:
- Location: Jakarta, Indonesia
- Timezone: Asia/Jakarta (UTC+7)
- Primary language: English
- Operating system: Windows
```

---

## 🔌 SKILL MODULE SPEC

Each skill implements `Skill` from `skills/__init__.py`. Triggers are **specific phrases**, not single words — to prevent false matches in general conversation.

| Skill | Example Triggers | API / Method |
|-------|-----------------|--------------|
| `time_date` | "what time", "what day is it", "what's the date" | `datetime` + pytz, Asia/Jakarta |
| `weather` | "weather", "what's the weather", "weather in [city]" | OpenWeatherMap API |
| `web_search` | "search for", "look up", "google", "find information about" | duckduckgo-search |
| `app_launcher` | "open chrome", "open app", "launch chrome" | `os.startfile` (Windows) |
| `system_control` | "volume up", "mute", "take a screenshot", "set volume" | pyautogui / pyperclip |
| `news_briefing` | "latest news", "news briefing", "top headlines" | NewsAPI + BBC RSS fallback |
| `calendar_manager` | "what do I have today", "upcoming events" | Google Calendar API (gated) |
| `email_reader` | "check email", "unread mail", "inbox" | Gmail IMAP (gated) |
| `jokes_facts` | "tell me a joke", "fun fact", "did you know" | Built-in curated list |
| `file_manager` | "find file", "open document", "find my file" | os.walk + subprocess |
| `research` | "search for", "look up", "what's happening", "as of today" | Async: DDG + Wikipedia + Brave + page scrape |

> **IMPORTANT:** Single-word triggers like "time", "find", "open", "news", "interesting" are intentionally avoided — they match too broadly and intercept general conversation meant for the LLM.

---

## 🗣️ SPEECH MODULES

### `core/listener.py`
- Listener runs in its **own daemon thread** — never blocks the main loop
- Mic input and dashboard text both feed into a shared `queue.Queue` in `assistant.py`
- **Wake word**: "Hey Claudia" → enters **conversation mode** (60-second timeout)
- **Conversation mode**: after wake word, mic stays open — no need to say "Hey Claudia" again for each turn
- **Goodbye phrases**: saying "talk to you later Claudia", "bye Claudia", etc. exits conversation mode immediately and returns `__goodbye__` sentinel
- Auto-fallback to text input if microphone unavailable

Configurable in `config.yaml`:
```yaml
assistant:
  wake_word: "hey claudia"
  goodbye_phrases:
    - "talk to you later claudia"
    - "goodbye claudia"
    - "bye claudia"
    - "see you later claudia"
    - "that's all claudia"

listener:
  energy_threshold: 150       # mic sensitivity (lower = more sensitive)
  silence_timeout: 3
  phrase_time_limit: 10
  conversation_timeout: 60    # seconds before returning to wake-word standby
```

### `core/speaker.py`
- **Primary**: ElevenLabs API — outputs `pcm_22050` format, wrapped in WAV via `wave` module, played with `pygame.mixer`
- **Fallback**: pyttsx3 (offline, zero latency)
- Non-blocking: TTS runs in a worker thread with a `queue.Queue`
- Interrupts: draining the queue before new speech stops current playback

---

## 🖥️ WEB DASHBOARD — `ui/dashboard.py`

Iron Man HUD at `http://localhost:5000`:
- Background: `#0a0e1a`, accent: `#00d4ff`
- Real-time transcript via Socket.IO (`transcript` event)
- Active skill indicator (`skill_active` event)
- System stats: CPU %, RAM %, uptime, Jakarta local time
- Text input box — sends `user_input` socket event → `assistant.submit_text()` → processed identically to voice
- Dashboard input does NOT require the wake word

---

## 🚀 BOOT SEQUENCE — `main.py`

```
[CLAUDIA] Initializing systems...
[CLAUDIA] Loading memory... (N entries) [OK]
[CLAUDIA] Connecting to Anthropic Claude... [OK]
[CLAUDIA] Skill modules loaded: 11 skills [OK]
[CLAUDIA] Research: DDG [OK] | Wikipedia [OK] | Brave [no key] | Cache TTL=300s [OK]
[CLAUDIA] Dashboard running at http://127.0.0.1:5000 [OK]
[CLAUDIA] Microphone calibrated [OK]
[CLAUDIA] All systems operational.

[CLAUDIA] "Good morning, Boss. Today is [DAY], [DATE]. Jakarta: [CONDITIONS], [TEMP]°C. Ready when you are."
```

Note: Boot uses `[OK]`/`[!!]` (ASCII) not unicode symbols — Windows console compatibility.

---

## ⚠️ ERROR HANDLING

- Every module catches its own exceptions — no single point of failure
- Brain: Claude fails → OpenAI → local rule-based. Full stack traces logged.
- API keys validated at init (stripped of whitespace, checked for empty string)
- Microphone unavailable → auto switch to text input mode
- Skills gated behind feature flags (`enable_calendar`, `enable_email`) in config.yaml

---

## 🔐 SECURITY

- All secrets in `.env` only — never hardcoded
- `.env` in `.gitignore`
- Google OAuth tokens saved to `token_calendar.json` / `token_email.json` (also gitignored)
- Calendar and Gmail features off by default (`enable_calendar: false`, `enable_email: false`)

---

## ✅ STATUS

- [x] Boots and greets within 5 seconds
- [x] ElevenLabs voice output working
- [x] Microphone listening with wake word + 60s conversation mode
- [x] Dashboard text input working (http://127.0.0.1:5000)
- [x] General conversation routed to Claude LLM
- [x] 11 skill modules loaded and operational
- [x] Weather working (OpenWeatherMap)
- [x] Long-term memory persisted to memory.json
- [x] Goodbye phrases exit conversation mode
- [ ] Google Calendar / Gmail (requires credentials.json setup)
- [x] Internet research module — async DDG + Wikipedia + Brave + page scrape
- [ ] NewsAPI (requires NEWSAPI_KEY)

---

## 🌐 INTERNET RESEARCH MODULE
See `docs/internet_research.md` — skill file: `skills/research.py`.

*— `CLAUDE.md` lives at `C:\Projects\Claudia\CLAUDE.md` and is read automatically by Claude Code on every session start.*
