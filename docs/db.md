# Database Structure

Cozy uses SQLite (`data/cozy_chat.db`) for chat history, user personas, settings, and a lightweight character index. Character card data itself is stored in PNG files on disk — see the `characters` table below.

## Tables

### characters

Lightweight index mapping character IDs to PNG files in `data/characters/`. All character card data (name, description, personality, etc.) is read from the embedded `chara` tEXt chunk in each PNG file at runtime.

| Column     | Type     | Description                                                 |
|------------|----------|-------------------------------------------------------------|
| id         | INTEGER  | Primary key (auto-increment). Referenced by `chats`.        |
| filename   | TEXT     | PNG filename in `data/characters/`. Unique.                 |
| crc        | TEXT     | CRC32 hex of the file contents. Survives renames.           |
| missing    | INTEGER  | `1` if the file was removed from disk, `0` otherwise.       |
| pinned_at  | DATETIME | Pin timestamp, or `NULL`; newest pinned characters sort first. |
| created_at | DATETIME | When the character was first registered.                    |

### chats

Each chat belongs to one character. Deleting a character cascades to all its chats.

| Column       | Type     | Description                              |
|--------------|----------|------------------------------------------|
| id           | INTEGER  | Primary key (auto-increment).            |
| character_id | INTEGER  | FK to `characters.id` (ON DELETE CASCADE).|
| name         | TEXT     | Chat display name. New chats are named for their local creation time, `YYYY-MM-DD:HH-MM-SS`, until renamed. |
| created_at   | DATETIME | Creation timestamp.                      |
| updated_at   | DATETIME | Last activity timestamp.                 |
| active_lorebook_id | INTEGER | Optional standalone lorebook selected for this chat. |
| active_lorebook_embedded | INTEGER | `1` when the character card's embedded lorebook is selected. |
| lorebook_notice_dismissed | INTEGER | `1` once the embedded-lorebook notice has been dismissed. |
| author_note | TEXT | Per-chat Author's Note injected by the prompt builder. |
| summary_enabled | INTEGER | `1` when Auto Summaries are enabled for this chat. |
| summary_json | TEXT | Running structured summary: `{"lines": [{"section": "story"\|"bonds", "text": …, "start_msg_id": …, "end_msg_id": …}]}`. One `story` entry per summarized batch, stamped with the message range it covers; the id pair is story-only and absent on summaries written before it existed. |
| summary_up_to_msg_id | INTEGER | Highest message ID safely folded into the running summary, or `NULL`. |
| summary_status | TEXT | Background summary job state: `idle`, `running`, or `error`. |
| summary_status_detail | TEXT | Progress, warning, or error detail shown in the memory panel. |
| persona_id | INTEGER | Persona this chat is spoken by, or `NULL`. No SQLite foreign key is declared. |

`active_lorebook_id` is an application-level reference; SQLite does not declare
it as a foreign key. Lorebook deletion explicitly clears matching chat values.

`persona_id` is what makes the persona follow the conversation instead of the
browser. It is written whenever a user message is sent and whenever a persona is
selected while the chat is open, and read back when the chat is opened — so a
second machine speaks as the same person rather than as whatever persona it
last selected. A chat that has never recorded one (or whose persona has since
been deleted) falls back to the browser's own last-used persona, then to the
default persona.

### messages

Individual messages within a chat. Deleting a chat cascades to its messages.

| Column     | Type     | Description                                         |
|------------|----------|-----------------------------------------------------|
| id         | INTEGER  | Primary key (auto-increment).                       |
| chat_id    | INTEGER  | FK to `chats.id` (ON DELETE CASCADE).               |
| role       | TEXT     | `'user'` or `'character'`.                          |
| content    | TEXT     | Message text.                                       |
| persona_id | INTEGER  | Optional persona ID. No SQLite foreign key is declared. |
| created_at | DATETIME | Creation timestamp.                                 |

### message_swipes

Alternate versions of a message (swipe variants). Deleting a message cascades to its swipes.

| Column     | Type     | Description                                |
|------------|----------|--------------------------------------------|
| id         | INTEGER  | Primary key (auto-increment).              |
| message_id | INTEGER  | FK to `messages.id` (ON DELETE CASCADE).   |
| content    | TEXT     | Swipe text.                                |
| created_at | DATETIME | Creation timestamp.                        |

### personas

User personas that can be attached to messages.

| Column      | Type     | Description                                     |
|-------------|----------|-------------------------------------------------|
| id          | INTEGER  | Primary key (auto-increment).                   |
| name        | TEXT     | Persona display name.                           |
| tagline     | TEXT     | Short subtitle.                                 |
| description | TEXT     | Full persona description.                       |
| avatar_path | TEXT     | Filename in `data/personas/`. Null if no avatar.|
| is_default  | INTEGER  | `1` for the default persona, `0` otherwise.     |
| created_at  | DATETIME | Creation timestamp.                             |
| updated_at  | DATETIME | Last modification timestamp.                    |

