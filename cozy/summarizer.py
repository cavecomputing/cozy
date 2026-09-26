"""Pure logic for the Auto Summaries feature.

Summary-object parsing/serialization, token estimation, size-cap enforcement, and
the summarizer prompt. Deliberately free of Flask / DB / network so it can be unit
tested in isolation and imported by both ``routes/chats.py`` and
``routes/summaries.py`` without a circular dependency.

The summary object is::

    {"lines": [ {"section": "story" | "bonds", "text": str,
                 "start_msg_id": int, "end_msg_id": int}, ... ]}

rendered to / parsed from a two-heading plain-text form (``STORY SO FAR`` / ``BONDS``
with ``- `` bullets) — the same shape the summarizer model emits.

One batch of messages produces exactly one ``story`` entry, stamped with the message
range it covers. The id pair is story-only and optional: bonds span the whole chat, and
summaries written before ranges existed carry none.
"""

import json
import math
import re

STORY_HEADING = 'STORY SO FAR'
BONDS_HEADING = 'BONDS'

# The summary's size cap is split between its two sections. STORY and BONDS grow for
# different reasons, and a single combined cap let whichever section the model was allowed
# to rewrite absorb all the pressure — which is how BONDS eroded into one-line stubs.
# Reserving a floor for each keeps them independent.
STORY_CAP_FRACTION = 0.6
BONDS_CAP_FRACTION = 0.4

# A batch entry is a compact delta, not permission to consume its whole section. These
# ceilings keep a normal-sized summary useful across many batches; the proportional
# floors below scale them down further when the configured summary cap is small. A bond
# gets more room than a story entry's share suggests because the central relationship
# is rewritten nearly every batch and carries the whole chat's history; the batch total
# stays three full dossiers.
STORY_ENTRY_MAX_TOKENS = 240
BOND_ENTRY_MAX_TOKENS = 200
BONDS_UPDATE_MAX_TOKENS = 600

# How much chat one story entry covers, in estimated tokens. Measured in text rather than
# messages because reply length is set by the prompt, the model, the card and the user —
# a message count that suits one-paragraph replies crams 400-word ones into one entry.
# Mirrored as BATCH_TARGET_TOKENS in static/js/summaries.js, which picks the run's end.
BATCH_TARGET_TOKENS = 3200

THINKING_TAG_PAIRS = (
    ('<think>', '</think>'),
    ('<thinking>', '</thinking>'),
    ('<|thinking|>', '<|/thinking|>'),
)


