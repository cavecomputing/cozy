#!/usr/bin/env python3
"""Assemble the BigBear prompt presets from the Writer's Block SillyTavern export.

BigBear is a derivative, not a port. The prose is ~88% the upstream author's,
verbatim, but the configuration is ours: 15 fragments selected out of 125, and
the two that govern behavior — narrative mode and pacing — rewritten. Upstream
ships Active Persona with Adaptive Blitz, which renders the player's stated
intent and stops; BigBear runs Director Mode with Adaptive Novel, which takes
the player's line as a stage direction and writes the scene out, protagonist
included. Naming it after Writer's Block would put the author's name on
behavior they didn't choose, hence the separate name. Credit belongs in each
preset's description instead.

SillyTavern presets are a flat pool of ~125 prompt fragments plus a per-character
`prompt_order` that says which are enabled and in what sequence. Cozy has no
equivalent toggle system — a preset here is one `system_prompts` row holding two
templates (`content`, rendered as the leading system message, and
`post_history_content`, rendered as a trailing user message). So each variation
below picks one point in the ST toggle space and flattens it into that pair.

Fragments are copied byte-for-byte out of the export. The exceptions are the
targeted rewrites in `adapt_cot` and `adapt_director`, each of which exists
because a fragment assumes something SillyTavern provides and Cozy does not.

Placement follows the export's own metadata:

  * `injection_position: 0` — sequenced by `prompt_order`, so it lands in the
    system template in that relative order.
  * `injection_position: 1` — absolute depth, injected after the chat history,
    so it lands in the post-history template. Higher depth stacks earlier, which
    is why pacing (depth 2) precedes the depth-0 fragments.

Output matches the payload `/api/system-prompts/<id>/export` produces, so each
file imports straight back through the Prompts panel.

Output lands in default_prompts/, where seed_default_prompts() picks it up.
Regenerate after editing this file:

    uv run python scripts/build_bigbear_presets.py path/to/preset.json
"""

import argparse
import json
import os
import re
import sys

DEFAULT_OUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'default_prompts'
)

SHARED_HEAD = ['De-Positivity']

BASELINES = [
    'Narrative Core',
    'Character Architecture',
    'Dialogue Rules',
    'Anti-Resolution',
    'Explicit Content Instructions',
    'Dynamic Sentence Structure',
]

POV = '3rd Person Omniscient'

# Add-ons are optional in the export. Enhanced World is not really optional
# here: every CoT variant instructs the model to pull rules from
# `<world_and_context>`, and that tag exists only inside this fragment. Shipping
# the CoT without it leaves a dangling reference.
ADDONS = [
    '🌍 Enhanced World🌎',
    '👫Better Side Characters ',
    '🐱Anthropomorphic Realism 🐶',
]

ANTI_SLOP = 'ANTI-SLOP '

# Director Mode is the only narrative mode that hands the protagonist to the
# model; the other two stop the moment {{user}}'s reaction is due. Adaptive
# Novel is the only pacing fragment that tiers length by beat weight, which is
# what keeps a quiet scene from being padded to novel length — Epic Mode's flat
# "12+ paragraphs" cannot do that. It costs more editing (see adapt_pacing):
# every pacing fragment except Epic carries turn-taking clauses.
MODE = 'Director Mode'
PACING = 'Adaptive Novel'
COT = 'Bare Essentials COT'

# Style choices are constrained by the Director/Novel chassis. Two of the
# export's styles carry their own turn-taking rules and were dropped rather than
# edited: Conversational Roleplay ("Mirror their pacing and length", "pass the
# momentum back to {{user}}") and John Steinbeck, whose wide observational
# camera and withheld interiority leave the protagonist un-driven. Each style
# below instructs interiority directly — '# INTERIORITY' in the first three,
# '## INTERNAL VOICE' in Hentai Author — so the model inhabits {{user}} without
# further prompting.
# (preset name, prose style fragment, pacing rewrite or None)
VARIATIONS = [
    ('BigBear - General',     'General Purpose ',                   None),
    ('BigBear - Light Novel', 'Light Novel/Anime Author',           'dense'),
    ('BigBear - Grimdark',    'Joe Abercrombie (Grimdark Comedy) ', None),
    ('BigBear - Smut',        'Hentai Author',                      None),
]

