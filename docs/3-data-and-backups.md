# Data and Backups

## Data location

Cozy stores local data in `data/` by default:

```text
data/
  cozy_chat.db
  cozy_chat.before-update.db
  characters/
  personas/
  themes/
  thumbs/
```

The SQLite database contains chats, settings, API keys, presets, personas,
lorebooks, and character organization. Character card content is stored inside
the PNG files in `characters/`.

`cozy_chat.before-update.db` appears the first time an update changes the
database: it is a copy of the database as it was just before, made by Cozy
itself when it starts. See [Go back after an update](#go-back-after-an-update).

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

- **Download backup** writes a `.zip` of the data directory. It arrives as one
  stream and cannot be resumed or fetched in parts — each request builds a new
  archive, so a download manager that asked for byte ranges would mix two of
  them together. The database goes
  in as a snapshot taken through SQLite's own backup API, so it is consistent
  even if a reply is being written while the download runs, and the rebuildable
  thumbnail cache is left out. Alongside the data is a small
  `cozy-backup.json` naming the database version the backup was taken at.
- **Restore from backup** asks for confirmation, then **deletes everything in
  the data directory** and unpacks the archive in its place — chats,
  characters, personas, themes, settings and API keys all become the ones in
  the backup. The page reloads when it finishes. With a
  [password](7-security.md#require-a-password) set, devices stay signed in.
  The old data is only deleted once the backup is fully in place, so the disk
  needs room for the backup unpacked beside what is already there, and a
  restore that fails partway puts the old data back and says so. If Cozy is
  stopped in the middle of one, the old data is in a `.restore-…` folder
  inside the data directory.

A backup made by a newer version of Cozy is refused, since this build cannot
know what changed in the database since. An older one restores fine: Cozy
migrates it on the way in, exactly as it would at startup, and puts back any
bundled prompt preset that postdates it.

Restoring is not a sandbox. Cozy checks that the archive is one of its own,
that the disk has room for it unpacked and that its database opens before it
deletes anything, but it does not police what is inside beyond that — restore
archives you made, not ones you were sent.

## Back up by hand

Stop Cozy before copying its data.

Docker:

```bash
docker compose -f docker/compose.yml down
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

## Go back after an update

An update can change the database, and an older version of Cozy cannot read
the changed one. So when Cozy starts after an update that changes it, it first
copies the database to `cozy_chat.before-update.db`, next to `cozy_chat.db`.
Only one copy is kept: the next update that changes the database replaces it.
The copy is not part of **Download backup** and does not count toward
**Your data**.

To go back to the version you ran before the update:

1. Stop Cozy.
2. Move `cozy_chat.db` out of the way, along with `cozy_chat.db-wal` and
   `cozy_chat.db-shm` if they exist.
3. Rename `cozy_chat.before-update.db` to `cozy_chat.db`.
4. Check out the older version of Cozy and start it.

Chats and settings from after the update stay behind in the file you moved
away. Character cards are separate files and are not rolled back. This is a
safety net, not a substitute for a backup: it only reaches back one update.

## Custom data directory

For Python, set `COZY_DATA_DIR` before starting Cozy. See
[Advanced setup](2-advanced-setup.md#use-a-different-data-directory).

For Docker, change the left side of the volume mapping in
`docker/compose.yml`:

```yaml
volumes:
  - /path/to/cozy-data:/data
```

Use an absolute path when the location might otherwise be unclear.

## Private information

API keys are stored as plain text in `cozy_chat.db`. Store backups somewhere
private and do not commit the data directory to Git.
