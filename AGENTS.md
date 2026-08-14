# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Git workflow

The repository is **public and has users**, so anything that lands on `main` is something a stranger
may run. Every change gets a human review before it becomes history.

**Never commit unless asked.** Finish the work, leave it in the working tree, and report what
changed and how it was verified. The user reviews the diff themselves and then either asks for a
commit or makes it manually — that decision is theirs, not a step to anticipate. Don't stage files,
don't commit "so the work isn't lost", and don't treat a task being finished as permission.

When a commit *is* requested: commit directly to `main`, since this project uses no feature branches
or PRs. Only include files belonging to the task — leave unrelated pre-existing changes and stray
untracked files alone.

**Never push.** The user always pushes themselves. Commit locally and stop there — do not run
`git push`.

[AGENTS.md](AGENTS.md) is a byte-identical copy of this file, kept in sync so non-Claude agents read
the same guidance. **Every edit to CLAUDE.md must be duplicated verbatim into AGENTS.md right
away**, in the same turn — never leave the working tree with the two out of step. `diff CLAUDE.md
AGENTS.md` should stay silent.

## Least code that does the job

Aim for the smallest diff that completes the task in full. This is a vanilla-JS, no-build,
single-process app, and it stays approachable only while changes stay small.

- Prefer editing existing code over adding new code, and a few lines at the right call site over a
  new helper, module or class. Add a file only when something forces it — the node-importable rule
  is the usual reason (see Testing gotchas).
- Reuse what is already here: `get_db()`, `card_store`, `regex-engine.js`, the existing CSS
  variables, an existing `settings` row. A second implementation of something the repo already does
  is the most expensive kind of code.
- **No new dependency without asking first.**
- Leave out what nothing needs yet: config knobs, feature flags, an abstraction with one caller,
  `try`/`except` around code that doesn't raise, re-validation of data the route already validated.
- Deleting code is a legitimate way to finish a task. Say so when the fix turns out to be a removal.

This governs the amount of code, not the amount of work. Deliver everything that was asked, run the
verification the change calls for, and keep the duplication this file deliberately requires —
CLAUDE.md/AGENTS.md, the JS and Python copies of the regex escaping, the two acknowledgement
locations.

## Run / test

```bash
# Dev server (Flask, auto-reload, 127.0.0.1:5001; --host / --port to override)
uv run python app.py

# Custom data directory (default: ./data)
COZY_DATA_DIR=/path/to/data uv run python app.py

# Tests (use `uv run` — the bare `python`/`pytest` on PATH may not resolve the project env)
uv run pytest                                 # full suite
uv run pytest tests/test_characters.py        # one file
uv run pytest tests/test_characters.py::test_name -x   # one test, stop on fail

# Docker (run from repository root; localhost port 5001)
docker compose -f docker/docker-compose.yml up --build
```

Production (Docker) runs gunicorn with the `gthread` worker class — required because `/api/llm/chat` streams SSE and the default sync worker buffers responses.

### How much verification a change needs

Match the effort to the change; a public repo makes a broken `main` everyone's problem, but it does
not make every edit worth a full boot-and-click cycle.

Run the tests that cover what was touched, and the full suite before anything the user is likely to
commit. `uv run pytest` is fast, so when in doubt run all of it. If a change is in a module with
node-backed tests, confirm they actually ran rather than skipped (see Testing gotchas).

**Booting the dev server is not required after every change.** Start it when the change can only be
confirmed in a browser — layout, theming, flyout and mobile behaviour, streaming, anything where the
tests can't tell you whether it looks or feels right — or when asked. For backend logic, pure
frontend helpers with test coverage, docs and comments, skip it and say so; a server that proves
nothing is just noise. Never claim a change was verified in the app when it wasn't.

## Architecture

Single-process Flask app. Entry point [app.py](app.py) registers eight blueprints from [routes/](routes/), all serving `/api/*`. [shared.py](shared.py) owns paths, the SQLite connection (`get_db()` context manager), `init_db()` schema + seed data, and the `DEFAULT_PROMPT_TEMPLATE`. The frontend is a single SPA loaded from [templates/index.html](templates/index.html) with vanilla-JS modules under [static/js/](static/js/) (entry: [main.js](static/js/main.js)).

Logic shared between route modules sits in top-level modules rather than inside `routes/`:
[card_store.py](card_store.py) reads and writes character cards and is the layer routes actually
call ([png_utils.py](png_utils.py) is the raw tEXt-chunk reader underneath it);
[thumbs.py](thumbs.py) backs the `/thumbs/...` routes with downscaled WebP avatars, keyed by image
content rather than card CRC and safe to delete wholesale; [summarizer.py](summarizer.py) holds the
Auto Summaries logic, deliberately free of Flask, DB and network so both `routes/chats.py` and
`routes/summaries.py` can import it.

