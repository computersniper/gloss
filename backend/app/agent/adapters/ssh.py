"""Remote (SSH) adapter: ``remote.ssh.run`` and ``remote.ssh.hosts``.

Commands run on hosts configured in ``connections.ssh_hosts`` (``{name, host,
user, port, workdir}``) through the system ``ssh`` binary with key-based auth
only (``BatchMode=yes``). Small files can be uploaded first with ``scp``
(staged in the run's sandbox workspace). Returns exit code plus stdout/stderr
tails. ``remote.ssh.run`` needs approval (``remote:ssh``); listing hosts does not.
"""
from __future__ import annotations

import asyncio
import shlex
import shutil
from typing import Any

from ...platform_support import subprocess_group_options, terminate_process_tree
from ..capabilities import Capability, RunContext
from .cli_delegate import safe_relative, workspace_for

RUN_TIMEOUT = 15 * 60.0
STATUS_TIMEOUT = 12.0
MAX_UPLOAD_FILES = 20
MAX_UPLOAD_BYTES = 256_000
TAIL_CHARS = 3000

SSH_OPTIONS = [
    "-o", "BatchMode=yes",
    "-o", "PasswordAuthentication=no",
    "-o", "KbdInteractiveAuthentication=no",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ConnectTimeout=15",
]


def _binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise FileNotFoundError(f"the system '{name}' binary was not found on PATH")
    return path


def _target(host: dict[str, Any]) -> str:
    return f"{host['user']}@{host['host']}"


def _quote_remote_path(path: str) -> str:
    """Shell-quote a remote path while keeping a leading ``~`` expandable."""
    if path == "~":
        return '"$HOME"'
    if path.startswith("~/"):
        return '"$HOME"/' + shlex.quote(path[2:])
    return shlex.quote(path)


def _remote_command(host: dict[str, Any], command: str) -> str:
    workdir = host.get("workdir") or ""
    if workdir:
        quoted = _quote_remote_path(workdir)
        return f"mkdir -p {quoted} && cd {quoted} && {command}"
    return command


def ssh_command(host: dict[str, Any], command: str, *, connect_timeout: int | None = None) -> list[str]:
    opts = list(SSH_OPTIONS)
    if connect_timeout:
        opts[-1] = f"ConnectTimeout={connect_timeout}"
    return [_binary("ssh"), *opts, "-p", str(int(host["port"])), "--", _target(host),
            _remote_command(host, command)]


async def _exec(cmd: list[str], timeout: float) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **subprocess_group_options(),
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except (asyncio.TimeoutError, TimeoutError):
        await terminate_process_tree(proc)
        raise RuntimeError(f"ssh timed out after {int(timeout)}s") from None
    finally:
        if proc.returncode is None:
            await terminate_process_tree(proc)
    return (
        proc.returncode if proc.returncode is not None else -1,
        out.decode("utf-8", errors="replace"),
        err.decode("utf-8", errors="replace"),
    )


def _hosts(conns: dict[str, Any]) -> list[dict[str, Any]]:
    return [h for h in conns.get("ssh_hosts") or [] if h.get("enabled", True)]


async def _upload(host: dict[str, Any], files: Any, run_id: str) -> list[str]:
    if files in (None, {}):
        return []
    if not isinstance(files, dict) or len(files) > MAX_UPLOAD_FILES:
        raise ValueError(f"'files' must be an object of at most {MAX_UPLOAD_FILES} files")
    staging = workspace_for(run_id) / "ssh-upload"
    staging.mkdir(parents=True, exist_ok=True)
    remote_dir = host.get("workdir") or "."
    if remote_dir != ".":
        code, _, err = await _exec(ssh_command(host, "true"), 60)  # creates the workdir
        if code != 0:
            raise RuntimeError(f"could not prepare remote workdir: {err[-400:]}")
    uploaded = []
    for name, content in files.items():
        rel = safe_relative(name)
        if len(rel.parts) != 1:
            raise ValueError("uploads must be plain file names (no directories)")
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_UPLOAD_BYTES:
            raise ValueError(f"{rel} must be text of at most {MAX_UPLOAD_BYTES} bytes")
        local = staging / rel.name
        local.write_text(content, encoding="utf-8")
        dest = f"{_target(host)}:{remote_dir.rstrip('/')}/{rel.name}"
        cmd = [_binary("scp"), *SSH_OPTIONS, "-P", str(int(host["port"])), "--", str(local), dest]
        code, _, err = await _exec(cmd, 120)
        if code != 0:
            raise RuntimeError(f"scp of {rel.name} failed: {err[-400:]}")
        uploaded.append(rel.name)
    return uploaded


