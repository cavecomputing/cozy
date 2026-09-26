"""Tests for the Auto Summaries feature — pure summary logic, the background
summarizer worker/endpoints, per-chat persistence, settings masking, and the
startup recovery migration."""

import json
from contextlib import contextmanager

import pytest

from cozy import shared
from cozy import schema
import cozy.routes.chats as chat_routes
from cozy.routes.settings import get_settings
import cozy.routes.summaries as summaries
from cozy.summarizer import (
    append_entry_problems,
    append_token_limits,
    append_summary,
    bond_key,
    build_append_messages,
    collapse_story_lines,
    dump_summary_json,
    enforce_cap,
    estimate_tokens,
    fit_append_entries,
    fork_summary,
    parse_summary,
    parse_summary_json,
    parse_summarizer_output,
    resolve_names,
    section_cap,
    section_to_text,
    strip_thinking_content,
    summary_to_text,
    token_batches,
)


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    """A batch retries after a pause; the tests have no endpoint to wait for."""
    monkeypatch.setattr(summaries, 'RETRY_DELAY_SECONDS', 0)


# ── Pure summarizer logic ───────────────────────────────────────────────────

def test_parse_summary_roundtrip():
    text = (
        "STORY SO FAR\n"
        "- Nell pulled Luna from the sea during a storm.\n"
        "- They sailed the Widow's Teeth.\n"
        "\n"
        "BONDS\n"
        "- Luna & Nell: deep trust, turning romantic."
    )
    obj = parse_summary(text)
    sections = [l['section'] for l in obj['lines']]
    assert sections == ['story', 'story', 'bonds']
    # Rendering back produces both headings and all three bullets.
    rendered = summary_to_text(obj)
    assert 'STORY SO FAR' in rendered and 'BONDS' in rendered
    assert rendered.count('\n- ') + rendered.count('- ', 0, 20) >= 1
    assert len([l for l in rendered.splitlines() if l.startswith('- ')]) == 3


def test_parse_summary_strips_various_bullets_and_decorated_headings():
    obj = parse_summary("**STORY SO FAR**\n* first\n• second\n[BONDS]\n- a & b: allies")
    assert [l['text'] for l in obj['lines']] == ['first', 'second', 'a & b: allies']
    assert obj['lines'][-1]['section'] == 'bonds'


def test_enforce_cap_drops_the_oldest_story_entry_and_keeps_bonds():
    obj = {'lines': [
        {'section': 'story', 'text': 'x' * 400},
        {'section': 'story', 'text': 'a later beat ' * 5},
        {'section': 'bonds', 'text': 'A & B: allies ' * 5},
    ]}
    # Cap 100 splits into a 60-token story budget and a 40-token bonds budget. The story
    # section (~121 tokens) must shed its ~100-token oldest entry to fit; the later beat
    # (~20) and the bond (~29, inside its own budget) both survive.
    capped, warning = enforce_cap(obj, 100)
    texts = [l['text'] for l in capped['lines']]
    assert not any(t == 'x' * 400 for t in texts)
    assert 'a later beat ' * 5 in texts
    assert any(l['section'] == 'bonds' for l in capped['lines'])
    # Rolling off the oldest entry is the window working, not something to warn about.
    assert not warning


def test_enforce_cap_shortens_the_last_story_entry_instead_of_erasing_it():
    obj = {'lines': [
        {
            'section': 'story',
            'text': 'important recent context ' * 30,
            'start_msg_id': 1,
            'end_msg_id': 10,
        },
        {'section': 'bonds', 'text': 'A & B: allies'},
    ]}
    capped, warning = enforce_cap(obj, 100)
    story = [line for line in capped['lines'] if line['section'] == 'story']

    assert len(story) == 1
    assert story[0]['text'] != obj['lines'][0]['text']
    assert story[0]['start_msg_id'] == 1 and story[0]['end_msg_id'] == 10
    assert section_to_text(capped, 'bonds') == 'BONDS\n- A & B: allies'
    assert estimate_tokens(section_to_text(capped, 'story')) <= section_cap(100, 'story')
    assert warning




def test_enforce_cap_noop_when_under():
    obj = {'lines': [{'section': 'story', 'text': 'short'}]}
    capped, warning = enforce_cap(obj, 1000)
    assert capped['lines'] == obj['lines'] and not warning


def test_estimate_tokens_matches_heuristic():
    # max(words*1.3, chars/4): "a b c d" -> words 4*1.3=5.2 -> ceil 6
    assert estimate_tokens('a b c d') == 6
    assert estimate_tokens('') == 0








@pytest.mark.parametrize('a, b', [
    ('Cerina and Luna: sisters', 'Luna – Cerina: sisters'),   # order + separator differ
    ('A & B: allies', 'B and A: allies'),
    ('Kael/Lina: healer', 'Lina , Kael : healer'),
    ('A & B: allies.', 'A & B: something else entirely'),     # identity is the head only
])
def test_bond_key_identifies_the_same_relationship(a, b):
    assert bond_key(a) == bond_key(b)


@pytest.mark.parametrize('a, b', [
    ('Cerina and Luna: sisters', 'Cerina and Kael: allies'),
    ('A & B: allies', 'A & C: allies'),
    ('Preserve this exact shared moment.', 'Exact pinned bond.'),  # colon-less legacy
])
def test_bond_key_separates_different_relationships(a, b):
    assert bond_key(a) != bond_key(b)


def test_bond_key_keeps_hyphenated_names_intact():
    """A dash only separates participants when it is surrounded by whitespace."""
    assert bond_key('Jean-Luc and Mira: partners') == bond_key('Mira and Jean-Luc: x')
    assert 'jeanluc' in bond_key('Jean-Luc and Mira: partners')


def test_bond_key_falls_back_to_whole_text_without_a_colon():
    """Legacy bonds predate the 'Names: dossier' shape and must still have an identity."""
    assert bond_key('Exact pinned bond.') == bond_key('exact  pinned bond')


def test_parse_summary_joins_a_wrapped_bond_dossier():
    """A soft-wrapped dossier is one relationship, not one per physical line."""
    obj = parse_summary(
        'STORY SO FAR\n'
        '- a beat\n'
        '\n'
        'BONDS\n'
        '- Cerina and Luna: Sisters with an unbreakable bond.\n'
        "Luna's arm was taken by Cerina's transformation.\n"
        '- A & B: allies'
    )
    bonds = [l['text'] for l in obj['lines'] if l['section'] == 'bonds']
    assert bonds == [
        "Cerina and Luna: Sisters with an unbreakable bond. Luna's arm was taken by "
        "Cerina's transformation.",
        'A & B: allies',
    ]


def test_parse_summary_keeps_story_beats_on_separate_lines():
    """STORY is a timeline; joining unbulleted lines would merge distinct events."""
    obj = parse_summary('STORY SO FAR\n- first beat\nsecond beat')
    assert [l['text'] for l in obj['lines']] == ['first beat', 'second beat']


def test_append_summary_leaves_untouched_dossiers_byte_identical():
    """The whole point of the keyed merge: an omitted relationship cannot erode."""
    long_dossier = (
        'Cerina and Luna: Sisters with an unbreakable bond. Since childhood they have '
        "been at each other's side. Luna's missing arm was taken by Cerina's uncontrolled "
        'transformation, and neither has spoken of it since.'
    )
    prev = {'lines': [
        {'section': 'bonds', 'text': long_dossier},
        {'section': 'bonds', 'text': 'A & B: wary'},
    ]}
    reply = parse_summary('STORY SO FAR\n- a beat\n\nBONDS\n- A & B: now firm allies')
    out = append_summary(prev, reply)
    bonds = [l['text'] for l in out['lines'] if l['section'] == 'bonds']
    assert bonds == [long_dossier, 'A & B: now firm allies']


def test_append_summary_updates_a_renamed_separator_in_place():
    prev = {'lines': [{'section': 'bonds', 'text': 'Cerina – Luna: wary'}]}
    reply = parse_summary('STORY SO FAR\n\nBONDS\n- Luna and Cerina: inseparable now')
    out = append_summary(prev, reply)
    bonds = [l for l in out['lines'] if l['section'] == 'bonds']
    assert [l['text'] for l in bonds] == ['Luna and Cerina: inseparable now']


def test_append_summary_appends_a_genuinely_new_relationship():
    prev = {'lines': [{'section': 'bonds', 'text': 'A & B: allies'}]}
    reply = parse_summary('STORY SO FAR\n\nBONDS\n- C and D: rivals')
    out = append_summary(prev, reply)
    assert [l['text'] for l in out['lines'] if l['section'] == 'bonds'] == [
        'A & B: allies', 'C and D: rivals',
    ]




def test_enforce_cap_sections_do_not_evict_each_other():
    """A bloated story must not consume the bonds budget, or vice versa."""
    obj = {'lines': [
        {'section': 'story', 'text': 'x' * 4000},
        {'section': 'story', 'text': 'a short beat'},
        {'section': 'bonds', 'text': 'A & B: a modest dossier about two people'},
    ]}
    capped, _ = enforce_cap(obj, 200)
    texts = [l['text'] for l in capped['lines']]
    assert 'x' * 4000 not in texts
    assert 'a short beat' in texts
    assert 'A & B: a modest dossier about two people' in texts


def test_enforce_cap_drops_the_newest_bond_first():
    """Cap pressure sheds a relationship opened last batch, not a founding one."""
    obj = {'lines': [
        {'section': 'bonds', 'text': 'A & B: ' + 'founding history ' * 20},
        {'section': 'bonds', 'text': 'C & D: ' + 'newcomer history ' * 20},
    ]}
    capped, warning = enforce_cap(obj, 200)
    remaining = [l['text'] for l in capped['lines']]
    assert len(remaining) == 1
    assert remaining[0].startswith('A & B:')
    # Dropping C & D is routine; the warning is for A & B, still too long and shortened.
    assert warning and 'shortened' in warning


def test_section_cap_splits_sixty_forty_and_never_rounds_to_zero():
    assert section_cap(1000, 'story') == 600
    assert section_cap(1000, 'bonds') == 400
    assert section_cap(0, 'story') == 0          # uncapped
    assert section_cap(1, 'story') == 1          # a positive cap always leaves room
    assert section_cap(1, 'bonds') == 1


def test_section_to_text_renders_one_section_with_its_heading():
    obj = {'lines': [
        {'section': 'story', 'text': 'a beat'},
        {'section': 'bonds', 'text': 'A & B: allies'},
    ]}
    assert section_to_text(obj, 'story') == 'STORY SO FAR\n- a beat'
    assert section_to_text(obj, 'bonds') == 'BONDS\n- A & B: allies'
    assert section_to_text({'lines': []}, 'bonds') == ''










def test_parse_summary_json_tolerates_junk():
    assert parse_summary_json('not json') == {'lines': []}
    assert parse_summary_json('')['lines'] == []
    good = dump_summary_json({'lines': [{'section': 'bonds', 'text': 'a'}]})
    assert parse_summary_json(good)['lines'][0]['text'] == 'a'


