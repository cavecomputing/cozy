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
| created_at | DATETIME | When the character was first registered.                    |

### chats

Each chat belongs to one character. Deleting a character cascades to all its chats.

| Column       | Type     | Description                              |
|--------------|----------|------------------------------------------|
| id           | INTEGER  | Primary key (auto-increment).            |
| character_id | INTEGER  | FK to `characters.id` (ON DELETE CASCADE).|
| name         | TEXT     | Chat display name.                       |
| created_at   | DATETIME | Creation timestamp.                      |
| updated_at   | DATETIME | Last activity timestamp.                 |

### messages

Individual messages within a chat. Deleting a chat cascades to its messages.

| Column     | Type     | Description                                         |
|------------|----------|-----------------------------------------------------|
| id         | INTEGER  | Primary key (auto-increment).                       |
| chat_id    | INTEGER  | FK to `chats.id` (ON DELETE CASCADE).               |
| role       | TEXT     | `'user'` or `'character'`.                          |
| content    | TEXT     | Message text.                                       |
| persona_id | INTEGER  | Optional FK to `personas.id`. Null if not set.      |
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

### system_prompts

Saved system prompt templates.

| Column     | Type     | Description                     |
|------------|----------|---------------------------------|
| id         | INTEGER  | Primary key (auto-increment).   |
| name       | TEXT     | Prompt display name.            |
| content    | TEXT     | Prompt text.                    |
| created_at | DATETIME | Creation timestamp.             |
| updated_at | DATETIME | Last modification timestamp.    |

## Relationships

```
characters  1──*  chats  1──*  messages  1──*  message_swipes
                                   *──1  personas (optional)
```

All foreign keys use `ON DELETE CASCADE`, so deleting a character removes all its chats, messages, and swipes. Deleting a persona does not delete messages — the `persona_id` is left as a dangling reference.

## Seeded data

On first run, the database is seeded with:

- **Default persona**: "Default User" (tagline: "The brave adventurer", `is_default = 1`)
- **Default system prompt**: "Default" with a generic roleplay instruction