def _run_handler(conns: dict[str, Any]):
    async def handler(args: dict[str, Any], ctx: RunContext) -> Any:
        name = str(args.get("host") or "").strip()
        hosts = {h["name"]: h for h in _hosts(conns)}
        if not name and len(hosts) == 1:
            name = next(iter(hosts))
        host = hosts.get(name)
        if host is None:
            raise ValueError(f"unknown SSH host '{name}'; configured: {', '.join(hosts) or 'none'}")
        command = str(args.get("command") or "").strip()
        if not command:
            raise ValueError("'command' is required")
        if len(command) > 8000:
            raise ValueError("'command' is too long")
        try:
            timeout = float(args.get("timeout") or RUN_TIMEOUT)
        except (TypeError, ValueError):
            timeout = RUN_TIMEOUT
        timeout = max(5.0, min(RUN_TIMEOUT, timeout))
        uploaded = await _upload(host, args.get("files"), ctx.run_id)
        code, out, err = await _exec(ssh_command(host, command), timeout)
        return {
            "host": name,
            "exit_code": code,
            "stdout_tail": out[-TAIL_CHARS:],
            "stderr_tail": err[-TAIL_CHARS:],
            "uploaded": uploaded,
        }

    return handler


def _hosts_handler(conns: dict[str, Any]):
    async def handler(args: dict[str, Any], ctx: RunContext) -> Any:
        return {"hosts": [
            {"name": h["name"], "host": h["host"], "user": h["user"], "port": h["port"],
             "workdir": h.get("workdir") or ""}
            for h in _hosts(conns)
        ]}

    return handler


def ssh_capabilities(conns: dict[str, Any]) -> list[Capability]:
    hosts = _hosts(conns)
    if not hosts:
        return []
    names = ", ".join(h["name"] for h in hosts[:6])
    return [
        Capability(
            name="remote.ssh.run",
            kind="tool",
            card=f"Run a shell command on a configured remote host (e.g. a GPU server) over SSH; hosts: {names}.",
            input_schema={
                "host": "str: configured host name",
                "command": "str: shell command, run in the host's workdir",
                "files": "object (optional): {file name: small text content} uploaded first via scp",
                "timeout": "int (optional): seconds, max 900",
            },
            output_schema={"exit_code": "int", "stdout_tail": "str", "stderr_tail": "str"},
            handler=_run_handler(conns),
            permissions=["remote:ssh"],
            cost="network",
            source="ssh",
            trust="external",
            timeout=RUN_TIMEOUT + 180,
            requirements=["ssh client", "a configured remote host"],
            side_effects="runs commands on a remote host",
        ),
        Capability(
            name="remote.ssh.hosts",
            kind="source",
            card="List the remote SSH hosts configured for this GlossX install.",
            input_schema={},
            handler=_hosts_handler(conns),
            permissions=["connections:read"],
            source="ssh",
            trust="external",
        ),
    ]


async def host_status(host: dict[str, Any]) -> dict[str, Any]:
    name = host["name"]
    if not host.get("enabled", True):
        return {"name": name, "ok": False, "error": "disabled"}
    try:
        cmd = [_binary("ssh"), *SSH_OPTIONS[:-2], "-o", "ConnectTimeout=8",
               "-p", str(int(host["port"])), "--", _target(host), "true"]
        code, _, err = await _exec(cmd, STATUS_TIMEOUT)
    except Exception as error:
        return {"name": name, "ok": False, "error": (str(error) or type(error).__name__)[:400]}
    if code != 0:
        return {"name": name, "ok": False, "error": (err.strip() or f"ssh exited {code}")[-400:]}
    return {"name": name, "ok": True, "error": None}


async def status(conns: dict[str, Any]) -> list[dict[str, Any]]:
    hosts = conns.get("ssh_hosts") or []
    if not hosts:
        return []
    return list(await asyncio.gather(*(host_status(h) for h in hosts)))