# Card data and Cozy-specific context, slotted where the export puts the
# equivalent ST markers: world info and card fields after the POV block,
# add-ons after the card data, post-history instructions last. Author's note and
# the running summary have no ST counterpart and sit late, where recency helps.
CARD_BLOCK = """{{#lorebook}}[World Info / Character Lore]
{{lorebook}}{{/lorebook}}

{{#persona}}[Protagonist — {{user}}]
{{persona}}{{/persona}}

{{#description}}[Character Description]
{{description}}{{/description}}

{{#personality}}[Character Personality]
{{personality}}{{/personality}}

{{#scenario}}[Scenario]
{{scenario}}{{/scenario}}"""

TAIL_BLOCK = """{{#system_prompt}}[Character Instructions]
{{system_prompt}}{{/system_prompt}}

{{#mesExamples}}[Example Dialogue]
{{mesExamples}}{{/mesExamples}}"""

CLOSING_BLOCK = """{{#author_note}}[Author's Note]
{{author_note}}{{/author_note}}

{{#summary}}[Memory — Story So Far]
{{summary}}{{/summary}}

{{#post_history_instructions}}[Post-History Instructions]
{{post_history_instructions}}{{/post_history_instructions}}"""

# SillyTavern injects these fragments as system messages at depth 0. Cozy has no
# depth injection: post_history_content renders as a *user* message, and
# enforceTrackedAlternation then merges it into the player's own turn. Without a
# delimiter the model cannot tell where the player's text stops and the
# directives begin — which breaks every fragment defined in terms of
# "{{user}}'s input". This header restores the boundary.
DIRECTION_WRAPPER = """{{#user_message}}<direction>
{{user_message}}
</direction>{{/user_message}}

[Standing system directives. Never quote them, summarize them, or render them as story content.]"""


def load_fragments(path):
    """Map fragment name -> content for every prompt in the export.

    Keys are stripped: several fragment names in the export carry a trailing
    space ('Character Architecture ', '3rd Person Omniscient '), and matching on
    that is a needless way for this build to break on the next upstream save.
    Collisions are an error rather than a silent last-one-wins.
    """
    with open(path, encoding='utf-8') as handle:
        preset = json.load(handle)
    fragments = {}
    for prompt in preset.get('prompts', []):
        name = prompt.get('name')
        if name is None:
            continue
        key = name.strip()
        content = prompt.get('content') or ''
        if key in fragments and fragments[key].strip() != content.strip():
            raise ValueError(f'two distinct fragments share the name {key!r}')
        fragments[key] = content
    return fragments


def take(fragments, name):
    """Return a fragment's content verbatim, or fail loudly if it moved."""
    name = name.strip()
    if name not in fragments:
        raise KeyError(
            f'fragment {name!r} not found in the export — it was renamed or '
            f'removed upstream, so the preset build needs updating'
        )
    content = fragments[name].strip()
    if not content:
        raise ValueError(f'fragment {name!r} is empty')
    return content


def substitute(content, replacements, label):
    """Apply exact-match rewrites, failing if the upstream text has drifted."""
    for old, new in replacements:
        if old not in content:
            raise ValueError(
                f'{label}: expected text not found, so the rewrite would be a '
                f'no-op — upstream wording changed:\n  {old[:90]}...'
            )
        content = content.replace(old, new)
    return content


def join(parts):
    """Stitch blocks with one blank line between them, dropping empties."""
    return '\n\n'.join(part for part in parts if part.strip())


def adapt_cot(content):
    """Strip the two CoT artifacts that don't survive the port to Cozy.

    The `(tracker all the way at the end)` line asks for a status block these
    presets deliberately leave out. The outer `<cot>`…`</cot>` wrapper is
    redundant with the `<think>` tags nested inside it — thinking.js keys on
    `<think>` alone (THINKING_TAG_PAIRS), so a model that echoes the wrapper
    would print literal `<cot>` tags into the message body.
    """
    content = re.sub(r'^\s*\(tracker[^)]*\)\s*$', '', content, flags=re.MULTILINE | re.IGNORECASE)
    content = re.sub(r'^\s*</?cot>\s*$', '', content, flags=re.MULTILINE | re.IGNORECASE)
    return re.sub(r'\n{3,}', '\n\n', content).strip()


