"""Explicit workspace relocation: copy, verify, switch, preserve the source."""
from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from pathlib import Path

from .. import config


def normalize_path(value: str) -> Path:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError("Choose an absolute directory path")
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        raise ValueError("The workspace directory must be an absolute path on this machine")
    path = path.resolve()
    if path.parent == path:
        raise ValueError("A filesystem root cannot be a workspace directory")
    protected = [config.PAPERS_DIR, config.UPLOADS_DIR, config.PROJECTS_DIR,
                 config.SESSIONS_DIR, config.PLUGINS_DIR, config.CACHE_DIR,
                 config.GLOSSX_HOME, Path.home() / ".codex", Path.home() / ".claude"]
    for entry in protected:
        entry = entry.resolve()
        if path == entry or path in entry.parents or entry in path.parents:
            raise ValueError("Choose a directory separate from application data and CLI configuration")
    return path


def manifest(root: Path) -> dict[str, tuple]:
    """Include git metadata, checkpoints, empty directories, links and modes."""
    result = {}
    for directory, subdirs, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in subdirs + files:
            path = parent / name
            relative = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode & 0o777
            if path.is_symlink():
                result[relative] = ("link", os.readlink(path))
            elif path.is_dir():
                result[relative] = ("directory", mode)
            elif path.is_file():
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                result[relative] = ("file", path.stat().st_size, digest, mode)
            else:
                raise ValueError(f"Cannot relocate special file: {relative}")
    return result


def relocate(project_id: str, source: Path, destination: Path) -> dict:
    from ..agent import projects
    from . import vcs

    with vcs.lock_for(project_id):
        if source == destination:
            return {"project_id": project_id, "path": str(source), "previous_path": str(source),
                    "source_preserved": True, "verified_files": 0}
        if source in destination.parents or destination in source.parents:
            raise ValueError("The old and new workspace directories must not contain each other")
        for other_id, other in projects.workspace_paths():
            if other_id == project_id:
                continue
            if destination == other or destination in other.parents or other in destination.parents:
                raise ValueError("The destination overlaps another workspace")
        if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
            raise ValueError("The destination must be a new or empty directory")
        git_entry = source / ".git"
        if git_entry.exists() and not git_entry.is_dir():
            raise ValueError("Linked Git worktrees cannot be relocated; choose a standalone workspace")
        before = manifest(source)
        # Absolute internal symlinks would continue pointing into the preserved
        # source. Do not silently change their meaning during a relocation.
        for relative, entry in before.items():
            if entry[0] == "link" and Path(entry[1]).is_absolute():
                raise ValueError(f"Make this symlink relative before relocating: {relative}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage = destination.parent / f".glossx-copy-{uuid.uuid4().hex}"
        try:
            shutil.copytree(source, stage, symlinks=True)
            if manifest(stage) != before or manifest(source) != before:
                raise ValueError("Workspace changed during copying or verification failed; original location retained")
            if destination.exists():
                destination.rmdir()  # only a still-empty, explicitly chosen directory
            stage.rename(destination)
            projects.set_workspace_path(project_id, destination)
        except Exception as error:
            # No recursive deletion: both the source and a failed copy are
            # recoverable, and no unrelated destination files are overwritten.
            if stage.exists():
                raise ValueError(f"{error}. Incomplete copy retained at {stage}") from error
            raise
        return {"project_id": project_id, "path": str(destination), "previous_path": str(source),
                "source_preserved": True,
                "verified_files": sum(entry[0] == "file" for entry in before.values())}
