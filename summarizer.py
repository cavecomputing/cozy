"""Pure logic for the Auto Summaries feature.

Summary-object parsing/serialization, token estimation, size-cap enforcement, and
the summarizer prompt. Deliberately free of Flask / DB / network so it can be unit
tested in isolation and imported by both ``routes/chats.py`` and
``routes/summaries.py`` without a circular dependency.

The summary object is::

    {"lines": [ {"section": "story" | "bonds", "text": str, "pinned": bool}, ... ]}

rendered to / parsed from a two-heading plain-text form (``STORY SO FAR`` / ``BONDS``
with ``- `` bullets) — the same shape the summarizer model emits.
"""

import json
import math
import re

STORY_HEADING = 'STORY SO FAR'
BONDS_HEADING = 'BONDS'

# The summary's size cap is split between its two sections. STORY and BONDS grow for
# different reasons and compress differently, and a single combined cap let whichever
# section the model was allowed to rewrite absorb all the pressure — which is how BONDS
# eroded into one-line stubs. Reserving a floor for each keeps them independent.
STORY_CAP_FRACTION = 0.6
BONDS_CAP_FRACTION = 0.4

THINKING_TAG_PAIRS = (
    ('<think>', '</think>'),
    ('<thinking>', '</thinking>'),
    ('<|thinking|>', '<|/thinking|>'),
)


APPEND_INSTRUCTIONS = (
    "You maintain a running memory summary of an ongoing roleplay so the AI keeps "
    "remembering events that have scrolled out of its context window. You will be given "
    "the CURRENT STORY, the CURRENT BONDS, any PINNED LINES, and a batch of NEW MESSAGES.\n\n"
    "OUTPUT FORMAT — exactly two headings:\n"
    "STORY SO FAR\n"
    "- <one new plot beat per line>\n\n"
    "BONDS\n"
    "- <Name and Name>: <several sentences of accumulated relationship history>\n\n"
    "STORY RULES:\n"
    "1. STORY SO FAR is ADDITIVE. Output ONLY new bullets for events that actually happen "
    "in the NEW MESSAGES. Do NOT repeat, reword, reorder, or compress the existing story "
    "lines — the system keeps them exactly as they are. If nothing new happened in the "
    "story, output the STORY SO FAR heading with no bullets under it.\n"
    "2. PRESERVE THE WHY, NOT JUST THE WHAT — keep motivation, emotional stakes, and "
    "circumstances in the new bullets, not hollow one-liners.\n\n"
    "BONDS RULES:\n"
    "3. Output a line ONLY for a relationship that is NEW or that CHANGED in the NEW "
    "MESSAGES. Every relationship you do not output is kept exactly as it is — you never "
    "need to restate one to preserve it. Never write a placeholder such as \"not updated\", "
    "\"unchanged\", or \"no change\"; if nothing changed, simply leave that relationship "
    "out.\n"
    "4. A bond line is an EVOLVING DOSSIER, not a status label. Write several plain "
    "sentences covering how the two know each other, the specific moments that shaped it, "
    "what they want from each other now, and anything unresolved between them. Never reduce "
    "a relationship to a bare event or label like \"First meeting\", \"Killed\", or "
    "\"Protective bond\".\n"
    "5. When you output a relationship that already exists, REPRODUCE ITS EXISTING TEXT and "
    "weave the new development into it. Keep every specific already recorded — names, "
    "injuries, promises, debts, betrayals. The dossier should get longer and richer as the "
    "story advances. Losing detail that was already written down is a failure.\n"
    "6. Begin each bond line with the two names exactly as they appear in CURRENT BONDS, "
    "followed by a colon, so the entry is recognised as the same relationship: "
    "\"- Name and Name: …\". Keep the whole dossier on ONE line.\n"
    "7. NOT EVERY INTERACTION IS A BOND. A relationship earns a line only when it is "
    "ongoing and will shape how those two behave later. A single meeting, a passing "
    "exchange, or a one-off kill is a STORY beat — put it under STORY SO FAR and do not "
    "open a bond for it.\n"
    "8. When someone dies or leaves for good, fold that outcome into the closing state of "
    "their dossier rather than keeping a live relationship line for someone who is gone.\n"
    "9. Keep every PINNED LINE EXACTLY as written, word-for-word.\n"
    "Output ONLY the two headings and their lines — no preamble, no commentary."
)