APPEND_INSTRUCTIONS = (
    "You maintain a running memory summary of an ongoing roleplay so the AI keeps "
    "remembering events that have scrolled out of its context window. You will be given "
    "the CURRENT STORY, the CURRENT BONDS, and a batch of NEW MESSAGES.\n\n"
    "OUTPUT FORMAT — exactly two headings:\n"
    "STORY SO FAR\n"
    "- <one entry covering everything that happened in the new messages>\n\n"
    "BONDS\n"
    "- <Name and Name>: <several sentences of accumulated relationship history>\n\n"
    "STORY RULES:\n"
    "1. Output EXACTLY ONE story line, covering everything that happens in the NEW "
    "MESSAGES, in the order it happened. Never split the batch across several bullets. "
    "If little of note happened, say briefly what the exchange was about — the line is "
    "never left out.\n"
    "2. STORY SO FAR is ADDITIVE. Do NOT repeat, reword, reorder, or compress the "
    "existing story lines — the system keeps them exactly as they are. Your one line "
    "covers only the new messages.\n"
    "3. Write compact memory, not scene prose. Keep only durable events, decisions, "
    "discoveries, promises, injuries, possessions, locations, emotional changes, and "
    "unresolved objectives. Omit dialogue recap, atmosphere, repetition, and decorative "
    "detail. Obey the story-entry token limit in the request.\n\n"
    "BONDS RULES:\n"
    "4. Output a line ONLY for a relationship that is NEW or that CHANGED in the NEW "
    "MESSAGES. Every relationship you do not output is kept exactly as it is — you never "
    "need to restate one to preserve it. Never write a placeholder such as \"not updated\", "
    "\"unchanged\", or \"no change\"; if nothing changed, simply leave that relationship "
    "out. If no relationship changed at all, still output the BONDS heading with no "
    "bullets under it.\n"
    "5. A bond line is a concise CURRENT-STATE DOSSIER, not a scene log or a bare label. "
    "Keep how the two relate now, the few durable moments that still shape their behavior, "
    "what they want from each other, and anything unresolved.\n"
    "6. When an existing relationship changes, rewrite its dossier within the bond-entry "
    "token limit in the request. Preserve durable specifics such as active promises, debts, "
    "injuries, and betrayals, but remove repetition and details that are resolved or no "
    "longer affect the relationship. A dossier must not grow without bound.\n"
    "7. Begin each bond line with the two names exactly as they appear in CURRENT BONDS, "
    "followed by a colon, so the entry is recognised as the same relationship: "
    "\"- Name and Name: …\". Keep the whole dossier on ONE line.\n"
    "8. NOT EVERY INTERACTION IS A BOND. A relationship earns a line only when it is "
    "ongoing and will shape how those two behave later. A single meeting, a passing "
    "exchange, or a one-off kill is a STORY beat — put it under STORY SO FAR and do not "
    "open a bond for it.\n"
    "9. When someone dies or leaves for good, fold that outcome into the closing state of "
    "their dossier rather than keeping a live relationship line for someone who is gone.\n"
    "Output ONLY the two headings and their lines — no preamble, no commentary."
)


def estimate_tokens(text):
    """Heuristic token count mirroring ``estimateTextTokens`` in
    ``static/js/tokenizer.js``: ``max(words*1.3, chars/4)``, min 1 for non-empty text.
    Intentionally approximate — it just has to track the frontend meters."""
    if not text:
        return 0
    s = str(text)
    words = len([w for w in s.split() if w])
    return max(1, math.ceil(max(words * 1.3, len(s) / 4)))


def token_batches(sizes, target):
    """Split per-message token sizes, oldest first, into ``(start, stop)`` index ranges.

    A batch closes on the message that brings it to ``target``, so a message larger than
    the target is a batch of its own. A trailing group under half the target joins the
    batch before it: the browser picks the run's end with its own estimate, and a small
    disagreement must not turn the last message or two into an entry of their own.
    """
    bounds = []
    start = total = 0
    for i, size in enumerate(sizes):
        total += size
        if total >= target:
            bounds.append((start, i + 1))
            start, total = i + 1, 0
    if start < len(sizes):
        if bounds and total * 2 < target:
            bounds[-1] = (bounds[-1][0], len(sizes))
        else:
            bounds.append((start, len(sizes)))
    return bounds


def _norm_heading(line):
    """Uppercase, strip decoration (markdown/brackets/punctuation), collapse spaces."""
    upper = line.upper()
    kept = re.sub(r'[^A-Z ]', ' ', upper)
    return re.sub(r'\s+', ' ', kept).strip()


# Participant separators inside a bond's name head. ``&``, ``/`` and ``,`` bind tightly,
# but a dash only separates when it is surrounded by whitespace — otherwise ``Jean-Luc``
# would be split into two people.
_BOND_PARTICIPANT_SPLIT = re.compile(r'\s*[&/,]\s*|\s+(?:and|[-–—])\s+')
_BULLET_PREFIX = re.compile(r'^[-*••]\s*')