### settings

Key-value store for application settings (API endpoint, model, sampler parameters, etc.).

| Column | Type | Description        |
|--------|------|--------------------|
| key    | TEXT | Primary key.       |
| value  | TEXT | Setting value.     |

### schema_migrations

Ledger of one-time database migrations applied during startup.

| Column     | Type     | Description                              |
|------------|----------|------------------------------------------|
| version    | INTEGER  | Ordered migration version; primary key. |
| name       | TEXT     | Unique, stable migration name.          |
| applied_at | DATETIME | Timestamp recorded after successful application. |

### system_prompts

Saved system prompt templates.

| Column     | Type     | Description                     |
|------------|----------|---------------------------------|
| id         | INTEGER  | Primary key (auto-increment).   |
| name       | TEXT     | Prompt display name.            |
| content    | TEXT     | Main system-prompt template.    |
| post_history_content | TEXT | Paired post-history template. |
| created_at | DATETIME | Creation timestamp.             |
| updated_at | DATETIME | Last modification timestamp.    |

### api_presets

Saved OpenAI-compatible endpoint presets. API keys are stored here, but route responses always mask them before returning preset data to the frontend.

| Column             | Type     | Description                                  |
|--------------------|----------|----------------------------------------------|
| id                 | INTEGER  | Primary key (auto-increment).                |
| name               | TEXT     | Unique preset display name.                  |
| api_endpoint       | TEXT     | OpenAI-compatible base URL.                  |
| api_key            | TEXT     | API key for this preset.                     |
| api_model          | TEXT     | Model name to use with this preset.          |
| context_max_tokens | TEXT     | Token budget for chat history context.       |
| settings_json      | TEXT     | JSON object containing sampler and related preset settings. |
| created_at         | DATETIME | Creation timestamp.                          |

### regex_presets

Named, ordered lists of find/replace filters run over a finished character reply
before it is saved. A filter with `display` set runs at render time instead and
leaves the stored message alone; the key is absent from presets written before
the option existed, where it reads as `false`. See
[Regex output filters](regex.md).

| Column       | Type     | Description                                        |
|--------------|----------|----------------------------------------------------|
| id           | INTEGER  | Primary key (auto-increment).                      |
| name         | TEXT     | Unique preset display name.                        |
| scripts_json | TEXT     | JSON array of `{name, find, replace, flags, display}` filters. Defaults to `'[]'`. |
| created_at   | DATETIME | Creation timestamp.                                |

The selected preset is stored in `settings` under `active_regex_preset`, as the
preset ID or an empty string for no filtering. Deleting the active preset clears
that setting rather than selecting another preset.

### lorebooks

Standalone lorebooks store V2 `character_book` JSON. Embedded character-card lorebooks stay inside PNG card metadata.

| Column     | Type     | Description                     |
|------------|----------|---------------------------------|
| id         | INTEGER  | Primary key (auto-increment).   |
| name       | TEXT     | Lorebook display name.          |
| book       | TEXT     | Serialized `character_book`.    |
| created_at | DATETIME | Creation timestamp.             |
| updated_at | DATETIME | Last modification timestamp.    |

## Relationships

```text
characters             1──*  chats  1──*  messages  1──*  message_swipes

lorebooks  ···  chats.active_lorebook_id       (application-level reference)
personas   ···  messages.persona_id             (application-level reference)
personas   ···  chats.persona_id                (application-level reference)
```

SQLite foreign keys are declared only for chat ownership, message ownership,
and swipe ownership. Each uses `ON DELETE CASCADE`, and
`get_db()` enables enforcement with `PRAGMA foreign_keys=ON` for every connection.
The persona and active-lorebook IDs are currently unconstrained. Deleting a
persona leaves historical message IDs intact, and leaves any chat pointing at it
to fall back to the default persona; deleting a standalone lorebook clears
matching chat selections in the lorebook route.

## Indexes

- `idx_chats_character_created` on (`character_id`, `created_at`, `id`)
- `idx_messages_chat_id` on (`chat_id`, `id`)
- `idx_message_swipes_message` on (`message_id`, `id`)

## Initialization and upgrades

`init_db()` creates missing tables and indexes idempotently, enables WAL mode,
and sets SQLite synchronous mode to `NORMAL`. Existing databases receive newer
columns through `PRAGMA table_info` checks followed by `ALTER TABLE ADD COLUMN`.
After those shape checks, pending entries from the ordered migration registry
run inside a serialized transaction and are recorded in `schema_migrations`.
Repeated startup skips versions already present in the ledger.