COMPRESS_INSTRUCTIONS = (
    "You tighten the running memory summary of an ongoing roleplay. You will be given a "
    "few CONSECUTIVE numbered story beats, in chronological order (oldest first). Merge "
    "them into FEWER lines that still carry everything that mattered.\n\n"
    "OUTPUT FORMAT — only `- ` bullet lines, nothing else:\n"
    "- <merged beat>\n\n"
    "RULES:\n"
    "1. Output FEWER lines than you were given. Aim for a single line; use two only when "
    "the beats are genuinely unrelated and cannot be joined without nonsense.\n"
    "2. PRESERVE THE WHY, NOT JUST THE WHAT. Keep motivation, emotional stakes, and "
    "circumstances. Shed length, never meaning — one beat with its real context beats "
    "several hollow one-liners.\n"
    "3. Prioritise RELATIONSHIPS and the specific moments that formed them, then "
    "unresolved threads/promises/debts, then plot events. Squeeze small talk and "
    "moment-to-moment action first.\n"
    "4. KEEP CHRONOLOGICAL ORDER. The input is oldest-first and your output must read in "
    "that same order. Never reorder events.\n"
    "5. Do not invent anything that is not in the given beats, and do not add commentary "
    "about the summarizing itself.\n"
    "Output ONLY the bullet lines — no headings, no preamble, no numbering."
)


BOND_COMPRESS_INSTRUCTIONS = (
    "You tighten ONE relationship entry from the running memory of an ongoing roleplay. "
    "You will be given a single relationship dossier. Rewrite it shorter while keeping "
    "everything that mattered.\n\n"
    "OUTPUT FORMAT — one line, nothing else:\n"
    "- <Name and Name>: <the tightened dossier>\n\n"
    "RULES:\n"
    "1. Output exactly ONE line, and keep it shorter than the one you were given.\n"
    "2. Begin with the SAME names and colon the dossier already starts with. Do not "
    "rename, reorder, or re-describe the participants.\n"
    "3. KEEP EVERY SPECIFIC — names, injuries, promises, debts, betrayals, who did what to "
    "whom. Shed wording, never facts. Merge sentences that circle the same point rather "
    "than dropping details.\n"
    "4. This is ONE relationship. Do not fold in, mention, or invent any other "
    "relationship, and do not add commentary about the summarizing itself.\n"
    "Output ONLY the single bullet line — no heading, no preamble."
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
    brand-new relationship (duplicating it) and made pinning one 409 as soon as the model
    reworded it. The participants are the part that stays put, so they are the key.

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
        lines.append({'section': section, 'text': content, 'pinned': False})
    return {'lines': lines}


def parse_summarizer_output(text):
    """Validate and parse a model response in the required summary format.

    A transport-level success is not enough to retire chat history: the response must
    begin with ``STORY SO FAR``, include ``BONDS``, and contain at least one summary
    line. Reasoning blocks are discarded before validation so they never become memory.
    """
    visible = strip_thinking_content(text)
    meaningful = [
        line.strip() for line in visible.splitlines()
        if line.strip() and not line.strip().startswith('```')
    ]
    if not meaningful:
        raise ValueError('Summarizer returned empty content')
    if _norm_heading(meaningful[0]) != STORY_HEADING:
        raise ValueError('Summarizer response is missing the STORY SO FAR heading')
    if not any(_norm_heading(line) == BONDS_HEADING for line in meaningful[1:]):
        raise ValueError('Summarizer response is missing the BONDS heading')

    parsed = parse_summary('\n'.join(meaningful))
    if not parsed['lines']:
        raise ValueError('Summarizer returned headings without any summary lines')
    return parsed


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
        if isinstance(line, dict) and str(line.get('text', '')).strip():
            out.append({
                'section': 'bonds' if line.get('section') == 'bonds' else 'story',
                'text': str(line['text']).strip(),
                'pinned': bool(line.get('pinned')),
            })
    return {'lines': out}


def dump_summary_json(obj):
    """Serialize a summary object to a compact JSON string for storage."""
    return json.dumps({'lines': summary_lines(obj)}, ensure_ascii=False)


def pinned_texts(obj):
    """Exact text of every pinned line, in order."""
    return [line['text'] for line in summary_lines(obj) if line.get('pinned')]


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


def enforce_cap(obj, cap_tokens):
    """Trim the summary so each section fits its own share of ``cap_tokens``.

    Drops unpinned ``story`` oldest-first — story is a timeline, and the oldest beats are
    the ones the compression pass has already had the most chances to merge. Drops
    unpinned ``bonds`` NEWEST-first, the opposite way round: a long-running relationship
    carries far more history than one opened a batch ago, so cap pressure sheds the
    newcomer. Pinned lines are never dropped. Returns ``(trimmed_obj, warning)``.

    A non-empty input is never reduced to an empty summary: if the model's rewrite still
    exceeds the cap, the last remaining unpinned line is shortened as a final safety net.
    """
    obj = {'lines': [dict(line) for line in summary_lines(obj)]}
    warning = ''
    trimmed = False
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
                if ((line.get('section', 'story') == 'bonds') == (section == 'bonds'))
                and not line.get('pinned')
            ]
            # The LLM call is already the semantic rewrite/tighten pass. This fallback
            # must not erase its final remaining memory line.
            if not positions or len(obj['lines']) == 1:
                break
            obj['lines'].pop(positions[0] if oldest_first else positions[-1])
            trimmed = True

    if fits():
        warning = (
            'The summary model exceeded the size cap; lowest-priority lines were omitted.'
            if trimmed else ''
        )
        return obj, warning

    # Preserve the longest fitting prefix of a lone unpinned line. Binary search keeps
    # this deterministic fallback small while retaining more context than dropping the
    # whole summary would.
    if len(obj['lines']) == 1 and not obj['lines'][0].get('pinned'):
        original = obj['lines'][0]['text']
        best = None
        low, high = 1, len(original)
        while low <= high:
            middle = (low + high) // 2
            candidate = original[:middle].rstrip()
            if middle < len(original):
                candidate += '…'
            obj['lines'][0]['text'] = candidate
            if fits():
                best = candidate
                low = middle + 1
            else:
                high = middle - 1
        if best:
            obj['lines'][0]['text'] = best
            return obj, 'The summary model exceeded the size cap; its last line was shortened.'
        obj['lines'][0]['text'] = original

    if not fits():
        if any(line.get('pinned') for line in obj['lines']):
            warning = 'Pinned lines alone exceed the summary size cap — consider unpinning some.'
        else:
            warning = 'The summary cannot fit the configured size cap without becoming empty.'
    return obj, warning


