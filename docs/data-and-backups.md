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

## Back up from inside Cozy

**Settings → About → Storage → Backup** does the whole thing without stopping
Cozy:

- **Download backup** writes a `.zip` of the data directory. The database goes
  in as a snapshot taken through SQLite's own backup API, so it is consistent
  even if a reply is being written while the download runs, and the rebuildable
  thumbnail cache is left out. Alongside the data is a small
  `cozy-backup.json` naming the database version the backup was taken at.
- **Restore from backup** asks for confirmation, then **deletes everything in
  the data directory** and unpacks the archive in its place — chats,
  characters, personas, themes, settings and API keys all become the ones in
  the backup. The page reloads when it finishes.

A backup made by a newer version of Cozy is refused, since this build cannot
know what changed in the database since. An older one restores fine: Cozy
migrates it on the way in, exactly as it would at startup, and puts back any
bundled prompt preset that postdates it.

Restoring is not a sandbox. Cozy checks that the archive is one of its own and
that its database opens before it deletes anything, but it does not police what
is inside beyond that — restore archives you made, not ones you were sent.

## Back up by hand

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

## Restore by hand

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