def bond_key(text):
    """Stable identity for a BONDS line: its participants, order-insensitive.

    A bond's text is rewritten as the relationship develops, so the line itself cannot be
    its own identity — matching on exact text is what made an updated dossier look like a
    brand-new relationship and duplicate it. The participants are the part that stays put,
    so they are the key.

    Everything before the first colon is the name head. Legacy bonds written without a
    colon fall back to their whole text, which is stable for as long as they survive
    untouched. Participants are sorted so ``Cerina and Luna`` and ``Luna – Cerina`` are
    recognised as the same relationship.
    """
    raw = '' if text is None else str(text)
    head = raw.split(':', 1)[0] if ':' in raw else raw
    head = re.sub(r'\s+', ' ', head).strip().lower()
    parts = []
    for part in _BOND_PARTICIPANT_SPLIT.split(head):
        cleaned = re.sub(r'[^a-z0-9 ]', '', part or '').strip()
        cleaned = re.sub(r'\s+', ' ', cleaned)
        if cleaned:
            parts.append(cleaned)
    if not parts:
        # Punctuation-only or empty head — fall back to the normalized head so the entry
        # still has *some* identity rather than colliding with every other keyless bond.
        return head
    return ' + '.join(sorted(parts))


def resolve_names(text, char, user):
    """``{{char}}`` and ``{{user}}`` replaced by the names they stand for.

    Stored messages keep the placeholders as written — a greeting copied from a card
    always does — and the chat view resolves them on screen. The summarizer reads the
    stored text, so it gets the same substitution. Case-insensitive, like the frontend's.
    """
    text = re.sub(r'\{\{char\}\}', lambda _: char, '' if text is None else str(text),
                  flags=re.IGNORECASE)
    return re.sub(r'\{\{user\}\}', lambda _: user, text, flags=re.IGNORECASE)


def strip_thinking_content(text):
    """Return the visible response with supported reasoning blocks removed.

    Mirrors the frontend's thinking-tag handling, including the streaming edge case
    where an opening tag has no closing tag yet. Multiple completed blocks are removed
    rather than exposing a later block to the summarizer.
    """
    remaining = '' if text is None else str(text)
    while remaining:
        found = []
        for opening, closing in THINKING_TAG_PAIRS:
            index = remaining.find(opening)
            if index >= 0:
                found.append((index, opening, closing))
        if not found:
            break
        index, opening, closing = min(found, key=lambda item: item[0])
        close_index = remaining.find(closing, index + len(opening))
        if close_index < 0:
            remaining = remaining[:index]
            break
        remaining = remaining[:index] + remaining[close_index + len(closing):]
    return remaining.strip()


def parse_summary(text):
    """Parse the model's ``STORY SO FAR`` / ``BONDS`` output into a summary object.

    Lines before any heading are treated as ``story``. Bullet markers (``- ``, ``* ``,
    ``• ``) are stripped. Blank lines are ignored.

    Inside BONDS only, an unbulleted line continues the previous entry instead of starting
    a new one: a bond is a multi-sentence dossier, and a model that soft-wraps one would
    otherwise have each wrapped fragment stored as its own relationship. STORY keeps
    one-entry-per-line — its bullets are discrete beats, and joining them would merge
    events that happened at different times.
    """
    lines = []
    section = 'story'
    for raw in (text or '').splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = _norm_heading(line)
        if heading == STORY_HEADING:
            section = 'story'
            continue
        if heading == BONDS_HEADING:
            section = 'bonds'
            continue
        bulleted = bool(_BULLET_PREFIX.match(line))
        content = _BULLET_PREFIX.sub('', line).strip()
        if not content:
            continue
        if (section == 'bonds' and not bulleted
                and lines and lines[-1]['section'] == 'bonds'):
            lines[-1]['text'] = f"{lines[-1]['text']} {content}"
            continue
        lines.append({'section': section, 'text': content})
    return {'lines': lines}


