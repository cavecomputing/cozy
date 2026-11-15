"""Content Cozy ships with: the two seeders that put bundled content into a
data directory, and what turns a prompt from before the fixed layout into one.

Characters hand their content over once, tracked by a ``*_seeded`` flag that
is never reset. Prompts are the exception and are
restored on every start, which makes default_prompts/ — not the database —
the source of truth for which presets exist.
"""

import json
import logging
import os
import re
import shutil

from cozy import shared
from cozy.shared import get_db

log = logging.getLogger('cozy')


# ── Templates into plain text ───────────────────────────────────────────────
# Cozy places the card, the persona, the lorebook and the memory itself now
# (the layout is in static/js/context-analysis.js), and a prompt is two boxes
# of plain text. A template gives up what it used to place and keeps its words.

# {{#name}}…{{/name}}: a part that was sent only when it had something to say.
_SECTION = re.compile(r'\{\{#(\w+)\}\}(.*?)\{\{/\1\}\}', re.S)
_PLACEHOLDER = re.compile(
    r'\{\{(?:description|personality|scenario|persona|mesExamples|lorebook|author_note'
    r'|summary|system_prompt|post_history_instructions|user_message)\}\}', re.I)
_HEADING = re.compile(r'^[ \t]*\[[^\]\n]*\][ \t]*$', re.M)
_EMPTY_TAG = re.compile(r'<([A-Za-z][\w.:-]*)(?:\s[^<>]*)?>\s*</\1\s*>')
# The headings the stock templates opened on, where a wrapper tag sits now.
_STOCK_HEADING = re.compile(r'\A\[(?:System|Post-History) Instructions\][ \t]*\n', re.I)


def _section_words(match):
    """A section without its placeholder, or nothing when only a heading is left."""
    body = _PLACEHOLDER.sub('', match.group(2))
    return body if _HEADING.sub('', body).strip() else ''


def _plain_text(template, wrapper):
    """One template as the text of its box, and whether that is all it said.

    It is when the template's own words all sat inside ``wrapper``, the tag
    Cozy now adds itself, or when the template placed nothing to begin with.
    Anything else had a layout of its own that the text alone does not carry.
    """
    text, removed = _SECTION.subn(_section_words, template)
    text, placeholders = _PLACEHOLDER.subn('', text)
    removed += placeholders
    emptied = 1
    while emptied:
        text, emptied = _EMPTY_TAG.subn('', text)
        removed += emptied
    whole = re.fullmatch(rf'\s*<{wrapper}(?:\s[^<>]*)?>(.*)</{wrapper}\s*>\s*', text, re.S)
    text = re.sub(rf'</?{wrapper}(?:\s[^<>]*)?>', '', text).strip()
    text = _STOCK_HEADING.sub('', text)
    text = re.sub(r'\n(?:[ \t]*\n){2,}', '\n\n', text).strip()
    return text, bool(whole) or not removed


def plain_prompt(content, post_history):
    """A prompt's two templates as its System and Post-History text.

    Returns ``(content, post_history, complete)``. ``complete`` is False when
    either template had a layout of its own, which is the caller's cue that the
    original is worth keeping. Text that is already plain comes back as it is.
    """
    content, system_complete = _plain_text(content, 'system_instructions')
    post_history, post_complete = _plain_text(post_history, 'post_history_instructions')
    return content, post_history, system_complete and post_complete