def build_compress_messages(batch_lines):
    """Assemble the ``messages`` array for one compression call.

    ``batch_lines`` is a list of consecutive story-line texts, oldest first. Only the
    beats themselves are sent — no running summary, no chat messages, no cap arithmetic.
    Keeping each call this narrow is the point: merging a few adjacent beats loses far
    less than re-deriving the whole summary at once.

    The lines are numbered so the model has an explicit ordering to preserve.
    """
    numbered = '\n'.join(f'{i}. {text}' for i, text in enumerate(batch_lines, start=1))
    user = (
        f"STORY BEATS TO MERGE (chronological, oldest first):\n{numbered}\n\n"
        f"Merge these {len(batch_lines)} beats into fewer bullet lines, keeping them in "
        "the same chronological order."
    )
    return [
        {'role': 'system', 'content': COMPRESS_INSTRUCTIONS},
        {'role': 'user', 'content': user},
    ]


def parse_compressed_lines(text, input_count):
    """Parse and validate a compression reply into a list of line texts.

    ``parse_summarizer_output`` cannot be reused here: it demands the two summary
    headings, which a "merge these three beats" call has no reason to emit.

    A reply is only usable if it actually compressed — at least one line, and strictly
    fewer lines than it was given. Raises ``ValueError`` otherwise so the caller can
    preserve the previous summary checkpoint.
    """
    visible = strip_thinking_content(text)
    out = []
    for raw in (visible or '').splitlines():
        line = raw.strip()
        if not line or line.startswith('```'):
            continue
        if _norm_heading(line) in (STORY_HEADING, BONDS_HEADING):
            continue
        # Tolerate a model that numbers its output despite being told not to.
        content = re.sub(r'^\d+[.)]\s*', '', re.sub(r'^[-*••]\s*', '', line)).strip()
        if content:
            out.append(content)
    if not out:
        raise ValueError('Compressor returned no usable lines')
    if input_count > 0 and len(out) >= input_count:
        raise ValueError(
            f'Compressor returned {len(out)} lines for {input_count} inputs — not a compression'
        )
    return out


