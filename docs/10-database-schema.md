# Database Structure

Cozy uses SQLite (`data/cozy_chat.db`) for chat history, user personas, settings, and a lightweight character index. Character card data itself is stored in PNG files on disk — see [Data and Backups](3-data-and-backups.md).

## Schema reference

[schema reference](https://cavecomputing.github.io/cozy/10-database-schema.html) is the only copy of it: every
table with its real `CREATE TABLE`, a map of the three cascading foreign keys,
the indexes, the migration ledger, and the seeded settings. Open it in a
browser. A schema change belongs in `db.html`, not here.

## Seeded data

On first run, the database is seeded with a **Default persona** ("Default
Persona", tagline: "Change me!", `is_default = 1`) and the default settings
listed under "Seeded settings" in [10-database-schema.html](https://cavecomputing.github.io/cozy/10-database-schema.html#seeded).

Auto Summaries are disabled on new chats until the user enables them for that
chat.

## Bundled content

Three kinds of content ship with the repository and are copied into the user's
data at startup, each by its own seeder:

| Content | Source | Bookkeeping |
|---------|--------|-------------|
| Character cards | `default_characters/` | `default_characters_seeded` |
| Prompt presets | `default_prompts/` | none — restored every start |
| Regex presets | `DEFAULT_REGEX_PRESETS` in `cozy/defaults.py` | `default_regex_seeded` |

Both flags flip to `1` whether or not anything was inserted, and are never
reset, so deleting a bundled character or regex preset keeps it deleted. A name
already taken is skipped rather than duplicated.

`default_characters_seeded` starts at `0` only on a fresh install; an upgraded
install starts at `1`, so an existing library is never seeded. `default_regex_seeded`
starts at `0` regardless, because existing installs are owed those presets too.

### Prompt presets are restored on every start

Prompts are the exception to all of the above. `seed_default_prompts()` keeps no
bookkeeping and is deliberately not once-only: on every start it inserts any
bundled preset whose title **and version** are missing from `system_prompts`.
The directory, not the database, is the source of truth for which presets exist.

- Dropping a JSON file into `default_prompts/` makes it appear on the next
  start, on new and existing installs alike.
- Deleting a preset in Settings removes it until the next start, then it is
  back. Removing one for good means deleting its file — under Docker, that
  means rebuilding the image, since only `data/` is mounted.
- A title at a version already present is skipped, never overwritten, so edits
  to a bundled preset survive a restart. Renaming one does not: the original
  pair is missing again, so the bundled copy returns beside the renamed row.
- A file that fails to parse is logged and skipped; the next start retries it.

A preset's title is its **filename** minus `.json`, not the `name` inside it —
the two are kept identical so a hand-import lands under the same title.

A bundled file carries a `version` string, which seeds into the row's `version`
column and shows as a pill beside the name in the prompt picker. It must read as
a number with at most one decimal part — `2`, `2.1` — and must **not** include
the `v`, which the pill adds for you. A file without one seeds blank.

Name and version identify a prompt together, which is what makes a revision a
**version bump inside the existing file** rather than a new file: an install
holding `NanoBear` at `2.1` gains `NanoBear` at `2.2` beside it, and the older
row is left untouched. Editing a file without bumping its version changes
nothing on an install that already has that pair. A second `NanoBear` at `2.1`
is refused, by the seeder and the import route alike.

On a fresh install the bundle also picks the starting selection: seeding sets
`active_system_prompt` to the **standard NanoBear carrying the greatest
version**, which is how a newer house version takes over — bumping
`NanoBear.json` to `2.2` is the whole change. "Standard" is
`STANDARD_NANOBEAR_RE` in `cozy/defaults.py`: a title starting `NanoBear` whose
next word is not `Author`, so the Author variant never claims the default
whatever version it carries. With no standard title in the bundle at all the
pick falls back to the last preset seeded. An install that already has prompts
keeps its own selection, so this only ever applies once.

Versions compare as numbers, not text, so `2.10` ranks above `2.2` — the old
greatest-title rule sorted `v10.0` below `v2.1`, and that caveat is gone.

Seeded characters become ordinary cards on disk — the `characters` index picks
them up on the next listing request, and deleting one deletes it for good.

The bundled regex presets ship **inactive**: seeding deliberately leaves
`active_regex_preset` alone, so bundled rules never silently rewrite replies.

## Database version and backups

The highest version in the migration registry is what "the database version"
means for Cozy — see "The database version" in [10-database-schema.html](https://cavecomputing.github.io/cozy/10-database-schema.html#version).
That number is what a backup carries; how backup and restore treat it is
covered in [Data and Backups](3-data-and-backups.md).