def test_parse_summary_json_round_trips_story_message_ranges():
    stored = dump_summary_json({'lines': [
        {'section': 'story', 'text': 'a beat', 'start_msg_id': 4, 'end_msg_id': 9},
        {'section': 'bonds', 'text': 'A & B: allies'},
    ]})
    lines = parse_summary_json(stored)['lines']
    assert lines[0]['start_msg_id'] == 4 and lines[0]['end_msg_id'] == 9
    assert 'start_msg_id' not in lines[1]  # bonds span the whole chat


@pytest.mark.parametrize('line', [
    {'section': 'story', 'text': 'a beat'},                       # pre-range summary
    {'section': 'story', 'text': 'a beat', 'start_msg_id': 4},    # half a range
    {'section': 'story', 'text': 'a beat', 'start_msg_id': 4, 'end_msg_id': 'x'},
    {'section': 'story', 'text': 'a beat', 'start_msg_id': None, 'end_msg_id': None},
])
def test_parse_summary_json_drops_unusable_ranges(line):
    """Both ends or neither — half a range names nothing the UI can show."""
    out = parse_summary_json(dump_summary_json({'lines': [line]}))['lines'][0]
    assert out['text'] == 'a beat'
    assert 'start_msg_id' not in out and 'end_msg_id' not in out


def test_enforce_cap_keeps_the_range_on_a_surviving_entry():
    obj = {'lines': [
        {'section': 'story', 'text': 'x' * 400, 'start_msg_id': 1, 'end_msg_id': 2},
        {'section': 'story', 'text': 'a later beat', 'start_msg_id': 3, 'end_msg_id': 4},
    ]}
    capped, _ = enforce_cap(obj, 100)
    assert [l['text'] for l in capped['lines']] == ['a later beat']
    assert capped['lines'][0]['start_msg_id'] == 3
    assert capped['lines'][0]['end_msg_id'] == 4


def test_collapse_story_lines_joins_a_split_reply():
    """The prompt asks for one entry; a model that splits it must not fail the run."""
    obj = parse_summary('STORY SO FAR\n- first half\n- second half\n\nBONDS\n- A & B: allies')
    out = collapse_story_lines(obj)
    assert [l['text'] for l in out['lines'] if l['section'] == 'story'] == [
        'first half second half',
    ]
    # Bonds are separate relationships, not a timeline — never joined.
    assert [l['text'] for l in out['lines'] if l['section'] == 'bonds'] == ['A & B: allies']


def test_collapse_story_lines_leaves_one_entry_and_empty_story_alone():
    single = collapse_story_lines(parse_summary('STORY SO FAR\n- only beat'))
    assert [l['text'] for l in single['lines']] == ['only beat']
    empty = collapse_story_lines(parse_summary('STORY SO FAR\n\nBONDS\n- A & B: allies'))
    assert [l['section'] for l in empty['lines']] == ['bonds']


def test_collapse_story_lines_drops_a_copied_back_current_story():
    """A model that echoes CURRENT STORY before its new line must not stamp every older
    entry onto this batch's range."""
    echoed = parse_summary('STORY SO FAR\n- old one\n- old two\n- the new beat')
    out = collapse_story_lines(echoed, ['old one', 'old two'])
    assert [l['text'] for l in out['lines']] == ['the new beat']
    # One line that happens to read like an older entry is still this batch's own.
    quiet = collapse_story_lines(parse_summary('STORY SO FAR\n- old one'), ['old one'])
    assert [l['text'] for l in quiet['lines']] == ['old one']












# ── Append-mode pure logic ──────────────────────────────────────────────────

def test_append_summary_accumulates_and_merges_bonds_by_relationship():
    prev = {'lines': [
        {'section': 'story', 'text': 'S1'},
        {'section': 'bonds', 'text': 'A & B: wary allies'},
    ]}
    reply = {'lines': [
        {'section': 'story', 'text': 'S1'},   # duplicate — dropped
        {'section': 'story', 'text': 'S2'},   # new — appended
        {'section': 'bonds', 'text': 'A & B: firm allies'},  # same pair
    ]}
    out = append_summary(prev, reply)
    story = [l['text'] for l in out['lines'] if l['section'] == 'story']
    bonds = [l['text'] for l in out['lines'] if l['section'] == 'bonds']
    assert story == ['S1', 'S2']              # accumulates, no duplicate
    assert bonds == ['A & B: firm allies']    # same relationship, updated in place
    # Story block precedes bonds block (matches summary_to_text ordering).
    sections = [l['section'] for l in out['lines']]
    assert sections == ['story', 'story', 'bonds']
    # Inputs are not mutated.
    assert [l['text'] for l in prev['lines']] == ['S1', 'A & B: wary allies']


def test_append_summary_keeps_identical_text_from_distinct_message_ranges():
    prev = {'lines': [
        {'section': 'story', 'text': 'They continue onward.',
         'start_msg_id': 1, 'end_msg_id': 2},
    ]}
    reply = {'lines': [{'section': 'story', 'text': 'They continue onward.'}]}

    out = append_summary(prev, reply, msg_range=(3, 4))
    assert [(line['start_msg_id'], line['end_msg_id']) for line in out['lines']] == [
        (1, 2), (3, 4),
    ]

    # Retrying the same completed range remains idempotent.
    retried = append_summary(out, reply, msg_range=(3, 4))
    assert retried == out


@pytest.mark.parametrize('placeholder', [
    'A & B: unchanged',
    'A & B: No change from before.',
    'A & B: N/A',
    'No relationships changed.',
    '(none)',
])
def test_append_summary_skips_placeholder_bond_lines(placeholder):
    """Rule 4 forbids these, but one that slipped through used to overwrite the whole
    dossier or open a junk bond that never went away."""
    prev = {'lines': [{'section': 'bonds', 'text': 'A & B: allies since the flood'}]}
    reply = {'lines': [
        {'section': 'story', 'text': 'S2'},
        {'section': 'bonds', 'text': placeholder},
    ]}
    out = append_summary(prev, reply)
    assert [l['text'] for l in out['lines'] if l['section'] == 'bonds'] == [
        'A & B: allies since the flood',
    ]


def test_append_summary_first_batch_from_empty():
    out = append_summary({'lines': []}, parse_summary('STORY SO FAR\n- first\n\nBONDS\n- A & B: allies'))
    assert [l['text'] for l in out['lines']] == ['first', 'A & B: allies']


def test_append_summary_keeps_previous_bonds_when_reply_has_none():
    """A reply with an empty BONDS section must not erase relationship state.

    Under the keyed merge this holds by construction — a relationship the reply does not
    mention is never touched — which is exactly what lets the prompt tell the model to
    omit unchanged relationships instead of re-transcribing them.
    """
    prev = {'lines': [
        {'section': 'story', 'text': 'S1'},
        {'section': 'bonds', 'text': 'A & B: wary allies'},
        {'section': 'bonds', 'text': 'A & C: owes a debt'},
    ]}
    # parse_summarizer_output accepts a bare BONDS heading with no bullets.
    reply = parse_summarizer_output('STORY SO FAR\n- S2\n\nBONDS')
    out = append_summary(prev, reply)
    bonds = [l['text'] for l in out['lines'] if l['section'] == 'bonds']
    assert bonds == ['A & B: wary allies', 'A & C: owes a debt']
    assert [l['text'] for l in out['lines'] if l['section'] == 'story'] == ['S1', 'S2']


def test_build_append_messages_asks_for_exactly_one_story_entry():
    from cozy.summarizer import APPEND_INSTRUCTIONS
    msgs = build_append_messages(
        'PREV STORY', 'PREV BONDS', [{'role': 'user', 'content': 'hi there'}],
        240, 120, 360)
    assert msgs[0]['content'] == APPEND_INSTRUCTIONS
    user = msgs[1]['content']
    # The two sections travel as separate blocks so the model can tell what it may extend
    # from what it must leave alone.
    assert 'CURRENT STORY' in user and 'PREV STORY' in user
    assert 'CURRENT BONDS' in user and 'PREV BONDS' in user
    assert 'hi there' in user
    assert 'exactly ONE new story entry' in user and 'BONDS' in user
    assert '240 tokens' in user
    assert '120 tokens' in user
    assert '360 tokens' in user
    # The one-entry rule reaches the model through the system prompt too.
    assert 'EXACTLY ONE story line' in APPEND_INSTRUCTIONS


def _ranged_summary():
    return dump_summary_json({'lines': [
        {'section': 'story', 'text': 'First.', 'start_msg_id': 10, 'end_msg_id': 15},
        {'section': 'story', 'text': 'Second.', 'start_msg_id': 16, 'end_msg_id': 21},
        {'section': 'bonds', 'text': 'A and B: allies.'},
    ]})


def test_fork_after_the_watermark_keeps_the_whole_summary_remapped():
    new_ids = {old: old + 100 for old in range(10, 30)}
    summary, watermark = fork_summary(_ranged_summary(), 21, 25, new_ids)
    lines = parse_summary_json(summary)['lines']
    assert [l['text'] for l in lines] == ['First.', 'Second.', 'A and B: allies.']
    assert (lines[0]['start_msg_id'], lines[0]['end_msg_id']) == (110, 115)
    assert (lines[1]['start_msg_id'], lines[1]['end_msg_id']) == (116, 121)
    assert watermark == 121


def test_fork_inside_the_summary_keeps_only_what_happened_before_it():
    new_ids = {old: old + 100 for old in range(10, 19)}
    summary, watermark = fork_summary(_ranged_summary(), 21, 18, new_ids)
    lines = parse_summary_json(summary)['lines']
    # "Second." covers messages after the fork point, and a bond may too.
    assert [l['text'] for l in lines] == ['First.']
    assert watermark == 115


def test_fork_remaps_the_watermark_past_deleted_messages():
    # Message 21, the old watermark, was deleted; 20 is the last copy it still covers.
    new_ids = {old: old + 100 for old in range(10, 21)}
    new_ids.update({22: 122})
    summary, watermark = fork_summary(_ranged_summary(), 21, 22, new_ids)
    lines = parse_summary_json(summary)['lines']
    assert watermark == 120
    # The range whose end is gone keeps its text but no longer claims a label.
    assert 'start_msg_id' not in lines[1] and lines[1]['text'] == 'Second.'


def test_fork_with_nothing_to_carry_over():
    assert fork_summary('', None, 5, {5: 50}) == ('', None)
    assert fork_summary(_ranged_summary(), 21, 12, {10: 1, 11: 2, 12: 3}) == ('', None)


