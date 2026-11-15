# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Git workflow

The repository is **public and has users**, and `main` is what they run, so **`main` always
matches the most recent release**. All work happens on `dev`. The user reviews changes on `dev`
after they land rather than before, so the verification below is what keeps `dev` ready to release.

**Work on `dev`.** A fresh checkout starts on `main`, so begin with
`git fetch origin dev && git checkout -B dev origin/dev`. `dev` is the one working branch: no
feature branches or PRs.

**Commit and push finished work to `dev` without waiting to be asked.** When a task is done and
verified, commit it to `dev` and push (`git push origin HEAD:dev`). Then report what changed, how it
was verified, and the commit hashes, so the user can review or revert it. Only include files
belonging to the task — leave unrelated pre-existing changes and stray untracked files alone. Don't
commit work that is half done or has failing tests.

**Nothing that changes code reaches `main` except a release, and a release is the user's call.**
The release number is `version` in [pyproject.toml](pyproject.toml), and the user picks it.
Versions follow Semantic Versioning (`MAJOR.MINOR.PATCH`), starting at `1.0.0`. A release is:

1. One commit on `dev` that sets the new `version`, with `uv lock` run so `uv.lock` carries the
   same number. Push `dev`.
2. A merge commit that brings `dev` into `main`:
   `git fetch origin && git checkout -B main origin/main && git merge --no-ff origin/dev -m "Release v<version>" && git push origin main`.
   A merge rather than a fast-forward, because `main` can hold docs commits that `dev` lacks under
   their own hashes (below), and Git refuses to fast-forward over those.
3. Nothing by hand after that. The push changes the version on `main`, which makes
   [release.yml](.github/workflows/release.yml) tag the merge commit `v<version>` and publish its
   GitHub release. The workflow fails on a version that isn't Semantic Versioning or was released
   already. Nothing else bumps the version.