def adapt_director(content):
    """Reconcile Director Mode with Cozy's persona binding and continuous prose.

    Two assumptions in the upstream fragment don't hold here.

    First, it treats `{{user}}` as the director — a voice outside the story with
    no body. Cozy resolves `{{user}}` to the *persona name*, which is the
    protagonist, so the original line reads as an order never to describe the
    protagonist. The director and the protagonist have to be named separately.

    Second, "Stop writing immediately if the protagonist's reaction is required"
    contradicts the same fragment's "You control ALL characters, including the
    Protagonist", and is the specific instruction that turns a novel into a
    turn-taking exchange. Its replacement has to do more than lift the stop —
    it has to say the direction is a starting point, or the model renders the
    directed beat, considers the instruction discharged, and ends there. That
    also overrides Narrative Core's "Cut mid-action or mid-thought", which is
    scene-break craft advice the model otherwise reads as leave to stop early;
    this fragment wins on recency, sitting in post-history.
    """
    return substitute(content, [
        (
            '### {{user}} Role "DIRECTOR". {{user}} is in Director Mode. '
            '{{user}} is the Director/Narrator, NOT a character.',
            '### Director Mode. The input you receive is DIRECTION from outside '
            'the story — a stage note from the director, never a character\'s '
            'speech or action. {{user}} is the Protagonist, written by you.',
        ),
        (
            "1. ZERO Agency for {{user}}: Do not address {{user}} or describe "
            "{{user}}'s physical presence. {{user}} is an invisible "
            "instructional voice.",
            '1. ZERO Agency for the director: never address the director, quote '
            'their instruction, or place them in the scene. The direction is an '
            'invisible instructional voice and leaves no trace in the prose.',
        ),
        (
            '2. AI Responsibility: You control ALL characters, including the Protagonist.',
            '2. AI Responsibility: You control ALL characters, including the '
            'Protagonist {{user}}. Write {{user}}\'s dialogue, body language, '
            'and interiority from their established personality and history, '
            'exactly as you would any other character.',
        ),
        (
            "4. Stop writing immediately if the protagonist's reaction is required.",
            '4. Carrying out the direction is the START of your response, not '
            'the end of it. The direction sets the scene moving; keep writing '
            'past it. Other characters pursue their own goals, consequences '
            'land, conversations run their course, time advances. Continue '
            'through as many beats as the scene needs and stop only at a '
            'genuine scene break. Never end because the direction has been '
            'satisfied, never end on a prompt for a reply, and never hand the '
            'scene back to ask what happens next.',
        ),
    ], label='Director Mode')


def adapt_pacing(content):
    """Strip Adaptive Novel's turn-taking clauses, keeping its beat tiering.

    The fragment is built for an exchange: it ends the response on an opening
    for {{user}} and forbids moving past their input. Four clauses say so, and
    rule 1's "Stay locked in the current minute" blocks the multi-beat
    continuation Director Mode asks for just as effectively. What survives — and
    the reason for preferring this fragment over Epic Mode — is the paragraph
    budget tiered by beat weight, which stops a quiet scene from being padded
    out to the same length as a climax.

    The header rewrite also settles a conflict the original doesn't have:
    "never fast-forward plot" would otherwise refuse a direction like Director
    Mode's own "Skip to the next morning" example.
    """
    content = substitute(content, [
        (
            'Calibrate length based on narrative. Maintain novel-quality prose '
            'density. Expand the current moment—never fast-forward plot. End '
            'your response with an open ended action requires a response from '
            "{{user}}. Don't move the scene beyond {{user}}'s input",
            'Calibrate length based on narrative. Maintain novel-quality prose '
            'density. Expand the current moment rather than racing the plot — '
            'depth before movement. When the direction explicitly moves time '
            '("skip to the next morning"), make the jump, then settle into the '
            'new moment and expand from there.',
        ),
        (
            '1. Never skip ahead or resolve prematurely. Stay locked in the '
            'current minute.',
            '1. Never resolve prematurely. Let the scene run its course — time '
            'may pass within a response as the beats require.',
        ),
        (
            '3. Anti-Resolution: no narrative dead ends. End on an open ended '
            'response. Allow space for {{user}} input.',
            '3. Anti-Resolution: no narrative dead ends. End on lingering '
            'tension, an unfinished gesture, or a cut mid-motion — never on a '
            'tidy close.',
        ),
        ("4. Don't move the scene beyond {{user}}'s input.", ''),
    ], label='Adaptive Novel')
    return re.sub(r'\n{3,}', '\n\n', content).strip()