def test_build_append_messages_labels_speakers_by_name():
    msgs = build_append_messages('', '', [
        {'role': 'character', 'content': 'Halt.', 'name': 'Sasha'},
        {'role': 'user', 'content': 'Hello.', 'name': 'Morgan'},
        {'role': 'user', 'content': 'Unlabelled.'},
    ], 240, 120, 360)
    user = msgs[1]['content']
    assert 'Sasha: Halt.' in user and 'Morgan: Hello.' in user
    # A caller with no names still gets the role labels.
    assert 'User: Unlabelled.' in user


def test_resolve_names_matches_the_frontend():
    assert resolve_names('{{char}} meets {{USER}}.', 'Sasha', 'Morgan') == 'Sasha meets Morgan.'
    # Names are inserted literally, never read as replacement syntax.
    assert resolve_names('{{char}}', r'\1 $&', 'x') == r'\1 $&'
    assert resolve_names(None, 'a', 'b') == ''


def test_append_token_limits_keep_room_for_multiple_entries():
    # A 12k summary gives STORY 7.2k tokens, but one batch still tops out at 240.
    assert append_token_limits(12000) == (240, 200, 600)
    # A 64k context at the default 10% cap gives a bond the full 200.
    assert append_token_limits(6553)[1] == 200
    # Small summaries scale down so one entry does not occupy most of its section.
    assert append_token_limits(800) == (60, 40, 106)


def test_append_entry_problems_names_each_line_over_its_limit():
    problems = append_entry_problems({'lines': [
        {'section': 'story', 'text': 'sprawling detail ' * 30},
        {'section': 'bonds', 'text': 'Lina and Mark: ' + 'long history ' * 30},
        {'section': 'bonds', 'text': 'Lina and Sol: brief'},
    ]}, 60, 40, 100)
    assert len(problems) == 3
    assert 'story line' in problems[0] and 'limit 60' in problems[0]
    assert '"Lina and Mark"' in problems[1] and 'limit 40' in problems[1]
    assert 'total' in problems[2] and 'limit 100' in problems[2]
    assert append_entry_problems({'lines': [{'section': 'story', 'text': 'fine'}]},
                                 60, 40, 100) == []
    assert append_entry_problems({'lines': []}, 60, 40, 100) == ['no story line']


def test_fit_append_entries_trims_every_line_into_its_limit():
    fitted = fit_append_entries({'lines': [
        {'section': 'story', 'text': 'sprawling detail ' * 30},
        {'section': 'bonds', 'text': 'A & B: ' + 'history ' * 40},
        {'section': 'bonds', 'text': 'A & C: ' + 'history ' * 40},
        {'section': 'bonds', 'text': 'A & D: ' + 'history ' * 40},
    ]}, 60, 40, 100)
    assert append_entry_problems(fitted, 60, 40, 100) == []
    assert fitted['lines'][0]['text'].startswith('sprawling detail')
    assert fitted['lines'][0]['text'].endswith('…')
    # The batch total drops the last updates; the earlier ones keep their names.
    bonds = [l['text'] for l in fitted['lines'] if l['section'] == 'bonds']
    assert [b.split(':')[0] for b in bonds] == ['A & B', 'A & C']


def test_parse_summarizer_output_rejects_empty_and_malformed_content():
    for content in ('', 'just some prose', 'BONDS\n- A & B: allies'):
        with pytest.raises(ValueError):
            parse_summarizer_output(content)
    # Headings alone parse to nothing; the worker reports the missing story line.
    assert parse_summarizer_output('STORY SO FAR\n\nBONDS') == {'lines': []}


def test_parse_summarizer_output_skips_a_preamble_before_the_story_heading():
    obj = parse_summarizer_output(
        'Here is the update:\n\nSTORY SO FAR\n- they reach the gate\n\nBONDS\n- A & B: allies'
    )
    assert [l['text'] for l in obj['lines']] == ['they reach the gate', 'A & B: allies']


def test_parse_summarizer_output_accepts_an_omitted_bonds_section():
    """Rule 4 tells the model to write a bond line only when one changed, so a batch
    that moved no relationship legitimately drops the whole section. Failing that reply
    stalled the run on the most ordinary batch there is."""
    obj = parse_summarizer_output('STORY SO FAR\n- they walk east until dusk')
    assert [l['text'] for l in obj['lines']] == ['they walk east until dusk']
    assert not [l for l in obj['lines'] if l['section'] == 'bonds']


def test_parse_summarizer_output_strips_reasoning_block():
    obj = parse_summarizer_output(
        '<think>private chain of thought</think>\n'
        'STORY SO FAR\n- visible event\n\nBONDS\n- A & B: allies'
    )
    assert 'private chain of thought' not in summary_to_text(obj)
    assert 'visible event' in summary_to_text(obj)


def test_strip_thinking_content_handles_multiple_and_incomplete_blocks():
    assert strip_thinking_content(
        'before<think>one</think>middle<thinking>two</thinking>after'
    ) == 'beforemiddleafter'
    assert strip_thinking_content('visible<|thinking|>unfinished') == 'visible'


def test_enforce_cap_never_erases_a_nonempty_summary():
    capped, warning = enforce_cap({
        'lines': [{'section': 'story', 'text': 'important context ' * 20}],
    }, 1)
    assert capped['lines'][0]['text']
    assert warning


# ── Helpers for endpoint/worker tests ───────────────────────────────────────

def _add_messages(client, chat_id, n):
    ids = []
    for i in range(n):
        role = 'user' if i % 2 == 0 else 'character'
        r = client.post(f'/api/chats/{chat_id}/messages', json={'role': role, 'content': f'msg {i}'})
        assert r.status_code in (200, 201), r.get_data(as_text=True)
        ids.append(r.get_json()['id'])
    return ids


def _batch_every(monkeypatch, n):
    """Size each batch at ``n`` of ``_add_messages``' messages, which estimate at 3 tokens."""
    monkeypatch.setattr(summaries, '_batch_target_tokens', lambda _settings: 3 * n)


def _store_summary(chat_id, summary_obj, watermark=None):
    """Seed server-owned summary state without using the public chat patch route."""
    with shared.get_db() as conn:
        conn.execute(
            'UPDATE chats SET summary_json=?, summary_up_to_msg_id=? WHERE id=?',
            (dump_summary_json(summary_obj), watermark, chat_id),
        )


CANNED = "STORY SO FAR\n- Something happened.\n\nBONDS\n- A & B: close allies."


# ── The worker ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize('body', [
    {},
    {'choices': []},
    {'choices': [{}]},
    {'choices': [{'message': {'content': ''}}]},
    {'choices': [{'message': {'content': None}}]},
])
def test_call_summarizer_rejects_malformed_completion_body(client, monkeypatch, body):
    client.put('/api/settings', json={
        'summary_api_endpoint': 'http://summarizer',
        'summary_api_model': 'summary-model',
    })

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return body

    monkeypatch.setattr(summaries.http_requests, 'post', lambda *args, **kwargs: FakeResponse())

    with pytest.raises(RuntimeError):
        summaries.call_summarizer([{'role': 'user', 'content': 'summarize'}])


@pytest.mark.parametrize(('cap_tokens', 'expected'), [(50, 512), (500, 625), (4000, 5000)])
def test_call_summarizer_bounds_single_response_with_headroom(
        client, monkeypatch, cap_tokens, expected):
    """The completion stays bounded but keeps headroom over the cap: a
    compress-mode overshoot must hit enforce_cap's oldest-first trim, not the
    provider's newest-first truncation."""
    client.put('/api/settings', json={
        'summary_api_endpoint': 'http://summarizer',
        'summary_api_model': 'summary-model',
    })
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {'choices': [{'message': {'content': CANNED}}]}

    def fake_post(*args, **kwargs):
        captured.update(kwargs['json'])
        return FakeResponse()

    monkeypatch.setattr(summaries.http_requests, 'post', fake_post)

    assert summaries.call_summarizer(
        [{'role': 'user', 'content': 'summarize'}], cap_tokens
    ) == CANNED
    assert captured['max_tokens'] == expected


def test_call_summarizer_rejects_length_truncated_completion(client, monkeypatch):
    """finish_reason == 'length' means chopped memory; it must fail the batch
    rather than being parsed (a cut mid-bullet still parses cleanly)."""
    client.put('/api/settings', json={
        'summary_api_endpoint': 'http://summarizer',
        'summary_api_model': 'summary-model',
    })

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {'choices': [{
                'finish_reason': 'length',
                'message': {'content': 'STORY SO FAR\n- Beat.\n\nBONDS\n- A & B: allies bound by the'},
            }]}

    monkeypatch.setattr(summaries.http_requests, 'post', lambda *a, **k: FakeResponse())

    with pytest.raises(RuntimeError, match='cut off'):
        summaries.call_summarizer([{'role': 'user', 'content': 'summarize'}], 500)


def test_run_job_folds_and_advances_watermark(client, sample_chat, monkeypatch):
    calls = []
    monkeypatch.setattr(summaries, 'call_summarizer',
                        lambda messages, cap_tokens=0: calls.append(messages) or CANNED)
    ids = _add_messages(client, sample_chat['id'], 5)

    summaries._run_summary_job(sample_chat['id'], ids[-1], rebuild=False)

    with shared.get_db() as conn:
        row = conn.execute('SELECT * FROM chats WHERE id=?', (sample_chat['id'],)).fetchone()
    assert row['summary_status'] == 'idle'
    assert row['summary_up_to_msg_id'] == ids[-1]
    obj = parse_summary_json(row['summary_json'])
    assert any(l['section'] == 'bonds' for l in obj['lines'])
    assert len(calls) >= 1


def test_run_job_batches_by_tokens(client, sample_chat, monkeypatch):
    _batch_every(monkeypatch, 2)
    calls = []
    monkeypatch.setattr(summaries, 'call_summarizer',
                        lambda messages, cap_tokens=0: calls.append(1) or CANNED)
    ids = _add_messages(client, sample_chat['id'], 5)
    summaries._run_summary_job(sample_chat['id'], ids[-1], rebuild=False)
    # Two full batches, and the last message is half a batch: enough to stand alone.
    assert len(calls) == 3


def test_run_job_folds_a_trailing_sliver_into_the_last_batch(
        client, sample_chat, monkeypatch):
    """Under half a batch left at the end joins the batch before it, not an entry of its own."""
    _batch_every(monkeypatch, 4)
    calls = []
    monkeypatch.setattr(summaries, 'call_summarizer',
                        lambda messages, cap_tokens=0: calls.append(messages) or CANNED)
    ids = _add_messages(client, sample_chat['id'], 5)

    summaries._run_summary_job(sample_chat['id'], ids[-1], rebuild=False)

    assert len(calls) == 1
    assert 'msg 4' in calls[0][1]['content']