### Data lives in two places

Character cards are stored as **PNG files on disk** (`data/characters/*.png`) with a `chara` tEXt chunk holding base64-encoded V2 JSON — same format SillyTavern reads/writes. The SQLite `characters` table is just a lightweight index (`id`, `filename`, `crc`, `missing`, plus `pinned_at` for pin state). Routes that need card data read it from the PNG through [card_store.py](card_store.py) at request time.

Everything else lives in `data/cozy_chat.db`: `chats`, `messages`, `message_swipes`, `personas`, `settings`, `system_prompts`, `api_presets`, `regex_presets`, `lorebooks`, and the `schema_migrations` ledger. The current schema and startup seed data are defined in `init_db()` in [shared.py](shared.py).

### Bundled content is seeded once, then belongs to the user

Three kinds of content ship with the repo and are copied into the user's data on startup, each by
its own seeder called from [app.py](app.py) and each guarded by its own flag in `settings`:

- **Character cards** — [default_characters/](default_characters/) → `seed_default_characters()`, flag `default_characters_seeded`. Fresh installs only; upgrading must not drop a card into a library the user already curates.
- **Prompt presets** — [default_prompts/](default_prompts/) → `seed_default_prompts()`, flag `default_prompts_seeded`.
- **Regex presets** — `DEFAULT_REGEX_PRESETS` inline in [shared.py](shared.py) → `seed_default_regex_presets()`, flag `default_regex_seeded`. Ships *inactive*: `active_regex_preset` is deliberately left alone so bundled rules never silently rewrite replies.

Unlike characters, the prompt and regex presets are owed to existing installs too, so their flags
start at `'0'` regardless of `fresh_install`. The invariant is the same for all three: the flag
flips to `'1'` whether or not anything was inserted, is never reset, and a name already taken is
skipped rather than duplicated. From then on the content is ordinary user data — deleting a bundled
item keeps it deleted, so **never re-seed on a schedule or reset a `*_seeded` flag**.

### Two upgrade mechanisms, not interchangeable

Keep startup idempotent so calling `init_db()` repeatedly is safe.

- **Adding a column** to existing databases goes in the `PRAGMA table_info` / `ALTER TABLE ADD COLUMN` block near the end of `init_db()`, guarded by a column-presence check.
- **Changing existing rows** (rewriting a stock template, renaming, deleting a retired setting) goes in the `MIGRATIONS` tuple in [shared.py](shared.py), run by `_run_migrations()` inside the serialized transaction and recorded in `schema_migrations` so each runs exactly once. Append with the next version number and a new name: `_run_migrations()` raises if versions aren't unique and increasing, or if a recorded version's name no longer matches, so a shipped entry must never be renumbered, renamed or reordered. Migrations that touch stock prompts check for user edits first and skip customized rows.

### Prompt template system

System prompts are not plain text — they are Mustache-ish templates with `{{variable}}` and `{{#var}}…{{/var}}` conditional sections (see `DEFAULT_PROMPT_TEMPLATE` in [shared.py](shared.py)). Fresh databases seed the default template directly, with `{{system_prompt}}` left as a live variable for per-character instructions.

Each saved prompt is **paired**: a `content` (system) template and a `post_history_content` template injected after the chat history (`DEFAULT_POST_HISTORY_TEMPLATE` in [shared.py](shared.py)). Both are stored on the `system_prompts` row and travel together through the import/export endpoints in [routes/settings.py](routes/settings.py).

### LLM proxy and streaming

[routes/llm.py](routes/llm.py) proxies to any OpenAI-compatible endpoint configured in settings. `/api/llm/chat` always streams SSE (`text/event-stream`). Don't introduce middleware that buffers responses (this is also why `app.py` uses Flask's dev server directly instead of `livereload.Server`, which buffered SSE — see the comment in the `__main__` block of [app.py](app.py)).

### Regex output filters

A preset is a named, ordered list of find/replace filters run over a finished character reply.
[static/js/regex-engine.js](static/js/regex-engine.js) is the matcher, and is deliberately free of
imports, DOM and app state: the settings preview, both save points — [send.js](static/js/send.js)
and [messages.js](static/js/messages.js) — and the renderer all run that one copy, so what the
preview shows is what happens. Don't add a second implementation.