The migration ledger currently contains eleven migrations:

1. Retire the historical duplicate-greeting repair.
2. Delete the retired `context_max_messages` setting.
3. Add Auto Summary memory to untouched copies of the old default prompt.
4. Add the narrative preamble to untouched copies of the default prompt.
5. Upgrade untouched default prompts to the V4 paired-template layout.
6. Upgrade untouched post-history templates to the current house style.
7. Rename the stock "Default" prompt to "NanoBear".
8. Remove the character gallery setting, collection tables, and archive column.
9. Backfill `chats.persona_id` from each chat's most recent user message.
10. Delete the retired `summary_compress_batch` setting.
11. Delete the retired `default_prompts_seeded` setting.

Prompt migrations change only known stock templates. Customized prompts are
preserved. The rename in migration 7 is the one exception to matching on
content: it changes the name of a prompt still called "Default" whether or not
its templates were edited, so a user's edits carry over under the new label. It
is skipped when a "NanoBear" prompt already exists.

Migrations 3–7 are the last of their kind. They repair a stock prompt that
`init_db()` used to insert inline; the house prompt now ships as a file in
`default_prompts/` and a revision arrives as a new file, so no future release
rewrites a prompt row. They remain for databases written before them.

## Seeded data

On first run, the database is seeded with:

- **Default persona**: "Default User" (tagline: "The brave adventurer", `is_default = 1`)
- **Default settings**: context token budget (`32768`), visible context meter, an
  empty extra-request-parameters value, blank summarizer endpoint/key/model
  overrides, a 10% summary cap (`summary_cap_pct`), and 10 messages per
  summarizer batch (`summary_trigger_interval`)

Auto Summaries are disabled on new chats until the user enables them for that
chat.

## Bundled content

Three kinds of content ship with the repository and are copied into the user's
data at startup, each by its own seeder:

| Content | Source | Bookkeeping |
|---------|--------|-------------|
| Character cards | `default_characters/` | `default_characters_seeded` |
| Prompt presets | `default_prompts/` | none — restored every start |
| Regex presets | `DEFAULT_REGEX_PRESETS` in `shared.py` | `default_regex_seeded` |

Both flags flip to `1` whether or not anything was inserted, and are never
reset, so deleting a bundled character or regex preset keeps it deleted. A name
already taken is skipped rather than duplicated.

`default_characters_seeded` starts at `0` only on a fresh install; an upgraded
install starts at `1`, so an existing library is never seeded. `default_regex_seeded`
starts at `0` regardless, because existing installs are owed those presets too.

### Prompt presets are restored on every start

Prompts are the exception to all of the above. `seed_default_prompts()` keeps no
bookkeeping and is deliberately not once-only: on every start it inserts any
bundled preset whose title is missing from `system_prompts`. The directory, not
the database, is the source of truth for which presets exist.

- Dropping a JSON file into `default_prompts/` makes it appear on the next
  start, on new and existing installs alike.
- Deleting a preset in Settings removes it until the next start, then it is
  back. Removing one for good means deleting its file — under Docker, that
  means rebuilding the image, since only `data/` is mounted.
- A title already present is skipped, never overwritten, so edits to a bundled
  preset survive a restart. Renaming one does not: the original title is
  missing again, so the bundled copy returns beside the renamed row.
- A file that fails to parse is logged and skipped; the next start retries it.

A preset's title is its **filename** minus `.json`, not the `name` inside it —
the two are kept identical so a hand-import lands under the same title. That is
what makes a revised preset a new file: an install holding `NanoBear v2.0` gains
`NanoBear v2.1` alongside it and the older row is left untouched.

On a fresh install the bundle also picks the starting selection: seeding sets
`active_system_prompt` to the **alphabetically greatest** title, which is how a
newer house version takes over — `NanoBear v2.2.json` outranks `v2.1` with
nothing else to update. Left alone the picker would fall back to whichever
preset sorts *first*, which is a BigBear. An install that already has prompts
keeps its own selection, so this only ever applies once.

Two consequences of the rule are worth knowing before adding a file: a preset
titled after `NanoBear` alphabetically would claim the default, and `NanoBear
v10.0` would sort *below* `v2.1`. `test_the_house_prompt_sorts_last` guards the
first case.

Seeded characters become ordinary cards on disk — the `characters` index picks
them up on the next listing request, and deleting one deletes it for good.

The bundled regex presets ship **inactive**: seeding deliberately leaves
`active_regex_preset` alone, so bundled rules never silently rewrite replies.
