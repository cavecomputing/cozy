<p align="center">
  <img src="assets/logo.png" alt="Cozy" width="300">
</p>

<p align="center">
  A single-user Flask web app for chatting with LLM character cards.
</p>

## About

Cozy is a lightweight, self-hosted chat interface for SillyTavern-compatible
V2 character cards. It supports personas, lorebooks, prompt presets, themes,
and any OpenAI-compatible LLM endpoint.

## Quick start

### Python

```bash
uv venv
uv pip install -r requirements.txt
uv run python app.py
```

App runs on http://localhost:5001.

### Docker

```bash
cd docker
docker compose up --build
```

## Documentation

- [docs/run.md](docs/run.md) — running the app
- [docs/db.md](docs/db.md) — database schema