def parse_summarizer_output(text):
    """Validate and parse a model response in the required summary format.

    A transport-level success is not enough to retire chat history: the response must
    carry the ``STORY SO FAR`` heading. Everything before that heading is discarded, so a
    model that opens with "Here is the update:" still counts as answering in the format.
    Reasoning blocks are discarded too, so they never become memory. A reply with
    headings but no lines parses to an empty object; ``append_entry_problems`` reports
    the missing story line, so the worker can ask again instead of failing outright.

    A missing ``BONDS`` heading is accepted rather than fatal. Rule 4 tells the model to
    write a bond line only for a relationship that is new or that changed and forbids
    placeholders, so a batch that moved no relationship invites it to drop the empty
    section — failing an obedient reply stalled the run on batches where nothing about
    the cast had changed. Omitting the section already means "no bond updates": every
    stored bond is carried through untouched by ``append_summary``.
    """
    visible = strip_thinking_content(text)
    meaningful = [
        line.strip() for line in visible.splitlines()
        if line.strip() and not line.strip().startswith('```')
    ]
    if not meaningful:
        raise ValueError('Summarizer returned empty content')
    start = next(
        (index for index, line in enumerate(meaningful)
         if _norm_heading(line) == STORY_HEADING),
        None,
    )
    if start is None:
        raise ValueError('Summarizer response is missing the STORY SO FAR heading')
    return parse_summary('\n'.join(meaningful[start:]))


def collapse_story_lines(obj, previous=()):
    """Fold a reply's story lines into the single entry one batch is allowed to produce.

    The prompt asks for exactly one line, but a model that splits the batch into two or
    three bullets anyway must not fail the run: raising here would stall the feature on a
    cosmetic deviation, and the beats are consecutive by construction, so joining them
    loses nothing. Bonds are untouched — they are separate relationships, not a timeline.

    ``previous`` is the stored story text. A multi-line reply that repeats some of it is
    the model copying CURRENT STORY back before its new line, despite rule 2; the copies
    are dropped before the join, or this batch's entry would carry every older one. A
    single-line reply is kept even when it matches an older entry — two quiet batches can
    legitimately read the same.
    """
    story = [line for line in summary_lines(obj) if line.get('section') != 'bonds']
    if len(story) > 1:
        seen = set(previous)
        story = [line for line in story if line.get('text') not in seen]
    bonds = [dict(line) for line in summary_lines(obj) if line.get('section') == 'bonds']
    merged = []
    if story:
        text = ' '.join(line.get('text', '').strip() for line in story if line.get('text'))
        if text:
            merged = [{'section': 'story', 'text': text}]
    return {'lines': merged + bonds}


def summary_lines(obj):
    """Return the normalized list of line dicts from a summary object (never None)."""
    if not isinstance(obj, dict):
        return []
    lines = obj.get('lines')
    return lines if isinstance(lines, list) else []


def parse_summary_json(raw):
    """Load a stored ``summary_json`` string into a normalized summary object.
    Tolerant of empty/invalid input (returns an empty object)."""
    if not raw:
        return {'lines': []}
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return {'lines': []}
    out = []
    for line in summary_lines(obj):
        if not isinstance(line, dict) or not str(line.get('text', '')).strip():
            continue
        section = 'bonds' if line.get('section') == 'bonds' else 'story'
        normalized = {'section': section, 'text': str(line['text']).strip()}
        # Provenance is story-only and optional: summaries written before ranges existed,
        # and every bond, simply carry no ids. Both ends or neither — half a range names
        # nothing the UI can show.
        if section != 'bonds':
            try:
                normalized['start_msg_id'] = int(line['start_msg_id'])
                normalized['end_msg_id'] = int(line['end_msg_id'])
            except (KeyError, TypeError, ValueError):
                normalized.pop('start_msg_id', None)
        out.append(normalized)
    return {'lines': out}


