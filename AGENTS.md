# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Run / test

```bash
# Dev server (Flask, auto-reload, port 5001)
uv run python app.py

# Custom data directory (default: ./data)
COZY_DATA_DIR=/path/to/data uv run python app.py

# Tests (always run through uv)
uv run pytest                                 # full suite
uv run pytest tests/test_characters.py        # one file
uv run pytest tests/test_characters.py::test_name -x   # one test, stop on fail

# Docker (host port 80 -> container 5001)
cd docker && docker compose up --build
```

Use `uv run ...` for Python execution and testing in this repo. Do not start with bare `python` or bare `pytest` unless the user explicitly asks for that.

## Git workflow

Commit directly to `main` unless the user explicitly asks for a branch or PR workflow.

Before committing code changes:

1. Run the relevant `uv run pytest ...` target, and prefer the full `uv run pytest` suite when the change touches shared route, DB, import/export, or frontend/API behavior.
2. Add or update tests when the change introduces behavior, fixes a bug, changes response shapes, touches persistence, or covers a regression-prone path.
3. For frontend/UI changes, start the dev server with `uv run python app.py`, open the app in the in-harness browser, exercise the affected workflow, and check that the relevant screens are not visually broken on desktop and mobile-sized viewports when practical.
4. For CSS/template/layout changes, verify the affected UI visually rather than relying only on backend tests.
5. If the dev server or browser smoke test is blocked by the environment, say so clearly in the final note and run the closest available substitute, such as Flask test-client checks or syntax checks.

Production (Docker) runs gunicorn with the `gthread` worker class — required because `/api/llm/chat` streams SSE and the default sync worker buffers responses.

## Architecture

Single-process Flask app. Entry point [app.py](app.py) registers seven blueprints from [routes/](routes/), all serving `/api/*`. [shared.py](shared.py) owns paths, the SQLite connection (`get_db()` context manager), `init_db()` schema + seed data, and the `DEFAULT_PROMPT_TEMPLATE`. The frontend is a single SPA loaded from [templates/index.html](templates/index.html) with vanilla-JS modules under [static/js/](static/js/) (entry: [main.js](static/js/main.js)).

### Data lives in two places

Character cards are stored as **PNG files on disk** (`data/characters/*.png`) with a `chara` tEXt chunk holding base64-encoded V2 JSON — same format SillyTavern reads/writes. The SQLite `characters` table is just a lightweight index (`id`, `filename`, `crc`, `missing`). Routes that need card data read it from the PNG via [png_utils.py](png_utils.py) at request time.

Everything else (chats, messages, message_swipes, personas, settings, system_prompts, api_presets, lorebooks) lives in `data/cozy_chat.db`. The current schema and startup seed data are defined in `init_db()` in [shared.py](shared.py). Keep startup idempotent so calling `init_db()` repeatedly is safe.

### Prompt template system

System prompts are not plain text — they are Mustache-ish templates with `{{variable}}` and `{{#var}}…{{/var}}` conditional sections (see `DEFAULT_PROMPT_TEMPLATE` in [shared.py](shared.py)). Fresh databases seed the default template directly, with `{{system_prompt}}` left as a live variable for per-character instructions.

### LLM proxy and streaming

[routes/llm.py](routes/llm.py) proxies to any OpenAI-compatible endpoint configured in settings. `/api/llm/chat` always streams SSE (`text/event-stream`). Don't introduce middleware that buffers responses (this is also why `app.py` uses Flask's dev server directly instead of `livereload.Server`, which buffered SSE — see the comment in the `__main__` block of [app.py](app.py)).

### Themes

CSS files in [static/themes/](static/themes/) are built-in; user-added themes live in `$DATA_DIR/themes/` and **take precedence** over built-ins with the same filename — see `serve_theme()` in [app.py](app.py). `/api/themes` returns the merged set.

## Testing gotchas

[tests/conftest.py](tests/conftest.py) sets `COZY_DATA_DIR` to a temp dir at *import* time, then a per-test fixture monkeypatches `shared.DATABASE`/`CHARACTERS_DIR`/`PERSONAS_DIR`/`THEMES_DIR`. [app.py](app.py) and route modules resolve data paths through `shared`, so new path constants used at runtime should follow the same pattern or be included in the fixture when tests need to isolate them.

When creating a character in tests, use `make_minimal_png()` from [png_utils.py](png_utils.py) — anything smaller is rejected by Pillow.
