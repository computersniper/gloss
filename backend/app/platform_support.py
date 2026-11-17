"""Cross-platform paths and local CLI command resolution for GlossX."""

from __future__ import annotations

import asyncio
import locale
import multiprocessing
import os
import re
import signal
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path


# Keep the preview cache separate from the Gloss 1.x runtime cache.
CACHE_APP_DIR_NAME = "GlossX"

_WINDOWS_CREATE_NEW_PROCESS_GROUP = getattr(
    subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
)
_WINDOWS_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


class ProcessCancelledError(RuntimeError):
    """Raised when a caller requests cancellation of spawned blocking work."""


def _process_entry(send_connection, target: Callable, args: tuple) -> None:
    try:
        send_connection.send(("ok", target(*args)))
    except BaseException as error:
        try:
            send_connection.send(("error", error))
        except BaseException:
            send_connection.send(
                ("error_text", f"{type(error).__name__}: {error}")
            )
    finally:
        send_connection.close()


def run_in_process_with_timeout(
    target: Callable,
    args: tuple,
    timeout: float,
    cancel_event=None,
):
    """Run blocking work in a spawn process that can be killed or cancelled."""
    context = multiprocessing.get_context("spawn")
    receive_connection, send_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=_process_entry,
        args=(send_connection, target, args),
        daemon=True,
    )
    process.start()
    send_connection.close()

    def stop_worker() -> None:
        if process.is_alive():
            process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)

    try:
        deadline = time.monotonic() + timeout
        while True:
            if cancel_event is not None and cancel_event.is_set():
                stop_worker()
                raise ProcessCancelledError("worker was cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                stop_worker()
                raise TimeoutError(f"worker timed out after {timeout:g}s")
            if receive_connection.poll(min(0.1, remaining)):
                break
        try:
            status, payload = receive_connection.recv()
        except EOFError as error:
            raise RuntimeError(
                f"worker exited without a result (exit code {process.exitcode})"
            ) from error
        process.join(5)
        if status == "ok":
            return payload
        if status == "error" and isinstance(payload, BaseException):
            raise payload
        raise RuntimeError(str(payload))
    finally:
        receive_connection.close()
        stop_worker()


def read_utf8_text(path: Path) -> str:
    """Read UTF-8 or text written with this install's legacy system encoding.

    Legacy encodings are inherently ambiguous: some CP1252 byte pairs are also
    valid GBK and decode to unrelated characters.  ``GLOSS_LEGACY_ENCODING``
    provides an explicit migration override when data moves between locales.
    """
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as utf8_error:
        encoding = os.environ.get("GLOSS_LEGACY_ENCODING") or locale.getencoding()
        if encoding.lower().replace("_", "-") in {"utf-8", "utf8", "utf-8-sig"}:
            raise utf8_error
        try:
            return data.decode(encoding)
        except LookupError as error:
            raise RuntimeError(
                f"Unknown GLOSS_LEGACY_ENCODING value: {encoding}"
            ) from error
        except UnicodeDecodeError:
            raise utf8_error


def _is_windows(platform_name: str) -> bool:
    return platform_name.startswith("win")


def subprocess_group_options(platform_name: str = sys.platform) -> dict:
    if _is_windows(platform_name):
        return {
            "creationflags": (
                _WINDOWS_CREATE_NEW_PROCESS_GROUP | _WINDOWS_CREATE_NO_WINDOW
            )
        }
    return {"start_new_session": True}


async def terminate_process_tree(process, platform_name: str = sys.platform) -> None:
    """Terminate a provider subprocess and its descendants, then reap it."""
    if process.returncode is not None:
        return
    if _is_windows(platform_name):
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=_WINDOWS_CREATE_NO_WINDOW,
        )
        await killer.wait()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
    try:
        await asyncio.wait_for(process.communicate(), timeout=5)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


def default_data_dir(
    platform_name: str = sys.platform,
    env: Mapping[str, str] = os.environ,
    home: Path | None = None,
) -> Path:
    home = home or Path.home()
    if _is_windows(platform_name):
        base = Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local")
        return base / "Gloss" / "data"
    base = Path(env.get("XDG_DATA_HOME") or home / ".local" / "share")
    return base / "gloss"


def default_cache_dir(
    platform_name: str = sys.platform,
    env: Mapping[str, str] = os.environ,
    home: Path | None = None,
) -> Path:
    home = home or Path.home()
    if _is_windows(platform_name):
        base = Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local")
        return base / CACHE_APP_DIR_NAME / "cache"
    base = Path(env.get("XDG_CACHE_HOME") or home / ".cache")
    return base / CACHE_APP_DIR_NAME


def resolve_data_dir(
    platform_name: str,
    env: Mapping[str, str],
    home: Path,
    legacy_data_dir: Path,
) -> tuple[Path, bool]:
    explicit = env.get("GLOSS_DATA_DIR")
    if explicit:
        return Path(explicit).expanduser().resolve(), False

    try:
        if legacy_data_dir.is_dir() and any(legacy_data_dir.iterdir()):
            return legacy_data_dir.resolve(), True
    except OSError:
        pass

    gloss_dir = default_data_dir(platform_name, env, home).expanduser().resolve()
    preview_dir = (
        gloss_dir.parent.parent / "GlossX" / "data"
        if _is_windows(platform_name)
        else gloss_dir.with_name("GlossX")
    )
    # Prefer the classic installation when both contain data. A preview-only
    # installation can continue in place, including any absolute workspace paths.
    if any((gloss_dir / name).is_file() for name in ("moonlight.db", "config.json")):
        return gloss_dir, False
    if any((preview_dir / name).is_file() for name in ("moonlight.db", "config.json")):
        return preview_dir.resolve(), False
    return gloss_dir, False


