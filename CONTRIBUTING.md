# Contributing to RSS Sidecar

## Development Setup

```bash
git clone <repo-url>
cd projects__rss_sidecar
pip install -e ".[dev]"
cp .env.example .env  # Fill in your API key
```

## Running Tests

```bash
pytest tests/ -v
```

## Architecture

```
src/rss_sidecar/
├── config.py          Settings (pydantic-settings)
├── models.py          SQLite schema + state machine + CRUD
├── fetcher.py         RSS parsing (feedparser) + SSRF guard
├── extractor.py       Full-text extraction (trafilatura) + fallback
├── translator.py      LLM translation (chunked, parallel, TM-aware)
├── memory.py          Translation memory (SHA-256 paragraph matching)
├── graph_builder.py   Knowledge graph (entity extraction + networkx)
├── rss_output.py      Dual RSS feeds (stable + bilingual)
├── freshrss_client.py FreshRSS Google Reader API integration
└── main.py            FastAPI app + scheduler + pipeline + templates
```

## Key Design Decisions

- **Sidecar pattern**: Enhances FreshRSS/Miniflux, doesn't replace them
- **State machine**: `fetched → extracted → translated → published`
- **Translation memory**: Paragraph-level SHA-256 matching for consistency
- **Knowledge graph**: LLM entity extraction + networkx cross-article links
- **Dual RSS output**: Stable feed (unchanging guid) + bilingual feed (versioned guid)

## Submitting Changes

1. Create a feature branch from `main`
2. Write tests for new functionality
3. Ensure `pytest tests/ -v` passes
4. Keep commits focused (one feature per PR)

## Adding a New Translation Provider

Any OpenAI-compatible API works. See `.env.example` for configuration examples.

## Adding Glossary Terms

Edit `glossary.yaml` — changes are hot-reloaded on next translation.
