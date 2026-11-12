import io

from cozy import auth
from cozy import banner
from cozy import shared


def test_the_banner_names_build_address_data_and_password(monkeypatch):
    monkeypatch.setattr(auth, 'PASSWORD', 'secret')
    text = banner.banner(['http://127.0.0.1:5000', 'http://192.0.2.7:5000'], color=False)
    lines = text.splitlines()
    assert f'  build     {shared.BUILD_INFO.display}' in lines
    assert '  open      http://127.0.0.1:5000' in lines
    assert '            http://192.0.2.7:5000' in lines
    assert f'  data      {shared.DATA_DIR}' in lines
    assert '  password  on' in lines
    assert 'secret' not in text


def test_colour_only_on_a_terminal_without_no_color(monkeypatch, capsys):
    """Docker logs and log files get plain text: no escape codes."""
    assert '\033[' in banner.banner(['http://127.0.0.1:5000'], color=True)

    banner.print_banner(['http://127.0.0.1:5000'])  # capsys is not a terminal
    assert '\033[' not in capsys.readouterr().out

    monkeypatch.setattr(banner.sys.stdout, 'isatty', lambda: True)
    monkeypatch.setenv('NO_COLOR', '1')
    banner.print_banner(['http://127.0.0.1:5000'])
    assert '\033[' not in capsys.readouterr().out


def test_a_stdout_without_block_characters_gets_the_name_instead(monkeypatch):
    """As on Windows, writing a redirected stdout in its ANSI code page."""
    class Cp1252(io.StringIO):
        encoding = 'cp1252'

    monkeypatch.setattr(banner.sys, 'stdout', Cp1252())
    banner.print_banner(['http://127.0.0.1:5000'])
    out = banner.sys.stdout.getvalue()
    assert '█' not in out
    assert '  Cozy   a cozy roleplay frontend' in out.splitlines()