def build_append_messages(story_text, bonds_text, batch_messages, pins, bonds_cap_tokens):
    """Assemble the ``messages`` array for one *additive* fold-in call.

    The model returns only new STORY bullets plus the relationships that are new or that
    changed; the worker keeps every existing story line and every untouched bond verbatim.

    The two sections are shown as separate blocks so the model can tell what it is being
    asked to extend from what it must not touch, and the token budget quoted here is the
    BONDS budget alone. Quoting the *whole* summary's budget while forbidding story edits
    is what made BONDS the only text the model could shrink.
    """
    convo = []
    for msg in batch_messages:
        who = 'User' if msg.get('role') == 'user' else 'Character'
        convo.append(f"{who}: {msg.get('content', '')}")
    convo_text = '\n\n'.join(convo)
    pins_text = '\n'.join(f'- {p}' for p in pins) if pins else '(none)'
    budget = (
        f"under {bonds_cap_tokens}"
        if bonds_cap_tokens and bonds_cap_tokens > 0 else "within the given"
    )
    user = (
        f"CURRENT STORY:\n{story_text or '(empty — this is the first batch)'}\n\n"
        f"CURRENT BONDS:\n{bonds_text or '(none yet)'}\n\n"
        f"PINNED LINES (reproduce these EXACTLY, word-for-word):\n{pins_text}\n\n"
        f"NEW MESSAGES TO FOLD IN:\n{convo_text}\n\n"
        f"Add only NEW story bullets for what happens in the new messages, and output a "
        f"BONDS line only for each relationship that is new or that changed. Do NOT rewrite "
        f"or compress the existing story lines. Keep the whole BONDS section {budget} tokens."
    )
    return [
        {'role': 'system', 'content': APPEND_INSTRUCTIONS},
        {'role': 'user', 'content': user},
    ]


def build_bond_compress_messages(bond_text):
    """Assemble the ``messages`` array for one bond-compression call.

    Deliberately narrow: exactly one relationship's dossier travels, with no running
    summary and no chat messages. Compressing relationships in batches the way story beats
    are batched would merge unrelated people into a single line.
    """
    user = (
        f"RELATIONSHIP DOSSIER TO TIGHTEN:\n- {bond_text}\n\n"
        "Rewrite this one dossier shorter, keeping the same opening names and every "
        "specific it records."
    )
    return [
        {'role': 'system', 'content': BOND_COMPRESS_INSTRUCTIONS},
        {'role': 'user', 'content': user},
    ]


def parse_compressed_bond(text, original):
    """Parse and validate a bond-compression reply into a single dossier line.

    ``parse_compressed_lines`` cannot be reused: this call returns one line rather than
    fewer-than-N, and it must stay the *same relationship* — a reply that drifted onto
    another pairing would silently overwrite one dossier with another.

    Raises ``ValueError`` unless the reply is a single line, actually shorter than the
    original, and still keyed to the same relationship, so the caller can preserve the
    previous summary checkpoint.
    """
    visible = strip_thinking_content(text)
    out = []
    for raw in (visible or '').splitlines():
        line = raw.strip()
        if not line or line.startswith('```'):
            continue
        if _norm_heading(line) in (STORY_HEADING, BONDS_HEADING):
            continue
        bulleted = bool(_BULLET_PREFIX.match(line))
        content = re.sub(r'^\d+[.)]\s*', '', _BULLET_PREFIX.sub('', line)).strip()
        if not content:
            continue
        # Soft-wrapped prose continues the dossier rather than starting a second one.
        if out and not bulleted:
            out[-1] = f'{out[-1]} {content}'
        else:
            out.append(content)
    if not out:
        raise ValueError('Bond compressor returned no usable lines')
    if len(out) > 1:
        raise ValueError(
            f'Bond compressor returned {len(out)} relationships for 1 input'
        )
    compressed = out[0]
    if len(compressed) >= len(original):
        raise ValueError('Bond compressor did not shorten the dossier')
    if bond_key(compressed) != bond_key(original):
        raise ValueError('Bond compressor changed which relationship the line describes')
    return compressed


def append_summary(prev_obj, new_obj):
    """Fold an *append-mode* reply into the existing summary.

    STORY is additive: existing story lines are kept verbatim (order preserved) and any
    new story line whose exact text is not already present is appended — so a model that
    re-emits an existing beat, or an identical retried reply, stays idempotent.

    BONDS is merged per relationship, keyed by ``bond_key``. A dossier the reply mentions
    is updated in place, keeping its position and pin state; every relationship the reply
    does not mention is carried through untouched. This is what makes a bond safe to leave
    out of a reply: replacing the section wholesale meant re-transcribing every
    relationship on every batch, and each lossy re-copy became the next batch's input.

    Returns fresh copies and never mutates its inputs.
    """
    prev_story = [
        dict(line) for line in summary_lines(prev_obj)
        if line.get('section') != 'bonds'
    ]
    seen = {line['text'] for line in prev_story}
    new_story = []
    for line in summary_lines(new_obj):
        if line.get('section') == 'bonds':
            continue
        text = line.get('text', '')
        if text and text not in seen:
            new_story.append({'section': 'story', 'text': text, 'pinned': bool(line.get('pinned'))})
            seen.add(text)

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
        if not text:
            continue
        key = bond_key(text)
        existing = by_key.get(key)
        if existing is not None:
            # Same relationship, further along. Keep its slot and its pin.
            existing['text'] = text
            continue
        fresh = {'section': 'bonds', 'text': text, 'pinned': bool(line.get('pinned'))}
        bonds.append(fresh)
        by_key[key] = fresh
    return {'lines': prev_story + new_story + bonds}


