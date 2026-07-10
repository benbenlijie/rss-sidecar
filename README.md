# RSS Sidecar

AI translation + knowledge graph sidecar for FreshRSS/Miniflux.

## Quick Start

```bash
cp .env.example .env
# Edit .env with your API keys

docker compose up -d
```

## Manual Mode (without FreshRSS)

```bash
# Add a feed to process
curl "http://localhost:8000/feeds/manual?url=https://example.com/feed.xml"

# Run processing pipeline
curl -X POST "http://localhost:8000/process"

# Subscribe to translated feed in any RSS reader:
# Stable:  http://localhost:8000/feed/stable/1.xml
# Bilingual: http://localhost:8000/feed/bilingual/1.xml
```

## Architecture

See [docs/DESIGN.md](docs/DESIGN.md) for the full design document.