def test_batches_are_sized_on_the_text_the_browser_measures(
        client, sample_chat, monkeypatch):
    """Reasoning is left out of a message's size, and names are resolved only after it
    is measured: the browser picked the run's end from the stored text."""
    sizes_seen = []
    real_token_batches = summaries.token_batches
    monkeypatch.setattr(
        summaries, 'token_batches',
        lambda sizes, target: sizes_seen.append(sizes) or real_token_batches(sizes, target),
    )
    monkeypatch.setattr(summaries, 'call_summarizer', lambda messages, cap_tokens=0: CANNED)
    stored = '<think>' + 'private reasoning ' * 50 + '</think>' + '{{user}} ' * 40
    message = client.post(f'/api/chats/{sample_chat["id"]}/messages', json={
        'role': 'user', 'content': stored,
    }).get_json()

    summaries._run_summary_job(sample_chat['id'], message['id'])

    visible = strip_thinking_content(stored)
    # The default persona's name is longer than the placeholder, so resolving first
    # would measure a different size.
    assert estimate_tokens(resolve_names(visible, 'TestChar', 'Default Persona')) \
        != estimate_tokens(visible)
    assert sizes_seen == [[estimate_tokens(visible)]]


def test_run_job_sends_names_not_placeholders(client, sample_chat, sample_persona, monkeypatch):
    """The stored greeting keeps {{char}}/{{user}}; the summarizer must read names."""
    sent = []
    monkeypatch.setattr(summaries, 'call_summarizer',
                        lambda messages, cap_tokens=0: sent.append(messages) or CANNED)
    chat_id = sample_chat['id']
    client.put(f'/api/chats/{chat_id}', json={'persona_id': sample_persona['id']})
    greeting = client.post(f'/api/chats/{chat_id}/messages', json={
        'role': 'character', 'content': '{{char}} salutes {{user}} at the gate.',
    }).get_json()
    reply = client.post(f'/api/chats/{chat_id}/messages', json={
        'role': 'user', 'content': 'Hi, {{char}}.', 'persona_id': sample_persona['id'],
    }).get_json()

    summaries._run_summary_job(chat_id, reply['id'], rebuild=False)

    prompt = sent[0][1]['content']
    assert '{{' not in prompt
    assert 'TestChar: TestChar salutes TestUser at the gate.' in prompt
    assert 'TestUser: Hi, TestChar.' in prompt
    assert greeting['id'] < reply['id']


def test_run_job_incremental(client, sample_chat, monkeypatch):
    monkeypatch.setattr(summaries, 'call_summarizer', lambda messages, cap_tokens=0: CANNED)
    ids = _add_messages(client, sample_chat['id'], 3)
    summaries._run_summary_job(sample_chat['id'], ids[1], rebuild=False)
    with shared.get_db() as conn:
        wm1 = conn.execute('SELECT summary_up_to_msg_id FROM chats WHERE id=?',
                           (sample_chat['id'],)).fetchone()[0]
    assert wm1 == ids[1]
    # Second run only folds messages after the watermark.
    seen = []
    monkeypatch.setattr(summaries, 'call_summarizer',
                        lambda messages, cap_tokens=0: seen.append(messages) or CANNED)
    summaries._run_summary_job(sample_chat['id'], ids[2], rebuild=False)
    with shared.get_db() as conn:
        wm2 = conn.execute('SELECT summary_up_to_msg_id FROM chats WHERE id=?',
                           (sample_chat['id'],)).fetchone()[0]
    assert wm2 == ids[2]
    assert 'msg 2' in seen[-1][1]['content']  # only the new message went in


def _chat_row(chat_id):
    with shared.get_db() as conn:
        return conn.execute('SELECT * FROM chats WHERE id=?', (chat_id,)).fetchone()


def test_run_job_error_sets_error_status(client, sample_chat, monkeypatch):
    calls = []

    def boom(messages, cap_tokens=0):
        calls.append(messages)
        raise RuntimeError('summarizer exploded')
    monkeypatch.setattr(summaries, 'call_summarizer', boom)
    ids = _add_messages(client, sample_chat['id'], 2)
    summaries._run_summary_job(sample_chat['id'], ids[-1], rebuild=False)
    row = _chat_row(sample_chat['id'])
    assert row['summary_status'] == 'error'
    assert 'exploded' in row['summary_status_detail']
    # One retry, then the failure stands.
    assert len(calls) == summaries.BATCH_ATTEMPTS == 2


def test_expected_job_failures_log_one_line_and_bugs_keep_their_traceback(
        client, sample_chat, monkeypatch, caplog):
    ids = _add_messages(client, sample_chat['id'], 2)

    def provider_down(messages, cap_tokens=0):
        raise RuntimeError('503 Service Unavailable')
    monkeypatch.setattr(summaries, 'call_summarizer', provider_down)
    with caplog.at_level('INFO', logger='cozy'):
        summaries._run_summary_job(sample_chat['id'], ids[-1], rebuild=False)
    failed = [r for r in caplog.records if 'Summary job failed' in r.getMessage()]
    assert [r.levelname for r in failed] == ['WARNING']
    assert '503 Service Unavailable' in failed[0].getMessage()
    assert not any(r.exc_info for r in caplog.records if r.levelno >= 30)

    caplog.clear()

    def bug(messages, cap_tokens=0):
        raise KeyError('lines')
    monkeypatch.setattr(summaries, 'call_summarizer', bug)
    with caplog.at_level('INFO', logger='cozy'):
        summaries._run_summary_job(sample_chat['id'], ids[-1], rebuild=False)
    failed = [r for r in caplog.records if 'Summary job failed' in r.getMessage()]
    assert failed and failed[0].levelname == 'ERROR' and failed[0].exc_info


def test_run_job_retries_a_failed_request_once(client, sample_chat, monkeypatch):
    """A dropped connection or a 502 from a restarting endpoint must not end the run."""
    outcomes = iter([RuntimeError('502 Bad Gateway'), CANNED])

    def flaky(messages, cap_tokens=0):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(summaries, 'call_summarizer', flaky)
    ids = _add_messages(client, sample_chat['id'], 2)
    summaries._run_summary_job(sample_chat['id'], ids[-1])

    row = _chat_row(sample_chat['id'])
    assert row['summary_status'] == 'idle'
    assert row['summary_up_to_msg_id'] == ids[-1]


@pytest.mark.parametrize('reply', [
    '',
    'not the requested format',
])
def test_run_job_rejects_invalid_model_content_without_advancing(
        client, sample_chat, monkeypatch, reply):
    calls = []
    monkeypatch.setattr(summaries, 'call_summarizer',
                        lambda messages, cap_tokens=0: calls.append(messages) or reply)
    ids = _add_messages(client, sample_chat['id'], 2)

    summaries._run_summary_job(sample_chat['id'], ids[-1])

    row = _chat_row(sample_chat['id'])
    assert row['summary_json'] == ''
    assert row['summary_up_to_msg_id'] is None
    assert row['summary_status'] == 'error'
    # The retry told the model what was wrong with the first reply.
    assert len(calls) == 2
    assert 'YOUR PREVIOUS REPLY WAS REJECTED' in calls[1][1]['content']


def test_run_job_asks_again_for_a_missing_story_line_then_moves_on(
        client, sample_chat, monkeypatch):
    """Headings with nothing under them get one retry. A second empty reply retires the
    batch without an entry rather than stalling the chat on it forever."""
    calls = []
    monkeypatch.setattr(summaries, 'call_summarizer',
                        lambda messages, cap_tokens=0: calls.append(messages)
                        or 'STORY SO FAR\n\nBONDS')
    ids = _add_messages(client, sample_chat['id'], 2)

    summaries._run_summary_job(sample_chat['id'], ids[-1])

    assert len(calls) == 2
    assert 'no story line' in calls[1][1]['content']
    row = _chat_row(sample_chat['id'])
    assert row['summary_status'] == 'idle'
    assert row['summary_up_to_msg_id'] == ids[-1]
    assert parse_summary_json(row['summary_json'])['lines'] == []
    # The status says the batch has no entry, not that Cozy "kept what fit".
    assert 'no story entry for batch 1/1' in row['summary_status_detail']
    assert 'kept what fit' not in row['summary_status_detail']


def test_overlong_bond_is_sent_back_with_a_note_and_the_corrected_reply_is_kept(
        client, sample_chat, monkeypatch):
    """The reported stall: one bond line over its limit failed the batch, and every
    later run resent the same messages and got the same reply."""
    long_bond = 'Lina and Mark: ' + 'a long shared history of debts and favours ' * 30
    replies = iter([
        f'STORY SO FAR\n- They cross the river.\n\nBONDS\n- {long_bond}',
        'STORY SO FAR\n- They cross the river.\n\nBONDS\n- Lina and Mark: wary allies.',
    ])
    calls = []

    def complete(messages, cap_tokens=0):
        calls.append(messages)
        return next(replies)

    monkeypatch.setattr(summaries, 'call_summarizer', complete)
    ids = _add_messages(client, sample_chat['id'], 2)
    summaries._run_summary_job(sample_chat['id'], ids[-1])

    assert len(calls) == 2
    note = calls[1][1]['content'].split('YOUR PREVIOUS REPLY WAS REJECTED')[1]
    bond_entry_tokens = append_token_limits(summaries._cap_tokens(get_settings()))[1]
    assert '"Lina and Mark"' in note and f'limit {bond_entry_tokens}' in note
    row = _chat_row(sample_chat['id'])
    assert row['summary_status'] == 'idle'
    assert row['summary_status_detail'] == ''
    assert row['summary_up_to_msg_id'] == ids[-1]
    bonds = [l['text'] for l in parse_summary_json(row['summary_json'])['lines']
             if l['section'] == 'bonds']
    assert bonds == ['Lina and Mark: wary allies.']


def test_overlong_reply_twice_is_trimmed_and_the_run_moves_on(
        client, sample_chat, monkeypatch):
    client.put('/api/settings', json={
        'context_max_tokens': '500',
        'summary_cap_pct': '10',
    })
    over_cap = f"STORY SO FAR\n- {'important context ' * 100}\n\nBONDS\n- A & B: trust"
    calls = []

    def complete(messages, cap_tokens=0):
        calls.append(messages)
        return over_cap

    monkeypatch.setattr(summaries, 'call_summarizer', complete)
    ids = _add_messages(client, sample_chat['id'], 2)
    summaries._run_summary_job(sample_chat['id'], ids[-1])

    assert len(calls) == 2
    row = _chat_row(sample_chat['id'])
    assert row['summary_status'] == 'idle'
    assert row['summary_up_to_msg_id'] == ids[-1]
    assert 'after a retry' in row['summary_status_detail']
    story_entry_tokens = append_token_limits(summaries._cap_tokens(get_settings()))[0]
    story = [l['text'] for l in parse_summary_json(row['summary_json'])['lines']
             if l['section'] == 'story']
    assert story and story[0].startswith('important context')
    assert estimate_tokens(story[0]) <= story_entry_tokens