**Docs are the one exception.** A commit that changes only text for readers, such as
[README.md](README.md), [docs/](docs/) or this file, may also go to `main` between releases, as long
as it is true of the release `main` holds: documentation of something only `dev` has waits for the
release. Commit it on `dev` first as usual, then copy it onto `main` with
`git checkout -B main origin/main && git cherry-pick <hash> && git push origin main`, and report both
hashes. Such a commit has to touch nothing but those files, so split a change that also touches code.
A code fix that can't wait for a release still lands on `dev` first; ask the user before it reaches
`main`, because it means a release.

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
docker compose up --build
```

Production (Docker) runs gunicorn with the `gthread` worker class — required because `/api/llm/chat` streams SSE and the default sync worker buffers responses.

### How much verification a change needs

Match the effort to the change; a public repo makes a broken release everyone's problem, but it does
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
| [cozy/schema.py](cozy/schema.py) | `init_db()`, `BASELINE_VERSION` and the `MIGRATIONS` tuple. |
| [cozy/defaults.py](cozy/defaults.py) | The two seeders, and `plain_prompt()`, which turns an imported template from the old editor into plain text. |
| [cozy/card_store.py](cozy/card_store.py) | Character-card reads/writes — the layer routes actually call. [cozy/png_utils.py](cozy/png_utils.py) is the raw tEXt-chunk reader beneath it. |
| [cozy/thumbs.py](cozy/thumbs.py) | `/thumbs/...` WebP avatars, keyed by image content. Pure cache, safe to delete wholesale. |
| [cozy/summarizer.py](cozy/summarizer.py) | Auto Summaries logic. **Keep it free of Flask, DB and network** so `cozy/routes/chats.py` and `cozy/routes/summaries.py` can both import it. |
| [templates/index.html](templates/index.html) | The entire SPA. JS modules in [static/js/](static/js/), entry [main.js](static/js/main.js). |

### Data lives in two places

Character cards are **PNG files on disk** (`data/characters/*.png`) carrying a `chara` tEXt chunk of
base64 V2 JSON — the format SillyTavern reads. The SQLite `characters` table is only an index, so
card data is read back out of the PNG through [cozy/card_store.py](cozy/card_store.py) at request
time.

Everything else is in `data/cozy_chat.db`; [docs/database-schema.md](docs/database-schema.md) is the schema reference.

### Bundled content, and who owns it afterwards

Two seeders run from [app.py](app.py): characters from
[cozy/bundled/characters/](cozy/bundled/characters/) and prompts from
[cozy/bundled/prompts/](cozy/bundled/prompts/). Characters are handed over once and never looked at again;
prompts are not.

- Characters use the `default_characters_seeded` flag. It flips to `'1'` whether or not anything
  was inserted, and is **never reset**. A name already taken is skipped rather than duplicated.
  **Never re-seed on a schedule.**
- Characters seed on fresh installs only — an upgrade must not drop a card into a library the user
  curates.
- Prompts are the exception to every line above: no flag, and **restored on every start**. Anything
  in [cozy/bundled/prompts/](cozy/bundled/prompts/) missing from `system_prompts` is reinserted, so the
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
  [docs/database-schema.md](docs/database-schema.md).

A seeded character is ordinary user data afterwards — deleting it keeps it deleted.
A seeded prompt is a copy of a file that outranks it.

### Two upgrade mechanisms, not interchangeable

`init_db()` must stay idempotent.

- **Adding a column** → the `CREATE TABLE` in `init_db()` in [cozy/schema.py](cozy/schema.py), plus
  an `ALTER TABLE ADD COLUMN` at the marked spot after the script, guarded by a `PRAGMA table_info`
  column-presence check, since a new database already has the column.
- **Changing existing rows** (rewriting a stock template, renaming, deleting a retired setting) →
  the `MIGRATIONS` tuple in [cozy/schema.py](cozy/schema.py). Append with the next version and a new
  name; a shipped entry must **never be renumbered, renamed or reordered**, because
  `_run_migrations()` raises on versions that aren't unique and increasing or on a recorded name
  that no longer matches. Migrations touching stock prompts check for user edits first and skip
  customized rows.

Both lists are empty today. On 2026-11-14 the owner had the 21 migrations and every column upgrade
removed, since theirs was the only install. `BASELINE_VERSION` (21) is what a new database is created
at and the oldest one `init_db()` opens: on an older one it raises, before touching it, and names
`LAST_UPGRADING_COMMIT`, the last build that can still upgrade it. The next migration is 22.

### The database version, and backups

The highest version in `MIGRATIONS` **is** the database version, or `BASELINE_VERSION` while the
tuple is empty; `database_version()` in [cozy/schema.py](cozy/schema.py) is the one place that says
so. There is no second scheme, and the release number in `pyproject.toml` and the About build
string are not it. A database's own version is the highest row in its `schema_migrations`
ledger.

`/api/backup` zips the data directory with a `cozy-backup.json` naming that
number; `/api/backup/restore` refuses an archive numbered **higher** than this
build's registry or **below the baseline**, and migrates a lower one up by running `init_db()`
after the swap. So appending a migration is also what makes older backups restorable —
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

A card's own `system_prompt` and `post_history_instructions` are not sent unless the
`use_character_prompts` setting is on (off by default). Then each replaces its own box for that
character, never adds to it, and `{{original}}` is not supported. `cardPrompt()` in
[context-budget.js](static/js/context-budget.js) is the one place that decides, for the request, the
meter and the Prompt page alike; the page shows the card's text read-only and never writes it into
the user's box, which is saved from whatever that box holds.

The meter counts a tag with the part it wraps, the first part for an opening tag and the last for
a closing one, so the layout costs the parts it serves and nothing is left over to explain.

Prompts used to be templates the user wrote. The import route runs `plain_prompt()` in
[cozy/defaults.py](cozy/defaults.py) over a file exported back then, so it comes in as plain text.

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
- **[docs/](docs/)** is a hand-maintained user manual, so it goes stale silently. Its seven
  guides are numbered in the order a user needs them and each ends by linking the next, so a new
  subject goes into the guide it belongs to rather than onto a page of its own.
  [docs/database-schema.md](docs/database-schema.md) is the schema reference and the only copy of it (tables with
  their real `CREATE TABLE`, the foreign-key map, the migration ledger, the seeded settings) — a
  schema change, a new migration or a new default setting is not finished until it is reflected
  there. The same page covers seeder behaviour, so a change in what gets seeded or restored means
  checking it too. A user-visible
  feature also means checking the README feature list and the matching `docs/` page.

The About page's build string comes from the current Git commit via
[cozy/build_info.py](cozy/build_info.py) (checkouts read `.git`, Docker embeds `.cozy-commit`). It
names the exact build, while the release number in `pyproject.toml` (see Git workflow) names the
release.

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
