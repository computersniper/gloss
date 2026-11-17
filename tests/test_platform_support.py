from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path, PureWindowsPath

import pytest


@pytest.fixture
def platform_support():
    spec = importlib.util.find_spec("app.platform_support")
    assert spec is not None, "cross-platform support module is missing"
    return importlib.import_module("app.platform_support")


def test_linux_uses_xdg_directories(platform_support) -> None:
    env = {
        "XDG_DATA_HOME": "/srv/user-data",
        "XDG_CACHE_HOME": "/srv/user-cache",
    }

    assert platform_support.default_data_dir("linux", env, Path("/home/alice")) == Path(
        "/srv/user-data/gloss"
    )
    assert platform_support.default_cache_dir("linux", env, Path("/home/alice")) == Path(
        "/srv/user-cache/GlossX"
    )


def test_linux_falls_back_to_home_directories(platform_support) -> None:
    assert platform_support.default_data_dir("linux", {}, Path("/home/alice")) == Path(
        "/home/alice/.local/share/gloss"
    )
    assert platform_support.default_cache_dir("linux", {}, Path("/home/alice")) == Path(
        "/home/alice/.cache/GlossX"
    )


def test_windows_uses_local_app_data(platform_support) -> None:
    env = {"LOCALAPPDATA": r"C:\Users\Alice\AppData\Local"}

    assert PureWindowsPath(
        platform_support.default_data_dir("win32", env, Path(r"C:\Users\Alice"))
    ) == PureWindowsPath(r"C:\Users\Alice\AppData\Local\Gloss\data")
    assert PureWindowsPath(
        platform_support.default_cache_dir("win32", env, Path(r"C:\Users\Alice"))
    ) == PureWindowsPath(r"C:\Users\Alice\AppData\Local\GlossX\cache")


def test_windows_provider_processes_never_open_a_console_window(platform_support) -> None:
    options = platform_support.subprocess_group_options("win32")
    flags = options["creationflags"]

    assert flags & platform_support._WINDOWS_CREATE_NEW_PROCESS_GROUP
    assert flags & platform_support._WINDOWS_CREATE_NO_WINDOW


def test_empty_platform_environment_values_use_home_fallbacks(platform_support) -> None:
    assert PureWindowsPath(
        platform_support.default_data_dir(
            "win32", {"LOCALAPPDATA": ""}, Path(r"C:\Users\Alice")
        )
    ) == PureWindowsPath(r"C:\Users\Alice\AppData\Local\Gloss\data")
    assert platform_support.default_data_dir(
        "linux", {"XDG_DATA_HOME": ""}, Path("/home/alice")
    ) == Path("/home/alice/.local/share/gloss")


def test_explicit_data_directory_beats_legacy_data(tmp_path: Path, platform_support) -> None:
    legacy = tmp_path / "backend" / "data"
    legacy.mkdir(parents=True)
    (legacy / "library.db").write_text("existing")
    explicit = tmp_path / "custom-data"

    resolved, used_legacy = platform_support.resolve_data_dir(
        "linux",
        {"GLOSS_DATA_DIR": str(explicit)},
        tmp_path,
        legacy,
    )

    assert resolved == explicit.resolve()
    assert used_legacy is False


def test_non_empty_legacy_data_is_preserved(tmp_path: Path, platform_support) -> None:
    legacy = tmp_path / "backend" / "data"
    legacy.mkdir(parents=True)
    (legacy / "moonlight.db").write_text("existing")

    resolved, used_legacy = platform_support.resolve_data_dir("linux", {}, tmp_path, legacy)

    assert resolved == legacy.resolve()
    assert used_legacy is True


def test_empty_legacy_data_uses_platform_default(tmp_path: Path, platform_support) -> None:
    legacy = tmp_path / "backend" / "data"
    legacy.mkdir(parents=True)

    resolved, used_legacy = platform_support.resolve_data_dir("linux", {}, tmp_path, legacy)

    assert resolved == (tmp_path / ".local" / "share" / "gloss").resolve()
    assert used_legacy is False