def test_append_mode_accumulates_story_across_batches(client, sample_chat, monkeypatch):
    """The core fix: consecutive batches ADD story beats instead of compressing."""
    from cozy.summarizer import APPEND_INSTRUCTIONS
    _batch_every(monkeypatch, 1)
    replies = iter((
        'STORY SO FAR\n- S1 happened\n\nBONDS\n- A & B: allies',
        'STORY SO FAR\n- S2 happened\n\nBONDS\n- A & B: allies',
    ))
    calls = []

    def complete(messages, cap_tokens=0):
        calls.append(messages)
        return next(replies)

    monkeypatch.setattr(summaries, 'call_summarizer', complete)
    ids = _add_messages(client, sample_chat['id'], 2)
    summaries._run_summary_job(sample_chat['id'], ids[-1], rebuild=False)

    with shared.get_db() as conn:
        row = conn.execute('SELECT summary_json FROM chats WHERE id=?',
                           (sample_chat['id'],)).fetchone()
    story = summary_to_text(parse_summary_json(row['summary_json']))
    assert 'S1 happened' in story and 'S2 happened' in story  # additive, both kept
    # Both batches used the additive builder while under cap.
    assert len(calls) == 2
    assert all(m[0]['content'] == APPEND_INSTRUCTIONS for m in calls)


def test_each_batch_becomes_one_entry_stamped_with_its_message_range(
        client, sample_chat, monkeypatch):
    """The whole contract: N messages in, one ranged entry out, per batch."""
    _batch_every(monkeypatch, 2)
    replies = iter((
        'STORY SO FAR\n- First stretch.\n\nBONDS\n- A & B: allies',
        'STORY SO FAR\n- Second stretch.\n\nBONDS\n- A & B: allies',
    ))
    monkeypatch.setattr(
        summaries, 'call_summarizer', lambda messages, cap_tokens=0: next(replies))
    ids = _add_messages(client, sample_chat['id'], 4)

    summaries._run_summary_job(sample_chat['id'], ids[-1])

    with shared.get_db() as conn:
        row = conn.execute('SELECT summary_json FROM chats WHERE id=?',
                           (sample_chat['id'],)).fetchone()
    story = [l for l in parse_summary_json(row['summary_json'])['lines']
             if l['section'] == 'story']
    assert [l['text'] for l in story] == ['First stretch.', 'Second stretch.']
    assert [(l['start_msg_id'], l['end_msg_id']) for l in story] == [
        (ids[0], ids[1]), (ids[2], ids[3]),
    ]


def test_multi_batch_job_reports_overall_progress(client, sample_chat, monkeypatch):
    _batch_every(monkeypatch, 2)
    monkeypatch.setattr(
        summaries, 'call_summarizer', lambda messages, cap_tokens=0: CANNED)
    details = []
    set_status = summaries._set_status

    def record_status(*args, **kwargs):
        details.append(kwargs.get('detail'))
        return set_status(*args, **kwargs)

    monkeypatch.setattr(summaries, '_set_status', record_status)
    ids = _add_messages(client, sample_chat['id'], 5)

    summaries._run_summary_job(sample_chat['id'], ids[-1])

    assert details[:3] == [
        'Summarizing… (batch 1/3)',
        'Summarizing… (batch 2/3)',
        'Summarizing… (batch 3/3)',
    ]


def test_a_split_reply_still_produces_exactly_one_entry(
        client, sample_chat, monkeypatch):
    """A model that ignores the one-entry rule must not desync entries from batches."""
    monkeypatch.setattr(
        summaries, 'call_summarizer',
        lambda messages, cap_tokens=0: (
            'STORY SO FAR\n- Beat one.\n- Beat two.\n- Beat three.\n\nBONDS\n- A & B: allies'
        ),
    )
    ids = _add_messages(client, sample_chat['id'], 2)

    summaries._run_summary_job(sample_chat['id'], ids[-1])

    with shared.get_db() as conn:
        row = conn.execute('SELECT summary_json FROM chats WHERE id=?',
                           (sample_chat['id'],)).fetchone()
    story = [l for l in parse_summary_json(row['summary_json'])['lines']
             if l['section'] == 'story']
    assert [l['text'] for l in story] == ['Beat one. Beat two. Beat three.']
    assert (story[0]['start_msg_id'], story[0]['end_msg_id']) == (ids[0], ids[-1])


def test_full_summary_sheds_its_oldest_entry(client, sample_chat, monkeypatch):
    """With no compression pass left, the cap is a rolling window over recent entries."""
    client.put('/api/settings', json={
        'context_max_tokens': '600',
        'summary_cap_pct': '10',
    })
    _batch_every(monkeypatch, 1)
    # Each delta fits its 36-token per-entry budget, while two entries cannot both fit
    # the 36-token STORY section, exercising ordinary oldest-first rolloff.
    beat = 'a fairly wordy beat carrying real narrative weight ' * 2
    replies = iter((
        f'STORY SO FAR\n- OLDEST {beat}\n\nBONDS',
        f'STORY SO FAR\n- MIDDLE {beat}\n\nBONDS',
        f'STORY SO FAR\n- NEWEST {beat}\n\nBONDS',
    ))
    monkeypatch.setattr(
        summaries, 'call_summarizer', lambda messages, cap_tokens=0: next(replies))
    ids = _add_messages(client, sample_chat['id'], 3)

    summaries._run_summary_job(sample_chat['id'], ids[-1])

    with shared.get_db() as conn:
        row = conn.execute('SELECT summary_json FROM chats WHERE id=?',
                           (sample_chat['id'],)).fetchone()
    stored = summary_to_text(parse_summary_json(row['summary_json']))
    assert 'OLDEST' not in stored      # rolled off
    assert 'NEWEST' in stored          # the recent past survives


def test_rebuild_drops_legacy_pinned_flags(client, sample_chat, monkeypatch):
    """Summaries written when pinning existed rebuild into ordinary entries."""
    cid = sample_chat['id']
    _store_summary(cid, {'lines': [
        {'section': 'story', 'text': 'Old beat.', 'pinned': True},
        {'section': 'bonds', 'text': 'A & B: allies.', 'pinned': True},
    ]})
    monkeypatch.setattr(
        summaries, 'call_summarizer',
        lambda messages, cap_tokens=0: 'STORY SO FAR\n- Fresh beat.\n\nBONDS\n- A & B: allies',
    )
    ids = _add_messages(client, cid, 2)

    summaries._run_summary_job(cid, ids[-1], rebuild=True)

    with shared.get_db() as conn:
        row = conn.execute('SELECT summary_json FROM chats WHERE id=?', (cid,)).fetchone()
    lines = parse_summary_json(row['summary_json'])['lines']
    assert [l['text'] for l in lines] == ['Fresh beat.', 'A & B: allies']
    assert not any('pinned' in l for l in lines)
    assert 'Old beat.' not in row['summary_json']



def test_append_overflow_cannot_evict_the_previous_entries(client, sample_chat, monkeypatch):
    """An oversized delta is cut to its entry limit, not left to swamp the section and
    push older memory out through enforce_cap."""
    from cozy.summarizer import APPEND_INSTRUCTIONS
    # An 800-token summary: STORY holds 480 tokens, one entry at most 60.
    client.put('/api/settings', json={'context_max_tokens': '8000', 'summary_cap_pct': '10'})
    over_cap = f"STORY SO FAR\n- {'sprawling detail ' * 100}\n\nBONDS\n- A & B: allies"
    calls = []

    def complete(messages, cap_tokens=0):
        calls.append(messages)
        return over_cap

    monkeypatch.setattr(summaries, 'call_summarizer', complete)
    ids = _add_messages(client, sample_chat['id'], 2)
    _store_summary(sample_chat['id'], {
        'lines': [{'section': 'story', 'text': 'previous useful memory'}],
    }, watermark=ids[0])
    summaries._run_summary_job(sample_chat['id'], ids[-1], rebuild=False)

    assert len(calls) == 2
    assert all(call[0]['content'] == APPEND_INSTRUCTIONS for call in calls)
    row = _chat_row(sample_chat['id'])
    story = [l['text'] for l in parse_summary_json(row['summary_json'])['lines']
             if l['section'] == 'story']
    assert story[0] == 'previous useful memory'
    assert story[1].startswith('sprawling detail') and estimate_tokens(story[1]) <= 60
    assert row['summary_up_to_msg_id'] == ids[-1]
    assert row['summary_status'] == 'idle'


def test_append_mode_merges_bonds_without_duplicating(client, sample_chat, monkeypatch):
    """Append mode replaces the BONDS section, so a relationship stays a single line."""
    monkeypatch.setattr(
        summaries, 'call_summarizer',
        lambda messages, cap_tokens=0: 'STORY SO FAR\n- new beat\n\nBONDS\n- A & B: close now')
    ids = _add_messages(client, sample_chat['id'], 2)
    _store_summary(sample_chat['id'], {'lines': [
        {'section': 'story', 'text': 'earlier beat'},
        {'section': 'bonds', 'text': 'A & B: uneasy'},
    ]}, watermark=ids[0])
    summaries._run_summary_job(sample_chat['id'], ids[-1], rebuild=False)

    with shared.get_db() as conn:
        row = conn.execute('SELECT summary_json FROM chats WHERE id=?',
                           (sample_chat['id'],)).fetchone()
    obj = parse_summary_json(row['summary_json'])
    bonds = [l['text'] for l in obj['lines'] if l['section'] == 'bonds']
    story = [l['text'] for l in obj['lines'] if l['section'] == 'story']
    assert bonds == ['A & B: close now']            # merged, not duplicated
    assert story == ['earlier beat', 'new beat']    # story accumulated


def test_append_mode_survives_reply_with_empty_bonds_section(client, sample_chat, monkeypatch):
    """A batch whose reply omits BONDS bullets keeps the stored relationship state."""
    monkeypatch.setattr(
        summaries, 'call_summarizer',
        lambda messages, cap_tokens=0: 'STORY SO FAR\n- new beat\n\nBONDS')
    ids = _add_messages(client, sample_chat['id'], 2)
    _store_summary(sample_chat['id'], {'lines': [
        {'section': 'story', 'text': 'earlier beat'},
        {'section': 'bonds', 'text': 'A & B: uneasy'},
    ]}, watermark=ids[0])
    summaries._run_summary_job(sample_chat['id'], ids[-1], rebuild=False)

    with shared.get_db() as conn:
        row = conn.execute('SELECT summary_json, summary_status FROM chats WHERE id=?',
                           (sample_chat['id'],)).fetchone()
    obj = parse_summary_json(row['summary_json'])
    assert row['summary_status'] == 'idle'
    assert [l['text'] for l in obj['lines'] if l['section'] == 'bonds'] == ['A & B: uneasy']
    assert [l['text'] for l in obj['lines'] if l['section'] == 'story'] == ['earlier beat', 'new beat']


