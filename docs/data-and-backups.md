# Data and Backups

## Data location

Cozy stores local data in `data/` by default:

```text
data/
  cozy_chat.db
  characters/
  personas/
  themes/
  thumbs/
```

The SQLite database contains chats, settings, API keys, presets, personas,
lorebooks, and character organization. Character card content is stored inside
the PNG files in `characters/`.

`thumbs/` is a cache of downscaled avatar images that Cozy generates from the
files in `characters/` and `personas/` as they are requested. It holds nothing
of its own, so it is safe to delete at any time and safe to leave out of a
backup — Cozy rebuilds what it needs. Deleting it while Cozy is running is fine
too.

Settings → About shows a current storage breakdown for the database, character
cards, persona avatars, custom themes, and any other files in the data directory.
Its **Your data** total covers the durable files that belong in a backup. The
rebuildable thumbnail cache is shown separately and is not included in that
total.

Docker mounts the same host `data/` directory at `/data` inside the container.

## Back up

Stop Cozy before copying its data.

Docker:

```bash
docker compose -f docker/docker-compose.yml down
```

Python:

```text
Press Ctrl+C in the terminal running Cozy.
```

Copy the entire `data/` directory to a safe location. Do not copy only
`cozy_chat.db`; SQLite may also use `cozy_chat.db-wal` and
`cozy_chat.db-shm`.

## Restore

1. Stop Cozy.
2. Move the current `data/` directory out of the way.
3. Put the backed-up `data/` directory in the repository root.
4. Start Cozy.

Keep the old directory until the restored copy has opened correctly.

## Custom data directory

For Python, set `COZY_DATA_DIR` before starting Cozy. See
[Running Cozy](run.md#use-a-different-data-directory).

For Docker, change the left side of the volume mapping in
`docker/docker-compose.yml`:

```yaml
volumes:
  - /path/to/cozy-data:/data
```

Use an absolute path when the location might otherwise be unclear.

## Private information

API keys are stored as plain text in `cozy_chat.db`. Store backups somewhere
private and do not commit the data directory to Git.
