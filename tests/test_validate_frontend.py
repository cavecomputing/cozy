"""Character-card validation rules (static/js/validate.js) under bare node.

The rules used to live inside modal.js, tangled with DOM effects, which is why
they were the one submit-blocking path in the app with no coverage at all.
"""

from .helpers import run_node_module

IMPORT = "import { validateCharacter } from './static/js/validate.js';"


def fields(code):
    """Run *code* and return the fields it reported, one per line."""
    return run_node_module(f"{IMPORT}\n{code}").split()


def test_a_named_card_is_valid():
    out = run_node_module(f"""{IMPORT}
        const errs = validateCharacter({{ name: 'Sasha' }});
        console.log(errs.length);
    """)
    assert out.strip() == '0'


def test_a_missing_name_is_reported_against_the_name_field():
    out = run_node_module(f"""{IMPORT}
        const errs = validateCharacter({{ name: '' }});
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
            console.log(validateCharacter({ name: n })[0].field);
    """) == ['name'] * 4


def test_a_new_card_does_not_need_an_image():
    """A card saved without one lives on the placeholder PNG, drawn as initials."""
    out = run_node_module(f"""{IMPORT}
        console.log(validateCharacter({{ name: 'Sasha', isNew: true, hasImage: false }}).length);
    """)
    assert out.strip() == '0'


def test_missing_input_does_not_throw():
    out = run_node_module(f"""{IMPORT}
        console.log(validateCharacter().length, validateCharacter({{}}).length);
    """)
    assert out.split() == ['1', '1']
