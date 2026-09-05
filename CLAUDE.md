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

**One commit, one concern.** A commit has to be reviewable on its own and safe to revert on its own,
so split unrelated work rather than bundling it: a bug fix and a docs correction that happened to
land in the same session are two commits. Size is not the test — a change that genuinely touches
twenty files is still one commit if it is one concern. Say in the message what broke and why the fix
works, not just what you typed.

**Never push.** The user always pushes themselves. Commit locally and stop there — do not run
`git push`.

[AGENTS.md](AGENTS.md) is a short pointer telling other agents to read this file, not a copy of it.
This file is the single source of truth: **edit it directly and leave AGENTS.md alone** unless the
arrangement itself changes.

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
verification the change calls for, and keep the duplication this file deliberately requires — the JS
and Python copies of the regex escaping, the two acknowledgement locations.

## Naming

A name is the cheapest documentation in the file. Make it a concise nameplate for what the thing is
or does — long enough to be unambiguous where it is *used*, short enough to read at a glance.

- Prefer the specific noun to the category: `watermark`, `agedOut`, `chunk_ids` over `value`,
  `data`, `items`.
- Name a function for what it returns or does, not how it works — `oldestRetirableId()`,
  `summariesActive()`, `enforce_cap()`.
- Don't encode the type or the scope in the name (`summaryObjDict`, `tmpList`), and don't abbreviate
  past recognition (`sm_cap_tk`).
- Follow the module you are editing — `snake_case` in Python, `camelCase` in JS — over any
  preference of your own.

Renaming existing code is its own task. Don't fold a rename into an unrelated change, where it
buries the real diff under noise.

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

Single-process Flask app, vanilla-JS SPA, no build step. Read the module you are touching — what
follows is the map, plus the rules the code cannot tell you on its own.

| Module | Owns |
|---|---|
| [app.py](app.py) | Entry point, and the only Python file at the repo root — `Flask(__name__)` resolves `templates/` and `static/` beside it, and `gunicorn app:app` names it. Registers eight blueprints from [cozy/routes/](cozy/routes/) (all `/api/*`), runs `init_db()` and the seeders. |
| [cozy/shared.py](cozy/shared.py) | Paths, `get_db()`, and the small request helpers. `BASE_DIR` is the *repo root*, one level above the package — bundled content and `static/themes` hang off it. |
| [cozy/schema.py](cozy/schema.py) | `init_db()` and the `MIGRATIONS` tuple. |
| [cozy/defaults.py](cozy/defaults.py) | `DEFAULT_PROMPT_TEMPLATE`, `DEFAULT_REGEX_PRESETS`, and the three seeders. |
| [cozy/card_store.py](cozy/card_store.py) | Character-card reads/writes — the layer routes actually call. [cozy/png_utils.py](cozy/png_utils.py) is the raw tEXt-chunk reader beneath it. |
| [cozy/thumbs.py](cozy/thumbs.py) | `/thumbs/...` WebP avatars, keyed by image content. Pure cache, safe to delete wholesale. |
| [cozy/summarizer.py](cozy/summarizer.py) | Auto Summaries logic. **Keep it free of Flask, DB and network** so `cozy/routes/chats.py` and `cozy/routes/summaries.py` can both import it. |
| [templates/index.html](templates/index.html) | The entire SPA. JS modules in [static/js/](static/js/), entry [main.js](static/js/main.js). |

### Data lives in two places

Character cards are **PNG files on disk** (`data/characters/*.png`) carrying a `chara` tEXt chunk of
base64 V2 JSON — the format SillyTavern reads. The SQLite `characters` table is only an index, so
card data is read back out of the PNG through [cozy/card_store.py](cozy/card_store.py) at request
time.

Everything else is in `data/cozy_chat.db`; [docs/db.md](docs/db.md) is the schema reference.

### Bundled content, and who owns it afterwards

Three seeders run from [app.py](app.py): characters from
[default_characters/](default_characters/), prompts from
[default_prompts/](default_prompts/), and regex presets from `DEFAULT_REGEX_PRESETS` inline in
[cozy/defaults.py](cozy/defaults.py) rather than a directory. Two hand their content over once
and never look again; prompts do not.

