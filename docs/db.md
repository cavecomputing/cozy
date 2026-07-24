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
| archived_at | DATETIME | Archive timestamp, or `NULL` for active characters.        |
| created_at | DATETIME | When the character was first registered.                    |

### character_collections

Named groups used to organize characters.

| Column     | Type     | Description                                      |
|------------|----------|--------------------------------------------------|
| id         | INTEGER  | Primary key (auto-increment).                    |
| name       | TEXT     | Unique collection name.                         |
| icon       | TEXT     | Optional display icon; defaults to an empty string. |
| created_at | DATETIME | Creation timestamp.                             |
| updated_at | DATETIME | Last modification timestamp.                    |

### character_collection_members

Join table between characters and collections.

| Column        | Type     | Description                                      |
|---------------|----------|--------------------------------------------------|
| collection_id | INTEGER  | FK to `character_collections.id` (`ON DELETE CASCADE`). |
| character_id  | INTEGER  | FK to `characters.id` (`ON DELETE CASCADE`).    |
| created_at    | DATETIME | Membership creation timestamp.                  |

The composite primary key is (`collection_id`, `character_id`).

### chats

Each chat belongs to one character. Deleting a character cascades to all its chats.

| Column       | Type     | Description                              |
|--------------|----------|------------------------------------------|
| id           | INTEGER  | Primary key (auto-increment).            |
| character_id | INTEGER  | FK to `characters.id` (ON DELETE CASCADE).|
| name         | TEXT     | Chat display name.                       |
| created_at   | DATETIME | Creation timestamp.                      |
| updated_at   | DATETIME | Last activity timestamp.                 |
| active_lorebook_id | INTEGER | Optional standalone lorebook selected for this chat. |
| active_lorebook_embedded | INTEGER | `1` when the character card's embedded lorebook is selected. |
| lorebook_notice_dismissed | INTEGER | `1` once the embedded-lorebook notice has been dismissed. |
| author_note | TEXT | Per-chat Author's Note injected by the prompt builder. |
| summary_enabled | INTEGER | `1` when Auto Summaries are enabled for this chat. |
| summary_json | TEXT | Running structured summary and per-line pin state. |
| summary_up_to_msg_id | INTEGER | Highest message ID safely folded into the running summary, or `NULL`. |
| summary_status | TEXT | Background summary job state: `idle`, `running`, or `error`. |
| summary_status_detail | TEXT | Progress, warning, or error detail shown in the memory panel. |

`active_lorebook_id` is an application-level reference; SQLite does not declare
it as a foreign key. Lorebook deletion explicitly clears matching chat values.

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
character_collections  1──*  character_collection_members  *──1  characters
characters             1──*  chats  1──*  messages  1──*  message_swipes

lorebooks  ···  chats.active_lorebook_id       (application-level reference)
personas   ···  messages.persona_id             (application-level reference)
```

SQLite foreign keys are declared only for collection membership, chat ownership,
message ownership, and swipe ownership. Each uses `ON DELETE CASCADE`, and
`get_db()` enables enforcement with `PRAGMA foreign_keys=ON` for every connection.
The persona and active-lorebook IDs are currently unconstrained. Deleting a
persona leaves historical message IDs intact; deleting a standalone lorebook
clears matching chat selections in the lorebook route.

## Indexes

- `idx_chats_character_created` on (`character_id`, `created_at`, `id`)
- `idx_char_members_character` on (`character_id`)
- `idx_messages_chat_id` on (`chat_id`, `id`)
- `idx_message_swipes_message` on (`message_id`, `id`)

## Initialization and upgrades

`init_db()` creates missing tables and indexes idempotently, enables WAL mode,
and sets SQLite synchronous mode to `NORMAL`. Existing databases receive newer
columns through `PRAGMA table_info` checks followed by `ALTER TABLE ADD COLUMN`.
After those shape checks, pending entries from the ordered migration registry
run inside a serialized transaction and are recorded in `schema_migrations`.
Repeated startup skips versions already present in the ledger.

The migration ledger currently contains six migrations:

1. Retire the historical duplicate-greeting repair.
2. Delete the retired `context_max_messages` setting.
3. Add Auto Summary memory to untouched copies of the old default prompt.
4. Add the narrative preamble to untouched copies of the default prompt.
5. Upgrade untouched default prompts to the V4 paired-template layout.
6. Upgrade untouched post-history templates to the current house style.

Prompt migrations change only known stock templates. Customized prompts are
preserved.

## Seeded data

On first run, the database is seeded with:

- **Default persona**: "Default User" (tagline: "The brave adventurer", `is_default = 1`)
- **Default system prompt**: "Default" with paired main and post-history Prompt Builder templates
- **Default settings**: context token budget (`32768`), visible context meter, an
  empty extra-request-parameters value, blank summarizer endpoint/key/model
  overrides, a 10% summary cap, and 20 messages per summarizer batch

Auto Summaries are disabled on new chats until the user enables them for that
chat.
