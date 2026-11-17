import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import cli_discovery as cli


@pytest.fixture(autouse=True)
def isolated_cache():
    cli._cache.clear()
    yield
    cli._cache.clear()


def test_codex_selects_newer_version_instead_of_first_path(tmp_path, monkeypatch):
    old, new = tmp_path / 'old.exe', tmp_path / 'new.exe'
    old.touch(); new.touch()
    monkeypatch.setattr(cli, 'candidate_paths', lambda name: [(old, 'PATH'), (new, 'Codex desktop')])
    monkeypatch.setattr(cli, 'resolve_cli_command', lambda path: [path])
    calls = []
    def probe(command, **kwargs):
        assert command[-1] == '--version'
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout='codex-cli ' + ('0.9.0' if command[0] == str(old) else '0.10.0'))
    monkeypatch.setattr(cli.subprocess, 'run', probe)
    assert cli.select_command('codex') == [str(new)]
    assert len(calls) == 2
    cli.discover('codex')
    assert len(calls) == 2  # repeated turns reuse detection, not repeated probes


def test_claude_detects_npm_metadata_without_running_any_command(tmp_path, monkeypatch):
    shim = tmp_path / 'claude.cmd'; shim.touch()
    package = tmp_path / 'node_modules/@anthropic-ai/claude-code/package.json'
    package.parent.mkdir(parents=True)
    package.write_text(json.dumps({'name': '@anthropic-ai/claude-code', 'version': '2.1.0'}))
    monkeypatch.setattr(cli, 'candidate_paths', lambda name: [(shim, 'PATH')])
    monkeypatch.setattr(cli, 'resolve_cli_command', lambda path: ['node', 'cli.js'])
    def forbidden(*args, **kwargs):
        raise AssertionError('Claude detection must never execute a subprocess')
    monkeypatch.setattr(cli.subprocess, 'run', forbidden)
    result = cli.discover('claude')
    assert result[0]['version'] == '2.1.0'
    assert result[0]['version_checked'] is False
    assert cli.select_command('claude') == ['node', 'cli.js']


def test_manual_path_wins_without_automatic_scan(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('manual selection should bypass discovery')
    monkeypatch.setattr(cli, 'discover', forbidden)
    monkeypatch.setattr(cli, 'resolve_cli_command', lambda path: [path])
    assert cli.select_command('codex', ' C:/Chosen CLI/codex.exe ') == ['C:/Chosen CLI/codex.exe']


def test_broken_candidate_does_not_hide_working_installation(tmp_path, monkeypatch):
    broken, working = tmp_path/'bad.cmd', tmp_path/'good.cmd'
    monkeypatch.setattr(cli, 'candidate_paths', lambda name: [(broken, 'PATH'), (working, 'PATH')])
    def resolve(path):
        if path == str(broken): raise RuntimeError('Node missing')
        return [path]
    monkeypatch.setattr(cli, 'resolve_cli_command', resolve)
    assert cli.select_command('claude') == [str(working)]


def test_filesystem_scan_finds_installations_beyond_first_path(tmp_path, monkeypatch):
    home = tmp_path/'home'; home.mkdir()
    first, second = tmp_path/'one', tmp_path/'two'
    first.mkdir(); second.mkdir()
    suffix = '.cmd' if cli.sys.platform == 'win32' else ''
    old, other = first/('codex'+suffix), second/('codex'+suffix)
    old.touch(); other.touch()
    desktop = tmp_path/'local/OpenAI/Codex/bin/release/codex.exe'
    desktop.parent.mkdir(parents=True); desktop.touch()
    monkeypatch.setenv('PATH', str(first)+cli.os.pathsep+str(second))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path/'local'))
    monkeypatch.setenv('APPDATA', str(tmp_path/'roaming'))
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    found = {path for path, source in cli.candidate_paths('codex')}
    assert {old.resolve(), other.resolve()} <= found
    if cli.sys.platform == 'win32': assert desktop.resolve() in found


def test_native_claude_npm_shim_resolves_without_running_it(tmp_path):
    from app.platform_support import resolve_cli_command
    native = tmp_path / 'node_modules/@anthropic-ai/claude-code/bin/claude.exe'
    native.parent.mkdir(parents=True); native.touch()
    shim = tmp_path / 'claude.cmd'
    shim.write_text('"%dp0%\\node_modules\\@anthropic-ai\\claude-code\\bin\\claude.exe" %*')
    command = resolve_cli_command('claude', platform_name='win32', which=lambda name: str(shim) if name=='claude' else None)
    assert command == [str(native)]