- Characters and regex presets use a `*_seeded` flag. It flips to `'1'` whether or not anything was
  inserted, and is **never reset**. A name already taken is skipped rather than duplicated.
  **Never re-seed on a schedule.**
- Characters seed on fresh installs only — an upgrade must not drop a card into a library the user
  curates. Regex presets are owed to existing installs too, so `default_regex_seeded` starts at
  `'0'` regardless of `fresh_install`.
- Regex presets ship **inactive**: `active_regex_preset` is deliberately left alone so bundled rules
  never silently rewrite replies.
- Prompts are the exception to every line above: no flag, and **restored on every start**. Anything
  in [default_prompts/](default_prompts/) missing from `system_prompts` is reinserted, so the
  directory is the source of truth and a deleted preset comes back — removing one means deleting
  its file. A title is the **filename**, so shipping a revised preset means adding
  `NanoBear v2.1.json`, never editing an existing file. An existing title is skipped, never
  overwritten, so user edits survive. A fresh install activates the **alphabetically greatest
  standard-NanoBear** title (`STANDARD_NANOBEAR_RE` in [cozy/defaults.py](cozy/defaults.py),
  mirrored in [system-prompts.js](static/js/system-prompts.js)), so a new house version takes over
  by sorting after the old one. An `Author` variant never wins, and with no standard title the
  default falls back to the greatest title overall. See the prompt section of
  [docs/db.md](docs/db.md).

A seeded character or regex preset is ordinary user data afterwards — deleting it keeps it deleted.
A seeded prompt is a copy of a file that outranks it.

### Two upgrade mechanisms, not interchangeable

`init_db()` must stay idempotent.

- **Adding a column** → the `PRAGMA table_info` / `ALTER TABLE ADD COLUMN` block near the end of
  `init_db()` in [cozy/schema.py](cozy/schema.py), guarded by a column-presence check.
- **Changing existing rows** (rewriting a stock template, renaming, deleting a retired setting) →
  the `MIGRATIONS` tuple in [cozy/schema.py](cozy/schema.py). Append with the next version and a new
  name; a shipped entry must **never be renumbered, renamed or reordered**, because
  `_run_migrations()` raises on versions that aren't unique and increasing or on a recorded name
  that no longer matches. Migrations touching stock prompts check for user edits first and skip
  customized rows.

### Prompt templates

Mustache-ish: `{{variable}}` plus `{{#var}}…{{/var}}` conditional sections — see
`DEFAULT_PROMPT_TEMPLATE`, which leaves `{{system_prompt}}` live so per-character instructions still
flow through. Each saved prompt is **paired**, a `content` template and a `post_history_content`
(`DEFAULT_POST_HISTORY_TEMPLATE`) injected after the chat history; both live on the `system_prompts`
row and travel together through the import/export endpoints in
[cozy/routes/settings.py](cozy/routes/settings.py).

### LLM proxy and streaming

[cozy/routes/llm.py](cozy/routes/llm.py) proxies any OpenAI-compatible endpoint, and `/api/llm/chat`
always streams SSE — so **never introduce middleware that buffers responses**. That is also why
`app.py` uses Flask's dev server directly instead of `livereload.Server`, which buffered SSE.

### Regex output filters

[regex-engine.js](static/js/regex-engine.js) is the one matcher, deliberately free of imports, DOM
and app state so the settings preview, both save points ([send.js](static/js/send.js),
[messages.js](static/js/messages.js)) and the renderer all run that same copy — which is what makes
the preview honest. **Don't add a second implementation.**

- A filter's `display` flag decides *where* it runs, one or the other, never both. `selectFilters()`
  splits the preset and [regex-filters.js](static/js/regex-filters.js) wraps each half:
  `applyOutputFilters()` rewrites
  the stored reply at the save points; `applyDisplayFilters()` rewrites only the bubble inside
  `renderMarkdown()`, leaving `dataset.rawText`, the DB row and the next prompt untouched. A missing
  `display` key means the save-point half, which keeps presets written before the option existed
  behaving exactly as they did.
- The display pass is character-messages-only and fires on **every** draw of the same text —
  greetings, old messages, each token of a stream — so it must stay free of side effects.