def venv_python(repo_root: Path, platform_name: str = sys.platform) -> Path:
    venv = repo_root / "backend" / ".venv"
    if _is_windows(platform_name):
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _resolve_npm_node_shim(
    shim_path: Path, which: Callable[[str], str | None]
) -> list[str] | None:
    """Resolve a standard npm PowerShell/CMD shim without invoking a shell."""
    try:
        text = shim_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    match = re.search(
        r'(?:%dp0%|\$basedir)[\\/]+([^"\r\n]+?\.(?:js|exe))', text, re.IGNORECASE
    )
    if not match:
        return None
    relative_parts = [part for part in re.split(r"[\\/]", match.group(1)) if part]
    script = shim_path.parent.joinpath(*relative_parts)
    if not script.is_file():
        return None
    if script.suffix.lower() == ".exe":
        return [str(script)]
    bundled_node = shim_path.parent / "node.exe"
    node = str(bundled_node) if bundled_node.is_file() else (which("node.exe") or which("node"))
    if not node:
        return None
    return [node, str(script)]


def resolve_cli_command(
    name: str,
    platform_name: str = sys.platform,
    env: Mapping[str, str] = os.environ,
    which: Callable[[str], str | None] = shutil.which,
) -> list[str]:
    path = which(name)
    if not path:
        raise FileNotFoundError(
            f"{name} CLI was not found on PATH. Install it and sign in before testing this provider."
        )

    suffix = Path(path).suffix.lower()
    if _is_windows(platform_name) and suffix in {".cmd", ".bat", ".ps1"}:
        resolved = _resolve_npm_node_shim(Path(path), which)
        if resolved:
            return resolved
        raise RuntimeError(
            f"{name} resolved to {path}, but Gloss could not safely resolve that Windows "
            "command shim. Reinstall the CLI with npm or put its executable on PATH."
        )
    return [path]


# ---------------------------------------------------------------------------
# GlossX home (``~/.glossx``) and the system ``git`` binary
# ---------------------------------------------------------------------------
def glossx_home(env: Mapping[str, str] = os.environ, home: Path | None = None) -> Path:
    """The user's GlossX agent home (``GLOSSX_HOME`` overrides ``~/.glossx``)."""
    explicit = (env.get("GLOSSX_HOME") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    return ((home or Path.home()) / ".glossx").resolve()


def hidden_subprocess_options(platform_name: str = sys.platform) -> dict:
    """Options for short helper processes: no console window on Windows."""
    if _is_windows(platform_name):
        return {"creationflags": _WINDOWS_CREATE_NO_WINDOW}
    return {}


_GIT_ENV_DROP = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_PREFIX",
    "GIT_EDITOR", "GIT_SEQUENCE_EDITOR", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
)
_git_cache: dict = {"ts": 0.0, "path": None}


def git_executable(which: Callable[[str], str | None] = shutil.which) -> str | None:
    """Path of the system ``git`` binary, or ``None`` when git is not installed.

    The lookup is cached briefly (PATH scans are slow on Windows) but not
    forever, so installing git does not need a restart.
    """
    if which is not shutil.which:
        return which("git")
    now = time.monotonic()
    if _git_cache["path"] is None or now - _git_cache["ts"] > 30.0:
        _git_cache["path"] = which("git") or ""
        _git_cache["ts"] = now
    return _git_cache["path"] or None


def git_environment(
    cwd: Path | None = None, env: Mapping[str, str] = os.environ
) -> dict[str, str]:
    """Environment for non-interactive git: never prompts, stable C messages,
    and never picks up a repository above ``cwd``'s parent."""
    out = {k: v for k, v in env.items() if k.upper() not in _GIT_ENV_DROP}
    out.update(
        GIT_TERMINAL_PROMPT="0",
        GIT_ASKPASS="echo",
        SSH_ASKPASS="echo",
        GCM_INTERACTIVE="never",
        GIT_PAGER="cat",
        PAGER="cat",
        GIT_EDITOR="true",
        GIT_MERGE_AUTOEDIT="no",
        GIT_OPTIONAL_LOCKS="0",
        LC_ALL="C",
        LANGUAGE="C",
    )
    if cwd is not None:
        out["GIT_CEILING_DIRECTORIES"] = str(Path(cwd).parent)
    return out


def run_git(
    args: list[str],
    cwd: Path,
    *,
    timeout: float = 60.0,
    input_text: str | None = None,
    config: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run ``git <args>`` in ``cwd`` without a console window or prompts.

    Returns the completed process with **decoded** ``stdout`` / ``stderr``
    (UTF-8, undecodable bytes replaced). Raises ``FileNotFoundError`` when git
    is not installed and ``TimeoutError`` when the command does not finish.
    """
    git = git_executable()
    if not git:
        raise FileNotFoundError("git is not installed or not on PATH")
    argv = [git]
    for key, value in (config or {}).items():
        argv += ["-c", f"{key}={value}"]
    argv += list(args)
    try:
        done = subprocess.run(
            argv,
            cwd=str(cwd),
            env=git_environment(Path(cwd)),
            input=None if input_text is None else input_text.encode("utf-8"),
            stdin=subprocess.DEVNULL if input_text is None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            **hidden_subprocess_options(),
        )
    except subprocess.TimeoutExpired as error:
        raise TimeoutError(f"git {args[0] if args else ''} timed out after {timeout:g}s") from error
    return subprocess.CompletedProcess(
        argv,
        done.returncode,
        (done.stdout or b"").decode("utf-8", errors="replace"),
        (done.stderr or b"").decode("utf-8", errors="replace"),
    )