@pytest.mark.parametrize("platform_name", ["win32", "linux", "darwin"])
def test_upgrade_selects_existing_library_without_combining_data(tmp_path, platform_support, platform_name):
    env = {"LOCALAPPDATA": str(tmp_path), "XDG_DATA_HOME": str(tmp_path)}
    if platform_name == "win32":
        classic = tmp_path / "Gloss" / "data"
        preview = tmp_path / "GlossX" / "data"
    else:
        classic = tmp_path / "gloss"
        preview = tmp_path / "GlossX"
    legacy = tmp_path / "backend" / "data"

    preview.mkdir(parents=True)
    (preview / "moonlight.db").write_bytes(b"preview")
    assert platform_support.resolve_data_dir(platform_name, env, tmp_path, legacy) == (
        preview.resolve(), False
    )

    classic.mkdir(parents=True)
    (classic / "moonlight.db").write_bytes(b"classic")
    assert platform_support.resolve_data_dir(platform_name, env, tmp_path, legacy) == (
        classic.resolve(), False
    )
    assert platform_support.resolve_data_dir(
        platform_name, {**env, "GLOSS_DATA_DIR": str(preview)}, tmp_path, legacy
    ) == (preview.resolve(), False)
    assert (classic / "moonlight.db").read_bytes() == b"classic"
    assert (preview / "moonlight.db").read_bytes() == b"preview"


def test_virtual_environment_python_is_platform_specific(tmp_path: Path, platform_support) -> None:
    assert platform_support.venv_python(tmp_path, "win32") == tmp_path / "backend" / ".venv" / "Scripts" / "python.exe"
    assert platform_support.venv_python(tmp_path, "linux") == tmp_path / "backend" / ".venv" / "bin" / "python"


def test_posix_cli_command_runs_binary_directly(platform_support) -> None:
    command = platform_support.resolve_cli_command(
        "claude",
        "linux",
        {},
        lambda name: "/usr/local/bin/claude" if name == "claude" else None,
    )

    assert command == ["/usr/local/bin/claude"]


def test_windows_npm_command_shim_resolves_to_node_without_shell(
    tmp_path: Path, platform_support
) -> None:
    shim = tmp_path / "claude.cmd"
    node = tmp_path / "node.exe"
    script = tmp_path / "node_modules" / "example" / "cli.js"
    script.parent.mkdir(parents=True)
    node.touch()
    script.touch()
    shim.write_text(
        '"%_prog%" "%dp0%\\node_modules\\example\\cli.js" %*',
        encoding="utf-8",
    )

    command = platform_support.resolve_cli_command(
        "claude",
        "win32",
        {},
        lambda name: str(shim) if name == "claude" else None,
    )

    assert command == [str(node), str(script)]
    assert not any("cmd" in part.lower() for part in command)


def test_windows_unknown_command_shim_is_rejected(tmp_path: Path, platform_support) -> None:
    shim = tmp_path / "claude.cmd"
    shim.write_text("@echo off\necho unsafe %*", encoding="utf-8")

    with pytest.raises(RuntimeError, match="could not safely resolve"):
        platform_support.resolve_cli_command(
            "claude", "win32", {}, lambda name: str(shim) if name == "claude" else None
        )


def test_windows_npm_shim_arguments_are_not_interpreted_by_shell(
    tmp_path: Path, platform_support
) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    shim = tmp_path / "fake-cli.cmd"
    script = tmp_path / "node_modules" / "example" / "cli.js"
    script.parent.mkdir(parents=True)
    script.write_text(
        "process.stdout.write(JSON.stringify(process.argv.slice(2)));",
        encoding="utf-8",
    )
    shim.write_text(
        '"%_prog%" "%dp0%\\node_modules\\example\\cli.js" %*',
        encoding="utf-8",
    )

    def find(name: str) -> str | None:
        if name == "fake-cli":
            return str(shim)
        if name in {"node", "node.exe"}:
            return node
        return None

    command = platform_support.resolve_cli_command("fake-cli", "win32", {}, find)
    dangerous = ["model=x&echo INJECTED", "100%", "a|b", "redirect>file"]
    result = subprocess.run(command + dangerous, capture_output=True, text=True, check=True)

    assert json.loads(result.stdout) == dangerous


