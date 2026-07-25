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

THINKING_TAG_PAIRS = (
    ('<think>', '</think>'),
    ('<thinking>', '</thinking>'),
    ('<|thinking|>', '<|/thinking|>'),
)


APPEND_INSTRUCTIONS = (
    "You maintain a running memory summary of an ongoing roleplay so the AI keeps "
    "remembering events that have scrolled out of its context window. You will be given "
    "the CURRENT SUMMARY, any PINNED LINES, and a batch of NEW MESSAGES. The summary has "
    "plenty of room, so ADD to it — do not compress what is already there.\n\n"
    "OUTPUT FORMAT — exactly two headings, each followed by short `- ` bullet lines:\n"
    "STORY SO FAR\n"
    "- <one plot beat per line>\n\n"
    "BONDS\n"
    "- <one relationship per line: where it stands now + the key shared moment behind it>\n\n"
    "RULES:\n"
    "1. STORY SO FAR is ADDITIVE. Output ONLY new bullets for events that actually happen "
    "in the NEW MESSAGES. Do NOT repeat, reword, reorder, or compress the existing story "
    "lines — the system keeps them exactly as they are. If nothing new happened in the "
    "story, output the STORY SO FAR heading with no bullets under it.\n"
    "2. BONDS is CURRENT STATE, one line per relationship. Output the FULL updated BONDS "
    "section: fold any new development into the existing line for that relationship rather "
    "than adding a duplicate, and carry forward relationships the new messages did not touch.\n"
    "3. PRESERVE THE WHY, NOT JUST THE WHAT — keep motivation, emotional stakes, and "
    "circumstances in the new bullets, not hollow one-liners.\n"
    "4. Keep every PINNED LINE EXACTLY as written, word-for-word.\n"
    "Output ONLY the two headings and their bullet lines — no preamble, no commentary."
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
        content = re.sub(r'^[-*••]\s*', '', line).strip()
        if content:
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


def summary_to_text(obj):
    """Render a summary object back to bare ``STORY SO FAR`` / ``BONDS`` text.

    This render feeds the previous summary into the next fold-in and is what
    ``enforce_cap`` measures, so the headings must stay exactly as ``_norm_heading``
    matches them. The frontend's ``summaryToText`` renders the *chat* prompt and
    deliberately annotates the story heading with its ordering; keep these two in step
    on structure, not on that wording.
    """
    story = [line for line in summary_lines(obj) if line.get('section') != 'bonds']
    bonds = [line for line in summary_lines(obj) if line.get('section') == 'bonds']
    out = []
    if story:
        out.append(STORY_HEADING)
        out.extend(f"- {line['text']}" for line in story)
    if bonds:
        if out:
            out.append('')
        out.append(BONDS_HEADING)
        out.extend(f"- {line['text']}" for line in bonds)
    return '\n'.join(out)


def enforce_cap(obj, cap_tokens):
    """Trim the summary so its rendered form fits ``cap_tokens``.

    Drops the lowest-priority unpinned lines first — unpinned ``story`` (oldest first),
    then unpinned ``bonds`` (oldest first). Pinned lines are never dropped. Returns
    ``(trimmed_obj, warning)``. A non-empty input is never reduced to an empty summary:
    if the model's rewrite still exceeds the cap, the last remaining unpinned line is
    shortened as a final safety net.
    """
    obj = {'lines': [dict(line) for line in summary_lines(obj)]}
    warning = ''
    trimmed = False
    if not cap_tokens or cap_tokens <= 0:
        return obj, warning

    def fits():
        return estimate_tokens(summary_to_text(obj)) <= cap_tokens

    if fits():
        return obj, warning

    for target in ('story', 'bonds'):
        i = 0
        while not fits() and i < len(obj['lines']):
            line = obj['lines'][i]
            if line.get('section', 'story') == target and not line.get('pinned'):
                # The LLM call is already the semantic rewrite/tighten pass. This
                # fallback must not erase its final remaining memory line.
                if len(obj['lines']) == 1:
                    break
                obj['lines'].pop(i)
                trimmed = True
                continue
            i += 1
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

    A reply is only usable if it actually compressed — at least one line, and no more
    lines than it was given. Raises ``ValueError`` otherwise so the caller can keep the
    original beats for this batch and carry on rather than failing the whole pass.
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
    if input_count > 0 and len(out) > input_count:
        raise ValueError(
            f'Compressor returned {len(out)} lines for {input_count} inputs — not a compression'
        )
    return out


def build_append_messages(prev_text, batch_messages, pins, cap_tokens):
    """Assemble the ``messages`` array for one *additive* fold-in call.

    Used while the summary is well under its size budget: the model returns only new
    STORY bullets plus the full current BONDS section, and the worker keeps every
    existing story line verbatim. Mirrors ``build_summarizer_messages``'s block shape.
    """
    convo = []
    for msg in batch_messages:
        who = 'User' if msg.get('role') == 'user' else 'Character'
        convo.append(f"{who}: {msg.get('content', '')}")
    convo_text = '\n\n'.join(convo)
    pins_text = '\n'.join(f'- {p}' for p in pins) if pins else '(none)'
    budget = f"under {cap_tokens}" if cap_tokens and cap_tokens > 0 else "within the given"
    user = (
        f"CURRENT SUMMARY:\n{prev_text or '(empty — this is the first batch)'}\n\n"
        f"PINNED LINES (reproduce these EXACTLY, word-for-word):\n{pins_text}\n\n"
        f"NEW MESSAGES TO FOLD IN:\n{convo_text}\n\n"
        f"Add only NEW story bullets for what happens in the new messages, and output the "
        f"full current BONDS section (merging, not duplicating). Do NOT rewrite or compress "
        f"the existing story lines. Keep the whole summary {budget} tokens."
    )
    return [
        {'role': 'system', 'content': APPEND_INSTRUCTIONS},
        {'role': 'user', 'content': user},
    ]


def append_summary(prev_obj, new_obj):
    """Fold an *append-mode* reply into the existing summary.

    STORY is additive: existing story lines are kept verbatim (order preserved) and any
    new story line whose exact text is not already present is appended — so a model that
    re-emits an existing beat, or an identical retried reply, stays idempotent. BONDS is
    current-state: the reply's BONDS section replaces the old one — unless the reply has
    no bonds bullets at all, in which case the previous section is carried forward (a
    model may wrongly generalize the additive story rule to BONDS and omit unchanged
    relationships). Returns fresh copies and never mutates its inputs.
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
    new_bonds = [
        {'section': 'bonds', 'text': line['text'], 'pinned': bool(line.get('pinned'))}
        for line in summary_lines(new_obj)
        if line.get('section') == 'bonds' and line.get('text')
    ]
    if not new_bonds:
        # An empty BONDS section is accepted by the parser (only the heading is
        # required), so replacing here would silently erase every unpinned bond.
        new_bonds = [
            dict(line) for line in summary_lines(prev_obj)
            if line.get('section') == 'bonds'
        ]
    return {'lines': prev_story + new_story + new_bonds}


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
    """
    new_lines = [dict(line) for line in summary_lines(new_obj)]

    def key_of(line):
        return ('bonds' if line.get('section') == 'bonds' else 'story', line.get('text', ''))

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
        restored = {'section': key[0], 'text': key[1], 'pinned': True}
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
    # Insert from the end so earlier insertions cannot shift later targets.
    for fraction, line in sorted(held_pins, key=lambda item: item[0], reverse=True):
        at = round(max(0.0, min(1.0, fraction)) * len(out))
        out.insert(min(at, len(out)), dict(line))
    return out