def test_run_job_strips_hidden_thinking(client, sample_chat, monkeypatch):
    response = client.post(
        f'/api/chats/{sample_chat["id"]}/messages',
        json={
            'role': 'character',
            'content': '<think>private reasoning</think>The visible answer.',
        },
    )
    message_id = response.get_json()['id']
    calls = []
    monkeypatch.setattr(
        summaries,
        'call_summarizer',
        lambda messages, cap_tokens=0: calls.append(messages) or CANNED,
    )

    summaries._run_summary_job(sample_chat['id'], message_id)

    prompt = calls[0][1]['content']
    assert 'private reasoning' not in prompt
    assert 'The visible answer.' in prompt


def test_thinking_only_chunk_advances_without_model_call(
        client, sample_chat, monkeypatch):
    cid = sample_chat['id']
    original = {'lines': [
        {'section': 'story', 'text': 'Existing visible memory.'},
    ]}
    _store_summary(cid, original)
    response = client.post(f'/api/chats/{cid}/messages', json={
        'role': 'character',
        'content': '<think>reasoning that should stay hidden</think>',
    })
    message_id = response.get_json()['id']
    monkeypatch.setattr(
        summaries,
        'call_summarizer',
        lambda *args, **kwargs: pytest.fail('thinking-only history must not call the model'),
    )

    summaries._run_summary_job(cid, message_id)

    with shared.get_db() as conn:
        row = conn.execute(
            'SELECT summary_json, summary_up_to_msg_id, summary_status FROM chats WHERE id=?',
            (cid,),
        ).fetchone()
    assert parse_summary_json(row['summary_json']) == original
    assert row['summary_up_to_msg_id'] == message_id
    assert row['summary_status'] == 'idle'


def test_thinking_only_rebuild_publishes_empty_without_model_call(
        client, sample_chat, monkeypatch):
    """A rebuild starts from a blank slate, so nothing visible means nothing kept."""
    cid = sample_chat['id']
    _store_summary(cid, {'lines': [
        {'section': 'story', 'text': 'Old detail.'},
        {'section': 'bonds', 'text': 'A & B: allies.'},
    ]})
    response = client.post(f'/api/chats/{cid}/messages', json={
        'role': 'character', 'content': '<thinking>hidden only</thinking>',
    })
    message_id = response.get_json()['id']
    monkeypatch.setattr(
        summaries,
        'call_summarizer',
        lambda *args, **kwargs: pytest.fail('thinking-only rebuild must not call the model'),
    )

    summaries._run_summary_job(cid, message_id, rebuild=True)

    with shared.get_db() as conn:
        row = conn.execute(
            'SELECT summary_json, summary_up_to_msg_id FROM chats WHERE id=?',
            (cid,),
        ).fetchone()
    assert parse_summary_json(row['summary_json']) == {'lines': []}
    assert row['summary_up_to_msg_id'] == message_id


def test_pause_discards_inflight_result_and_keeps_last_checkpoint(
        client, sample_chat, monkeypatch):
    cid = sample_chat['id']
    client.put(f'/api/chats/{cid}', json={'summary_enabled': True})
    _batch_every(monkeypatch, 1)
    ids = _add_messages(client, cid, 2)
    with shared.get_db() as conn:
        conn.execute(
            "UPDATE chats SET summary_status='running', summary_status_detail='Starting…' "
            'WHERE id=?',
            (cid,),
        )
    calls = 0

    def pause_on_second_call(messages, cap_tokens=0):
        nonlocal calls
        calls += 1
        if calls == 2:
            client.put(f'/api/chats/{cid}', json={'summary_enabled': False})
        return CANNED

    monkeypatch.setattr(summaries, 'call_summarizer', pause_on_second_call)

    summaries._run_summary_job(cid, ids[-1], require_running=True)

    with shared.get_db() as conn:
        row = conn.execute(
            'SELECT summary_json, summary_up_to_msg_id, summary_status, '
            'summary_status_detail FROM chats WHERE id=?',
            (cid,),
        ).fetchone()
    assert calls == 2
    assert row['summary_up_to_msg_id'] == ids[0]
    assert parse_summary_json(row['summary_json'])['lines']
    assert row['summary_status'] == 'idle'
    assert row['summary_status_detail'] == ''


def test_per_chat_pause_during_rebuild_preserves_previous_state(
        client, sample_chat, monkeypatch):
    cid = sample_chat['id']
    ids = _add_messages(client, cid, 2)
    original = {'lines': [
        {'section': 'story', 'text': 'Complete previous memory.'},
    ]}
    _store_summary(cid, original, ids[-1])
    client.put(f'/api/chats/{cid}', json={'summary_enabled': True})
    with shared.get_db() as conn:
        conn.execute(
            "UPDATE chats SET summary_status='running', summary_status_detail='Starting…' "
            'WHERE id=?',
            (cid,),
        )

    def disable_during_call(messages, cap_tokens=0):
        response = client.put(f'/api/chats/{cid}', json={'summary_enabled': False})
        assert response.status_code == 200
        return CANNED

    monkeypatch.setattr(summaries, 'call_summarizer', disable_during_call)
    summaries._run_summary_job(cid, ids[-1], rebuild=True, require_running=True)

    with shared.get_db() as conn:
        row = conn.execute(
            'SELECT summary_json, summary_up_to_msg_id, summary_status, '
            'summary_status_detail FROM chats WHERE id=?',
            (cid,),
        ).fetchone()
    assert parse_summary_json(row['summary_json']) == original
    assert row['summary_up_to_msg_id'] == ids[-1]
    assert row['summary_status'] == 'idle'
    assert row['summary_status_detail'] == ''


def test_disable_reenable_new_run_cannot_revive_old_worker(
        client, sample_chat, monkeypatch):
    cid = sample_chat['id']
    client.put(f'/api/chats/{cid}', json={'summary_enabled': True})
    message_id = _add_messages(client, cid, 1)[0]
    spawned_tokens = []

    def capture_spawn(chat_id, up_to_msg_id, rebuild, job_token):
        assert chat_id == cid and up_to_msg_id == message_id
        spawned_tokens.append(job_token)

    monkeypatch.setattr(summaries, '_spawn_job', capture_spawn)
    first = client.post(
        f'/api/chats/{cid}/summary/run', json={'up_to_msg_id': message_id}
    )
    assert first.status_code == 202
    old_token = spawned_tokens[0]

    def replace_run_during_old_call(messages, cap_tokens=0):
        paused = client.put(f'/api/chats/{cid}', json={'summary_enabled': False})
        assert paused.get_json()['summary_status'] == 'idle'
        client.put(f'/api/chats/{cid}', json={'summary_enabled': True})

        replacement = client.post(
            f'/api/chats/{cid}/summary/run', json={'up_to_msg_id': message_id}
        )
        assert replacement.status_code == 202
        return CANNED

    monkeypatch.setattr(summaries, 'call_summarizer', replace_run_during_old_call)
    summaries._run_summary_job(
        cid,
        message_id,
        require_running=True,
        job_token=old_token,
    )

    assert len(spawned_tokens) == 2
    assert spawned_tokens[1] != old_token
    assert summaries._job_tokens.get(cid) == spawned_tokens[1]
    with shared.get_db() as conn:
        row = conn.execute(
            'SELECT summary_json, summary_up_to_msg_id, summary_status, '
            'summary_status_detail FROM chats WHERE id=?',
            (cid,),
        ).fetchone()
    assert parse_summary_json(row['summary_json']) == {'lines': []}
    assert row['summary_up_to_msg_id'] is None
    assert row['summary_status'] == 'running'
    assert row['summary_status_detail'] == 'Starting…'


@pytest.mark.parametrize('terminal_event', ['pause', 'delete'])
def test_terminal_worker_releases_current_generation_when_status_cannot_change(
        client, sample_chat, monkeypatch, terminal_event):
    cid = sample_chat['id']
    client.put(f'/api/chats/{cid}', json={'summary_enabled': True})
    message_id = _add_messages(client, cid, 1)[0]
    spawned_tokens = []

    monkeypatch.setattr(
        summaries,
        '_spawn_job',
        lambda chat_id, up_to_msg_id, rebuild, job_token, *a: spawned_tokens.append(job_token),
    )
    started = client.post(
        f'/api/chats/{cid}/summary/run', json={'up_to_msg_id': message_id}
    )
    assert started.status_code == 202
    job_token = spawned_tokens[0]
    assert summaries._job_tokens.get(cid) == job_token

    if terminal_event == 'pause':
        paused = client.put(f'/api/chats/{cid}', json={'summary_enabled': False})
        assert paused.status_code == 200
    else:
        deleted = client.delete(f'/api/chats/{cid}')
        assert deleted.status_code == 200

    monkeypatch.setattr(
        summaries,
        'call_summarizer',
        lambda *args, **kwargs: pytest.fail('paused/deleted worker must not call the model'),
    )
    summaries._run_summary_job(
        cid,
        message_id,
        require_running=True,
        job_token=job_token,
    )

    assert cid not in summaries._job_tokens


def test_run_job_rebuild_resets(client, sample_chat, monkeypatch):
    monkeypatch.setattr(summaries, 'call_summarizer', lambda messages, cap_tokens=0: CANNED)
    ids = _add_messages(client, sample_chat['id'], 3)
    summaries._run_summary_job(sample_chat['id'], ids[-1], rebuild=False)
    # Rebuild folds from scratch and still ends at the same watermark.
    summaries._run_summary_job(sample_chat['id'], ids[-1], rebuild=True)
    with shared.get_db() as conn:
        row = conn.execute('SELECT summary_up_to_msg_id, summary_json FROM chats WHERE id=?',
                           (sample_chat['id'],)).fetchone()
    assert row['summary_up_to_msg_id'] == ids[-1]
    assert parse_summary_json(row['summary_json'])['lines']


def test_rebuild_keeps_batches_completed_before_a_failure(
        client, sample_chat, monkeypatch):
    """A rebuild checkpoints per batch, so a failure keeps the work already paid for.

    The watermark lands on the last batch that succeeded, which is what lets the next
    run resume from there instead of re-summarizing the whole transcript.
    """
    ids = _add_messages(client, sample_chat['id'], 3)
    monkeypatch.setattr(summaries, 'call_summarizer', lambda messages, cap_tokens=0: CANNED)
    summaries._run_summary_job(sample_chat['id'], ids[-1])

    _batch_every(monkeypatch, 1)
    calls = 0

    def fail_second_batch(messages, cap_tokens=0):
        nonlocal calls
        calls += 1
        # Calls two and three are the second batch's attempt and its retry.
        if calls in (2, 3):
            raise RuntimeError('second batch failed')
        return CANNED

    monkeypatch.setattr(summaries, 'call_summarizer', fail_second_batch)
    summaries._run_summary_job(sample_chat['id'], ids[-1], rebuild=True)

    with shared.get_db() as conn:
        after = conn.execute(
            'SELECT summary_json, summary_up_to_msg_id, summary_status FROM chats WHERE id=?',
            (sample_chat['id'],),
        ).fetchone()
    # Batch one committed before batch two failed: its message range is retired, and
    # the rebuild's from-scratch summary holds that batch rather than nothing.
    assert after['summary_up_to_msg_id'] == ids[0]
    assert parse_summary_json(after['summary_json'])['lines']
    assert after['summary_status'] == 'error'