def test_missing_cli_has_actionable_error(platform_support) -> None:
    with pytest.raises(FileNotFoundError, match="claude CLI was not found"):
        platform_support.resolve_cli_command("claude", "win32", {}, lambda _name: None)


@pytest.mark.parametrize(
    ("text", "encoding"),
    [
        ("math ∗ symbol", "utf-8"),
        ("旧版中文", "gbk"),
        ("café", "cp1252"),
        ("éé and ää", "cp1252"),
    ],
)
def test_text_reader_supports_utf8_and_legacy_windows_encodings(
    tmp_path: Path, monkeypatch, platform_support, text: str, encoding: str
) -> None:
    path = tmp_path / "legacy.txt"
    path.write_bytes(text.encode(encoding))
    monkeypatch.setattr(platform_support.locale, "getencoding", lambda: encoding)

    assert platform_support.read_utf8_text(path) == text


def test_text_reader_uses_explicit_legacy_encoding_across_locales(
    tmp_path: Path, monkeypatch, platform_support
) -> None:
    path = tmp_path / "legacy.txt"
    # These byte pairs are also valid GBK, so relying on the first successful
    # decoder would silently turn the accents into unrelated CJK characters.
    text = "\u00e9\u00e9 and \u00e4\u00e4"
    path.write_bytes(text.encode("cp1252"))
    monkeypatch.setattr(platform_support.locale, "getencoding", lambda: "cp936")
    monkeypatch.setenv("GLOSS_LEGACY_ENCODING", "cp1252")

    assert platform_support.read_utf8_text(path) == text


def test_provider_process_termination_reaps_process_tree(tmp_path: Path, platform_support) -> None:
    async def exercise() -> None:
        pid_file = tmp_path / "child.pid"
        alive_file = tmp_path / "child-survived.txt"
        release_file = tmp_path / "release-child"
        ready_file = tmp_path / "child-ready"
        child_code = (
            "import pathlib,time; "
            f"pathlib.Path({str(ready_file)!r}).write_text('ready'); "
            f"release=pathlib.Path({str(release_file)!r}); "
            "exec('while not release.exists(): time.sleep(0.01)'); "
            f"pathlib.Path({str(alive_file)!r}).write_text('alive')"
        )
        parent_code = (
            "import pathlib,subprocess,sys,time; "
            f"child=subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
            f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid)); "
            "time.sleep(60)"
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            parent_code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **platform_support.subprocess_group_options(),
        )
        for _ in range(50):
            if pid_file.exists() and ready_file.exists():
                break
            await asyncio.sleep(0.02)
        assert pid_file.exists() and ready_file.exists(), "parent and child handshake failed"
        await platform_support.terminate_process_tree(process)
        assert process.returncode is not None
        release_file.write_text("release", encoding="utf-8")
        await asyncio.sleep(0.5)
        assert not alive_file.exists(), "provider descendant survived timeout cleanup"

    asyncio.run(exercise())


def test_blocking_work_can_be_hard_stopped_at_a_deadline(platform_support) -> None:
    started = time.monotonic()

    with pytest.raises(TimeoutError, match="timed out"):
        platform_support.run_in_process_with_timeout(time.sleep, (5,), 0.1)

    assert time.monotonic() - started < 2


def test_blocking_work_can_be_hard_stopped_on_request(platform_support) -> None:
    cancel = threading.Event()

    def request_cancel() -> None:
        time.sleep(0.1)
        cancel.set()

    requester = threading.Thread(target=request_cancel)
    requester.start()
    started = time.monotonic()
    try:
        with pytest.raises(platform_support.ProcessCancelledError):
            platform_support.run_in_process_with_timeout(
                time.sleep,
                (5,),
                10,
                cancel_event=cancel,
            )
    finally:
        requester.join()

    assert time.monotonic() - started < 2
