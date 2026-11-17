"""Git-backed version control for project workspaces (docs/glossx-workspaces.md, section 3).

Every workspace (``<DATA_DIR>/workspaces/<key>/``) is a real git repository.
It is created with the workspace (or on the first VCS use of a workspace that
predates this feature) with a ``main`` branch, a ``.gitignore`` for caches and
an initial commit.

All functions here are **blocking** (they run the system ``git`` binary through
``platform_support.run_git``); call them with ``asyncio.to_thread`` from async
code. Every operation holds the workspace's lock, so two git commands never run
at the same time on one workspace.

Errors: :class:`VcsUnavailable` (git is not installed), :class:`VcsError`
(a request git refused; ``code`` says why), ``ValueError`` (invalid input),
``LookupError`` (unknown branch / revision / path).
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from .. import platform_support
from . import workspace

logger = logging.getLogger(__name__)

DEFAULT_BRANCH = "main"
IDENTITY = ("GlossX", "glossx@local")
MAX_DIFF_CHARS = 2_000_000
MAX_FILE_BYTES = 2_000_000
MAX_UNTRACKED_DIFFS = 100
STASH_PREFIX = "glossx-auto:"
UNAVAILABLE = ("Version control is unavailable: git is not installed (or not on PATH). "
               "Install git to get history and branches; checkpoints keep working.")

GITIGNORE = """# GlossX: caches and local state (settings, commands, agents and memory in
# .glossx/ are tracked so they travel with the project)
.glossx/checkpoints/
.glossx/cache/
.glossx/tmp/
.glossx/*.local.json
__pycache__/
*.py[cod]
.ipynb_checkpoints/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.venv/
venv/
node_modules/
.DS_Store
Thumbs.db
"""
EXCLUDE = ".glossx/checkpoints/\n.glossx/cache/\n.glossx/tmp/\n"

# every git call: predictable output, no line-ending rewriting, no signing prompts
BASE_CONFIG = {
    "core.quotepath": "false",
    "core.autocrlf": "false",
    "core.safecrlf": "false",
    "core.fsmonitor": "false",
    "commit.gpgsign": "false",
    "tag.gpgsign": "false",
    "advice.detachedHead": "false",
    "color.ui": "false",
}

_REV = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./~^@{}-]{0,199}$")
_BRANCH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]{0,99}$")
_TRAILER = re.compile(r"^(Session|Turn):\s*(\S+)\s*$", re.M)
_STATUS_LABELS = {"M": "modified", "A": "added", "D": "deleted", "R": "renamed",
                  "C": "copied", "T": "modified", "U": "conflicted", "?": "untracked"}

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()
_head_cache: dict[str, tuple[float, str | None]] = {}


class VcsError(RuntimeError):
    """git refused the operation (HTTP 409). ``code`` is machine-readable:
    ``dirty``, ``conflict``, ``exists``, ``current_branch``, ``unmerged``,
    ``nothing_to_commit``, ``busy`` or ``git_failed``."""

    def __init__(self, message: str, code: str = "git_failed", **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.extra = extra


class VcsUnavailable(VcsError):
    """git is not installed (HTTP 409, ``available: false`` in the status)."""

    def __init__(self, message: str = UNAVAILABLE) -> None:
        super().__init__(message, "git_unavailable", available=False)


# -- plumbing ---------------------------------------------------------------------------
def available() -> bool:
    return platform_support.git_executable() is not None


def lock_for(key: str) -> threading.RLock:
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.RLock()
        return lock


def _root(key: str) -> Path:
    key = workspace.key_for(key)
    return workspace.root_for(None if key == "default" else key)


def _git(root: Path, *args: str, check: bool = True, timeout: float = 60.0,
         input_text: str | None = None, identity: bool = False) -> Any:
    """Run git in ``root``. With ``check`` a non-zero exit raises :class:`VcsError`."""
    cfg = dict(BASE_CONFIG)
    try:
        if identity:
            cfg.update(_identity_config(root))
        done = platform_support.run_git(list(args), root, timeout=timeout,
                                        input_text=input_text, config=cfg)
    except FileNotFoundError as error:
        raise VcsUnavailable() from error
    except TimeoutError as error:
        raise VcsError(str(error)) from error
    if check and done.returncode != 0:
        raise VcsError(_message(done, args))
    return done


def _message(done: Any, args: tuple[str, ...] | list[str]) -> str:
    text = (done.stderr or done.stdout or "").strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    lines = [re.sub(r"^(fatal|error):\s*", "", ln) for ln in lines if not ln.startswith("hint:")]
    return (" ".join(lines)[:600]
            or f"git {args[0] if args else ''} failed with exit code {done.returncode}")


def _identity_config(root: Path) -> dict[str, str]:
    """``user.name`` / ``user.email`` overrides when the user has no git identity."""
    done = platform_support.run_git(["config", "--get-regexp", r"^user\.(name|email)$"], root,
                                    timeout=20)
    have = {ln.split(" ", 1)[0].lower() for ln in done.stdout.splitlines() if " " in ln.strip()}
    out = {}
    if "user.name" not in have:
        out["user.name"] = IDENTITY[0]
    if "user.email" not in have:
        out["user.email"] = IDENTITY[1]
    return out


def is_repo(root: Path) -> bool:
    return (Path(root) / ".git").exists()


def head_branch(root: Path) -> str | None:
    """Current branch read straight from ``.git/HEAD`` (no subprocess; cached for
    a second because session summaries ask for it constantly)."""
    key = str(root)
    now = time.monotonic()
    hit = _head_cache.get(key)
    if hit is not None and now - hit[0] < 1.0:
        return hit[1]
    branch = None
    try:
        text = (Path(root) / ".git" / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
        if text.startswith("ref: refs/heads/"):
            branch = text[len("ref: refs/heads/"):] or None
    except OSError:
        branch = None
    _head_cache[key] = (now, branch)
    return branch


def _forget_head(root: Path) -> None:
    _head_cache.pop(str(root), None)


def brief(project_id: str | None) -> dict[str, Any]:
    """Cheap ``{available, initialized, branch}`` for summaries (no subprocess)."""
    try:
        root = workspace.root_for(project_id)
        return {"available": available(), "initialized": is_repo(root),
                "branch": head_branch(root)}
    except Exception:
        return {"available": available(), "initialized": False, "branch": None}


# -- repository creation ----------------------------------------------------------------
def ensure_repo(key: str) -> Path:
    """The workspace root, with its git repository created if it has none."""
    root = _root(key)
    if not available():
        raise VcsUnavailable()
    with lock_for(workspace.key_for(key)):
        if is_repo(root):
            return root
        done = _git(root, "init", "-b", DEFAULT_BRANCH, check=False)
        if done.returncode != 0:  # git < 2.28 has no ``init -b``
            _git(root, "init")
            _git(root, "symbolic-ref", "HEAD", f"refs/heads/{DEFAULT_BRANCH}")
        try:
            ignore = root / ".gitignore"
            if not ignore.exists():
                ignore.write_bytes(GITIGNORE.encode("utf-8"))
            info = root / ".git" / "info"
            info.mkdir(parents=True, exist_ok=True)
            with open(info / "exclude", "ab") as handle:
                handle.write(("\n" + EXCLUDE).encode("utf-8"))
        except OSError:
            logger.warning("could not write the ignore files of %s", root, exc_info=True)
        _git(root, "add", "-A")
        _git(root, "commit", "--allow-empty", "--no-verify", "-m", "Initialize workspace",
             identity=True)
        _forget_head(root)
        return root


def ensure_repo_quietly(key: str) -> bool:
    """``ensure_repo`` for background use: never raises."""
    try:
        ensure_repo(key)
        return True
    except VcsUnavailable:
        return False
    except Exception:
        logger.exception("could not initialise the git repository of workspace %s", key)
        return False


def ensure_repo_in_background(key: str) -> None:
    """Create the repository without making the caller wait (imports, uploads)."""
    if not available():
        return
    threading.Thread(target=ensure_repo_quietly, args=(key,), daemon=True,
                     name=f"glossx-git-init-{key[:8]}").start()


# -- validation -------------------------------------------------------------------------
def check_branch_name(name: Any) -> str:
    text = str(name or "").strip()
    if (not _BRANCH.fullmatch(text) or ".." in text or "//" in text or text.endswith(("/", "."))
            or text.endswith(".lock") or "@{" in text or text.upper() == "HEAD"
            or any(part.startswith(".") or part.endswith(".lock") for part in text.split("/"))):
        raise ValueError("invalid branch name (use letters, digits, '-', '_', '.' and '/')")
    return text


def _branch_exists(root: Path, name: str) -> bool:
    return _git(root, "show-ref", "--verify", "--quiet", f"refs/heads/{name}",
                check=False).returncode == 0


def _need_branch(root: Path, name: Any) -> str:
    name = check_branch_name(name)
    if not _branch_exists(root, name):
        raise LookupError(f"branch not found: {name}")
    return name


def _rev(root: Path, rev: Any, *, default: str | None = "HEAD") -> str:
    """Resolve a user-supplied revision to a full commit id."""
    text = str(rev or "").strip() or (default or "")
    if not text or not _REV.fullmatch(text) or ".." in text:
        raise ValueError("invalid revision")
    done = _git(root, "rev-parse", "--verify", "--quiet", f"{text}^{{commit}}", check=False)
    sha = done.stdout.strip()
    if done.returncode != 0 or not sha:
        raise LookupError(f"revision not found: {text}")
    return sha


def _rel(root: Path, path: Any, *, allow_root: bool = False) -> str:
    """A user-supplied path as a workspace-relative posix path."""
    try:
        target = workspace.resolve(root, path)
    except workspace.WorkspaceError as error:
        raise ValueError(str(error)) from error
    rel = workspace.relpath(root, target)
    if rel == "." and not allow_root:
        raise ValueError("path must be a file or folder inside the workspace")
    protected = workspace.is_protected_rel(rel)
    if protected:
        raise ValueError(protected)
    return rel


def _ps(rel: str) -> str:
    """A workspace-relative path as a literal git pathspec (no globbing / magic)."""
    return rel if rel == "." else f":(literal){rel}"


def _rels(root: Path, paths: Any, *, allow_root: bool = True) -> list[str]:
    if paths is None:
        return []
    if isinstance(paths, str):
        paths = [paths]
    if not isinstance(paths, (list, tuple)) or len(paths) > 500:
        raise ValueError("paths must be a list of at most 500 workspace paths")
    out = [_rel(root, p, allow_root=allow_root) for p in paths]
    return list(dict.fromkeys(out))


# -- status -----------------------------------------------------------------------------
def _changes(root: Path) -> list[dict[str, Any]]:
    out = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    fields = out.split("\0")
    changes: list[dict[str, Any]] = []
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue
        x, y, path = entry[0], entry[1], entry[3:]
        item: dict[str, Any] = {"path": path}
        if x in "RC" or y in "RC":
            if i < len(fields):
                item["old_path"] = fields[i]
                i += 1
        if x == "?" and y == "?":
            code = "?"
        elif "U" in (x, y) or (x == y and x in "AD"):
            code = "U"
        elif x not in " ?":
            code = x
        else:
            code = y
        item.update(status=code, label=_STATUS_LABELS.get(code, "modified"),
                    staged=x not in " ?" and code != "U", code=x + y)
        changes.append(item)
    changes.sort(key=lambda c: c["path"].lower())
    return changes


def _current(root: Path) -> str | None:
    done = _git(root, "symbolic-ref", "--short", "-q", "HEAD", check=False)
    return done.stdout.strip() or None


def _branches(root: Path, current: str | None) -> list[dict[str, Any]]:
    fmt = "%(refname:short)%09%(objectname)%09%(committerdate:unix)%09%(contents:subject)"
    out = _git(root, "for-each-ref", "--sort=-committerdate", f"--format={fmt}", "refs/heads").stdout
    branches = []
    for line in out.splitlines():
        parts = line.split("\t", 3)
        if len(parts) < 3:
            continue
        try:
            updated = float(parts[2])
        except ValueError:
            updated = None
        branches.append({"name": parts[0], "current": parts[0] == current, "head": parts[1],
                         "short": parts[1][:7], "updated_at": updated,
                         "subject": parts[3] if len(parts) > 3 else ""})
    branches.sort(key=lambda b: (not b["current"], b["name"] != DEFAULT_BRANCH, b["name"].lower()))
    return branches


def _base_branch(names: list[str]) -> str | None:
    for candidate in (DEFAULT_BRANCH, "master"):
        if candidate in names:
            return candidate
    return None


def _ahead_of(root: Path, current: str | None, names: list[str]) -> dict[str, Any] | None:
    base = _base_branch(names)
    if not current or not base or base == current:
        return None
    done = _git(root, "rev-list", "--left-right", "--count", f"{base}...HEAD", check=False)
    parts = done.stdout.split()
    if done.returncode != 0 or len(parts) != 2:
        return None
    return {"branch": base, "ahead": int(parts[1]), "behind": int(parts[0])}


def _stashes(root: Path) -> list[dict[str, Any]]:
    out = _git(root, "stash", "list", "--format=%gd%x09%gs", check=False).stdout
    items = []
    for line in out.splitlines():
        ref, _, subject = line.partition("\t")
        marker = subject.find(STASH_PREFIX)
        items.append({"ref": ref, "message": subject,
                      "branch": subject[marker + len(STASH_PREFIX):].strip() if marker >= 0 else None})
    return items


def unavailable_status(reason: str = UNAVAILABLE) -> dict[str, Any]:
    return {"available": False, "reason": reason, "initialized": False, "branch": None,
            "head": None, "branches": [], "changes": [], "ahead_of": None, "clean": True,
            "stashes": []}


def status(key: str) -> dict[str, Any]:
    """Status of the workspace (``available: false`` when git is missing)."""
    if not available():
        return unavailable_status()
    with lock_for(workspace.key_for(key)):
        from ..agent import projects
        root = _root(key)
        if projects.attached_folder(key) and not is_repo(root):
            # Opening an existing folder is read-only with respect to Git.
            # An explicit version-control operation or agent turn may initialize it.
            return {"available": True, "initialized": False, "branch": None,
                    "head": None, "detached": False, "branches": [], "changes": [],
                    "ahead_of": None, "clean": True, "stashes": [], "root": str(root)}
        root = ensure_repo(key)
        return _status(root)


def _status(root: Path) -> dict[str, Any]:
    current = _current(root)
    branches = _branches(root, current)
    changes = _changes(root)
    head = _git(root, "rev-parse", "--verify", "--quiet", "HEAD", check=False).stdout.strip() or None
    _forget_head(root)
    return {
        "available": True,
        "initialized": True,
        "branch": current,
        "head": head,
        "detached": current is None and head is not None,
        "branches": branches,
        "changes": changes,
        "ahead_of": _ahead_of(root, current, [b["name"] for b in branches]),
        "clean": not changes,
        "stashes": _stashes(root),
        "root": str(root),
    }


def _dirty(root: Path) -> list[dict[str, Any]]:
    return _changes(root)


def _require_clean(root: Path, action: str) -> None:
    changes = _dirty(root)
    if changes:
        count = len(changes)
        raise VcsError(
            f"The workspace has {count} uncommitted change{'s' if count != 1 else ''}. "
            f"Commit, stash or discard them before you {action}.",
            "dirty", changes=changes[:200])


# -- history ----------------------------------------------------------------------------
_LOG_FORMAT = "%x1e%H%x1f%h%x1f%an%x1f%ae%x1f%at%x1f%P%x1f%D%x1f%B%x1f"


def _parse_log(out: str) -> list[dict[str, Any]]:
    commits = []
    for record in out.split("\x1e"):
        parts = record.split("\x1f")
        if len(parts) < 9:
            continue
        sha, short, author, email, stamp, parents, refs, body, tail = parts[:9]
        sha = sha.strip()
        files = []
        for line in tail.splitlines():
            cols = line.split("\t")
            if len(cols) >= 2 and cols[0][:1] in "MADRCTU":
                item = {"status": cols[0][:1], "path": cols[-1]}
                if len(cols) >= 3:
                    item["old_path"] = cols[1]
                files.append(item)
        message = body.strip("\n")
        trailers = {m.group(1): m.group(2) for m in _TRAILER.finditer(message)}
        names = []
        for ref in (r.strip() for r in refs.split(",")):
            if not ref:
                continue
            if ref.startswith("HEAD -> "):
                names.extend(["HEAD", ref[len("HEAD -> "):]])
            else:
                names.append(ref)
        try:
            when = float(stamp)
        except ValueError:
            when = None
        turn = trailers.get("Turn")
        commits.append({
            "id": sha, "short": short.strip() or sha[:7],
            "message": message, "subject": message.splitlines()[0] if message else "",
            "author": author, "author_email": email, "time": when,
            "parents": parents.split(), "refs": names,
            "files_changed": len(files), "files": files[:300],
            "session_id": trailers.get("Session"),
            "turn": int(turn) if turn and turn.isdigit() else None,
        })
    return commits


def log(key: str, *, branch: str | None = None, limit: int = 50, path: str | None = None,
        skip: int = 0, all_branches: bool = False) -> list[dict[str, Any]]:
    with lock_for(workspace.key_for(key)):
        root = ensure_repo(key)
        args = ["log", f"--format={_LOG_FORMAT}", "--name-status", "--date-order",
                f"--max-count={max(1, min(500, int(limit)))}", f"--skip={max(0, int(skip))}"]
        text = str(branch or "").strip()
        if all_branches or text in {"*", "all"}:
            args.append("--branches")
        elif text:
            args.append(f"refs/heads/{_need_branch(root, text)}")
        else:
            if _git(root, "rev-parse", "--verify", "--quiet", "HEAD", check=False).returncode != 0:
                return []
            args.append("HEAD")
        args.append("--")
        if path:
            args.append(_ps(_rel(root, path, allow_root=True)))
        return _parse_log(_git(root, *args).stdout)


def _commit_info(root: Path, rev: str = "HEAD") -> dict[str, Any] | None:
    done = _git(root, "log", f"--format={_LOG_FORMAT}", "--name-status", "--max-count=1", rev,
                "--", check=False)
    commits = _parse_log(done.stdout) if done.returncode == 0 else []
    return commits[0] if commits else None


def _untracked_diff(root: Path, rel: str) -> str:
    target = root / rel
    head = f"diff --git a/{rel} b/{rel}\nnew file mode 100644\n"
    try:
        if target.stat().st_size > MAX_FILE_BYTES:
            return head + f"Binary files /dev/null and b/{rel} differ\n"
        data = target.read_bytes()
    except OSError:
        return ""
    if b"\0" in data[:8192]:
        return head + f"Binary files /dev/null and b/{rel} differ\n"
    text = data.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
        no_newline = False
    else:
        no_newline = bool(text)
    if not lines:
        return head
    body = "\n".join("+" + line.rstrip("\r") for line in lines)
    tail = "\n\\ No newline at end of file" if no_newline else ""
    return (f"{head}--- /dev/null\n+++ b/{rel}\n@@ -0,0 +1,{len(lines)} @@\n{body}{tail}\n")


def diff(key: str, *, from_rev: str | None = None, to_rev: str | None = None,
         path: str | None = None) -> dict[str, Any]:
    """Unified diff. ``to`` empty: the working tree against ``from`` (default
    ``HEAD``), including untracked files. ``from`` empty with ``to``: what that
    commit changed (against its first parent)."""
    with lock_for(workspace.key_for(key)):
        root = ensure_repo(key)
        rel = _rel(root, path, allow_root=True) if path else None
        spec = ["--", _ps(rel)] if rel and rel != "." else ["--"]
        common = ["--no-color", "--no-ext-diff", "--find-renames"]
        frm = str(from_rev or "").strip()
        to = str(to_rev or "").strip()
        text = ""
        if to:
            target = _rev(root, to)
            if frm:
                base = _rev(root, frm)
                text = _git(root, "diff", *common, base, target, *spec).stdout
            else:
                parents = _git(root, "rev-list", "--parents", "-n", "1", target).stdout.split()[1:]
                if parents:
                    text = _git(root, "diff", *common, parents[0], target, *spec).stdout
                else:
                    text = _git(root, "show", "--format=", "--root", *common, target, *spec).stdout
            resolved_from, resolved_to = (frm or None), to
        else:
            has_head = _git(root, "rev-parse", "--verify", "--quiet", "HEAD",
                            check=False).returncode == 0
            if has_head or frm:
                base = _rev(root, frm or "HEAD")
                text = _git(root, "diff", *common, base, *spec).stdout
            extra = []
            for change in _changes(root):
                if change["status"] != "?":
                    continue
                p = change["path"]
                if rel and rel != "." and p != rel and not p.startswith(rel.rstrip("/") + "/"):
                    continue
                if len(extra) >= MAX_UNTRACKED_DIFFS:
                    break
                extra.append(_untracked_diff(root, p))
            text += "".join(extra)
            resolved_from, resolved_to = (frm or "HEAD"), None
        truncated = len(text) > MAX_DIFF_CHARS
        return {"diff": text[:MAX_DIFF_CHARS], "from": resolved_from, "to": resolved_to,
                "path": rel if rel and rel != "." else None, "truncated": truncated}


def file_at(key: str, *, rev: str | None, path: str) -> dict[str, Any]:
    """Content of ``path`` at revision ``rev`` (default ``HEAD``)."""
    with lock_for(workspace.key_for(key)):
        root = ensure_repo(key)
        rel = _rel(root, path)
        sha = _rev(root, rev)
        size = _git(root, "cat-file", "-s", f"{sha}:{rel}", check=False)
        if size.returncode != 0:
            raise LookupError(f"{rel} does not exist at {str(rev or 'HEAD')[:40]}")
        if int(size.stdout.strip() or 0) > MAX_FILE_BYTES:
            raise ValueError("file is too large to show")
        kind = _git(root, "cat-file", "-t", f"{sha}:{rel}", check=False).stdout.strip()
        if kind != "blob":
            raise ValueError(f"{rel} is not a file at that revision")
        content = _git(root, "show", f"{sha}:{rel}").stdout
        if "\0" in content[:8192]:
            raise ValueError("binary file")
        return {"path": rel, "rev": sha, "content": content, "size": int(size.stdout.strip() or 0)}


# -- commits ----------------------------------------------------------------------------
def _clean_message(message: Any) -> str:
    text = str(message or "").replace("\r\n", "\n").replace("\0", "").strip()
    if not text:
        raise ValueError("commit message must not be empty")
    if len(text) > 5000:
        raise ValueError("commit message is too long (max 5000 characters)")
    return text


def _do_commit(root: Path, message: str, rels: list[str] | None = None) -> dict[str, Any] | None:
    """Stage (everything, or ``rels``) and commit. ``None`` when nothing changed."""
    specs = [_ps(r) for r in rels or []]
    if rels:
        _git(root, "add", "-A", "--", *specs)
        staged = _git(root, "diff", "--cached", "--quiet", "--", *specs, check=False)
    else:
        _git(root, "add", "-A")
        staged = _git(root, "diff", "--cached", "--quiet", check=False)
    has_head = _git(root, "rev-parse", "--verify", "--quiet", "HEAD", check=False).returncode == 0
    if staged.returncode == 0 and has_head:
        return None
    args = ["commit", "--no-verify", "--cleanup=whitespace", "-F", "-"]
    if rels:
        args += ["--", *specs]
    _git(root, *args, input_text=message + "\n", identity=True)
    _forget_head(root)
    return _commit_info(root)


def commit(key: str, message: Any, paths: Any = None) -> dict[str, Any]:
    text = _clean_message(message)
    with lock_for(workspace.key_for(key)):
        root = ensure_repo(key)
        rels = _rels(root, paths)
        if "." in rels:
            rels = []
        made = _do_commit(root, text, rels or None)
        if made is None:
            raise VcsError("Nothing to commit: the workspace has no changes"
                           + (" in those paths." if rels else "."), "nothing_to_commit")
        return {"committed": True, "commit": made, "status": _status(root)}


def auto_commit(key: str, message: str) -> dict[str, Any] | None:
    """Commit everything that changed (turn end). ``None`` when the workspace is
    clean or git is unavailable. Raises on git failures; the caller logs them."""
    if not available():
        return None
    with lock_for(workspace.key_for(key)):
        root = ensure_repo(key)
        if (root / ".git" / "MERGE_HEAD").exists():
            return None  # never commit a half-done merge
        if not _changes(root):
            return None
        made = _do_commit(root, _clean_message(message))
        if made is not None:
            made["branch"] = _current(root)
        return made


# -- branches ---------------------------------------------------------------------------
def create_branch(key: str, name: Any, from_rev: Any = None, *, checkout: bool = False
                  ) -> dict[str, Any]:
    name = check_branch_name(name)
    with lock_for(workspace.key_for(key)):
        root = ensure_repo(key)
        if _branch_exists(root, name):
            raise VcsError(f"A branch named {name} already exists.", "exists")
        start = _rev(root, from_rev)
        _git(root, "branch", "--no-track", name, start)
        if checkout:
            if start != _rev(root, "HEAD"):
                _require_clean(root, "switch branches")
            _git(root, "checkout", "--quiet", name)
            _forget_head(root)
        state = _status(root)
        branch = next((b for b in state["branches"] if b["name"] == name), None)
        return {"branch": branch, "status": state}


def _restore_stash(root: Path, branch: str) -> bool | None:
    """Re-apply the changes stashed when this branch was left. ``None``: there was
    no stash; ``False``: it did not apply cleanly (it stays in the stash list)."""
    entry = next((s for s in _stashes(root) if s["branch"] == branch), None)
    if entry is None:
        return None
    done = _git(root, "stash", "pop", "--index", entry["ref"], check=False)
    if done.returncode == 0:
        return True
    # put the tree back exactly as the checkout left it; the stash is kept
    _git(root, "reset", "--hard", "--quiet", "HEAD", check=False)
    _git(root, "clean", "-fd", "--quiet", check=False)
    return False


def checkout(key: str, branch: Any, *, stash: bool = False, discard: bool = False
             ) -> dict[str, Any]:
    if stash and discard:
        raise ValueError("choose either stash or discard, not both")
    with lock_for(workspace.key_for(key)):
        root = ensure_repo(key)
        name = _need_branch(root, branch)
        current = _current(root)
        result: dict[str, Any] = {"branch": name, "previous": current, "stashed": False,
                                  "discarded": False, "restored_stash": False}
        if name == current:
            result["status"] = _status(root)
            return result
        changes = _dirty(root)
        if changes:
            if stash:
                _git(root, "stash", "push", "--include-untracked", "-m",
                     f"{STASH_PREFIX}{current or 'detached'}", identity=True)
                result["stashed"] = True
            elif discard:
                _git(root, "reset", "--hard", "--quiet", "HEAD")
                _git(root, "clean", "-fd", "--quiet")
                result["discarded"] = True
            else:
                _require_clean(root, "switch branches")
        _git(root, "checkout", "--quiet", name)
        _forget_head(root)
        restored = _restore_stash(root, name)
        result["restored_stash"] = bool(restored)
        if restored is False:
            result["stash_conflict"] = True
        result["status"] = _status(root)
        return result


def merge(key: str, branch: Any) -> dict[str, Any]:
    """Merge ``branch`` into the current branch. A conflicting merge is aborted
    and reported; the repository is never left mid-merge."""
    with lock_for(workspace.key_for(key)):
        root = ensure_repo(key)
        name = _need_branch(root, branch)
        current = _current(root)
        if current is None:
            raise VcsError("The workspace is not on a branch.", "git_failed")
        if name == current:
            raise ValueError("cannot merge a branch into itself")
        _require_clean(root, "merge")
        before = _rev(root, "HEAD")
        theirs = _rev(root, f"refs/heads/{name}")
        result: dict[str, Any] = {"merged": False, "fast_forward": False, "conflicts": [],
                                  "branch": name, "into": current, "already_up_to_date": False,
                                  "commit": None}
        if _git(root, "merge-base", "--is-ancestor", theirs, before, check=False).returncode == 0:
            result.update(merged=True, already_up_to_date=True, status=_status(root))
            return result
        fast_forward = _git(root, "merge-base", "--is-ancestor", before, theirs,
                            check=False).returncode == 0
        done = _git(root, "merge", "--no-edit", "--no-verify", "-m",
                    f"Merge branch '{name}' into {current}", name, check=False, identity=True,
                    timeout=120.0)
        if done.returncode != 0:
            conflicts = [p for p in _git(root, "diff", "--name-only", "--diff-filter=U", "-z",
                                         check=False).stdout.split("\0") if p]
            _abort_merge(root, before)
            if not conflicts:
                raise VcsError(f"The merge failed: {_message(done, ['merge'])}")
            result.update(conflicts=sorted(conflicts), status=_status(root))
            result["message"] = (f"Merging {name} into {current} conflicts in "
                                 f"{len(conflicts)} file{'s' if len(conflicts) != 1 else ''}; "
                                 "the merge was aborted and nothing changed.")
            return result
        _forget_head(root)
        result.update(merged=True, fast_forward=fast_forward, commit=_commit_info(root),
                      status=_status(root))
        return result


def _abort_merge(root: Path, before: str) -> None:
    if (root / ".git" / "MERGE_HEAD").exists():
        if _git(root, "merge", "--abort", check=False).returncode != 0:
            _git(root, "reset", "--merge", check=False)
    if (root / ".git" / "MERGE_HEAD").exists() or _changes(root):
        _git(root, "reset", "--hard", "--quiet", before, check=False)
    _forget_head(root)


def delete_branch(key: str, name: Any, *, force: bool = False) -> dict[str, Any]:
    with lock_for(workspace.key_for(key)):
        root = ensure_repo(key)
        name = _need_branch(root, name)
        if name == _current(root):
            raise VcsError(f"{name} is the current branch; switch to another branch first.",
                           "current_branch")
        done = _git(root, "branch", "-D" if force else "-d", name, check=False)
        if done.returncode != 0:
            if "not fully merged" in (done.stderr or ""):
                raise VcsError(f"{name} has commits that are not merged into the current branch. "
                               "Merge it first, or delete it with force.", "unmerged")
            raise VcsError(_message(done, ["branch"]))
        return {"deleted": name, "status": _status(root)}


# -- restore / discard ------------------------------------------------------------------
def restore(key: str, rev: Any, paths: Any = None) -> dict[str, Any]:
    """Make the files (all, or ``paths``) match revision ``rev`` and record that
    as a new commit. Uncommitted changes are committed first, so nothing is lost."""
    with lock_for(workspace.key_for(key)):
        root = ensure_repo(key)
        sha = _rev(root, rev, default=None)
        rels = [r for r in _rels(root, paths) if r != "."] if paths else []
        if paths and not rels:
            rels = []
        short = sha[:7]
        saved = None
        if _changes(root):
            saved = _do_commit(root, f"Save changes before restoring {short}")
        done = _git(root, "restore", f"--source={sha}", "--staged", "--worktree", "--",
                    *([_ps(r) for r in rels] or ["."]), check=False)
        if done.returncode != 0:
            _git(root, "reset", "--hard", "--quiet", "HEAD", check=False)
            if "did not match" in (done.stderr or ""):
                raise LookupError("those paths exist neither now nor at that revision")
            raise VcsError(_message(done, ["restore"]))
        what = ", ".join(rels[:3]) + (f" and {len(rels) - 3} more" if len(rels) > 3 else "")
        made = _do_commit(root, f"Restore {what or 'workspace'} to {short}\n\nRestored-From: {sha}")
        return {"restored": made is not None, "commit": made, "rev": sha,
                "saved_commit": saved, "status": _status(root)}


def discard(key: str, paths: Any) -> dict[str, Any]:
    """Throw away uncommitted changes in ``paths`` (``"."`` = everything):
    tracked files go back to the last commit, new files are deleted."""
    with lock_for(workspace.key_for(key)):
        root = ensure_repo(key)
        rels = _rels(root, paths)
        if not rels:
            raise ValueError("paths must not be empty")
        everything = "." in rels

        def wanted(path: str) -> bool:
            return everything or any(path == r or path.startswith(r.rstrip("/") + "/") for r in rels)

        tracked, untracked = [], []
        for change in _changes(root):
            hit = wanted(change["path"]) or (change.get("old_path") and wanted(change["old_path"]))
            if not hit:
                continue
            if change["status"] == "?":
                untracked.append(change["path"])
            else:
                tracked.append(change["path"])
                if change.get("old_path"):
                    tracked.append(change["old_path"])
        has_head = _git(root, "rev-parse", "--verify", "--quiet", "HEAD", check=False).returncode == 0
        for start in range(0, len(tracked), 100):
            chunk = [_ps(p) for p in tracked[start:start + 100]]
            if has_head:
                _git(root, "restore", "--source=HEAD", "--staged", "--worktree", "--", *chunk)
            else:
                _git(root, "rm", "-r", "-f", "--quiet", "--cached", "--", *chunk, check=False)
                untracked.extend(tracked[start:start + 100])
        for rel in untracked:
            target = root / rel
            try:
                if target.is_file() or target.is_symlink():
                    target.unlink()
            except OSError:
                logger.warning("could not delete %s", target, exc_info=True)
            parent = target.parent
            while parent != root and root in parent.parents:
                try:
                    os.rmdir(parent)
                except OSError:
                    break
                parent = parent.parent
        return {"discarded": sorted(set(tracked) | set(untracked)), "status": _status(root)}


# -- sessions ---------------------------------------------------------------------------
def start_branch(key: str, name: Any) -> str:
    """Put the workspace on branch ``name`` for a new session, creating it from
    the current commit when it does not exist. Returns the branch name."""
    name = check_branch_name(name)
    with lock_for(workspace.key_for(key)):
        root = ensure_repo(key)
        if _current(root) == name:
            return name
        if _branch_exists(root, name):
            _require_clean(root, "switch branches")
            _git(root, "checkout", "--quiet", name)
            _restore_stash(root, name)
        else:
            # a new branch starts at the current commit: uncommitted work comes along
            _git(root, "checkout", "--quiet", "-b", name)
        _forget_head(root)
        return name


def turn_message(title: str, request: str, session_id: str, turn: int) -> str:
    """``<session title>: <first line of the user request>`` plus trailers."""
    title = " ".join(str(title or "").split()) or "Session"
    line = " ".join((str(request or "").strip().splitlines() or [""])[0].split())
    stem = title[:-3] if title.endswith("...") else title
    if not line or line.lower().startswith(stem.lower()):
        subject = line or title
    else:
        subject = f"{title[:50]}: {line}"
    if len(subject) > 100:
        subject = subject[:97] + "..."
    return f"{subject}\n\nSession: {session_id}\nTurn: {turn}"
