from subprocess import CompletedProcess

from cozy import build_info
from cozy import shared
from cozy.build_info import BuildInfo


COMMIT = '0123456789abcdef0123456789abcdef01234567'
OTHER_COMMIT = 'fedcba9876543210fedcba9876543210fedcba98'


def _write(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding='utf-8')


def test_embedded_commit_takes_precedence_and_is_clean(tmp_path, monkeypatch):
    _write(tmp_path / '.cozy-commit', f'{COMMIT}\n')
    _write(tmp_path / '.git' / 'HEAD', f'{OTHER_COMMIT}\n')

    def unexpected_git_status(*args, **kwargs):
        raise AssertionError('embedded builds must not inspect the worktree')

    monkeypatch.setattr(build_info.subprocess, 'run', unexpected_git_status)

    info = build_info.get_build_info(tmp_path)
    assert info == BuildInfo(COMMIT)
    assert info.display == COMMIT[:12]


def test_resolves_loose_symbolic_ref(tmp_path, monkeypatch):
    _write(tmp_path / '.git' / 'HEAD', 'ref: refs/heads/main\n')
    _write(tmp_path / '.git' / 'refs' / 'heads' / 'main', f'{COMMIT}\n')
    monkeypatch.setattr(build_info, '_git_worktree_is_dirty', lambda base_dir: False)

    assert build_info.get_build_info(tmp_path) == BuildInfo(COMMIT)


def test_resolves_checkout_from_relative_base_path(tmp_path, monkeypatch):
    _write(tmp_path / '.git' / 'HEAD', 'ref: refs/heads/main\n')
    _write(tmp_path / '.git' / 'refs' / 'heads' / 'main', f'{COMMIT}\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(build_info, '_git_worktree_is_dirty', lambda base_dir: False)

    assert build_info.get_build_info('.') == BuildInfo(COMMIT)


def test_resolves_packed_symbolic_ref(tmp_path, monkeypatch):
    _write(tmp_path / '.git' / 'HEAD', 'ref: refs/heads/main\n')
    _write(
        tmp_path / '.git' / 'packed-refs',
        f'# pack-refs with: peeled fully-peeled sorted\n{COMMIT} refs/heads/main\n',
    )
    monkeypatch.setattr(build_info, '_git_worktree_is_dirty', lambda base_dir: False)

    assert build_info.get_build_info(tmp_path) == BuildInfo(COMMIT)


def test_resolves_detached_head(tmp_path, monkeypatch):
    _write(tmp_path / '.git' / 'HEAD', f'{COMMIT}\n')
    monkeypatch.setattr(build_info, '_git_worktree_is_dirty', lambda base_dir: False)

    assert build_info.get_build_info(tmp_path) == BuildInfo(COMMIT)


def test_invalid_or_missing_metadata_returns_unknown(tmp_path):
    _write(tmp_path / '.cozy-commit', 'not-a-commit\n')
    _write(tmp_path / '.git' / 'HEAD', 'also-not-a-commit\n')

    info = build_info.get_build_info(tmp_path)
    assert info == BuildInfo()
    assert info.display == 'unknown'
    assert build_info.get_build_info(tmp_path / 'missing') == BuildInfo()


def test_direct_checkout_reports_dirty_suffix(tmp_path, monkeypatch):
    _write(tmp_path / '.git' / 'HEAD', f'{COMMIT}\n')
    monkeypatch.setattr(
        build_info.subprocess,
        'run',
        lambda *args, **kwargs: CompletedProcess(args[0], 0, stdout=' M app.py\n'),
    )

    info = build_info.get_build_info(tmp_path)
    assert info == BuildInfo(COMMIT, dirty=True)
    assert info.display == f'{COMMIT[:12]}-dirty'


def test_about_page_links_known_build(client, monkeypatch):
    monkeypatch.setattr(shared, 'BUILD_INFO', BuildInfo(COMMIT, dirty=True))

    body = client.get('/').get_data(as_text=True)

    assert f'Build <a href="https://github.com/cavecomputing/cozy/commit/{COMMIT}"' in body
    assert f'<code>{COMMIT[:12]}-dirty</code>' in body
    assert 'Version 1.1.0' not in body


def test_about_page_leaves_unknown_build_unlinked(client, monkeypatch):
    monkeypatch.setattr(shared, 'BUILD_INFO', BuildInfo())

    body = client.get('/').get_data(as_text=True)

    assert 'Build <code>unknown</code>' in body
    assert 'github.com/cavecomputing/cozy/commit/' not in body
