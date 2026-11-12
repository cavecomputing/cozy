"""The wordmark Cozy prints to stdout when it starts serving.

Its own module because two servers print it: app.py's development server, and
gunicorn's when_ready hook in docker/gunicorn.conf.py, which can't import
app.py without loading the whole app into gunicorn's master process.
"""

import os
import sys

from cozy import auth, shared

# "cozy" in half-block letters, two rows of pixels to each row of text. The y's
# tail has the last row to itself, and the tagline sits beside it.
WORDMARK = (
    '▄▀▀▀▀  ▄▀▀▀▄  ▀▀▀█▀  █   █',
    '█      █   █   ▄▀    █   █',
    '▀▄▄▄▄  ▀▄▄▄▀  █▄▄▄▄  ▀▄▄▄█',
    '                     ▄▄▄▄▀',
)
# Cozy's yellow warming to its orange, then grey labels and blue addresses, as
# in the Cozy theme. 256-colour codes rather than the theme's exact hex, which
# not every terminal can show.
SHADES = (214, 214, 208, 208)
GREY = 245
BLUE = 109


def banner(addresses, color, wordmark=WORDMARK):
    """The wordmark, then the build, where to open Cozy, the data directory and
    whether a password is set. Without *color* it is plain text."""
    def paint(code, text):
        return f'\033[38;5;{code}m{text}\033[0m' if color else text

    art = [paint(shade, row) for shade, row in zip(SHADES, wordmark)]
    art[-1] += '   ' + paint(GREY, 'a cozy roleplay frontend')
    facts = [
        ('build', shared.BUILD_INFO.display),
        *((('open' if i == 0 else ''), paint(BLUE, address))
          for i, address in enumerate(addresses)),
        ('data', os.path.abspath(shared.DATA_DIR)),
        ('password', 'on' if auth.PASSWORD else 'off'),
    ]
    rows = [paint(GREY, f'{label:<9}') + ' ' + value for label, value in facts]
    return '\n'.join(['', *(line and '  ' + line for line in art + [''] + rows), ''])


def print_banner(addresses):
    """Print the banner, in colour only to a terminal and only without NO_COLOR,
    so `docker logs` and a log file get plain text."""
    color = sys.stdout.isatty() and not os.environ.get('NO_COLOR')
    text = banner(addresses, color)
    try:
        text.encode(sys.stdout.encoding or 'utf-8')
    except UnicodeEncodeError:
        # Windows writes a redirected stdout in its ANSI code page, which has
        # no block characters.
        text = banner(addresses, color, wordmark=('Cozy',))
    print(text, flush=True)