def seed_default_characters():
    """Copy the bundled character cards into CHARACTERS_DIR on a fresh install.

    Runs at most once per data directory: the `default_characters_seeded`
    setting is flipped to '1' afterwards whether or not anything was copied, so
    a character the user later deletes stays deleted across restarts. The copies
    are ordinary cards on disk from then on — `_sync_characters` indexes them on
    the next `/api/characters` request, and nothing marks them as special.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='default_characters_seeded'"
        ).fetchone()
        if row is None or row['value'] != '0':
            return

        seeded = []
        if os.path.isdir(shared.BUNDLED_CHARACTERS_DIR):
            os.makedirs(shared.CHARACTERS_DIR, exist_ok=True)
            for filename in sorted(os.listdir(shared.BUNDLED_CHARACTERS_DIR)):
                if not filename.lower().endswith('.png') or filename.startswith('.'):
                    continue
                source = os.path.join(shared.BUNDLED_CHARACTERS_DIR, filename)
                target = os.path.join(shared.CHARACTERS_DIR, filename)
                if not os.path.isfile(source) or os.path.exists(target):
                    continue
                try:
                    shutil.copyfile(source, target)
                except OSError:
                    # A default character is a nicety, not a reason to refuse to
                    # start. Log it and leave the flag unset so a later run retries.
                    log.exception('Could not seed bundled character %s', filename)
                    return
                seeded.append(filename[:-len('.png')])
        if seeded:
            log.info('Added the bundled characters: %s', ', '.join(seeded))

        conn.execute(
            "UPDATE settings SET value='1' WHERE key='default_characters_seeded'"
        )


# Titles like "NanoBear": the standard house prompt. Anything with an "Author"
# second word ("NanoBear Author") is a variant and never the default, however
# it sorts or whatever version it carries.
STANDARD_NANOBEAR_RE = re.compile(r'^NanoBear(?!\s+Author\b)')


# A version is a plain number, optionally with one decimal part: "2", "2.1".
# Shared with the routes, which reject anything else, and mirrored in
# static/js/system-prompts.js. Never holds the "v" — the picker's badge adds it.
VERSION_RE = re.compile(r'^\d+(?:\.\d+)?$')


def version_key(version):
    """Order two preset versions: "2.10" is above "2.2", blank below both.

    Versions are numbers with at most one decimal part, so the two halves
    compare as integers rather than as text — which is what retires the old
    rule's caveat that "v10.0" sorted below "v2.1". Anything unparseable sorts
    lowest instead of raising: the bundle is a nicety and must not stop startup.
    """
    major, _, minor = version.partition('.')
    try:
        return (int(major), int(minor or 0))
    except ValueError:
        return (-1, 0)


def seed_default_prompts():
    """Restore every bundled prompt preset missing from system_prompts, each start.

    This is the one seeder that keeps no bookkeeping and is **deliberately not
    once-only**: the directory is the source of truth, so a preset the user
    deletes is back the next time Cozy starts. Removing one for good means
    deleting its file — or, under Docker, rebuilding without it. That is the
    trade for the folder being the whole interface: drop a JSON file in and it
    appears on the next start, on new and existing installs alike.

    A title is the *filename* minus .json, and a preset is identified by that
    title **together with its version** — the same pair the routes reject a
    duplicate on. So a revision ships by bumping `version` inside the existing
    file: an install holding "NanoBear" 2.1 gains "NanoBear" 2.2 beside it and
    the older row is left exactly as it is. Editing a file without bumping its
    version changes nothing on an install that already has that pair.

    A title *and* version already present is skipped, never overwritten, so
    edits to a bundled preset survive a restart. Renaming one does not — the
    original pair is missing again, and the bundled copy comes back beside it.

    On a fresh install the *standard NanoBear* carrying the greatest version
    also becomes the active prompt, which is how a new house version takes over
    without a constant to maintain: bumping NanoBear.json to 2.2 is the whole
    change. An "Author" variant never wins, whatever version it carries, and
    with no standard title in the bundle the default falls back to the last
    preset seeded.
    """
    if not os.path.isdir(shared.BUNDLED_PROMPTS_DIR):
        return

    with get_db() as conn:
        existing = {
            (r['name'], r['version']) for r in
            conn.execute('SELECT name, version FROM system_prompts').fetchall()
        }
        # No prompts of the user's own means a fresh install, where the bundle
        # also decides which preset starts out active.
        fresh_install = not existing
        last_id = None
        best_nanobear = None  # (version_key, id) of the greatest standard title

        for filename in sorted(os.listdir(shared.BUNDLED_PROMPTS_DIR)):
            if not filename.lower().endswith('.json') or filename.startswith('.'):
                continue
            title = filename[:-len('.json')]
            source = os.path.join(shared.BUNDLED_PROMPTS_DIR, filename)
            try:
                with open(source, encoding='utf-8') as handle:
                    preset = json.load(handle)
                content = preset['content']
                post_history = preset.get('post_history_content', '')
                description = preset.get('description', '')
                if not isinstance(description, str):
                    description = ''
                version = preset.get('version', '')
                # Anything the routes would reject is dropped rather than
                # stored: a bundled file is not validated input, and a bad
                # version would reach the picker's badge and the export
                # filename. Seeding blank is the same as shipping none.
                if not isinstance(version, str) or not VERSION_RE.match(version):
                    if version:
                        log.warning(
                            'Ignoring malformed version %r in bundled prompt %s',
                            version, filename,
                        )
                    version = ''
            except (OSError, ValueError, KeyError, TypeError):
                # A bundled preset is a nicety, not a reason to refuse to start.
                # Log it and move on; the next start retries the file.
                log.exception('Could not seed bundled prompt %s', filename)
                continue

            # Checked only once the version is in hand, since it is half the
            # identity: the same title at a new version is a preset this
            # install has not seen.
            if (title, version) in existing:
                continue

            cursor = conn.execute(
                'INSERT INTO system_prompts (name, version, description, content, post_history_content) '
                'VALUES (?, ?, ?, ?, ?)',
                (title, version, description, content, post_history),
            )
            existing.add((title, version))
            log.info('Added the bundled prompt %s %s', title, version)
            last_id = cursor.lastrowid
            # The greatest version wins, not the last file read — filenames no
            # longer carry the version, so their order says nothing about it.
            key = version_key(version)
            if STANDARD_NANOBEAR_RE.match(title) and (
                best_nanobear is None or key > best_nanobear[0]
            ):
                best_nanobear = (key, cursor.lastrowid)

        # Left to itself the picker falls back to whichever prompt sorts
        # *first*, which is not necessarily a NanoBear. An existing install
        # already has a selection, and gaining presets must not move it.
        default_id = best_nanobear[1] if best_nanobear else last_id
        if fresh_install and default_id is not None:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('active_system_prompt', ?) "
                "ON CONFLICT(key) DO NOTHING",
                (str(default_id),),
            )