def fork_summary(summary_json, watermark, fork_point, new_ids):
    """The memory a fork taken at message ``fork_point`` inherits: ``(json, watermark)``.

    ``new_ids`` maps each copied message's id to its copy's. A fork taken at or after the
    watermark keeps the whole summary. One taken inside the summarized range keeps only
    the story entries that end at or before the fork point, and no bonds — a bond is a
    dossier of the whole chat, so it may already describe what happens after the fork.
    Ranges are remapped onto the copies; one whose messages were since deleted keeps its
    text and loses its label. ``('', None)`` when nothing carries over.
    """
    whole = bool(watermark) and watermark <= fork_point
    kept, covered = [], watermark if whole else 0
    for line in summary_lines(parse_summary_json(summary_json)):
        start, end = line.get('start_msg_id'), line.get('end_msg_id')
        if not whole and (line['section'] == 'bonds' or end is None or end > fork_point):
            continue
        line = dict(line)
        if end is not None:
            covered = max(covered, end)
            if start in new_ids and end in new_ids:
                line['start_msg_id'], line['end_msg_id'] = new_ids[start], new_ids[end]
            else:
                del line['start_msg_id'], line['end_msg_id']
        kept.append(line)
    # The copies keep their order, so everything summarized is still one prefix of them.
    new_watermark = max((new for old, new in new_ids.items() if old <= covered), default=None)
    if not kept or new_watermark is None:
        return '', None
    return dump_summary_json({'lines': kept}), new_watermark


def dump_summary_json(obj):
    """Serialize a summary object to a compact JSON string for storage."""
    return json.dumps({'lines': summary_lines(obj)}, ensure_ascii=False)


def section_lines(obj, section):
    """The lines belonging to one section, in stored order."""
    if section == 'bonds':
        return [line for line in summary_lines(obj) if line.get('section') == 'bonds']
    return [line for line in summary_lines(obj) if line.get('section') != 'bonds']


def section_to_text(obj, section):
    """Render a single section with its heading, or '' when the section is empty.

    Each section is measured and budgeted on its own, so the heading is included here
    rather than only in the combined render — a section's real cost against its own cap
    includes the heading that has to travel with it.
    """
    lines = section_lines(obj, section)
    if not lines:
        return ''
    heading = BONDS_HEADING if section == 'bonds' else STORY_HEADING
    return '\n'.join([heading] + [f"- {line['text']}" for line in lines])


def summary_to_text(obj):
    """Render a summary object back to bare ``STORY SO FAR`` / ``BONDS`` text.

    This render feeds the previous summary into the next fold-in and is what the chat
    prompt measures, so the headings must stay exactly as ``_norm_heading`` matches them.
    The frontend's ``summaryToText`` renders the *chat* prompt and deliberately annotates
    the story heading with its ordering; keep these two in step on structure, not on that
    wording.
    """
    blocks = [section_to_text(obj, 'story'), section_to_text(obj, 'bonds')]
    return '\n\n'.join(block for block in blocks if block)


def section_cap(cap_tokens, section):
    """The token budget for one section, or 0 when the summary is uncapped.

    STORY and BONDS each get a guaranteed floor rather than competing for one pool. Under
    a single combined cap the section the model was free to rewrite absorbed all the
    pressure, which is what shrank BONDS into one-line stubs.
    """
    if not cap_tokens or cap_tokens <= 0:
        return 0
    fraction = BONDS_CAP_FRACTION if section == 'bonds' else STORY_CAP_FRACTION
    # Never round a positive cap down to zero: a tiny cap must still leave the shortening
    # fallback below something to aim at rather than demanding an empty section.
    return max(1, int(cap_tokens * fraction))


