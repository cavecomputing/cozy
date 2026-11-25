"""Pure logic for the Auto Summaries feature.

Summary-object parsing/serialization, token estimation, size-cap enforcement, and
the summarizer prompt. Deliberately free of Flask / DB / network so it can be unit
tested in isolation and imported by both ``routes/chats.py`` and
``routes/summaries.py`` without a circular dependency.

The summary object is the story so far, oldest entry first::

    {"lines": [ {"text": str, "start_msg_id": int, "end_msg_id": int}, ... ]}

rendered to / parsed from a ``STORY SO FAR`` heading with ``- `` bullets — the same
shape the summarizer model emits.

One batch of messages produces exactly one entry, stamped with the message range it
covers. The id pair is optional: a fork drops it from an entry whose messages it did
not copy.
"""

import json
import math
import re

STORY_HEADING = 'STORY SO FAR'

# A batch entry is a compact delta, not permission to consume the whole summary. This
# ceiling keeps a normal-sized summary useful across many batches; the proportional
# floor in ``entry_token_limit`` scales it down further when the summary cap is small.
STORY_ENTRY_MAX_TOKENS = 240

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
    "the CURRENT STORY and a batch of NEW MESSAGES.\n\n"
    "OUTPUT FORMAT — one heading and one line:\n"
    "STORY SO FAR\n"
    "- <one entry covering everything that happened in the new messages>\n\n"
    "RULES:\n"
    "1. Output EXACTLY ONE story line, covering everything that happens in the NEW "
    "MESSAGES, in the order it happened. Never split the batch across several bullets. "
    "If little of note happened, say briefly what the exchange was about — the line is "
    "never left out.\n"
    "2. The story is ADDITIVE. Do NOT repeat, reword, reorder, or compress the existing "
    "story lines — the system keeps them exactly as they are. Your one line covers only "
    "the new messages.\n"
    "3. Write compact memory, not scene prose. Keep only durable events, decisions, "
    "discoveries, promises, injuries, possessions, locations, changes in how characters "
    "feel about and treat each other, and unresolved objectives. Omit dialogue recap, "
    "atmosphere, repetition, and decorative detail. Obey the token limit in the request.\n"
    "Output ONLY the heading and its line — no preamble, no commentary."
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


_BULLET_PREFIX = re.compile(r'^[-*•]\s*')


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
    # A close tag with no opening one: the template opened the block, so
    # everything before it is reasoning.
    if not any(opening in remaining for opening, _ in THINKING_TAG_PAIRS):
        for _, closing in THINKING_TAG_PAIRS:
            if closing in remaining:
                remaining = remaining.split(closing, 1)[1]
                break
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


def close_incomplete_thinking(text):
    """Close a reasoning block that a reply cut short left open.

    The block moves to the front, closed, which is how the page puts an edited
    message back together. Left open, an edit's text would read back as reasoning.
    """
    found = [(text.find(opening), opening, closing)
             for opening, closing in THINKING_TAG_PAIRS if opening in text]
    if not found:
        return text
    index, opening, closing = min(found)
    if text.find(closing, index + len(opening)) >= 0:
        return text
    return text[index:] + closing + text[:index]


def parse_summary(text):
    """Parse the model's ``STORY SO FAR`` output into a summary object.

    The heading is skipped, bullet markers (``- ``, ``* ``, ``• ``) are stripped, and
    blank lines are ignored, so every other line is an entry.
    """
    lines = []
    for raw in (text or '').splitlines():
        line = raw.strip()
        if not line or _norm_heading(line) == STORY_HEADING:
            continue
        content = _BULLET_PREFIX.sub('', line).strip()
        if content:
            lines.append({'text': content})
    return {'lines': lines}


def parse_summarizer_output(text):
    """Validate and parse a model response in the required summary format.

    A transport-level success is not enough to retire chat history: the response must
    carry the ``STORY SO FAR`` heading. Everything before that heading is discarded, so a
    model that opens with "Here is the update:" still counts as answering in the format.
    Reasoning blocks are discarded too, so they never become memory. A reply with the
    heading but no line parses to an empty object; ``append_entry_problems`` reports the
    missing story line, so the worker can ask again instead of failing outright.
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
    """Fold a reply's lines into the single entry one batch is allowed to produce.

    The prompt asks for exactly one line, but a model that splits the batch into two or
    three bullets anyway must not fail the run: raising here would stall the feature on a
    cosmetic deviation, and the beats are consecutive by construction, so joining them
    loses nothing.

    ``previous`` is the stored story text. A multi-line reply that repeats some of it is
    the model copying CURRENT STORY back before its new line, despite rule 2; the copies
    are dropped before the join, or this batch's entry would carry every older one. A
    single-line reply is kept even when it matches an older entry — two quiet batches can
    legitimately read the same.
    """
    story = summary_lines(obj)
    if len(story) > 1:
        seen = set(previous)
        story = [line for line in story if line.get('text') not in seen]
    text = ' '.join(line.get('text', '').strip() for line in story if line.get('text'))
    return {'lines': [{'text': text}] if text else []}


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
        normalized = {'text': str(line['text']).strip()}
        # Both ends of the range or neither — half a range names nothing the UI can show.
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
    the entries that end at or before the fork point. Ranges are remapped onto the
    copies; one whose messages were since deleted keeps its text and loses its label.
    ``('', None)`` when nothing carries over.
    """
    whole = bool(watermark) and watermark <= fork_point
    kept, covered = [], watermark if whole else 0
    for line in summary_lines(parse_summary_json(summary_json)):
        start, end = line.get('start_msg_id'), line.get('end_msg_id')
        if not whole and (end is None or end > fork_point):
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


