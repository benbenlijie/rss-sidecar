# RSS Sidecar

AI translation + knowledge graph sidecar for FreshRSS/Miniflux.

Translate foreign RSS feeds into your language, with bilingual display and cross-article knowledge connections. Runs alongside your existing RSS backend — no need to switch readers.

## Quick Start

```bash
cp .env.example .env
# Edit .env — fill in your translation API key (any OpenAI-compatible provider)

pip install -e .
uvicorn rss_sidecar.main:app --reload
```

## Configuration

All settings are in `.env` (copy from `.env.example`). Key options:

### Translation Provider

Any OpenAI-compatible API works. Set three variables:

| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | Your API key |
| `OPENAI_BASE_URL` | API endpoint |
| `OPENAI_MODEL` | Model name |

**Examples:**

```bash
# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini

# GLM (Zhipu AI)
OPENAI_API_KEY=your-key
OPENAI_BASE_URL=https://api.z.ai/api/paas/v4
OPENAI_MODEL=glm-4-flash

# DeepSeek
OPENAI_API_KEY=your-key
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat

# Ollama (local, zero API cost)
OPENAI_API_KEY=ollama
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_MODEL=qwen2.5:7b
```

### Cost Tracking (optional)

For accurate cost tracking with unlisted models, set pricing per 1M tokens:

```bash
TRANSLATION_INPUT_PRICE=0.15   # USD per 1M input tokens
TRANSLATION_OUTPUT_PRICE=0.60  # USD per 1M output tokens
```

If unset, built-in defaults are used for known models (gpt-4o, gpt-4o-mini, deepseek-chat, glm-4-flash).

### FreshRSS Integration (optional)

```bash
FRESHRSS_URL=http://localhost:8080
FRESHRSS_USERNAME=admin
FRESHRSS_API_PASSWORD=your-api-password
```

Requires FreshRSS with API enabled (System → Authentication → "Allow API access") and an API password set in your user profile.

### Other Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `TARGET_LANGUAGE` | `zh-CN` | Translation target language |
| `DAILY_BUDGET_USD` | `1.00` | Daily spending cap |
| `MAX_ARTICLES_PER_DAY` | `100` | Max articles processed per day |
| `MAX_RETRIES` | `2` | Translation retry attempts |
| `PORT` | `8000` | Server port |

## Usage

### Standalone Mode (no FreshRSS)

```bash
# Add a feed to process
curl "http://localhost:8000/feeds/manual?url=https://hnrss.org/frontpage"

# Run processing pipeline
curl -X POST "http://localhost:8000/process?limit=5"

# Subscribe to translated feed in any RSS reader:
#   Stable (pure translation):   http://localhost:8000/feed/stable/1.xml
#   Bilingual (paragraph pairs): http://localhost:8000/feed/bilingual/1.xml

# Or read in browser with bilingual display:
open http://localhost:8000/article/1
```

### FreshRSS Mode

```bash
# Auto-discover all your FreshRSS subscriptions
curl "http://localhost:8000/feeds/discover"

# Run processing — translated feeds auto-added to FreshRSS
curl -X POST "http://localhost:8000/process"
```

### Health Check

```bash
curl http://localhost:8000/health
# {"status": "ok", "daily_cost_usd": 0.002, "daily_budget_usd": 1.0, ...}
```

## Docker

```bash
cp .env.example .env
# Edit .env

docker compose up -d
```

## Architecture

See [docs/DESIGN.md](docs/DESIGN.md) for the full design document.

**Pipeline:**
```
RSS Feed → feedparser → trafilatura (full text) → LLM translation → Enhanced RSS output
                                                         ↓
                                               SQLite (state machine + cost tracking)
```

**Tech stack:** Python 3.10+, FastAPI, SQLite, feedparser, trafilatura, openai SDK
