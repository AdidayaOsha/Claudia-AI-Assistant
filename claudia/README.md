# CLAUDIA — AI Personal Assistant

A J.A.R.V.I.S.-inspired AI personal assistant powered by Anthropic Claude.

---

## Quick Start

### 1. Install dependencies

```bash
cd claudia
pip install -r requirements.txt
```

### 2. Configure secrets

```bash
copy .env.example .env
```

Edit `.env` and fill in your API keys. At minimum you need:

```
ANTHROPIC_API_KEY=your_key_here
```

### 3. Run

```bash
python main.py
```

CLAUDIA will boot, greet you, and start listening. If no microphone is detected, it falls back to text input automatically.

The web dashboard is available at **http://localhost:5000**.

---

## API Keys

| Key | Required | Purpose |
|-----|----------|---------|
| `ANTHROPIC_API_KEY` | Yes | Primary LLM (Claude) |
| `OPENAI_API_KEY` | No | Fallback LLM (GPT-4o) |
| `OPENWEATHERMAP_API_KEY` | No | Weather skill |
| `NEWSAPI_KEY` | No | News briefing skill |
| `ELEVENLABS_API_KEY` | No | Premium voice (optional) |

---

## Optional Features

### Google Calendar & Gmail

1. Create a project at [Google Cloud Console](https://console.cloud.google.com)
2. Enable Calendar API and Gmail API
3. Download `credentials.json` and place it in the `claudia/` directory
4. In `config.yaml`, set:
   ```yaml
   features:
     enable_calendar: true
     enable_email: true
   ```
5. Add to `.env`:
   ```
   GMAIL_ADDRESS=you@gmail.com
   GMAIL_APP_PASSWORD=your_app_password
   ```

### Premium Voice (ElevenLabs)

In `config.yaml`:
```yaml
voice:
  engine: "elevenlabs"
  elevenlabs_voice_id: "your_voice_id"
```

---

## Running Tests

```bash
pip install pytest
cd claudia
python -m pytest tests/ -v
```

---

## Project Structure

```
claudia/
├── main.py              # Entry point
├── config.yaml          # Configuration
├── requirements.txt
├── .env                 # Your secrets (never commit this)
├── core/
│   ├── assistant.py     # Main orchestrator
│   ├── brain.py         # LLM interface
│   ├── listener.py      # Voice input
│   ├── speaker.py       # TTS output
│   ├── intent_router.py # Command routing
│   └── memory.py        # Session + long-term memory
├── skills/              # Drop new skills here
├── ui/                  # Flask dashboard
└── tests/
```

---

## Security

- All secrets live in `.env` — never hardcoded
- `.env` is in `.gitignore` — it will never be committed
- Google OAuth tokens saved locally to `token_*.json` (also gitignored)