# ── Endpoints ───────────────────────────────────────────────────────────────

def test_run_endpoint_overlap_guard(client, sample_chat, monkeypatch):
    # Stub the thread spawn so the claimed 'running' state persists for the test.
    monkeypatch.setattr(summaries, '_spawn_job', lambda *a, **k: None)
    client.put(f'/api/chats/{sample_chat["id"]}', json={'summary_enabled': True})
    ids = _add_messages(client, sample_chat['id'], 2)
    r1 = client.post(f'/api/chats/{sample_chat["id"]}/summary/run', json={'up_to_msg_id': ids[-1]})
    assert r1.status_code == 202
    assert r1.get_json()['summary_status'] == 'running'
    # A second run while one is in flight is a no-op.
    r2 = client.post(f'/api/chats/{sample_chat["id"]}/summary/run', json={'up_to_msg_id': ids[-1]})
    assert r2.status_code == 409
    assert r2.get_json()['already_running'] is True


@pytest.mark.parametrize('boundary', [None, 0, -1, '1', True])
def test_run_endpoint_requires_positive_integer_boundary(
        client, sample_chat, monkeypatch, boundary):
    monkeypatch.setattr(summaries, '_spawn_job', lambda *a, **k: None)
    response = client.post(
        f'/api/chats/{sample_chat["id"]}/summary/run',
        json={'up_to_msg_id': boundary},
    )
    assert response.status_code == 400
    with shared.get_db() as conn:
        status = conn.execute(
            'SELECT summary_status FROM chats WHERE id=?', (sample_chat['id'],)
        ).fetchone()[0]
    assert status == 'idle'


def test_run_endpoint_rejects_boundary_from_another_chat(
        client, sample_chat, sample_character, monkeypatch):
    other = client.post(
        f'/api/characters/{sample_character["id"]}/chats',
        json={'name': 'Other chat'},
    ).get_json()
    other_message = _add_messages(client, other['id'], 1)[0]
    monkeypatch.setattr(summaries, '_spawn_job', lambda *a, **k: None)

    response = client.post(
        f'/api/chats/{sample_chat["id"]}/summary/run',
        json={'up_to_msg_id': other_message},
    )

    assert response.status_code == 400
    assert 'this chat' in response.get_json()['error']


def test_run_endpoint_honors_per_chat_feature_gate(client, sample_chat, monkeypatch):
    message_id = _add_messages(client, sample_chat['id'], 1)[0]
    monkeypatch.setattr(summaries, '_spawn_job', lambda *a, **k: None)

    response = client.post(
        f'/api/chats/{sample_chat["id"]}/summary/run',
        json={'up_to_msg_id': message_id},
    )

    assert response.status_code == 409
    assert 'this chat' in response.get_json()['error']


def test_run_endpoint_missing_chat(client):
    r = client.post('/api/chats/999999/summary/run', json={'up_to_msg_id': 1})
    assert r.status_code == 404


def test_reset_endpoint_clears_summary_and_watermark(client, sample_chat, monkeypatch):
    monkeypatch.setattr(summaries, 'call_summarizer', lambda messages, cap_tokens=0: CANNED)
    ids = _add_messages(client, sample_chat['id'], 3)
    summaries._run_summary_job(sample_chat['id'], ids[-1], rebuild=False)
    _store_summary(sample_chat['id'], {
        'lines': [{'section': 'bonds', 'text': 'keep?'}],
    }, ids[-1])

    r = client.post(f'/api/chats/{sample_chat["id"]}/summary/reset')
    assert r.status_code == 200
    body = r.get_json()
    assert body['summary'] == {'lines': []}
    assert body['summary_up_to_msg_id'] is None
    assert body['summary_status'] == 'idle'
    with shared.get_db() as conn:
        row = conn.execute('SELECT summary_json, summary_up_to_msg_id FROM chats WHERE id=?',
                           (sample_chat['id'],)).fetchone()
    assert row['summary_json'] == '' and row['summary_up_to_msg_id'] is None


def test_cancel_endpoint_keeps_completed_batches_and_stops_the_rest(
        client, sample_chat, monkeypatch):
    """The point of the button: abandon a long backfill without losing its progress."""
    cid = sample_chat['id']
    client.put(f'/api/chats/{cid}', json={'summary_enabled': True})
    _batch_every(monkeypatch, 1)
    ids = _add_messages(client, cid, 6)
    with shared.get_db() as conn:
        conn.execute(
            "UPDATE chats SET summary_status='running', summary_status_detail='Starting…' "
            'WHERE id=?',
            (cid,),
        )
    calls = 0

    def cancel_during_third_batch(messages, cap_tokens=0):
        nonlocal calls
        calls += 1
        if calls == 3:
            assert client.post(f'/api/chats/{cid}/summary/cancel').status_code == 200
        return CANNED

    monkeypatch.setattr(summaries, 'call_summarizer', cancel_during_third_batch)

    summaries._run_summary_job(cid, ids[-1], require_running=True)

    with shared.get_db() as conn:
        row = conn.execute(
            'SELECT summary_json, summary_up_to_msg_id, summary_status, '
            'summary_status_detail FROM chats WHERE id=?',
            (cid,),
        ).fetchone()
    # The batch in flight when cancel landed is discarded; the two before it are kept.
    assert calls == 3
    assert row['summary_up_to_msg_id'] == ids[1]
    assert parse_summary_json(row['summary_json'])['lines']
    # Cancelling is a normal outcome, not a failure.
    assert row['summary_status'] == 'idle'
    assert row['summary_status_detail'] == ''


def test_cancel_during_the_retry_pause_sends_no_second_request(
        client, sample_chat, monkeypatch):
    cid = sample_chat['id']
    client.put(f'/api/chats/{cid}', json={'summary_enabled': True})
    ids = _add_messages(client, cid, 2)
    with shared.get_db() as conn:
        conn.execute(
            "UPDATE chats SET summary_status='running', summary_status_detail='Starting…' "
            'WHERE id=?',
            (cid,),
        )
    calls = []

    def unreachable(messages, cap_tokens=0):
        calls.append(messages)
        raise RuntimeError('connection refused')

    monkeypatch.setattr(summaries, 'call_summarizer', unreachable)
    monkeypatch.setattr(
        summaries.time, 'sleep',
        lambda seconds: client.post(f'/api/chats/{cid}/summary/cancel'),
    )

    summaries._run_summary_job(cid, ids[-1], require_running=True)

    assert len(calls) == 1
    row = _chat_row(cid)
    assert row['summary_status'] == 'idle'
    assert row['summary_status_detail'] == ''
    assert row['summary_up_to_msg_id'] is None


def test_cancel_endpoint_blocks_a_later_publish_from_the_stopped_worker(
        client, sample_chat, monkeypatch):
    """Dropping the generation token, not just the status, is what makes cancel bite."""
    cid = sample_chat['id']
    client.put(f'/api/chats/{cid}', json={'summary_enabled': True})
    ids = _add_messages(client, cid, 2)
    token = 'live-token'
    summaries._job_tokens[cid] = token
    with shared.get_db() as conn:
        conn.execute("UPDATE chats SET summary_status='running' WHERE id=?", (cid,))

    assert client.post(f'/api/chats/{cid}/summary/cancel').status_code == 200

    assert cid not in summaries._job_tokens
    with pytest.raises(summaries._SummaryPaused):
        summaries._persist_summary(
            cid, {'lines': [{'section': 'story', 'text': 'late work'}]}, ids[-1], 0,
            require_running=True, job_token=token,
        )
    with shared.get_db() as conn:
        row = conn.execute('SELECT summary_json FROM chats WHERE id=?', (cid,)).fetchone()
    assert row['summary_json'] == ''


def test_cancel_endpoint_is_idempotent_when_nothing_is_running(client, sample_chat):
    cid = sample_chat['id']
    r = client.post(f'/api/chats/{cid}/summary/cancel')
    assert r.status_code == 200
    assert r.get_json()['summary_status'] == 'idle'
    # A second call is equally harmless.
    assert client.post(f'/api/chats/{cid}/summary/cancel').status_code == 200


def test_cancel_endpoint_missing_chat(client):
    assert client.post('/api/chats/999999/summary/cancel').status_code == 404


def test_reset_endpoint_rejects_while_summary_is_running(client, sample_chat, monkeypatch):
    monkeypatch.setattr(summaries, '_spawn_job', lambda *a, **k: None)
    client.put(f'/api/chats/{sample_chat["id"]}', json={'summary_enabled': True})
    message_id = _add_messages(client, sample_chat['id'], 1)[0]
    started = client.post(
        f'/api/chats/{sample_chat["id"]}/summary/run',
        json={'up_to_msg_id': message_id},
    )
    assert started.status_code == 202

    response = client.post(f'/api/chats/{sample_chat["id"]}/summary/reset')

    assert response.status_code == 409
    assert response.get_json()['summary_status'] == 'running'


def test_reset_endpoint_missing_chat(client):
    assert client.post('/api/chats/999999/summary/reset').status_code == 404


def test_status_endpoint(client, sample_chat):
    r = client.get(f'/api/chats/{sample_chat["id"]}/summary/status')
    assert r.status_code == 200
    body = r.get_json()
    assert body['summary_status'] == 'idle'
    assert body['summary'] == {'lines': []}












# ── Chat persistence (update_chat / chat_to_dict) ───────────────────────────

def test_update_chat_can_toggle_summary_enablement(client, sample_chat):
    cid = sample_chat['id']
    r = client.put(f'/api/chats/{cid}', json={'summary_enabled': True})
    assert r.status_code == 200
    assert r.get_json()['summary_enabled'] is True


def test_update_chat_rejects_client_summary_content(client, sample_chat):
    cid = sample_chat['id']
    original = {'lines': [
        {'section': 'story', 'text': 'Server-owned memory.'},
    ]}
    _store_summary(cid, original)

    response = client.put(f'/api/chats/{cid}', json={
        'summary_json': {'lines': [
            {'section': 'story', 'text': 'Stale client memory.'},
        ]},
    })

    assert response.status_code == 400
    with shared.get_db() as conn:
        raw = conn.execute('SELECT summary_json FROM chats WHERE id=?', (cid,)).fetchone()[0]
    assert parse_summary_json(raw) == original


