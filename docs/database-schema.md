# Database reference

This page is for developers who work on Cozy's code. You do not need it to
use Cozy. For where your data is and how to back it up, see
[Where your data is](6-running-cozy.md#where-your-data-is).

Cozy keeps everything except character cards in one SQLite file,
`data/cozy_chat.db`. A character card is a PNG file in `data/characters/`,
and the database holds only an index of those files.

| Fact | Value |
|---|---|
| Tables | 10 |
| Indexes | 3 |
| Database version | 22 |
| Journal mode | WAL |
| `synchronous` | NORMAL |
| `foreign_keys` | ON, set on every connection |

This page is the only copy of the schema, and it is maintained by hand. A
schema change, a new migration or a new default setting is not finished until
it is written here. Every `CREATE TABLE` below was read out of a database that
Cozy had just created.

On this page:

- [How the tables are linked](#how-the-tables-are-linked)
- [Tables](#tables): [`characters`](#characters), [`chats`](#chats),
  [`messages`](#messages), [`message_swipes`](#message_swipes),
  [`personas`](#personas), [`lorebooks`](#lorebooks), [`settings`](#settings),
  [`api_presets`](#api_presets), [`system_prompts`](#system_prompts),
  [`schema_migrations`](#schema_migrations)
- [Indexes](#indexes)
- [Migrations](#migrations)
- [The database version, and backups](#the-database-version-and-backups)
- [What a new database holds](#what-a-new-database-holds)
- [Bundled content](#bundled-content)

## How the tables are linked

Three foreign keys are declared, and all three cascade. Deleting a character
deletes its chats, deleting a chat deletes its messages, and deleting a
message deletes its swipes.

```text
characters -> chats -> messages -> message_swipes
```

Every other link is an id that the application looks up and clears itself.
SQLite does not know about these, so it does not clean them up.

| Column | Points at | When the target is deleted |
|---|---|---|
| `chats.active_lorebook_id` | `lorebooks.id` | Application code sets the column back to `NULL`. |
| `chats.persona_id`, `messages.persona_id` | `personas.id` | The id is left as it is. The persona's name is copied to `messages.persona_name` first, so old messages keep the name they were written under. |
| `chats.parent_chat_id` | `chats.id` | Never cleared. With the parent chat gone, the fork is an ordinary chat with no branch pill. |
| `chats.parent_msg_id`, `chats.fork_msg_id` | `messages.id` | Never cleared. With the message gone, the branch pill has no place to land, so switching branches opens that chat at its newest message. |

Ids are never reused, so an id whose target is gone cannot point at something
else.

## Tables

The tables fall into three groups:

- **Story:** `characters`, `chats`, `messages`, `message_swipes`, `personas`,
  `lorebooks`.
- **Configuration:** `settings`, `api_presets`, `system_prompts`.
- **Bookkeeping:** `schema_migrations`.

In the **Default** column, "none" means the column has no `DEFAULT` clause.

### `characters`

An index, not the card. The card itself is base64 V2 JSON inside a `chara` tEXt
chunk in `data/characters/*.png`, read back through `card_store` at request
time.

| Column | Type | Default | Notes |
|---|---|---|---|
| `id` | INTEGER | none | Primary key, autoincrement. |
| `filename` | TEXT | none | PNG filename under `data/characters/`. NOT NULL, UNIQUE. |
| `crc` | TEXT | none | CRC32 hex of the file, so a rename on disk is recognized as the same card. |
| `missing` | INTEGER | `0` | `1` once the file is gone from disk. |
| `pinned_at` | DATETIME | `NULL` | Pin time, `NULL` when unpinned. Pinned characters list first; the sidebar sorts both groups by name. |
| `archived` | INTEGER | `0` | `1` while archived: the character is listed in the sidebar's folded Archived section. Archiving clears `pinned_at`. |
| `created_at` | DATETIME | `CURRENT_TIMESTAMP` | First registration. |

```sql
CREATE TABLE characters (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    filename   TEXT    NOT NULL UNIQUE,
    crc        TEXT    NOT NULL,
    missing    INTEGER DEFAULT 0,
    pinned_at  DATETIME DEFAULT NULL,
    archived   INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
```

### `chats`

One chat belongs to one character, and carries the per-chat memory state:
Author's Note, lorebook selection, and everything Auto Summaries needs to
resume.

| Column | Type | Default | Notes |
|---|---|---|---|
| `id` | INTEGER | none | Primary key, autoincrement. |
| `character_id` | INTEGER | none | NOT NULL. FK → `characters.id`, ON DELETE CASCADE. |
| `name` | TEXT | none | Named for its local creation time, `YYYY-MM-DD:HH-MM-SS`, until renamed. |
| `created_at` | DATETIME | `CURRENT_TIMESTAMP` | Creation. |
| `updated_at` | DATETIME | `CURRENT_TIMESTAMP` | Last activity; drives sidebar ordering. |
| `active_lorebook_id` | INTEGER | `NULL` | Standalone lorebook chosen for this chat. No declared FK. |
| `active_lorebook_embedded` | INTEGER | `0` | `1` when the card's own embedded lorebook is the one in use. |
| `author_note` | TEXT | `''` | Injected by the prompt builder for this chat only. |
| `summary_enabled` | INTEGER | `0` | Auto Summaries on for this chat. |
| `summary_json` | TEXT | `''` | `{"lines": [{"text": …, "start_msg_id": …, "end_msg_id": …}]}`, oldest first. One entry per summarized batch. |
| `summary_up_to_msg_id` | INTEGER | `NULL` | Highest message id already folded in. |
| `summary_status` | TEXT | `'idle'` | `idle`, `running`, or `error`. |
| `summary_status_detail` | TEXT | `''` | Progress or error text shown in the memory panel. |
| `persona_id` | INTEGER | `NULL` | Persona speaking this chat. No declared FK. |
| `parent_chat_id` | INTEGER | `NULL` | For a fork, the chat it was cut from; `NULL` for any other chat. No declared FK. A fork made at the message this chat was itself cut at names that chat's parent instead, so branches that meet at one message share a parent and a `parent_msg_id`. |
| `parent_msg_id` | INTEGER | `NULL` | The message in the parent the fork was cut at. No declared FK. |
| `fork_msg_id` | INTEGER | `NULL` | This chat's own copy of that message: where its branch pill sits, and where the other branches land when switching to it. No declared FK. |
| `opened_at` | DATETIME | `NULL` | Last time this chat was on screen, on any device. On load Cozy resumes the chat whose `opened_at` or `updated_at` is newest, and switching to a character does the same among its chats; `NULL` until first opened. |

```sql
CREATE TABLE chats (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER NOT NULL,
    name         TEXT    NOT NULL,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    active_lorebook_id INTEGER DEFAULT NULL,
    active_lorebook_embedded INTEGER NOT NULL DEFAULT 0,
    author_note TEXT NOT NULL DEFAULT '',
    summary_enabled INTEGER NOT NULL DEFAULT 0,
    summary_json TEXT NOT NULL DEFAULT '',
    summary_up_to_msg_id INTEGER DEFAULT NULL,
    summary_status TEXT NOT NULL DEFAULT 'idle',
    summary_status_detail TEXT NOT NULL DEFAULT '',
    persona_id INTEGER DEFAULT NULL,
    parent_chat_id INTEGER DEFAULT NULL,
    parent_msg_id INTEGER DEFAULT NULL,
    fork_msg_id INTEGER DEFAULT NULL,
    opened_at DATETIME DEFAULT NULL,
    FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE
)
```

### `messages`

The transcript. `role` is constrained at the database level, so a typo cannot
invent a third speaker.

| Column | Type | Default | Notes |
|---|---|---|---|
| `id` | INTEGER | none | Primary key, autoincrement. |
| `chat_id` | INTEGER | none | NOT NULL. FK → `chats.id`, ON DELETE CASCADE. |
| `role` | TEXT | none | `CHECK(role IN ('user','character'))`. |
| `content` | TEXT | none | The text on show: a copy of the selected swipe's, kept in step by the message and swipe routes, so anything reading a chat need not look at swipes. |
| `persona_id` | INTEGER | `NULL` | Persona this line was spoken by. No declared FK. |
| `persona_name` | TEXT | `NULL` | That persona's name, filled in when the persona is deleted. NULL while it exists, and its current name is shown. |
| `selected_swipe_id` | INTEGER | `NULL` | The `message_swipes.id` on show. NULL means the first, which is where a new message starts. No declared FK. Recorded because text cannot say which: two swipes can read the same. |
| `created_at` | DATETIME | `CURRENT_TIMESTAMP` | Creation. |

```sql
CREATE TABLE messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    role       TEXT    NOT NULL CHECK(role IN ('user','character')),
    content    TEXT    NOT NULL,
    persona_id INTEGER DEFAULT NULL,
    persona_name TEXT DEFAULT NULL,
    selected_swipe_id INTEGER DEFAULT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
)
```

### `message_swipes`

Alternate takes on one message. Every message has at least one, and
`messages.selected_swipe_id` says which is on show. Deleting the message takes
its swipes with it.

| Column | Type | Default | Notes |
|---|---|---|---|
| `id` | INTEGER | none | Primary key, autoincrement. |
| `message_id` | INTEGER | none | NOT NULL. FK → `messages.id`, ON DELETE CASCADE. |
| `content` | TEXT | none | The variant's text. |
| `created_at` | DATETIME | `CURRENT_TIMESTAMP` | Creation; also the swipe order. |
| `edited_at` | DATETIME | `NULL` | When an edit last changed this variant's text, `NULL` if none has. Saving the same text or picking a swipe leaves it alone. Shown as the message's (edited) label, and a fork copies it. |
| `output_tokens` | INTEGER | `NULL` | How many tokens the model wrote for this variant, its thinking included, as its API reported them. `NULL` for your own messages, greetings, imports, a reply stopped before the end, a provider that reports no count, and a variant an edit has rewritten. A fork copies it. |

```sql
CREATE TABLE message_swipes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    content    TEXT    NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    edited_at  DATETIME DEFAULT NULL,
    output_tokens INTEGER DEFAULT NULL,
    FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
)
```

### `personas`

The user's side of the story. Avatars are files in `data/personas/`; only the
path lives here.

| Column | Type | Default | Notes |
|---|---|---|---|
| `id` | INTEGER | none | Primary key, autoincrement. |
| `name` | TEXT | none | NOT NULL. Substituted for `{{user}}`. |
| `tagline` | TEXT | `''` | Short line under the name in the sidebar. |
| `description` | TEXT | `''` | Sent with each request as the persona block. |
| `avatar_path` | TEXT | `NULL` | Filename under `data/personas/`. |
| `is_default` | INTEGER | `0` | `1` on the seeded "User" persona. |
| `created_at` | DATETIME | `CURRENT_TIMESTAMP` | Creation. |
| `updated_at` | DATETIME | `CURRENT_TIMESTAMP` | Last edit; also the avatar cache key. |

```sql
CREATE TABLE personas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    tagline     TEXT    DEFAULT '',
    description TEXT    DEFAULT '',
    avatar_path TEXT    DEFAULT NULL,
    is_default  INTEGER DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
)
```

### `lorebooks`

A whole book (entries, keys and scan settings) serialized into one column, in
the format character cards embed theirs in.

| Column | Type | Default | Notes |
|---|---|---|---|
| `id` | INTEGER | none | Primary key, autoincrement. |
| `name` | TEXT | none | NOT NULL. Shown in the picker. |
| `book` | TEXT | `'{}'` | The book itself as JSON: entries, keys, insertion order, scan depth. |
| `created_at` | DATETIME | `CURRENT_TIMESTAMP` | Creation. |
| `updated_at` | DATETIME | `CURRENT_TIMESTAMP` | Last edit. |

```sql
CREATE TABLE lorebooks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    book        TEXT    NOT NULL DEFAULT '{}',
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
)
```

### `settings`

One row per setting, values always TEXT: `'1'` and `'0'` stand in for booleans.
The API key lives here in plain text and is masked on its way out of the
routes. With `COZY_PASSWORD` set, `secret_key` holds the random half of the
sign-in cookie's signing key: created on the first start with a password, never
sent by any route, left out of backups, and kept by a restore.

| Column | Type | Default | Notes |
|---|---|---|---|
| `key` | TEXT | none | Primary key. |
| `value` | TEXT | none | NOT NULL. Everything is a string, including numbers and flags. |

```sql
CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
```

### `api_presets`

A named connection plus the sampler state that goes with it. Activating one
writes its columns back into `settings`.

| Column | Type | Default | Notes |
|---|---|---|---|
| `id` | INTEGER | none | Primary key, autoincrement. |
| `name` | TEXT | none | NOT NULL, UNIQUE. A clash is a 409, not a second row. |
| `api_endpoint` | TEXT | `''` | OpenAI-compatible base URL, normally ending `/v1`. |
| `api_key` | TEXT | `''` | Stored plain; masked in every response. |
| `api_model` | TEXT | `''` | Model identifier sent with the request. |
| `context_max_tokens` | TEXT | `'32768'` | History budget for this preset. |
| `settings_json` | TEXT | `'{}'` | Every `sampler_*` value, the active-sampler selection, and extra request params. |
| `created_at` | DATETIME | `CURRENT_TIMESTAMP` | Creation; also the dropdown order. |

```sql
CREATE TABLE api_presets (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT NOT NULL UNIQUE,
    api_endpoint         TEXT NOT NULL DEFAULT '',
    api_key              TEXT NOT NULL DEFAULT '',
    api_model            TEXT NOT NULL DEFAULT '',
    context_max_tokens   TEXT NOT NULL DEFAULT '32768',
    settings_json        TEXT NOT NULL DEFAULT '{}',
    created_at           DATETIME DEFAULT CURRENT_TIMESTAMP
)
```

### `system_prompts`

Each prompt is a pair of plain text: `content` is the System prompt, sent in
front of the chat, and `post_history_content` the Post-History prompt, sent
after it. Cozy owns the layout around them, so neither holds tags or
placeholders for the card, the persona or the memory. Bundled prompts are
restored from `cozy/bundled/prompts/` on every start, so a deleted one comes back
and the filename is the title, with the edition in `version`. A revision is a
version bump inside the existing file. **Name and version identify a prompt
together**: two editions of one prompt sit side by side, while the same name at
the same version is refused.

| Column | Type | Default | Notes |
|---|---|---|---|
| `id` | INTEGER | none | Primary key, autoincrement. |
| `name` | TEXT | none | NOT NULL. No UNIQUE constraint. The routes reject a clash with 409 instead, on **name and version together**. |
| `version` | TEXT | `''` | A number, optionally with one decimal part, such as `2` or `2.1`, or blank. Never stores the `v`; the picker's pill adds it. |
| `description` | TEXT | `''` | One line under the selector. |
| `content` | TEXT | `''` | The System prompt, as typed. Sent inside `<system_instructions>`, ahead of the character, persona, lore and memory Cozy places after it. |
| `post_history_content` | TEXT | `''` | The Post-History prompt, as typed. Sent after the history inside `<post_history_instructions>`; travels with its partner through import/export. |
| `created_at` | DATETIME | `CURRENT_TIMESTAMP` | Creation. |
| `updated_at` | DATETIME | `CURRENT_TIMESTAMP` | Last edit. |

```sql
CREATE TABLE system_prompts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    version    TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    content    TEXT NOT NULL DEFAULT '',
    post_history_content TEXT NOT NULL DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
```

### `schema_migrations`

What this database has already had done to it. A recorded version whose name no
longer matches the registry is a hard error at startup, not a warning.

| Column | Type | Default | Notes |
|---|---|---|---|
| `version` | INTEGER | none | Primary key. Must be unique and increasing across the registry. |
| `name` | TEXT | none | NOT NULL, UNIQUE. Stable forever once shipped. |
| `applied_at` | DATETIME | `CURRENT_TIMESTAMP` | NOT NULL. Written after the migration succeeds. |

```sql
CREATE TABLE schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
)
```

## Indexes

All three are composite, and all three serve one screen: the list of a
character's chats and the transcript that opens from it.

| Index | On | Used for |
|---|---|---|
| `idx_chats_character_created` | `chats(character_id, created_at, id)` | Listing one character's chats in order. |
| `idx_messages_chat_id` | `messages(chat_id, id)` | Reading a transcript, and finding its tail. |
| `idx_message_swipes_message` | `message_swipes(message_id, id)` | Collecting one message's variants. |

## Migrations

A new database is created at version 21, which is the baseline, and
`schema_migrations` records it as one row named `baseline`. The migrations in
the registry in `cozy/schema.py` then run on it like on any other database.
The next migration is number 23.

| Version | Name | What it does |
|---|---|---|
| 22 | `drop_summary_bonds` | Deletes the **Bonds** entries from every saved Auto Summary and the `section` key from its story entries, since Auto Summary keeps only the story. |

Each migration runs once, in order, in its own transaction. An entry that has
shipped is never renumbered, renamed or reordered. Cozy refuses to start when
a version recorded in `schema_migrations` has a name that no longer matches
the registry, or when the registry holds a version that is not above the
baseline.

Cozy does not open a database below version 21. The 21 migrations that led up
to the baseline were removed, so nothing in this build can upgrade one. On
such a database Cozy stops at start, changes nothing, and names the last
commit that still has those migrations, `fc337a2`. A database that was
upgraded through them holds their 21 rows in `schema_migrations` where a new
database holds the one `baseline` row. Both are at version 21.

## The database version, and backups

There is no separate version number. The highest entry in the migration
registry is the schema this build understands, or the baseline while the
registry is empty. The highest row in a database's `schema_migrations` table
is the schema that database is at. Both are currently 22.

Two other numbers are not the database version: the `version` in
`pyproject.toml` is the release number, and the build string on the About
page names a Git commit.

A backup carries the database version. `GET /api/backup` zips the data
directory with a `cozy-backup.json` file that names it.
`POST /api/backup/restore` compares that number with the registry:

| The backup's version is | What restore does |
|---|---|
| Below 21 | Refuses with 409, because nothing in this build upgrades it. |
| Lower, and 21 or above | Restores it, then `init_db()` migrates it up. |
| Equal | Restores it as it is. |
| Higher | Refuses with 409, because that backup has migrations this build does not know. |

When Cozy starts and finds an existing database that is behind the registry,
it first copies it to `cozy_chat.before-update.db` in the same folder, through
SQLite's backup API. Migrations only go up, so that copy is the database as
the previous build left it. One copy is kept: the next start that migrates
replaces it. The copy is left out of backups and out of the storage total on
the About page.

A backup has to cover both places data is kept: this database, and the PNG
files in `data/characters/` that hold the card data. The `thumbs/` cache is
rebuilt on demand, so a backup leaves it out.

For the user's side of backup and restore, see
[Back up and restore](6-running-cozy.md#back-up-and-restore).

## What a new database holds

**Settings.** These are the rows in `settings` after the first start of a
fresh install, in the order they are written:

| Key | Value |
|---|---|
| `context_max_tokens` | `'32768'` |
| `show_context_token_meter` | `'0'` |
| `use_character_prompts` | `'0'` |
| `extra_request_params` | `''` |
| `summary_api_endpoint` | `''` |
| `summary_api_key` | `''` |
| `summary_api_model` | `''` |
| `summary_cap_pct` | `'10'` |
| `default_characters_seeded` | `'1'` |
| `setup_pending` | `'1'` |
| `active_system_prompt` | `'2'` |

- `default_characters_seeded` is `'1'` here because the character seeder has
  already run. It is set whether or not anything was inserted, and it is never
  reset. That is why a bundled character you delete stays deleted.
- `setup_pending` is `'1'` only on a fresh install, which makes the page open
  on the first-run setup. Finishing or skipping the setup writes `'0'`, and
  nothing sets it back. A database that had no row for it, from before the
  setup existed, gets `'0'`.
- `active_system_prompt` is the `id` of the selected row in `system_prompts`.
  On a fresh install that is the bundled **NanoBear**.
- With `COZY_PASSWORD` set there is one more row, `secret_key`. See
  [`settings`](#settings).

**One persona.** The `personas` table starts with one row: name "User",
tagline "Change me!", `is_default = 1`.

**Auto Summaries are off.** `chats.summary_enabled` is `0` for every new chat
until the user turns Auto Summary on for that chat.

## Bundled content

Two kinds of content ship with the repository and are copied into the user's
data at startup, each by its own seeder:

| Content | Source | Bookkeeping |
|---------|--------|-------------|
| Character cards | `cozy/bundled/characters/` | `default_characters_seeded` |
| Prompt presets | `cozy/bundled/prompts/` | none: restored every start |

The flag flips to `1` whether or not anything was inserted, and is never
reset, so deleting a bundled character keeps it deleted. A name already taken
is skipped rather than duplicated.

`default_characters_seeded` starts at `0` only on a fresh install; an upgraded
install starts at `1`, so an existing library is never seeded.

### Prompt presets are restored on every start

Prompts are the exception to all of the above. `seed_default_prompts()` keeps no
bookkeeping and is deliberately not once-only: on every start it inserts any
bundled preset whose title **and version** are missing from `system_prompts`.
The directory, not the database, is the source of truth for which presets exist.

- Dropping a JSON file into `cozy/bundled/prompts/` makes it appear on the next
  start, on new and existing installs alike.
- Deleting a preset in Settings removes it until the next start, then it is
  back. Removing one for good means deleting its file. Under Docker, that
  means rebuilding the image, since only `data/` is mounted.
- A title at a version already present is skipped, never overwritten, so edits
  to a bundled preset survive a restart. Renaming one does not: the original
  pair is missing again, so the bundled copy returns beside the renamed row.
- A file that fails to parse is logged and skipped; the next start retries it.

A preset's title is its **filename** minus `.json`, not the `name` inside it.
The two are kept identical so a hand-import lands under the same title.

A bundled file carries a `version` string, which seeds into the row's `version`
column and shows as a pill beside the name in the prompt picker. It must read as
a number with at most one decimal part, such as `2` or `2.1`, and must **not** include
the `v`, which the pill adds for you. A file without one seeds blank.

Name and version identify a prompt together, which is what makes a revision a
**version bump inside the existing file** rather than a new file: an install
holding `NanoBear` at `2.1` gains `NanoBear` at `2.2` beside it, and the older
row is left untouched. Editing a file without bumping its version changes
nothing on an install that already has that pair. A second `NanoBear` at `2.1`
is refused, by the seeder and the import route alike.

On a fresh install the bundle also picks the starting selection: seeding sets
`active_system_prompt` to the **standard NanoBear carrying the greatest
version**, which is how a newer house version takes over: bumping
`NanoBear.json` to `2.2` is the whole change. "Standard" is
`STANDARD_NANOBEAR_RE` in `cozy/defaults.py`: a title starting `NanoBear` whose
next word is not `Author`, so the Author variant never claims the default
whatever version it carries. With no standard title in the bundle at all the
pick falls back to the last preset seeded. An install that already has prompts
keeps its own selection, so this only ever applies once.

Versions compare as numbers, not text, so `2.10` ranks above `2.2`.

Seeded characters become ordinary cards on disk. The `characters` index picks
them up on the next listing request, and deleting one deletes it for good.

---

Back to the [list of all guides](../README.md#documentation).
