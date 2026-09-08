"""Character-card validation rules (static/js/validate.js) under bare node.

The rules used to live inside modal.js, tangled with DOM effects, which is why
they were the one submit-blocking path in the app with no coverage at all.
"""

from .helpers import run_node_module

IMPORT = "import { validateCharacter } from './static/js/validate.js';"


def fields(code):
    """Run *code* and return the fields it reported, one per line."""
    return run_node_module(f"{IMPORT}\n{code}").split()


def test_a_named_card_being_edited_is_valid():
    out = run_node_module(f"""{IMPORT}
        const errs = validateCharacter({{ name: 'Sasha', isNew: false, hasImage: true }});
        console.log(errs.length);
    """)
    assert out.strip() == '0'


def test_a_missing_name_is_reported_against_the_name_field():
    out = run_node_module(f"""{IMPORT}
        const errs = validateCharacter({{ name: '', isNew: false, hasImage: true }});
        console.log(errs.length, errs[0].field, JSON.stringify(errs[0].message));
    """)
    count, field, message = out.split(maxsplit=2)
    assert count == '1'
    assert field == 'name'
    # The message has to say what to do, not merely that something is wrong.
    assert message.strip().strip('"').lower().startswith('give')


def test_whitespace_is_not_a_name():
    assert fields("""
        for (const n of ['   ', '\\t', '\\n', ''])
            console.log(validateCharacter({ name: n, isNew: false, hasImage: true })[0].field);
    """) == ['name'] * 4


def test_a_new_card_needs_an_image_and_an_edited_one_does_not():
    out = run_node_module(f"""{IMPORT}
        const isNew = validateCharacter({{ name: 'Sasha', isNew: true,  hasImage: false }});
        const edit  = validateCharacter({{ name: 'Sasha', isNew: false, hasImage: false }});
        console.log(isNew.map(e => e.field).join(',') || '-', edit.length);
    """)
    new_fields, edit_count = out.split()
    assert new_fields == 'avatar'
    assert edit_count == '0', 'editing a card must not be blocked by its avatar'


def test_both_problems_are_reported_together():
    """One save reports everything wrong with it, not the first thing only."""
    assert fields("""
        for (const e of validateCharacter({ name: '', isNew: true, hasImage: false }))
            console.log(e.field);
    """) == ['name', 'avatar']


def test_missing_input_does_not_throw():
    out = run_node_module(f"""{IMPORT}
        console.log(validateCharacter().length, validateCharacter({{}}).length);
    """)
    assert out.split() == ['1', '1']
