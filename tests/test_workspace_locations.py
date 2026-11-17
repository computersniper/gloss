"""Relocation preserves user files/history and switches only verified copies."""
from pathlib import Path
import shutil
import threading

import pytest


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def project(client):
    return ok(client.post("/api/agent/projects", json={"name": "Custom location"}))


def move(client, pid, target, confirm=True):
    return client.post(f"/api/workspace/{pid}/location", json={"path": str(target), "confirm": confirm})


def test_verified_relocation_keeps_git_checkpoints_and_original(client, tmp_path):
    p = project(client)
    pid, source = p["id"], Path(p["workspace"]["path"])
    ws = f"/api/workspace/{pid}"
    if shutil.which("git"):
        ok(client.get(ws + "/vcs/status"))
    ok(client.put(ws + "/file", params={"path": "notes/research.txt"}, json={"content": "研究\n"}))
    before_checkpoints = ok(client.get(ws + "/checkpoints"))
    revision = None
    if shutil.which("git"):
        revision = ok(client.post(ws + "/vcs/commit", json={"message": "Preserve research"}))["commit"]["id"]
    destination = tmp_path / "my paper workspace"
    result = ok(move(client, pid, destination))
    assert result["source_preserved"] and result["verified_files"] > 0
    assert result["previous_path"] == str(source)
    assert (source / "notes/research.txt").read_text(encoding="utf-8") == "研究\n"
    assert ok(client.get(f"/api/agent/projects/{pid}"))["workspace"]["path"] == str(destination)
    assert ok(client.get(ws + "/file", params={"path": "notes/research.txt"}))["content"] == "研究\n"
    assert ok(client.get(ws + "/checkpoints")) == before_checkpoints
    if revision:
        assert ok(client.get(ws + "/vcs/status"))["head"] == revision
    ok(client.put(ws + "/file", params={"path": "notes/research.txt"}, json={"content": "new notes\n"}))
    assert (source / "notes/research.txt").read_text(encoding="utf-8") == "研究\n"
    assert (destination / "notes/research.txt").read_text(encoding="utf-8") == "new notes\n"


def test_default_location_changes_only_new_paper_projects(client, paper_id, tmp_path):
    import fitz

    old = ok(client.get(f"/api/papers/{paper_id}/project"))
    settings = ok(client.get("/api/settings"))["config"]
    base = tmp_path / "papers"
    try:
        ok(client.post("/api/settings", json={"paper_workspace_root": str(base)}))
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), "New custom workspace paper")
            paper = ok(client.post("/api/papers/upload", files={"file": ("custom.pdf", pdf.tobytes(), "application/pdf")}))
        new = ok(client.get(f"/api/papers/{paper['id']}/project"))
        assert Path(new["workspace"]["path"]) == base / new["id"]
        assert (base / new["id"] / "GLOSSX.md").is_file()
        assert ok(client.get(f"/api/papers/{paper_id}/project"))["workspace"]["path"] == old["workspace"]["path"]
    finally:
        ok(client.post("/api/settings", json={"paper_workspace_root": settings.get("paper_workspace_root", "")}))


def test_relocation_rejects_unconfirmed_overlap_and_nonempty(client, tmp_path):
    p, other = project(client), project(client)
    source = Path(p["workspace"]["path"])
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "keep.txt").write_text("user data", encoding="utf-8")
    for target, confirm in [(tmp_path / "new", False), (source / "nested", True),
                            (source.parent, True), (Path(other["workspace"]["path"]), True),
                            (occupied, True), (Path("relative-path"), True)]:
        assert move(client, p["id"], target, confirm).status_code == 400
    assert (occupied / "keep.txt").read_text(encoding="utf-8") == "user data"
    assert ok(client.get(f"/api/agent/projects/{p['id']}"))["workspace"]["path"] == str(source)


def test_failed_verification_retains_original_mapping(client, tmp_path, monkeypatch):
    from app.harness import locations

    p = project(client)
    original_copy = locations.shutil.copytree

    def altered_copy(source, destination, **kwargs):
        result = original_copy(source, destination, **kwargs)
        (Path(destination) / "extra.txt").write_text("bad copy", encoding="utf-8")
        return result

    monkeypatch.setattr(locations.shutil, "copytree", altered_copy)
    destination = tmp_path / "failed"
    response = move(client, p["id"], destination)
    assert response.status_code == 400 and "verification" in response.text
    assert not destination.exists()
    assert ok(client.get(f"/api/agent/projects/{p['id']}"))["workspace"]["path"] == p["workspace"]["path"]


def test_relocation_reserves_workspace_against_new_turn(client, tmp_path, monkeypatch):
    from app.harness import locations

    p = project(client)
    sid = ok(client.post("/api/sessions", json={"project_id": p["id"]}))["id"]
    entered, release = threading.Event(), threading.Event()
    original_copy = locations.shutil.copytree
    replies = []

    def slow_copy(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original_copy(*args, **kwargs)

    monkeypatch.setattr(locations.shutil, "copytree", slow_copy)
    worker = threading.Thread(target=lambda: replies.append(move(client, p["id"], tmp_path / "reserved")))
    worker.start()
    try:
        assert entered.wait(10)
        detail = ok(client.get(f"/api/agent/projects/{p['id']}"))
        assert detail["workspace"]["relocating"]
        assert any(item["id"] == p["id"] for item in ok(client.get("/api/agent/projects")))
        response = client.post(f"/api/sessions/{sid}/messages", json={"text": "Do not start"})
        assert response.status_code == 409
        assert move(client, p["id"], tmp_path / "second").status_code == 409
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive()
    ok(replies[0])


def test_open_terminal_prevents_relocation(client, tmp_path):
    p = project(client)
    with client.websocket_connect(f"/api/workspace/{p['id']}/terminal") as socket:
        assert socket.receive_json()["type"] == "ready"
        response = move(client, p["id"], tmp_path / "terminal-busy")
        assert response.status_code == 409 and response.json()["code"] == "busy"
        assert "terminal" in response.json()["detail"].lower()
        socket.send_json({"type": "close"})
    assert not (tmp_path / "terminal-busy").exists()
