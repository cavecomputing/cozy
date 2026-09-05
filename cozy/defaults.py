"""Content Cozy ships with: the stock prompt templates, the bundled regex
presets, and the three seeders that put bundled content into a data directory.

Characters and regex presets hand their content over once, tracked by a
``*_seeded`` flag that is never reset. Prompts are the exception and are
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


# ── Default Prompt Builder template ─────────────────────────────────────────
# Reproduces the legacy hardcoded system-block assembly. Conditional blocks
# ({{#var}}…{{/var}}) drop out when the variable is empty, so empty fields
# don't leave dangling section headers.
# Historical stock prompt values are exact-match migration sentinels. Do not
# edit an existing version; add a new version and migration instead.
_DEFAULT_PROMPT_TEMPLATE_V1 = """{{#system_prompt}}[System Instructions]
{{system_prompt}}{{/system_prompt}}

{{#description}}[Character Description]
{{description}}{{/description}}

{{#personality}}[Character Personality]
{{personality}}{{/personality}}

{{#scenario}}[Scenario]
{{scenario}}{{/scenario}}

{{#persona}}[{{user}}'s Persona]
{{persona}}{{/persona}}

{{#mesExamples}}[Example Dialogue]
{{mesExamples}}{{/mesExamples}}

{{#lorebook}}[WORLD INFO / CHARACTER LORE]
{{lorebook}}{{/lorebook}}

{{#author_note}}[AUTHOR'S NOTE]
{{author_note}}{{/author_note}}"""


_DEFAULT_PROMPT_TEMPLATE_V2 = _DEFAULT_PROMPT_TEMPLATE_V1 + """

{{#summary}}[MEMORY — STORY SO FAR]
{{summary}}{{/summary}}"""


# V3 keeps V2's section ordering but adds a narrative-guidance preamble to the
# System Instructions block (roleplay framing, prose style, {{user}} boundaries).
_DEFAULT_PROMPT_TEMPLATE_V3 = """{{#system_prompt}}[System Instructions]
You are participating in a simulated world. Narrate the thoughts, feelings, actions, and dialogue of {{char}} and all side characters except {{user}}—avoid narrating for {{user}}. {{char}} and side characters should act autonomously according to their established traits, personality, and background, with their own opinions, goals, and a capacity for disagreement. {{char}} and all side characters can only know, mention, or act on information they have personally witnessed, learned, or could plausibly deduce.

Respond with 1-2 paragraphs using "show, don't tell", driving the story forward in interesting ways. Keep scenes grounded with nuanced descriptions and natural-sounding dialogue. Use a slow-burn pace while avoiding melodrama and leave openings for {{user}}'s physical or social engagement. You are allowed to explore mature themes that align with the narrative. Vary your prose and avoid repetitive phrases or formulaic descriptions—keep each response fresh and unique. ((OOC: OOC instructions like this are narrative guidance.))
{{system_prompt}}{{/system_prompt}}

{{#description}}[Character Description]
{{description}}{{/description}}

{{#personality}}[Character Personality]
{{personality}}{{/personality}}

{{#scenario}}[Scenario]
{{scenario}}{{/scenario}}

{{#persona}}[{{user}}'s Persona]
{{persona}}{{/persona}}

{{#mesExamples}}[Example Dialogue]
{{mesExamples}}{{/mesExamples}}

{{#lorebook}}[WORLD INFO / CHARACTER LORE]
{{lorebook}}{{/lorebook}}

{{#author_note}}[AUTHOR'S NOTE]
{{author_note}}{{/author_note}}

{{#summary}}[MEMORY — STORY SO FAR]
{{summary}}{{/summary}}"""

# V4 removes the per-turn prose-guidance paragraph from the System Instructions
# block (it moves to the post-history template, V2 below) and title-cases the
# world-info / author-note / memory section headers.
_DEFAULT_PROMPT_TEMPLATE_V4 = """{{#system_prompt}}[System Instructions]
You are participating in a simulated world. Narrate the thoughts, feelings, actions, and dialogue of {{char}} and all side characters except {{user}}—avoid narrating for {{user}}. {{char}} and side characters should act autonomously according to their established traits, personality, and background, with their own opinions, goals, and a capacity for disagreement. {{char}} and all side characters can only know, mention, or act on information they have personally witnessed, learned, or could plausibly deduce.
{{system_prompt}}{{/system_prompt}}

{{#description}}[Character Description]
{{description}}{{/description}}

{{#personality}}[Character Personality]
{{personality}}{{/personality}}

{{#scenario}}[Scenario]
{{scenario}}{{/scenario}}

{{#persona}}[{{user}}'s Persona]
{{persona}}{{/persona}}

{{#mesExamples}}[Example Dialogue]
{{mesExamples}}{{/mesExamples}}

{{#lorebook}}[World Info / Character Lore]
{{lorebook}}{{/lorebook}}

{{#author_note}}[Author's Note]
{{author_note}}{{/author_note}}

{{#summary}}[Memory — Story So Far]
{{summary}}{{/summary}}"""

DEFAULT_PROMPT_TEMPLATE = _DEFAULT_PROMPT_TEMPLATE_V4


# Post-history templates are also versioned migration sentinels — same rule as
# the system templates above: never edit an existing version, add a new one.
_DEFAULT_POST_HISTORY_TEMPLATE_V1 = """{{#post_history_instructions}}[Post-History Instructions]
{{post_history_instructions}}{{/post_history_instructions}}"""


# V2 enforces the house prose style after the chat history and intentionally
# drops {{post_history_instructions}}, so a card's own post-history text is no
# longer rendered by default. The character editor surfaces this omission via
# the "field not used by active prompt" marker.
_DEFAULT_POST_HISTORY_TEMPLATE_V2 = """[Post-History Instructions]
Respond with 1-2 paragraphs using "show, don't tell", driving the story forward in interesting ways. Keep scenes grounded with nuanced descriptions and natural-sounding dialogue. Use a slow-burn pace while avoiding melodrama and leave openings for {{user}}'s physical or social engagement. You are allowed to explore mature themes that align with the narrative. Vary your prose and avoid repetitive phrases or formulaic descriptions—keep each response fresh and unique. ((OOC: OOC instructions like this are narrative guidance.))"""

DEFAULT_POST_HISTORY_TEMPLATE = _DEFAULT_POST_HISTORY_TEMPLATE_V2


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

        conn.execute(
            "UPDATE settings SET value='1' WHERE key='default_characters_seeded'"
        )


# Titles like "NanoBear v2.1": the standard house prompt. Anything with an
# "Author" second word ("NanoBear Author v1") is a variant and never the
# default, even when it sorts above every standard title.
STANDARD_NANOBEAR_RE = re.compile(r'^NanoBear(?!\s+Author\b)')


def seed_default_prompts():
    """Restore every bundled prompt preset missing from system_prompts, each start.

    This is the one seeder that keeps no bookkeeping and is **deliberately not
    once-only**: the directory is the source of truth, so a preset the user
    deletes is back the next time Cozy starts. Removing one for good means
    deleting its file — or, under Docker, rebuilding without it. That is the
    trade for the folder being the whole interface: drop a JSON file in and it
    appears on the next start, on new and existing installs alike.

    A title is the *filename* minus .json, which is what makes a revised preset
    a new file rather than an edit: an install holding "NanoBear v2.0" gains
    "NanoBear v2.1" alongside it and the older row is left exactly as it is.

    A title already present is skipped, never overwritten, so edits to a bundled
    preset survive a restart. Renaming one does not — the original title is
    missing again, and the bundled copy comes back beside it.

    On a fresh install the alphabetically greatest *standard NanoBear* title
    also becomes the active prompt, which is how a new house version takes over
    without a constant to maintain: "NanoBear v2.2" outranks "NanoBear v2.1" on
    its own. An "Author" variant never wins, however it sorts, and with no
    standard title in the bundle the default falls back to the alphabetically
    greatest title overall. One caveat survives from the old rule: "NanoBear
    v10.0" would sort *below* v2.1.
    """
    if not os.path.isdir(shared.BUNDLED_PROMPTS_DIR):
        return

    with get_db() as conn:
        existing = {
            r['name'] for r in
            conn.execute('SELECT name FROM system_prompts').fetchall()
        }
        # No prompts of the user's own means a fresh install, where the bundle
        # also decides which preset starts out active.
        fresh_install = not existing
        default_id = None
        nanobear_id = None

        for filename in sorted(os.listdir(shared.BUNDLED_PROMPTS_DIR)):
            if not filename.lower().endswith('.json') or filename.startswith('.'):
                continue
            title = filename[:-len('.json')]
            if title in existing:
                continue
            source = os.path.join(shared.BUNDLED_PROMPTS_DIR, filename)
            try:
                with open(source, encoding='utf-8') as handle:
                    preset = json.load(handle)
                content = preset['content']
                post_history = preset.get('post_history_content', '')
                description = preset.get('description', '')
                if not isinstance(description, str):
                    description = ''
            except (OSError, ValueError, KeyError, TypeError):
                # A bundled preset is a nicety, not a reason to refuse to start.
                # Log it and move on; the next start retries the file.
                log.exception('Could not seed bundled prompt %s', filename)
                continue

            cursor = conn.execute(
                'INSERT INTO system_prompts (name, description, content, post_history_content) '
                'VALUES (?, ?, ?, ?)',
                (title, description, content, post_history),
            )
            existing.add(title)
            # Titles arrive in ascending order, so the last one to land is the
            # greatest. That is the fresh-install default: "NanoBear v2.2"
            # outranks "NanoBear v2.1" on its own, with nothing to bump here.
            default_id = cursor.lastrowid
            if STANDARD_NANOBEAR_RE.match(title):
                nanobear_id = cursor.lastrowid

        # Left to itself the picker falls back to whichever prompt sorts
        # *first*, which is a BigBear. An existing install already has a
        # selection, and gaining presets must not move it.
        default_id = nanobear_id or default_id
        if fresh_install and default_id is not None:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('active_system_prompt', ?) "
                "ON CONFLICT(key) DO NOTHING",
                (str(default_id),),
            )


# Bundled regex presets, seeded by seed_default_regex_presets(). Small enough to
# live inline rather than in a bundled-file directory like default_prompts/.
#
# All of these are *optional conversions*, not fixes for anything broken. Cozy
# styles German, guillemet and Japanese speech as dialogue natively (see
# static/js/rp-dialogue.js), so none of this is needed to make a reply render
# correctly — these exist for people who simply want ASCII punctuation in the
# text that gets stored. They ship inactive, and double as worked examples of
# what the Regex tab can do.
#
# The quote characters are the entire point and are near-indistinguishable in a
# source listing, so, for reference:
#   U+201E „  German opening mark (sits on the baseline)
#   U+201C “  English opening mark — and German's *closing* mark
#   U+201D ”  English closing mark; models often emit it to close „ as well
#   U+00AB «  U+00BB »  guillemets
#   U+2018 ‘  U+2019 ’  curly singles, also used as apostrophes
#   U+00A0    no-break space   U+202F narrow no-break space (both invisible)
DEFAULT_REGEX_PRESETS = [
    {
        'name': 'German punctuation',
        'filters': [
            {
                # The pair-rebuilding rule: capture what's between the marks and
                # put it back inside straight ones.
                'name': 'Straighten German quotation marks',
                'find': '„([^“”"\\n]*)[“”"]',
                'replace': '"$1"',
                'flags': 'g',
            },
            {
                'name': 'Straighten inward guillemets',
                'find': '»([^«\\n]*)«',
                'replace': '"$1"',
                'flags': 'g',
            },
            {
                # Mop-up for any curly mark the pair rules didn't sit around.
                # Must run last, or it would eat the closers above.
                'name': 'Straighten stray curly quotes',
                'find': '[“”]',
                'replace': '"',
                'flags': 'g',
            },
        ],
    },
    {
        'name': 'French punctuation',
        'filters': [
            {
                # French pads the inside of its guillemets with a no-break
                # space, so the trims are part of the rule rather than optional.
                'name': 'Straighten guillemets',
                'find': '«[   ]*([^»\\n]*?)[   ]*»',
                'replace': '"$1"',
                'flags': 'g',
            },
            {
                # French also spaces off ; : ! ?, which reads as a typo in
                # English-looking text and often renders as a visible gap.
                'name': 'Remove space before ; : ! ?',
                'find': '[   ]+([;:!?])',
                'replace': '$1',
                'flags': 'g',
            },
        ],
    },
    {
        'name': 'Straighten all quote marks',
        'filters': [
            {
                'name': 'German pairs',
                'find': '„([^“”"\\n]*)[“”"]',
                'replace': '"$1"',
                'flags': 'g',
            },
            {
                'name': 'Inward guillemets',
                'find': '»([^«\\n]*)«',
                'replace': '"$1"',
                'flags': 'g',
            },
            {
                'name': 'Outward guillemets',
                'find': '«[   ]*([^»\\n]*?)[   ]*»',
                'replace': '"$1"',
                'flags': 'g',
            },
            {
                'name': 'Leftover curly doubles',
                'find': '[“”]',
                'replace': '"',
                'flags': 'g',
            },
            {
                # Also catches curly apostrophes — that’s the point, but it is
                # why this preset is separate from the language-specific ones.
                'name': 'Curly singles and apostrophes',
                'find': '[‘’]',
                'replace': "'",
                'flags': 'g',
            },
        ],
    },
]


def seed_default_regex_presets():
    """Insert the bundled regex preset into regex_presets, once per data dir.

    Follows seed_default_prompts(): existing installs are owed it too, the
    `default_regex_seeded` setting flips to '1' afterwards whether or not
    anything was inserted, and a name that is already taken is skipped rather
    than duplicated. From then on it is an ordinary row.

    Deliberately does *not* set `active_regex_preset`. The preset ships as a
    worked example to read, not as behaviour that silently rewrites replies —
    filtering stays off until it is picked from the dropdown, and the app then
    keeps using whatever was selected last.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='default_regex_seeded'"
        ).fetchone()
        if row is None or row['value'] != '0':
            return

        existing = {
            r['name'] for r in
            conn.execute('SELECT name FROM regex_presets').fetchall()
        }
        for preset in DEFAULT_REGEX_PRESETS:
            if preset['name'] in existing:
                continue
            conn.execute(
                'INSERT INTO regex_presets (name, scripts_json) VALUES (?, ?)',
                (preset['name'], json.dumps(preset['filters'], ensure_ascii=False)),
            )
            existing.add(preset['name'])

        conn.execute(
            "UPDATE settings SET value='1' WHERE key='default_regex_seeded'"
        )
