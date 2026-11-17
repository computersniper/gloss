from pathlib import Path
import shutil
import subprocess
import pytest


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def test_open_existing_folder_preserves_files_and_reuses_project(client, tmp_path):
    folder = tmp_path / "My Research"
    folder.mkdir()
    note = folder / "notes.txt"
    note.write_text("existing research", encoding="utf-8")
    first = ok(client.post("/api/agent/projects/open-folder", json={"path": str(folder)}))
    key = first["id"]
    assert Path(first["workspace"]["path"]) == folder.resolve()
    assert first["name"] == "My Research"
    assert ok(client.post("/api/agent/projects/open-folder", json={"path": str(folder / ".")}))["id"] == key
    assert note.read_text(encoding="utf-8") == "existing research"
    assert (folder / ".glossx").is_dir()
    assert not (folder / ".git").exists()
    status = ok(client.get(f"/api/workspace/{key}/vcs/status"))
    assert not status["initialized"]
    assert not (folder / ".git").exists()
    content = ok(client.get(f"/api/workspace/{key}/file", params={"path": "notes.txt"}))
    assert content["content"] == "existing research"
    other = ok(client.post("/api/sessions", json={"project_id": key}))
    assert Path(other["workspace"]["path"]) == folder.resolve()


def test_open_folder_rejects_invalid_overlap_and_archived_project(client, tmp_path):
    endpoint = "/api/agent/projects/open-folder"
    for path in ("relative", str(tmp_path / "missing"), str(Path(tmp_path.anchor))):
        assert client.post(endpoint, json={"path": path}).status_code == 400
    folder = tmp_path / "workspace"
    folder.mkdir()
    project = ok(client.post(endpoint, json={"path": str(folder)}))
    child = folder / "nested"
    child.mkdir()
    assert client.post(endpoint, json={"path": str(child)}).status_code == 400
    assert client.post(endpoint, json={"path": str(tmp_path)}).status_code == 400
    url = f"/api/agent/projects/{project['id']}/lifecycle"
    ok(client.post(url, json={"action": "archive"}))
    assert client.post(endpoint, json={"path": str(folder)}).status_code == 400
    ok(client.post(url, json={"action": "restore"}))
    assert ok(client.post(endpoint, json={"path": str(folder)}))["id"] == project["id"]
    ok(client.post(url, json={"action": "archive"}))
    ok(client.post(url, json={"action": "remove"}))
    reopened = ok(client.post(endpoint, json={"path": str(folder)}))
    assert reopened["id"] != project["id"]


def test_open_folder_preserves_existing_git_head_and_staging(client, tmp_path):
    git = shutil.which("git")
    if not git:
        pytest.skip("git is not installed")
    folder = tmp_path / "existing-repo"
    folder.mkdir()
    def run(*args):
        return subprocess.run([git, *args], cwd=folder, check=True, capture_output=True).stdout
    run("init")
    note = folder / "notes.txt"
    note.write_text("original", encoding="utf-8")
    run("add", "notes.txt")
    run("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "Existing research")
    note.write_text("staged changes", encoding="utf-8")
    run("add", "notes.txt")
    head, staged = run("rev-parse", "HEAD"), run("diff", "--cached")
    project = ok(client.post("/api/agent/projects/open-folder", json={"path": str(folder)}))
    assert ok(client.get(f"/api/workspace/{project['id']}/vcs/status"))["initialized"]
    assert run("rev-parse", "HEAD") == head
    assert run("diff", "--cached") == staged
    assert note.read_text(encoding="utf-8") == "staged changes"
