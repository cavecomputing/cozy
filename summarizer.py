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


SUMMARY_INSTRUCTIONS = (
    "You maintain a running memory summary of an ongoing roleplay so the AI keeps "
    "remembering events that have scrolled out of its context window. You will be given "
    "the CURRENT SUMMARY, any PINNED LINES, and a batch of NEW MESSAGES. Blend them into "
    "one updated summary that covers everything so far.\n\n"
    "OUTPUT FORMAT — exactly two headings, each followed by short `- ` bullet lines:\n"
    "STORY SO FAR\n"
    "- <one plot beat per line>\n\n"
    "BONDS\n"
    "- <one relationship per line: where it stands now + the key shared moment behind it>\n\n"
    "RULES:\n"
    "1. Prioritise RELATIONSHIPS and the specific moments that formed them, then "
    "unresolved threads/promises/debts, then plot events (as the reason for a bond, not "
    "for their own sake). Squeeze or drop small talk and moment-to-moment action first.\n"
    "2. PRESERVE THE WHY, NOT JUST THE WHAT. Keep motivation, emotional stakes, and "
    "circumstances. Shed length, never meaning — fewer events with their real context "
    "beat many hollow one-liners.\n"
    "3. Keep every PINNED LINE EXACTLY as written, word-for-word. You may reword, merge, "
    "or drop unpinned lines.\n"
    "4. Recent events stay more detailed; ancient history gets boiled down.\n"
    "5. Stay within the size budget given. If over budget, compress the lowest-priority, "
    "oldest material first.\n"
    "Output ONLY the two headings and their bullet lines — no preamble, no commentary."
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
    """Render a summary object back to ``STORY SO FAR`` / ``BONDS`` text (for prompt
    injection and for feeding the previous summary into the next fold-in)."""
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


def build_summarizer_messages(prev_text, batch_messages, pins, cap_tokens):
    """Assemble the OpenAI-style ``messages`` array for one fold-in call.

    ``batch_messages`` is a list of ``{'role': 'user'|'character', 'content': str}``.
    """
    convo = []
    for msg in batch_messages:
        who = 'User' if msg.get('role') == 'user' else 'Character'
        convo.append(f"{who}: {msg.get('content', '')}")
    convo_text = '\n\n'.join(convo)
    pins_text = '\n'.join(f'- {p}' for p in pins) if pins else '(none)'
    budget = f"no more than {cap_tokens}" if cap_tokens and cap_tokens > 0 else "the given"
    user = (
        f"CURRENT SUMMARY:\n{prev_text or '(empty — this is the first batch)'}\n\n"
        f"PINNED LINES (reproduce these EXACTLY, word-for-word):\n{pins_text}\n\n"
        f"NEW MESSAGES TO FOLD IN:\n{convo_text}\n\n"
        f"Rewrite the running summary so it now covers everything above. "
        f"The entire output, including headings and bullets, must use {budget} tokens. "
        "Tighten or merge older unpinned lines instead of merely appending new bullets."
    )
    return [
        {'role': 'system', 'content': SUMMARY_INSTRUCTIONS},
        {'role': 'user', 'content': user},
    ]


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
    current-state: the reply's BONDS section replaces the old one. Returns fresh copies
    and never mutates its inputs.
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
    return {'lines': prev_story + new_story + new_bonds}


def merge_pins(new_obj, prev_obj):
    """Carry pinned lines from the previous summary into a freshly parsed one.

    The model is asked to reproduce pinned lines verbatim, but to *guarantee* the
    design's "pinned lines are kept word-for-word" promise we re-assert them: any
    previously-pinned text missing from ``new_obj`` is re-added, and matching lines are
    re-flagged pinned. Pinned lines are placed under their original section.
    """
    new_lines = [dict(line) for line in summary_lines(new_obj)]
    existing = {
        ('bonds' if line.get('section') == 'bonds' else 'story', line['text'])
        for line in new_lines
    }
    for pin in summary_lines(prev_obj):
        if not pin.get('pinned'):
            continue
        section = 'bonds' if pin.get('section') == 'bonds' else 'story'
        key = (section, pin['text'])
        if key in existing:
            for line in new_lines:
                line_section = 'bonds' if line.get('section') == 'bonds' else 'story'
                if (line_section, line['text']) == key:
                    line['pinned'] = True
        else:
            new_lines.append({
                'section': section,
                'text': pin['text'],
                'pinned': True,
            })
    return {'lines': new_lines}