def append_token_limits(cap_tokens):
    """Return ``(story_entry, bond_entry, bonds_update)`` token ceilings.

    The fixed ceilings keep large-context models from writing novella-sized deltas. For a
    small configured summary, proportional limits preserve room for roughly eight story
    entries instead of letting the first one occupy most of the rolling window.
    """
    story_section = section_cap(cap_tokens, 'story')
    bonds_section = section_cap(cap_tokens, 'bonds')
    if not story_section or not bonds_section:
        return STORY_ENTRY_MAX_TOKENS, BOND_ENTRY_MAX_TOKENS, BONDS_UPDATE_MAX_TOKENS

    story_entry = min(
        story_section,
        max(48, min(STORY_ENTRY_MAX_TOKENS, story_section // 8)),
    )
    bond_entry = min(
        bonds_section,
        max(32, min(BOND_ENTRY_MAX_TOKENS, bonds_section // 8)),
    )
    bonds_update = min(
        bonds_section,
        max(bond_entry, min(BONDS_UPDATE_MAX_TOKENS, bonds_section // 3)),
    )
    return story_entry, bond_entry, bonds_update


def _bonds_update_size(obj):
    bonds = section_lines(obj, 'bonds')
    return estimate_tokens('\n'.join(line.get('text', '') for line in bonds))


def append_entry_problems(obj, story_entry_tokens, bond_entry_tokens,
                          bonds_update_tokens):
    """What in a batch delta breaks the per-batch limits, as short notes; [] when nothing.

    The limits stop one verbose reply from monopolizing the rolling summary. The notes
    name the offending line and its size because they are read twice: by the model as
    the reason it is being asked again, and by the user if the second reply misses too.
    """
    problems = []
    story = section_lines(obj, 'story')
    if not story:
        problems.append('no story line')
    for line in story:
        tokens = estimate_tokens(line.get('text', ''))
        if tokens > story_entry_tokens:
            problems.append(
                f'the story line is about {tokens} tokens (limit {story_entry_tokens})'
            )
    for line in section_lines(obj, 'bonds'):
        tokens = estimate_tokens(line.get('text', ''))
        if tokens > bond_entry_tokens:
            names = line.get('text', '').split(':', 1)[0].strip()[:60]
            problems.append(
                f'the bond line for "{names}" is about {tokens} tokens '
                f'(limit {bond_entry_tokens})'
            )
    bonds_tokens = _bonds_update_size(obj)
    if bonds_tokens > bonds_update_tokens:
        problems.append(
            f'the bond lines total about {bonds_tokens} tokens (limit {bonds_update_tokens})'
        )
    return problems


def fit_append_entries(obj, story_entry_tokens, bond_entry_tokens, bonds_update_tokens):
    """Force a batch delta inside the per-batch limits once the model has missed twice.

    Failing the batch instead stalls the whole chat: every later run resends the same
    messages to the same model, and sending waits on the summary. So an over-long line
    keeps the longest prefix that fits, and bond updates beyond the batch total are
    dropped last-first — a dropped update leaves that relationship's stored dossier as it
    was. A missing story line stays missing; the batch simply adds no entry.
    """
    obj = {'lines': [dict(line) for line in summary_lines(obj)]}
    for line in obj['lines']:
        limit = bond_entry_tokens if line.get('section') == 'bonds' else story_entry_tokens
        if estimate_tokens(line['text']) > limit:
            line['text'] = _longest_fitting_prefix(
                line['text'], lambda text: estimate_tokens(text) <= limit
            ) or line['text']
    while _bonds_update_size(obj) > bonds_update_tokens:
        last_bond = max(
            index for index, line in enumerate(obj['lines'])
            if line.get('section') == 'bonds'
        )
        obj['lines'].pop(last_bond)
    return obj


def retry_note(problems):
    """The note appended to a batch request that is being sent again after a bad reply."""
    listed = '\n'.join(f'- {problem}' for problem in problems)
    return (
        f"\n\nYOUR PREVIOUS REPLY WAS REJECTED:\n{listed}\n"
        "Write the reply again in the required format and within every limit. To shorten "
        "a line, drop resolved, repeated, or decorative detail; keep what is current and "
        "unresolved."
    )


def _longest_fitting_prefix(text, fits):
    """The longest prefix of ``text`` that ``fits``, marked with an ellipsis when cut, or
    None when not even one character does. Binary search keeps it small and deterministic.
    """
    best = None
    low, high = 1, len(text)
    while low <= high:
        middle = (low + high) // 2
        candidate = text[:middle].rstrip()
        if middle < len(text):
            candidate += '…'
        if fits(candidate):
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best


def enforce_cap(obj, cap_tokens):
    """Trim the summary so each section fits its own share of ``cap_tokens``.

    This is the only thing standing between a growing summary and its budget, so it is a
    rolling window: ``story`` sheds OLDEST-first, because it is a timeline and the newest
    beats are the ones the roleplay is still acting on. ``bonds`` sheds NEWEST-first, the
    opposite way round: a long-running relationship carries far more history than one
    opened a batch ago, so cap pressure drops the newcomer. Returns ``(trimmed_obj,
    warning)``.

    A non-empty section is never reduced to empty: if its final line still exceeds that
    section's cap, it is shortened as a final safety net.

    Dropping whole entries is the rolling window doing its job, so it warns about nothing;
    it once did, which left every mature chat's memory card permanently in warning. The
    warning is kept for what is not routine: an entry that had to be shortened, or a cap
    too small to hold anything.
    """
    obj = {'lines': [dict(line) for line in summary_lines(obj)]}
    warning = ''
    if not cap_tokens or cap_tokens <= 0:
        return obj, warning

    def section_fits(section):
        return (estimate_tokens(section_to_text(obj, section))
                <= section_cap(cap_tokens, section))

    def fits():
        return section_fits('story') and section_fits('bonds')

    if fits():
        return obj, warning

    for section, oldest_first in (('story', True), ('bonds', False)):
        while not section_fits(section):
            positions = [
                index for index, line in enumerate(obj['lines'])
                if (line.get('section', 'story') == 'bonds') == (section == 'bonds')
            ]
            # Preserve one line per non-empty section. Otherwise an oversized story entry
            # could be discarded merely because a bond kept the whole summary non-empty.
            if len(positions) <= 1:
                break
            obj['lines'].pop(positions[0] if oldest_first else positions[-1])

    # Preserve the longest fitting prefix of each section's final line. Binary search
    # keeps this deterministic fallback small while retaining more context than dropping
    # the whole section would.
    shortened = False
    for section in ('story', 'bonds'):
        positions = [
            index for index, line in enumerate(obj['lines'])
            if (line.get('section', 'story') == 'bonds') == (section == 'bonds')
        ]
        if section_fits(section) or len(positions) != 1:
            continue
        line = obj['lines'][positions[0]]
        original = line['text']

        def fits_in_section(candidate):
            line['text'] = candidate
            return section_fits(section)

        best = _longest_fitting_prefix(original, fits_in_section)
        line['text'] = best or original
        shortened = shortened or bool(best)

    if fits():
        if shortened:
            warning = 'The summary model exceeded the size cap; its last entry was shortened.'
        return obj, warning

    # Reaching here means the section floors above stopped the trim before it fit.
    warning = 'The summary cannot fit the configured size cap without becoming empty.'
    return obj, warning


def build_append_messages(story_text, bonds_text, batch_messages, story_entry_tokens,
                          bond_entry_tokens, bonds_update_tokens):
    """Assemble the ``messages`` array for one *additive* fold-in call.

    The model returns one new STORY entry plus the relationships that are new or that
    changed; the worker keeps every existing story line and every untouched bond verbatim.

    The two sections are shown as separate blocks so the model can tell what it is being
    asked to extend from what it must not touch. The quoted budgets cover only this batch's
    delta, never the whole stored summary.
    """
    convo = []
    for msg in batch_messages:
        # Named speakers let a bond line open with the right names (rule 7); the role is
        # only the fallback for a caller that has none.
        who = msg.get('name') or ('User' if msg.get('role') == 'user' else 'Character')
        convo.append(f"{who}: {msg.get('content', '')}")
    convo_text = '\n\n'.join(convo)
    user = (
        f"CURRENT STORY:\n{story_text or '(empty — this is the first batch)'}\n\n"
        f"CURRENT BONDS:\n{bonds_text or '(none yet)'}\n\n"
        f"NEW MESSAGES TO FOLD IN:\n{convo_text}\n\n"
        f"Write exactly ONE new story entry covering what happens in the new messages, "
        f"using at most {story_entry_tokens} tokens. Output a BONDS line only for each "
        f"relationship that is new or that changed. Each bond line may use at most "
        f"{bond_entry_tokens} tokens, and all BONDS updates together may use at most "
        f"{bonds_update_tokens} tokens. Do NOT rewrite or compress the existing story lines."
    )
    return [
        {'role': 'system', 'content': APPEND_INSTRUCTIONS},
        {'role': 'user', 'content': user},
    ]


# The body of a reply bond line that only declares the relationship unchanged.
_BOND_PLACEHOLDER = re.compile(
    r'^\W*(?:unchanged|no (?:real |significant )?changes?|not updated|no updates?'
    r'|nothing (?:new|changed)|no new developments?'
    r'|(?:none|n/?a|same(?: as before)?)\W*$)',
    re.IGNORECASE,
)


def _is_bond_update(text):
    """True when a reply bond line is a real ``Names: dossier`` update.

    Rules 4 and 7 forbid the alternatives, but a model that slips anyway must not reach
    the merge: a line naming no relationship ("- No changes") would open a junk bond that
    lives forever, and "- A and B: unchanged" would overwrite A and B's whole dossier with
    the placeholder.
    """
    head, colon, body = text.partition(':')
    return bool(colon and head.strip() and body.strip()
                and not _BOND_PLACEHOLDER.match(body.strip()))


def append_summary(prev_obj, new_obj, msg_range=None):
    """Fold an *append-mode* reply into the existing summary.

    STORY is additive: existing story lines are kept verbatim (order preserved). Ranged
    worker batches are distinct even when the model repeats earlier wording; only an exact
    retry of the same text and message range is suppressed. Legacy callers without a range
    remain idempotent by exact text.

    ``msg_range`` is the ``(start_msg_id, end_msg_id)`` of the batch that produced the
    reply, stamped onto the appended story line so the entry can name the messages it
    covers. Bonds carry no range: a dossier spans the whole chat.

    BONDS is merged per relationship, keyed by ``bond_key``. A dossier the reply mentions
    is updated in place, keeping its position; every relationship the reply does not
    mention is carried through untouched. This is what makes a bond safe to leave out of a
    reply: replacing the section wholesale meant re-transcribing every relationship on
    every batch, and each lossy re-copy became the next batch's input. Reply lines that
    fail ``_is_bond_update`` are skipped.

    Returns fresh copies and never mutates its inputs.
    """
    prev_story = [
        dict(line) for line in summary_lines(prev_obj)
        if line.get('section') != 'bonds'
    ]
    if msg_range:
        range_start, range_end = int(msg_range[0]), int(msg_range[1])
        seen_story = {
            (line['text'], line.get('start_msg_id'), line.get('end_msg_id'))
            for line in prev_story
        }
    else:
        range_start = range_end = None
        seen_story = {line['text'] for line in prev_story}
    new_story = []
    for line in summary_lines(new_obj):
        if line.get('section') == 'bonds':
            continue
        text = line.get('text', '')
        story_key = (text, range_start, range_end) if msg_range else text
        if text and story_key not in seen_story:
            fresh_story = {'section': 'story', 'text': text}
            if msg_range:
                fresh_story['start_msg_id'] = range_start
                fresh_story['end_msg_id'] = range_end
            new_story.append(fresh_story)
            seen_story.add(story_key)

    bonds = [
        dict(line) for line in summary_lines(prev_obj)
        if line.get('section') == 'bonds'
    ]
    by_key = {}
    for line in bonds:
        by_key.setdefault(bond_key(line.get('text', '')), line)
    for line in summary_lines(new_obj):
        if line.get('section') != 'bonds':
            continue
        text = line.get('text', '')
        if not _is_bond_update(text):
            continue
        key = bond_key(text)
        existing = by_key.get(key)
        if existing is not None:
            # Same relationship, further along. Keep its slot.
            existing['text'] = text
            continue
        fresh = {'section': 'bonds', 'text': text}
        bonds.append(fresh)
        by_key[key] = fresh
    return {'lines': prev_story + new_story + bonds}