A filter's `display` flag decides *where* it runs, and it is one or the other, never both.
`selectFilters()` splits the preset, and [regex-filters.js](static/js/regex-filters.js) wraps each
half: `applyOutputFilters()` runs the ordinary filters at the two save points, rewriting the stored
reply; `applyDisplayFilters()` runs the display-only ones inside `renderMarkdown()`, rewriting the
bubble and nothing else, so `dataset.rawText`, the DB row and the next prompt keep the original.
That render-time pass is for character messages only, and it fires on every draw of the same text —
greetings, old messages, and each token of a stream — so it must stay free of side effects. A
missing `display` key means the save-point half, which is what keeps presets written before the
option existed behaving exactly as they did.

There is no per-filter enable toggle by design: a filter is live when its Find pattern compiles, and
selecting no preset is how filtering is turned off. An uncompilable pattern is a normal state
(half-typed), so `runFilters()` skips the row instead of throwing mid-send, and it also drops any
single filter that would blank the reply outright.

Both Find and Replace are single-line `<input>`s, which silently strip CR/LF — hence
`escapeForInput()` on the way out and `expandEscapes()` on the way in. Patterns holding a real
newline (every bundled preset does, to stop a quote swallowing the next paragraph) only survive an
edit round trip because of that pair.

[routes/settings.py](routes/settings.py) carries its own copies of the slash-form splitter and
control-character escaping for the `/api/regex-presets` import/export endpoints, which accept both
Cozy's `{name, filters}` shape and SillyTavern regex scripts. **Escaping or slash-form changes have
to land on the JS and Python sides together.**

Separately, [static/js/rp-dialogue.js](static/js/rp-dialogue.js) owns which quote marks count as
speech for the `rpDialogue` marked extension in [main.js](static/js/main.js) — German `„…“`,
guillemets and Japanese corner brackets included — and the renderer puts back the marks the reply
actually used rather than anglicising them. Converting punctuation is the Regex tab's job, and only
when the user asks for it.

### Themes

CSS files in [static/themes/](static/themes/) are built-in; user-added themes live in `$DATA_DIR/themes/` and **take precedence** over built-ins with the same filename — see `serve_theme()` in [app.py](app.py). `/api/themes` returns the merged set.

### Acknowledgements appear in two places

The bundled-content acknowledgements (currently Sasha and the BigBear presets) are duplicated: the
`## Acknowledgements` section of [README.md](README.md) and the About page in Settings
(`data-section="about"` in [templates/index.html](templates/index.html)). **Changing an
attribution means changing both** — the wording is meant to match. The repository-attribution
requirement itself lives in [NOTICE](NOTICE) and is restated on the About page.

The build shown on that page comes from the current Git commit, resolved by [build_info.py](build_info.py)
at import time and passed into the template by `index()` in [app.py](app.py). Direct checkouts read
`.git`; Docker embeds `.cozy-commit` during its source stage. The `0.0.0` value in `pyproject.toml`
is a permanent packaging placeholder, not an application version, and should not be bumped.

### User-facing docs in docs/

[docs/](docs/) is the user manual (getting started, running, data & backups, samplers, themes,
auto-summaries, regex filters, troubleshooting, and [docs/db.md](docs/db.md)). It is prose maintained by hand, not
generated, so it goes stale silently. [docs/db.md](docs/db.md) enumerates every table, column, index,
migration and seeded default — a schema change, a new migration or a new default setting is not
finished until it is reflected there. When a change adds a user-visible feature, check whether the
README feature list and the relevant `docs/` page need it too.

## Testing gotchas

[tests/conftest.py](tests/conftest.py) sets `COZY_DATA_DIR` to a temp dir at *import* time, then a per-test fixture monkeypatches `shared.DATABASE`/`CHARACTERS_DIR`/`PERSONAS_DIR`/`THEMES_DIR`. [app.py](app.py) and route modules resolve data paths through `shared`, so new path constants used at runtime should follow the same pattern or be included in the fixture when tests need to isolate them.

When creating a character in tests, use `make_minimal_png()` from [png_utils.py](png_utils.py) — anything smaller is rejected by Pillow.

Eleven test files exercise frontend modules by shelling out to `node`: `test_request_builder.py`, `test_regex_engine.py`, `test_rp_dialogue.py`, `test_default_regex.py`, and the seven `test_*_frontend.py` files. Every one of them **skips** (not fails) when `node` isn't on PATH, so a green run on a machine without Node is covering none of that JS — check for skips before trusting a pass.

That testability is why [regex-engine.js](static/js/regex-engine.js) and [rp-dialogue.js](static/js/rp-dialogue.js) exist as separate modules at all. Frontend logic that needs coverage has to be importable under bare `node`: no `./state.js`, no DOM, and no CDN globals such as `marked`. Put new pure logic in its own module and keep the DOM wiring in the caller.