- No per-filter enable toggle by design: a filter is live when its Find pattern compiles, and
  selecting no preset is the off switch. A half-typed pattern is normal, so `runFilters()` skips
  that row instead of throwing mid-send, and it drops any filter that would blank the reply.
- Find and Replace are single-line `<input>`s that silently strip CR/LF, hence `escapeForInput()`
  out and `expandEscapes()` in — the only reason patterns holding a real newline (every bundled
  preset has one) survive an edit round trip.
- [cozy/routes/settings.py](cozy/routes/settings.py) keeps its own copies of the slash-form splitter
  and control-character escaping for the `/api/regex-presets` import/export, which accept both
  Cozy's `{name, filters}` shape and SillyTavern regex scripts. **Escaping or slash-form changes
  have to land on the JS and Python sides together.**

Separately, [rp-dialogue.js](static/js/rp-dialogue.js) owns which quote marks count as speech for
the `rpDialogue` extension — German `„…“`, guillemets, Japanese corner brackets — and the renderer
puts back the marks the reply actually used rather than anglicising them. Converting punctuation is
the Regex tab's job, and only when the user asks.

### Themes

User themes in `$DATA_DIR/themes/` **take precedence** over the built-ins in
[static/themes/](static/themes/) with the same filename — see `serve_theme()` in [app.py](app.py).
`/api/themes` returns the merged set.

### Things that must change together

- **Acknowledgements** (currently Sasha and the BigBear presets) appear in the `## Acknowledgements`
  section of [README.md](README.md) *and* on the About page (`data-section="about"` in
  [templates/index.html](templates/index.html)); the wording is meant to match, so changing an
  attribution means changing both. The requirement itself lives in [NOTICE](NOTICE).
- **[docs/](docs/)** is a hand-maintained user manual, so it goes stale silently.
  [docs/db.md](docs/db.md) enumerates every table, column, index, migration and seeded default — a
  schema change, a new migration or a new default setting is not finished until it is reflected
  there. A user-visible feature also means checking the README feature list and the matching
  `docs/` page.

The About page's build string comes from the current Git commit via
[cozy/build_info.py](cozy/build_info.py) (checkouts read `.git`, Docker embeds `.cozy-commit`). The
`0.0.0` in `pyproject.toml` is a permanent packaging placeholder, not a version — never bump it.

## Testing gotchas

[tests/conftest.py](tests/conftest.py) sets `COZY_DATA_DIR` to a temp dir at *import* time, then a per-test fixture monkeypatches `shared.DATABASE`/`CHARACTERS_DIR`/`PERSONAS_DIR`/`THEMES_DIR`. [app.py](app.py) and route modules resolve data paths through `cozy.shared`, so new path constants used at runtime should follow the same pattern or be included in the fixture when tests need to isolate them.

That fixture patches attributes **on the `cozy.shared` module object**, which is why every other module has to reach a path as `shared.DATABASE` / `shared.CHARACTERS_DIR` and never `from cozy.shared import DATABASE`. A `from` import binds the value once at import time, the patch never reaches it, and the tests quietly start writing into the real `data/` directory instead of failing. [cozy/schema.py](cozy/schema.py) and [cozy/defaults.py](cozy/defaults.py) both depend on this.

When creating a character in tests, use `make_minimal_png()` from [cozy/png_utils.py](cozy/png_utils.py) — anything smaller is rejected by Pillow.

Eleven test files exercise frontend modules by shelling out to `node`: `test_request_builder.py`, `test_regex_engine.py`, `test_rp_dialogue.py`, `test_default_regex.py`, and the seven `test_*_frontend.py` files. Every one of them **skips** (not fails) when `node` isn't on PATH, so a green run on a machine without Node is covering none of that JS — check for skips before trusting a pass.

That testability is why [regex-engine.js](static/js/regex-engine.js) and [rp-dialogue.js](static/js/rp-dialogue.js) exist as separate modules at all. Frontend logic that needs coverage has to be importable under bare `node`: no `./state.js`, no DOM, and no CDN globals such as `marked`. Put new pure logic in its own module and keep the DOM wiring in the caller.
