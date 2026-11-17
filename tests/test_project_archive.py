"""Project/thread archive lifecycle through the public API; no model calls."""
from pathlib import Path


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def test_project_archive_restore_and_remove_preserve_files(client, paper_id):
    project = ok(client.get(f"/api/papers/{paper_id}/project"))
    key = project["id"]
    root = Path(project["workspace"]["path"])
    marker = root / "keep-me.txt"
    marker.write_text("valuable notes", encoding="utf-8")
    own = ok(client.post("/api/sessions", json={"project_id": key, "title": "active"}))
    prior = ok(client.post("/api/sessions", json={"project_id": key, "title": "already archived"}))
    ok(client.patch(f"/api/sessions/{prior['id']}", json={"archived": True}))
    url = f"/api/agent/projects/{key}/lifecycle"
    assert client.post(url, json={"action": "remove"}).status_code == 400
    ok(client.post(url, json={"action": "archive"}))
    assert key not in {p["id"] for p in ok(client.get("/api/agent/projects"))}
    assert key in {p["id"] for p in ok(client.get("/api/agent/projects?archived=true"))}
    assert own["id"] not in {s["id"] for s in ok(client.get("/api/sessions"))}
    assert own["id"] in {s["id"] for s in ok(client.get("/api/sessions?archived=true"))}
    assert client.post("/api/sessions", json={"project_id": key}).status_code == 400
    assert client.post("/api/agent/runs", json={"project_id": key, "goal": "must not run"}).status_code == 400
    assert client.patch(f"/api/sessions/{own['id']}", json={"archived": False}).status_code == 400
    # Opening its paper must not create a replacement or unarchive it.
    assert ok(client.get(f"/api/papers/{paper_id}/project"))["id"] == key
    ok(client.post(url, json={"action": "restore"}))
    assert not ok(client.get(f"/api/sessions/{own['id']}"))["archived"]
    assert ok(client.get(f"/api/sessions/{prior['id']}"))["archived"]
    ok(client.post(url, json={"action": "archive"}))
    ok(client.post(url, json={"action": "remove"}))
    assert key not in {p["id"] for p in ok(client.get("/api/agent/projects?archived=true"))}
    assert marker.read_text(encoding="utf-8") == "valuable notes"
    assert ok(client.get(f"/api/papers/{paper_id}"))["id"] == paper_id
    assert ok(client.get(f"/api/papers/{paper_id}/project"))["removed"]
    assert client.post(url, json={"action": "restore"}).status_code == 400


def test_direction_archives_all_members_and_can_restore(client):
    projects = [ok(client.post("/api/agent/projects", json={"name": name})) for name in ("Alpha", "Beta")]
    for p in projects:
        ok(client.patch(f"/api/agent/projects/{p['id']}", json={"category": "Archive direction"}))
    hub = ok(client.post("/api/agent/categories/hub", json={"name": "Archive direction"}))
    ids = {p["id"] for p in projects} | {hub["id"]}
    body = {"name": "Archive direction", "action": "archive"}
    assert ok(client.post("/api/agent/categories/lifecycle", json=body))["changed"] == 3
    assert ids.isdisjoint(p["id"] for p in ok(client.get("/api/agent/projects")))
    assert ids <= {p["id"] for p in ok(client.get("/api/agent/projects?archived=true"))}
    body["action"] = "restore"
    assert ok(client.post("/api/agent/categories/lifecycle", json=body))["changed"] == 3
    assert ids <= {p["id"] for p in ok(client.get("/api/agent/projects"))}


def test_general_thread_archive_excludes_inbox_and_delete(client):
    thread = ok(client.post("/api/sessions", json={"title": "General archive"}))
    key = thread["id"]
    ok(client.patch(f"/api/sessions/{key}", json={"archived": True}))
    assert key not in {s["id"] for s in ok(client.get("/api/sessions?view=inbox"))}
    assert key not in {s["session_id"] for s in ok(client.get("/api/sessions/inbox/summary"))["items"]}
    assert client.post(f"/api/sessions/{key}/messages", json={"text": "must not call a model"}).status_code == 400
    assert client.post(f"/api/sessions/{key}/fork", json={}).status_code == 400
    ok(client.patch(f"/api/sessions/{key}", json={"archived": False}))
    assert key in {s["id"] for s in ok(client.get("/api/sessions"))}
    ok(client.patch(f"/api/sessions/{key}", json={"archived": True}))
    ok(client.delete(f"/api/sessions/{key}"))
    assert client.get(f"/api/sessions/{key}").status_code == 404


def test_running_thread_blocks_archive_and_archive_survives_reload(client):
    from app.harness.manager import SessionManager

    project = ok(client.post("/api/agent/projects", json={"name": "Busy archive"}))
    thread = ok(client.post("/api/sessions", json={"project_id": project["id"]}))
    rt = client.app.state.sessions._rt(thread["id"])
    rt.running = True
    try:
        assert client.patch(f"/api/sessions/{thread['id']}", json={"archived": True}).status_code == 409
        assert client.post(f"/api/agent/projects/{project['id']}/lifecycle", json={"action": "archive"}).status_code == 409
    finally:
        rt.running = False
    ok(client.post(f"/api/agent/projects/{project['id']}/lifecycle", json={"action": "archive"}))
    restored = SessionManager()
    assert restored.get(thread["id"])["archived"]
    assert thread["id"] not in {s["id"] for s in restored.list()}