def test_generic_chat_patch_cannot_overwrite_interleaved_worker_publish(
        client, sample_chat, monkeypatch):
    cid = sample_chat['id']
    message_id = _add_messages(client, cid, 1)[0]
    monkeypatch.setattr(summaries, 'call_summarizer', lambda messages, cap_tokens=0: CANNED)
    original_get_db = chat_routes.get_db
    interleaved = False

    class InterleavingConnection:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, params=()):
            nonlocal interleaved
            if not interleaved and sql.startswith('UPDATE chats SET'):
                interleaved = True
                summaries._run_summary_job(cid, message_id)
            return self._conn.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    @contextmanager
    def interleaving_get_db():
        with original_get_db() as conn:
            yield InterleavingConnection(conn)

    monkeypatch.setattr(chat_routes, 'get_db', interleaving_get_db)

    response = client.put(f'/api/chats/{cid}', json={'name': 'Renamed safely'})

    assert response.status_code == 200
    assert interleaved is True
    with shared.get_db() as conn:
        row = conn.execute(
            'SELECT name, summary_json, summary_up_to_msg_id FROM chats WHERE id=?',
            (cid,),
        ).fetchone()
    assert row['name'] == 'Renamed safely'
    assert row['summary_up_to_msg_id'] == message_id
    assert 'Something happened.' in summary_to_text(parse_summary_json(row['summary_json']))


def test_update_chat_ignores_client_watermark(client, sample_chat):
    cid = sample_chat['id']
    client.put(f'/api/chats/{cid}', json={'summary_up_to_msg_id': 12345})
    with shared.get_db() as conn:
        wm = conn.execute('SELECT summary_up_to_msg_id FROM chats WHERE id=?', (cid,)).fetchone()[0]
    assert wm is None  # watermark is server-managed only


# ── Settings masking + config ───────────────────────────────────────────────

def test_summary_api_key_masked_and_not_clobbered(client):
    client.put('/api/settings', json={'summary_api_key': 'secretkey1234'})
    r = client.get('/api/settings')
    body = r.get_json()
    assert 'summary_api_key' not in body
    assert body['summary_api_key_set'] is True
    assert body['summary_api_key_masked'] and body['summary_api_key_masked'] != 'secretkey1234'
    # Echoing the masked placeholder back must not overwrite the real key.
    client.put('/api/settings', json={'summary_api_key': body['summary_api_key_masked']})
    assert get_settings().get('summary_api_key') == 'secretkey1234'
    # An explicit empty value is different from the mask and restores main-key fallback.
    cleared = client.put('/api/settings', json={'summary_api_key': ''}).get_json()
    assert get_settings().get('summary_api_key') == ''
    assert cleared['summary_api_key_set'] is False


def test_summary_llm_settings_fallback(client):
    client.put('/api/settings', json={
        'api_endpoint': 'http://main', 'api_key': 'mainkey', 'api_model': 'main-model',
    })
    # Blank summary_* -> falls back to the main connection.
    assert summaries._summary_llm_settings() == ('http://main', 'mainkey', 'main-model')
    client.put('/api/settings', json={
        'summary_api_endpoint': 'http://sum', 'summary_api_model': 'sum-model',
    })
    ep, key, model = summaries._summary_llm_settings()
    assert ep == 'http://sum' and model == 'sum-model' and key == 'mainkey'


def test_config_defaults_seeded(client):
    s = get_settings()
    assert s['summary_cap_pct'] == '10'
    # A batch is an amount of text now, so there is no message count left to set.
    assert 'summary_trigger_interval' not in s
    # A batch is now one entry, so there is no second pass left to size.
    assert 'summary_compress_batch' not in s


# ── Batch sizing ────────────────────────────────────────────────────────────


def test_token_batches_close_on_the_message_that_reaches_the_target():
    assert token_batches([4, 4, 4, 4, 4, 4], 10) == [(0, 3), (3, 6)]


def test_token_batches_give_an_oversized_message_a_batch_of_its_own():
    assert token_batches([2, 50, 2, 2, 2, 2], 10) == [(0, 2), (2, 6)]
    assert token_batches([50, 50], 10) == [(0, 1), (1, 2)]


def test_token_batches_fold_a_trailing_sliver_into_the_previous_batch():
    # 4 tokens left over is under half of 10: it joins the batch before it.
    assert token_batches([6, 6, 4], 10) == [(0, 3)]
    # 5 is half: enough to stand as a batch of its own.
    assert token_batches([6, 6, 5], 10) == [(0, 2), (2, 3)]


def test_token_batches_keep_a_short_run_as_one_batch():
    assert token_batches([1, 2], 10) == [(0, 2)]
    assert token_batches([], 10) == []


def test_token_batches_carry_zero_size_messages_along():
    """A turn that was all hidden reasoning measures 0 and rides with its neighbours."""
    assert token_batches([0, 0, 10, 0, 10], 10) == [(0, 3), (3, 5)]
    assert token_batches([0, 0], 10) == [(0, 2)]


@pytest.mark.parametrize('context, expected', [
    ('32768', 3200),
    ('128000', 3200),
    ('16384', 2048),
    ('8192', 1024),
    ('4', 1),
    ('0', 3200),
    ('', 3200),
    ('not-a-number', 3200),
])
def test_batch_target_is_capped_at_an_eighth_of_the_context(context, expected):
    assert summaries._batch_target_tokens({'context_max_tokens': context}) == expected


# ── Summary cap sizing ──────────────────────────────────────────────────────


def test_unlimited_context_disables_summary_cap():
    assert summaries._cap_tokens({
        'context_max_tokens': '0',
        'summary_cap_pct': '10',
    }) == 0


@pytest.mark.parametrize(
    'pct',
    ['not-a-number', 'nan', 'inf', '-inf', float('nan'), float('inf'), 10 ** 400],
)
def test_summary_cap_defaults_malformed_or_non_finite_percent(pct):
    assert summaries._cap_tokens({
        'context_max_tokens': '1000',
        'summary_cap_pct': pct,
    }) == 100


@pytest.mark.parametrize(
    ('pct', 'expected'),
    [('0', 10), ('-5', 10), ('1', 10), ('90', 900), ('91', 900), ('1e308', 900)],
)
def test_summary_cap_clamps_percent_to_ui_bounds(pct, expected):
    assert summaries._cap_tokens({
        'context_max_tokens': '1000',
        'summary_cap_pct': pct,
    }) == expected


@pytest.mark.parametrize(
    'context_tokens',
    ['not-an-integer', 'nan', 'inf', '-inf', float('nan'), float('inf')],
)
def test_summary_cap_defaults_malformed_or_non_finite_context(context_tokens):
    assert summaries._cap_tokens({
        'context_max_tokens': context_tokens,
        'summary_cap_pct': '10',
    }) == 3276


def test_summary_cap_handles_arbitrarily_large_context_without_float_overflow():
    context_tokens = 10 ** 400
    assert summaries._cap_tokens({
        'context_max_tokens': context_tokens,
        'summary_cap_pct': '90',
    }) == context_tokens * 9 // 10


# ── Completion budget ───────────────────────────────────────────────────────


def test_completion_budget_uses_the_main_connections_response_reserve():
    """A summary run and a chat completion never overlap, so the summarizer spends the
    allowance already reserved for the main connection — headroom a reasoning model
    needs, since its thinking is billed against this same budget."""
    assert summaries._completion_budget({'sampler_max_tokens': '4096'}, 3276) == 4096


def test_completion_budget_keeps_the_cap_derived_floor():
    # A small Max response tokens must not starve a large configured summary.
    assert summaries._completion_budget({'sampler_max_tokens': '512'}, 3276) == 4095


def test_completion_budget_prefers_extra_params_max_tokens():
    # buildChatPayload merges extra_request_params *over* the samplers, so that value
    # is what the main connection actually sends.
    assert summaries._completion_budget(
        {'sampler_max_tokens': '512', 'extra_request_params': '{"max_tokens": 8000}'}, 0
    ) == 8000


@pytest.mark.parametrize('raw', ['', 'not json', '[]', '{}', '{"temperature": 0.7}'])
def test_completion_budget_falls_back_when_extra_params_carry_no_max_tokens(raw):
    assert summaries._completion_budget(
        {'sampler_max_tokens': '2048', 'extra_request_params': raw}, 0
    ) == 2048


@pytest.mark.parametrize('bad', ['0', '-1', 'lots'])
def test_completion_budget_sends_none_for_an_unusable_override(bad):
    # An unusable override still wins in the real payload, so honour it as "no bound"
    # rather than quietly substituting a limit the main connection would not send.
    assert summaries._completion_budget(
        {'sampler_max_tokens': '2048',
         'extra_request_params': '{"max_tokens": %s}' % json.dumps(bad)}, 0
    ) == 0


@pytest.mark.parametrize('raw', [None, '', 'lots'])
def test_completion_budget_defaults_without_a_saved_sampler_row(raw):
    # Samplers only reach settings once saved; SAMPLER_DEFAULTS is 512.
    assert summaries._completion_budget({'sampler_max_tokens': raw}, 0) == 512


# ── Migration ───────────────────────────────────────────────────────────────

def test_migration_idempotent_and_columns_present():
    # Fixture already ran init_db once; a second call must not error.
    schema.init_db()
    with shared.get_db() as conn:
        cols = {c[1] for c in conn.execute('PRAGMA table_info(chats)').fetchall()}
    for col in ('summary_enabled', 'summary_json', 'summary_up_to_msg_id',
                'summary_status', 'summary_status_detail'):
        assert col in cols


def test_migration_deletes_the_retired_compress_batch_setting(client):
    """Migration 10 removes the setting from databases that already have it."""
    with shared.get_db() as conn:
        conn.execute(
            'INSERT INTO settings (key, value) VALUES (?, ?) '
            'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            ('summary_compress_batch', '3'),
        )
        conn.execute('DELETE FROM schema_migrations WHERE version=?', (10,))

    schema.init_db()

    assert 'summary_compress_batch' not in get_settings()
    schema.init_db()  # rerunning must be a no-op, not an error
    assert 'summary_compress_batch' not in get_settings()


def test_migration_deletes_the_retired_trigger_interval_setting(client):
    """Migration 15 removes the message-count batch setting, and startup never re-seeds it."""
    with shared.get_db() as conn:
        conn.execute(
            'INSERT INTO settings (key, value) VALUES (?, ?) '
            'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            ('summary_trigger_interval', '4'),
        )
        conn.execute('DELETE FROM schema_migrations WHERE version=?', (15,))

    schema.init_db()

    assert 'summary_trigger_interval' not in get_settings()
    schema.init_db()  # rerunning must be a no-op, not an error
    assert 'summary_trigger_interval' not in get_settings()


def test_startup_recovery_resets_running(client, sample_chat):
    cid = sample_chat['id']
    with shared.get_db() as conn:
        conn.execute(
            "UPDATE chats SET summary_status='running', summary_status_detail='stale progress' "
            'WHERE id=?',
            (cid,),
        )
    schema.init_db()  # should clear stale 'running'
    with shared.get_db() as conn:
        row = conn.execute(
            'SELECT summary_status, summary_status_detail FROM chats WHERE id=?', (cid,)
        ).fetchone()
    assert row['summary_status'] == 'idle'
    assert row['summary_status_detail'] == ''