def summary_to_text(obj):
    """Render the summary with its heading, or '' when it is empty.

    The heading is included because the summary's real cost against its cap includes the
    heading that travels with it. This render also feeds the summary back to the
    summarizer, so the heading must stay exactly as ``_norm_heading`` matches it.
    """
    lines = summary_lines(obj)
    if not lines:
        return ''
    return '\n'.join([STORY_HEADING] + [f"- {line['text']}" for line in lines])


def entry_token_limit(cap_tokens):
    """The token ceiling for one batch's story entry.

    The fixed ceiling keeps large-context models from writing novella-sized entries. For
    a small configured summary, the proportional limit preserves room for roughly eight
    entries instead of letting the first one occupy most of the rolling window.
    """
    if not cap_tokens or cap_tokens <= 0:
        return STORY_ENTRY_MAX_TOKENS
    return min(cap_tokens, max(48, min(STORY_ENTRY_MAX_TOKENS, cap_tokens // 8)))


def append_entry_problems(obj, entry_tokens):
    """What in a batch delta breaks the per-batch limit, as short notes; [] when nothing.

    The limit stops one verbose reply from monopolizing the rolling summary. The notes
    name the size because they are read twice: by the model as the reason it is being
    asked again, and by the user if the second reply misses too.
    """
    story = summary_lines(obj)
    if not story:
        return ['no story line']
    problems = []
    for line in story:
        tokens = estimate_tokens(line.get('text', ''))
        if tokens > entry_tokens:
            problems.append(f'the story line is about {tokens} tokens (limit {entry_tokens})')
    return problems


def fit_append_entries(obj, entry_tokens):
    """Force a batch delta inside the per-batch limit once the model has missed twice.

    Failing the batch instead stalls the whole chat: every later run resends the same
    messages to the same model, and sending waits on the summary. So an over-long line
    keeps the longest prefix that fits. A missing story line stays missing; the batch
    simply adds no entry.
    """
    obj = {'lines': [dict(line) for line in summary_lines(obj)]}
    for line in obj['lines']:
        if estimate_tokens(line['text']) > entry_tokens:
            line['text'] = _longest_fitting_prefix(
                line['text'], lambda text: estimate_tokens(text) <= entry_tokens
            ) or line['text']
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
    """Trim the summary so it fits ``cap_tokens``. Returns ``(trimmed_obj, warning)``.

    This is the only thing standing between a growing summary and its budget, so it is a
    rolling window: entries shed OLDEST-first, because the story is a timeline and the
    newest beats are the ones the roleplay is still acting on.

    A non-empty summary is never reduced to empty: if its final entry still exceeds the
    cap, it is shortened as a final safety net.

    Dropping whole entries is the rolling window doing its job, so it warns about nothing;
    it once did, which left every mature chat's memory card permanently in warning. The
    warning is kept for what is not routine: an entry that had to be shortened, or a cap
    too small to hold anything.
    """
    obj = {'lines': [dict(line) for line in summary_lines(obj)]}
    if not cap_tokens or cap_tokens <= 0:
        return obj, ''

    def fits():
        return estimate_tokens(summary_to_text(obj)) <= cap_tokens

    while not fits() and len(obj['lines']) > 1:
        obj['lines'].pop(0)
    if fits():
        return obj, ''

    # Preserve the longest fitting prefix of the last entry. Binary search keeps this
    # deterministic fallback small while retaining more context than dropping it would.
    line = obj['lines'][0]
    original = line['text']

    def fits_as(candidate):
        line['text'] = candidate
        return fits()

    best = _longest_fitting_prefix(original, fits_as)
    line['text'] = best or original
    if best:
        return obj, 'The summary model exceeded the size cap; its last entry was shortened.'
    return obj, 'The summary cannot fit the configured size cap without becoming empty.'


def build_append_messages(story_text, batch_messages, entry_tokens):
    """Assemble the ``messages`` array for one *additive* fold-in call.

    The model returns one new story entry; the worker keeps every existing entry
    verbatim. The quoted budget covers only this batch's entry, never the whole stored
    summary.
    """
    convo = []
    for msg in batch_messages:
        # Named speakers let the entry say who did what; the role is only the fallback
        # for a caller that has none.
        who = msg.get('name') or ('User' if msg.get('role') == 'user' else 'Character')
        convo.append(f"{who}: {msg.get('content', '')}")
    convo_text = '\n\n'.join(convo)
    user = (
        f"CURRENT STORY:\n{story_text or '(empty — this is the first batch)'}\n\n"
        f"NEW MESSAGES TO FOLD IN:\n{convo_text}\n\n"
        f"Write exactly ONE new story entry covering what happens in the new messages, "
        f"using at most {entry_tokens} tokens. Do NOT rewrite or compress the existing "
        f"story lines."
    )
    return [
        {'role': 'system', 'content': APPEND_INSTRUCTIONS},
        {'role': 'user', 'content': user},
    ]


def append_summary(prev_obj, new_obj, msg_range):
    """Fold an *append-mode* reply into the existing summary.

    Existing entries are kept verbatim, in order. ``msg_range`` is the
    ``(start_msg_id, end_msg_id)`` of the batch that produced the reply, stamped onto the
    appended entry so it can name the messages it covers. Batches are distinct even when
    the model repeats earlier wording; only an exact retry of the same text and message
    range is suppressed.

    Returns fresh copies and never mutates its inputs.
    """
    lines = [dict(line) for line in summary_lines(prev_obj)]
    start, end = int(msg_range[0]), int(msg_range[1])
    seen = {(line['text'], line.get('start_msg_id'), line.get('end_msg_id')) for line in lines}
    for line in summary_lines(new_obj):
        text = line.get('text', '')
        if text and (text, start, end) not in seen:
            lines.append({'text': text, 'start_msg_id': start, 'end_msg_id': end})
            seen.add((text, start, end))
    return {'lines': lines}