def merge_pins(new_obj, prev_obj):
    """Carry pinned lines from the previous summary into a freshly parsed one.

    The model is asked to reproduce pinned lines verbatim, but to *guarantee* the
    design's "pinned lines are kept word-for-word" promise we re-assert them: any
    previously-pinned text missing from ``new_obj`` is re-added, and matching lines are
    re-flagged pinned. Pinned lines are placed under their original section.

    A restored pin goes back to its *chronological* slot, not the end of the list:
    STORY is a timeline, so appending a recovered beat after newer ones would misreport
    when it happened. Each missing pin is inserted just after the nearest earlier line
    from ``prev_obj`` that still survives in ``new_obj`` — an order-preserving merge.

    Bonds are identified by ``bond_key`` rather than by their text, because a dossier is
    *expected* to be rewritten as the relationship develops. Matching on exact text would
    read an expanded pinned bond as a missing one and restore the stale copy alongside its
    own update.
    """
    new_lines = [dict(line) for line in summary_lines(new_obj)]

    def key_of(line):
        if line.get('section') == 'bonds':
            return 'bonds', bond_key(line.get('text', ''))
        return 'story', line.get('text', '')

    existing = {key_of(line) for line in new_lines}
    # Walk the previous summary in order, tracking the most recent line that is still
    # present. That line's position in new_lines anchors any pin restored after it.
    anchor = None
    for prev_line in summary_lines(prev_obj):
        key = key_of(prev_line)
        if key in existing:
            if prev_line.get('pinned'):
                for line in new_lines:
                    if key_of(line) == key:
                        line['pinned'] = True
            anchor = key
            continue
        if not prev_line.get('pinned'):
            continue
        # Restore the line's own text, not ``key[1]`` — a bond's key is derived from its
        # participants and is not the dossier the user pinned.
        restored = {'section': key[0], 'text': prev_line.get('text', ''), 'pinned': True}
        # No surviving predecessor means nothing that came before this beat is left,
        # so it belongs at the front — appending would date it after everything.
        # BONDS carries no ordering, so it can simply go last.
        at = len(new_lines) if key[0] == 'bonds' else 0
        if anchor is not None:
            for index, line in enumerate(new_lines):
                if key_of(line) == anchor:
                    at = index + 1
                    break
        new_lines.insert(at, restored)
        existing.add(key)
        anchor = key
    return {'lines': new_lines}


def reinsert_pins_proportionally(story_lines, held_pins):
    """Place held story pins back into a freshly rebuilt story at their old position.

    A rebuild regenerates every story line from the transcript, so a pin's original slot
    cannot be recovered from its text alone. Both the old and new lists are chronological
    over the same conversation, which makes relative position a workable proxy: a pin
    that sat 30% of the way through the old story is inserted 30% of the way through the
    new one.

    ``held_pins`` is a list of ``(fraction, line_dict)`` with ``fraction`` in [0, 1].
    Deliberately a heuristic — a pin can land a beat or two off its true place.
    """
    out = [dict(line) for line in story_lines]

    def key_of(line):
        section = 'bonds' if line.get('section') == 'bonds' else 'story'
        return section, line.get('text', '')

    existing = {}
    for line in out:
        existing.setdefault(key_of(line), []).append(line)

    # Insert from the end so earlier insertions cannot shift later targets.
    for fraction, line in sorted(held_pins, key=lambda item: item[0], reverse=True):
        key = key_of(line)
        if key in existing:
            # A deterministic rebuild can regenerate the pinned beat verbatim. Reuse
            # that chronological placement rather than creating a pinned/unpinned
            # duplicate whose shared text would make later pin reconciliation ambiguous.
            for match in existing[key]:
                match['pinned'] = True
            continue
        at = round(max(0.0, min(1.0, fraction)) * len(out))
        inserted = dict(line)
        out.insert(min(at, len(out)), inserted)
        existing.setdefault(key, []).append(inserted)
    return out