def adapt_pacing_for_dense_whitespace(content):
    """Retarget the beat tiers for styles that break a paragraph every line.

    The tiers are measured in paragraphs. Light Novel prose sets "one
    action/thought/line per paragraph. Break often", so a twelve-paragraph
    response can be twelve single lines — the budget reads as satisfied by
    something very short. Recounting the tiers in beats decouples them from the
    line breaks without touching the style's signature whitespace.

    The beat numbers are well below the paragraph numbers they replace. Beats
    are plot movement, so matching them one-for-one would buy length by racing
    the story forward; the length is meant to come from expanding each beat.
    """
    return substitute(content, [
        (
            'Beat Categories:',
            'Beat Categories — this style breaks to a new paragraph after '
            'nearly every line, so these budgets count beats, not paragraphs. '
            'A single beat may run several short paragraphs, and a one-line '
            'paragraph on its own counts for nothing. Length comes from '
            'expanding beats, never from adding more of them:',
        ),
        ('- Climactic (13–15+ paragraphs):', '- Climactic (6–8 beats):'),
        ('- Developmental (10–12 paragraphs):', '- Developmental (4–6 beats):'),
        ('- Transitional (5–6 paragraphs):', '- Transitional (2–3 beats):'),
        ('- Reactive (3–4 paragraphs):', '- Reactive (1–2 beats):'),
    ], label='Adaptive Novel (dense whitespace)')


def download_stem(name):
    """Mirror shared.safe_download_name so filenames match a UI export."""
    stem = ''.join(
        c for c in name
        if (c.isascii() and c.isalnum()) or c in (' ', '-', '_')
    ).strip()
    return re.sub(r'\s+', ' ', stem) or 'prompt'


def build(fragments, style, pacing_rewrite=None):
    system_parts = [take(fragments, name) for name in SHARED_HEAD]
    system_parts.append(take(fragments, style))
    system_parts.extend(take(fragments, name) for name in BASELINES)
    system_parts.append(take(fragments, POV))
    system_parts.append(CARD_BLOCK)
    system_parts.append(take(fragments, 'Enhance Definitions'))
    system_parts.append(TAIL_BLOCK)
    system_parts.extend(take(fragments, name) for name in ADDONS)
    system_parts.append(CLOSING_BLOCK)

    pacing = adapt_pacing(take(fragments, PACING))
    if pacing_rewrite == 'dense':
        pacing = adapt_pacing_for_dense_whitespace(pacing)

    post_parts = [
        DIRECTION_WRAPPER,
        pacing,
        take(fragments, ANTI_SLOP),
        adapt_director(take(fragments, MODE)),
        adapt_cot(take(fragments, COT)),
    ]
    return join(system_parts), join(post_parts)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', help="path to the Writer's Block SillyTavern preset JSON")
    parser.add_argument(
        '-o', '--out', default=DEFAULT_OUT_DIR,
        help='directory to write the presets into (default: default_prompts/)',
    )
    args = parser.parse_args(argv)

    fragments = load_fragments(args.source)
    os.makedirs(args.out, exist_ok=True)

    for name, style, pacing_rewrite in VARIATIONS:
        content, post_history = build(fragments, style, pacing_rewrite)
        payload = {
            'name': name,
            'content': content,
            'post_history_content': post_history,
        }
        filename = f'{download_stem(name)}.json'
        with open(os.path.join(args.out, filename), 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        print(f'{filename}: {len(content)} + {len(post_history)} chars')

    return 0


if __name__ == '__main__':
    sys.exit(main())
