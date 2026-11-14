# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Git workflow

The repository is **public and has users**, so anything that lands on `main` is something a stranger
may run. The user reviews changes after they land rather than before, so the verification below is
what keeps `main` working.

**Commit and push finished work without waiting to be asked.** When a task is done and verified,
commit it directly to `main`, since this project uses no feature branches or PRs, and push. Then
report what changed, how it was verified, and the commit hashes, so the user can review or revert
it. Only include files belonging to the task — leave unrelated pre-existing changes and stray
untracked files alone. Don't commit work that is half done or has failing tests.

**One commit, one concern.** A commit has to be reviewable on its own and safe to revert on its own,
so split unrelated work rather than bundling it: a bug fix and a docs correction that happened to
land in the same session are two commits. Size is not the test — a change that genuinely touches
twenty files is still one commit if it is one concern. Say in the message what broke and why the fix
works, not just what you typed.

[AGENTS.md](AGENTS.md) is a short pointer telling other agents to read this file, not a copy of it.
This file is the single source of truth: **edit it directly and leave AGENTS.md alone** unless the
arrangement itself changes.

## Least code that does the job

Aim for the smallest diff that completes the task in full. This is a vanilla-JS, no-build,
single-process app, and it stays approachable only while changes stay small.

- Prefer editing existing code over adding new code, and a few lines at the right call site over a
  new helper, module or class. Add a file only when something forces it — the node-importable rule
  is the usual reason (see Testing gotchas).
- Reuse what is already here: `get_db()`, `card_store`, the existing CSS variables, an existing
  `settings` row. A second implementation of something the repo already does is the most expensive
  kind of code.
- **No new dependency without asking first.**
- Leave out what nothing needs yet: config knobs, feature flags, an abstraction with one caller,
  `try`/`except` around code that doesn't raise, re-validation of data the route already validated.
- **Change the style itself, never pile an override on top.** When a look changes, edit the rule
  that owns it in [static/css/style.css](static/css/style.css), or give two elements one shared
  class. Don't add a later rule, a longer selector or an `!important` to beat the old one, or set
  styles from JS. A design mocked with injected override CSS gets rewritten into the real rules
  before it ships.
- Deleting code is a legitimate way to finish a task. Say so when the fix turns out to be a removal.

This governs the amount of code, not the amount of work. Deliver everything that was asked, run the
verification the change calls for, and keep the duplication this file deliberately requires — the two
acknowledgement locations.

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
# Dev server (Flask, 127.0.0.1:5000; --host / --port to override). --debug adds
# auto-reload and the debugger; it is opt-in because users run this same command.
uv run app.py --debug

# Custom data directory (default: ./data)
COZY_DATA_DIR=/path/to/data uv run app.py

# Tests (use `uv run` — the bare `python`/`pytest` on PATH may not resolve the project env)
uv run pytest                                 # full suite
uv run pytest tests/test_characters.py        # one file
uv run pytest tests/test_characters.py::test_name -x   # one test, stop on fail

# Docker (run from repository root; localhost port 5000)
docker compose -f docker/compose.yml up --build
```

Production (Docker) runs gunicorn with the `gthread` worker class — required because `/api/llm/chat` streams SSE and the default sync worker buffers responses.

### How much verification a change needs

Match the effort to the change; a public repo makes a broken `main` everyone's problem, but it does
not make every edit worth a full boot-and-click cycle.

Run the tests that cover what was touched, and the full suite before every
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
| [app.py](app.py) | Entry point, and the only Python file at the repo root — `Flask(__name__)` resolves `templates/` and `static/` beside it, and `gunicorn app:app` names it. Registers eight blueprints from [cozy/routes/](cozy/routes/) (all `/api/*`), runs `init_db()` and the seeders, then installs the sign-in guard when `COZY_PASSWORD` is set. |
| [cozy/auth.py](cozy/auth.py) | The optional password: sign-in page, the guard in front of every route, sign-out. |
| [cozy/shared.py](cozy/shared.py) | Paths, `get_db()`, and the small request helpers. `BASE_DIR` is the *repo root*, one level above the package — bundled content and `static/themes` hang off it. |
| [cozy/schema.py](cozy/schema.py) | `init_db()` and the `MIGRATIONS` tuple. |
| [cozy/defaults.py](cozy/defaults.py) | The two seeders, the stock prompt texts old migrations match on, and `plain_prompt()`, which turns a template from the old editor into plain text. |
| [cozy/card_store.py](cozy/card_store.py) | Character-card reads/writes — the layer routes actually call. [cozy/png_utils.py](cozy/png_utils.py) is the raw tEXt-chunk reader beneath it. |
| [cozy/thumbs.py](cozy/thumbs.py) | `/thumbs/...` WebP avatars, keyed by image content. Pure cache, safe to delete wholesale. |
| [cozy/summarizer.py](cozy/summarizer.py) | Auto Summaries logic. **Keep it free of Flask, DB and network** so `cozy/routes/chats.py` and `cozy/routes/summaries.py` can both import it. |
| [templates/index.html](templates/index.html) | The entire SPA. JS modules in [static/js/](static/js/), entry [main.js](static/js/main.js). |

### Data lives in two places

Character cards are **PNG files on disk** (`data/characters/*.png`) carrying a `chara` tEXt chunk of
base64 V2 JSON — the format SillyTavern reads. The SQLite `characters` table is only an index, so
card data is read back out of the PNG through [cozy/card_store.py](cozy/card_store.py) at request
time.

Everything else is in `data/cozy_chat.db`; [docs/10-database-schema.html](https://cavecomputing.github.io/cozy/10-database-schema.html) is the schema reference.

### Bundled content, and who owns it afterwards

Two seeders run from [app.py](app.py): characters from
[default_characters/](default_characters/) and prompts from
[default_prompts/](default_prompts/). Characters are handed over once and never looked at again;
prompts are not.

- Characters use the `default_characters_seeded` flag. It flips to `'1'` whether or not anything
  was inserted, and is **never reset**. A name already taken is skipped rather than duplicated.
  **Never re-seed on a schedule.**
- Characters seed on fresh installs only — an upgrade must not drop a card into a library the user
  curates.
- Prompts are the exception to every line above: no flag, and **restored on every start**. Anything
  in [default_prompts/](default_prompts/) missing from `system_prompts` is reinserted, so the
  directory is the source of truth and a deleted preset comes back — removing one means deleting
  its file. A title is the **filename**, and a preset is identified by that title **together with
  its `version`** — the same pair `/api/system-prompts` rejects a duplicate on. So shipping a
  revision means **bumping `version` inside the existing file**, not adding a new one: an install
  holding `NanoBear` 2.1 gains `NanoBear` 2.2 beside it. An existing title *at that version* is
  skipped, never overwritten, so user edits survive — and editing a file without bumping its
  version reaches nobody who already has it. A fresh install activates the **standard NanoBear
  carrying the greatest version** (`STANDARD_NANOBEAR_RE` in [cozy/defaults.py](cozy/defaults.py),
  mirrored in [system-prompts.js](static/js/system-prompts.js)), compared numerically by
  `version_key()` so `2.10` outranks `2.2`. An `Author` variant never wins, and with no standard
  title the default falls back to the last preset seeded. A version is a number with at most one
  decimal part and never carries the `v`; the picker's badge adds that. See the prompt section of
  [docs/10-database-schema.md](docs/10-database-schema.md).

A seeded character is ordinary user data afterwards — deleting it keeps it deleted.
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

### The database version, and backups

The highest version in `MIGRATIONS` **is** the database version — there is no
second scheme, and `pyproject.toml`'s `0.0.0` and the About build string are
not it. A database's own version is the highest row in its `schema_migrations`
ledger.

`/api/backup` zips the data directory with a `cozy-backup.json` naming that
number; `/api/backup/restore` refuses an archive numbered **higher** than this
build's registry and migrates a lower one up by running `init_db()` after the
swap. So appending a migration is also what makes older backups restorable —
nothing else needs bumping, and nothing may renumber what shipped.

A start that finds an existing database behind the registry copies it first to
`cozy_chat.before-update.db` beside it (`_copy_before_migrating()` in
[cozy/schema.py](cozy/schema.py)), since nothing migrates back down. One copy, replaced by the
next migrating start. It still holds the sign-in secret, so `/api/backup` leaves it out, as the
storage total does; anything new that walks the data directory has to skip
`before_update_path()` too.

`/api/backup` builds a **different archive on every request**, so its response
must never look resumable — it streams with `Accept-Ranges: none` and no ETag.
Chrome splits a download of more than a couple of megabytes across parallel
range requests, and ranges served from separate archives stitch into a zip that
fails its own CRCs. That is also why it streams from a temp copy the response
owns and deletes: `send_file` plus a delete-on-close left a full copy of the
data directory in the system temp directory after every backup.

Restore is destructive: it replaces everything in `$DATA_DIR` with the
archive's contents. Four rules keep that from being a data-loss bug, and each
has a test: the archive's database is opened and read **while still staged**,
so a broken backup is refused before anything is touched; an archive that would
not fit unpacked in the free space is refused before it is staged
(`_room_to_restore()`); the old contents are only renamed into a `.restore-*`
folder inside the data directory and deleted **after** the whole backup has
landed, so a move that fails partway is undone and the route says the data was
put back (`_replace_data_dir()`); and the route holds `_RESTORE_LOCK` so two
restores can't interleave. The
per-request `max_content_length` has to be a **number** — Flask falls back to
the app-wide 20 MB cap when it is `None`, which would 413 any real backup.

### The prompt layout

Cozy owns the layout of what is sent; a prompt is only the words in two boxes. `SYSTEM_LAYOUT` and
`POST_HISTORY_LAYOUT` in [context-analysis.js](static/js/context-analysis.js) are that layout: which
tag wraps which part, each tag's `note`, and the order. A part with nothing in it is left out, and
so is a tag left with nothing inside. Each saved prompt is **paired**, `content` (the System box)
and `post_history_content` (the Post-History box, sent after the chat history); both live on the
`system_prompts` row as plain text and travel together through the import/export endpoints in
[cozy/routes/settings.py](cozy/routes/settings.py). In a box `{{char}}`, `{{user}}`, `{{time}}`,
`{{date}}` and `{{random:a,b}}` are filled in and any other `{{…}}` is dropped, so a card field can't
be sent twice by naming it there.

The meter counts a tag with the part it wraps, the first part for an opening tag and the last for
a closing one, so the layout costs the parts it serves and nothing is left over to explain.

Prompts used to be templates the user wrote. Migration 21 and the import route both run
`plain_prompt()` in [cozy/defaults.py](cozy/defaults.py) over them, and the migration writes a
template whose words did not all sit inside the two wrapper tags to
`$DATA_DIR/old-prompt-templates.json` first. That file is the user's own text, so it travels in
backups like any other.

### Optional password

`COZY_PASSWORD` turns on [cozy/auth.py](cozy/auth.py), ported from the owner's imgy and binny apps.
[app.py](app.py) installs it only when the password is set, so without one the guard doesn't exist
and nothing changes.

- Every route is protected by default. `OPEN_ENDPOINTS` (the sign-in page, `/static`) plus `.css`
  files under `/themes` are the whole list of exceptions; never open anything else that serves from
  the data directory. [tests/test_auth.py](tests/test_auth.py) sweeps the whole url_map, so a new
  route that slips through fails it.
- The guard stays registered after `refuse_cross_site_writes`, so a page on another site can't post
  a sign-in. The one-second lockout is global on purpose, as in imgy and binny: a client that
  reaches `/login` directly can hold off new sign-ins while it guesses, but a lockout per address
  would let many addresses guess side by side.
- The cookie's signing key mixes the `secret_key` setting with the password. That row never leaves:
  `read_settings()` drops it, `_database_snapshot()` keeps it out of backups, and a restore puts
  this install's back (`restore_backup()`).
- The password is read when app.py is imported, so the tests load app.py again with it set
  (`load_app()`) rather than using the password-less app the rest of the suite shares.

### LLM proxy and streaming

[cozy/routes/llm.py](cozy/routes/llm.py) proxies any OpenAI-compatible endpoint, and `/api/llm/chat`
always streams SSE — so **never introduce middleware that buffers responses**. That is also why
`app.py` uses Flask's dev server directly instead of `livereload.Server`, which buffered SSE.

**The server owns a reply, not the page.** `POST /api/llm/chat?chat_id=…` starts a thread
(`_write_reply()`) that reads the model's stream to its end and saves the reply itself, as a new
message or as a new swipe of `message_id`, so a locked phone or a closed tab loses nothing. The
response to that POST only *follows* the reply, and so does a `GET` of the same URL from a page that
comes back or from another device; each is played the reply from its first event. A closed
connection therefore means nothing. Only `DELETE` ends a reply, which is what the Stop button sends,
and `_finish()` holds the one rule for what a reply cut short keeps: the part that arrived, unless
that was reasoning alone. The stream ends by saying what was saved (`{"saved": row}`), and that row
is what the page shows; **the page never posts a reply**. In the page, aborting
`llm.abortController` only stops listening, as leaving a chat does, and `stopGeneration()` is what
asks the server to stop. A reply that **failed** stays in `_replies` until the next `GET` for its
chat, which is answered once with why as JSON, or until the chat is sent another: the page that
asked may have been gone when it failed, and would come back to no reply and no reason. Replies
live in memory (`_replies`, one per chat), as the summary jobs do, so both need the **single
gunicorn worker** Docker runs, and a restart loses a reply in flight.

### Context analysis

`analyzeContext()` in [context-analysis.js](static/js/context-analysis.js) decides what the model
sees, and the meter, the context separator, the summary check and the request all call it. It hands
back the analysis it already has when a call's inputs equal the last call's, so those four share
one. That is only safe because `contextInputs()` in
[context-budget.js](static/js/context-budget.js) is the **single place the analysis reads the app
from**: context-analysis.js does not import `state.js`, and a test fails if it starts to. Anything
new the analysis needs goes into `contextInputs()`, where the comparison sees it — read around it
and a changed chat is answered with a stale context, with nothing on screen to show it. The result
is frozen because its callers share it, and it is dropped when the browser next takes a turn,
since `{{time}}`, `{{date}}` and `{{random}}` are not inputs.

### Messages and swipes

A message is its swipes, and each fact about them is kept once. On the server,
`messages.selected_swipe_id` names the swipe on show and `messages.content` is
a copy of that swipe's text for everything that reads a chat without its
swipes; only `insert_swipe()` and the message update route in
[cozy/routes/messages.py](cozy/routes/messages.py) write it, and a swipe is
named to the routes by its index, never by its text, since two swipes can read
the same. In the page the entry in `state.messages` is the
only copy. A bubble carries `data-msg-id` and nothing else about its message:
`drawnMessages` in [messages.js](static/js/messages.js) links it to the entry
it was drawn from, and every handler reads and changes that entry. Don't put a
message's text or swipes on the element, where a redraw from `state.messages`
and the bubble it replaces can disagree. That is how a new swipe was once lost
and the next edit landed on a different one.

### Dialogue quote marks

[rp-dialogue.js](static/js/rp-dialogue.js) owns which quote marks count as speech for the
`rpDialogue` extension — German `„…“`, guillemets, Japanese corner brackets — and the renderer puts
back the marks the reply actually used rather than anglicising them. A reply is stored and shown
as the model wrote it; nothing rewrites its text.

### Themes

User themes in `$DATA_DIR/themes/` **take precedence** over the built-ins in
[static/themes/](static/themes/) with the same filename — see `serve_theme()` in [app.py](app.py).
`/api/themes` returns the merged set.

The theme sheet blocks the first paint, so the page asks for it as `/themes/<name>.css?v=<version>`,
the version being a hash of the file when the page was rendered, and only a request naming the
current version is served as immutable. That is what lets an edited user theme show on the next
reload: its version, and so its address, has changed. `themeHref()` in the head of
[templates/index.html](templates/index.html) is the one place that address is built — the inline
script and `applyTheme()` both use it, because two spellings of it would fetch the sheet twice.

### Things that must change together

- **Acknowledgements** appear in the `## Acknowledgements`
  section of [README.md](README.md) *and* on the About page (`data-section="about"` in
  [templates/index.html](templates/index.html)); the wording is meant to match, so changing an
  attribution means changing both. The requirement itself lives in [NOTICE](NOTICE).
- **[docs/](docs/)** is a hand-maintained user manual, so it goes stale silently.
  [docs/10-database-schema.html](https://cavecomputing.github.io/cozy/10-database-schema.html) is the schema reference (tables with their real `CREATE TABLE`,
  the foreign-key map, the migration ledger, the seeded settings) — a schema change, a new
  migration or a new default setting is not finished until it is reflected there.
  [docs/10-database-schema.md](docs/10-database-schema.md) only points at it for the schema and covers seeder behaviour around
  it, so a change in what gets seeded or restored means checking that page too. A user-visible
  feature also means checking the README feature list and the matching `docs/` page.

The About page's build string comes from the current Git commit via
[cozy/build_info.py](cozy/build_info.py) (checkouts read `.git`, Docker embeds `.cozy-commit`). The
`0.0.0` in `pyproject.toml` is a permanent packaging placeholder, not a version — never bump it.

Every response carries that label as `X-Cozy-Build`, and the page carries the one it was rendered
with (`data-build`). A page that comes back to the front and finds the two differ reloads itself,
once and only with nothing open or under way (`reloadAfterUpdate()` in
[main.js](static/js/main.js)), which is what lets a change to what the page sends ship without
stranding pages left open, an installed phone app above all.

## Testing gotchas

[tests/conftest.py](tests/conftest.py) sets `COZY_DATA_DIR` to a temp dir at *import* time, then a per-test fixture monkeypatches `shared.DATABASE`/`CHARACTERS_DIR`/`PERSONAS_DIR`/`THEMES_DIR`. [app.py](app.py) and route modules resolve data paths through `cozy.shared`, so new path constants used at runtime should follow the same pattern or be included in the fixture when tests need to isolate them.

That fixture patches attributes **on the `cozy.shared` module object**, which is why every other module has to reach a path as `shared.DATABASE` / `shared.CHARACTERS_DIR` and never `from cozy.shared import DATABASE`. A `from` import binds the value once at import time, the patch never reaches it, and the tests quietly start writing into the real `data/` directory instead of failing. [cozy/schema.py](cozy/schema.py) and [cozy/defaults.py](cozy/defaults.py) both depend on this.

When creating a character in tests, use `make_minimal_png()` from [cozy/png_utils.py](cozy/png_utils.py) — anything smaller is rejected by Pillow.

Eighteen test files exercise frontend modules by shelling out to `node`: `test_request_builder.py`, `test_rp_dialogue.py`, and the sixteen `test_*_frontend.py` files. Every one of them **skips** (not fails) when `node` isn't on PATH, so a green run on a machine without Node is covering none of that JS — check for skips before trusting a pass.

That testability is why [rp-dialogue.js](static/js/rp-dialogue.js) exists as a separate module at all. Frontend logic that needs coverage has to be importable under bare `node`: no `./state.js`, no DOM, and no page globals such as `marked` (a classic script from [static/vendor/](static/vendor/), not a module). Put new pure logic in its own module and keep the DOM wiring in the caller.
