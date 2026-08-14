"""Paper import/list/get/patch/delete lifecycle — the library CRUD contract."""


def test_upload_extracts_title_and_page_count(client, pdf_bytes, pdf_meta):
    r = client.post(
        "/api/papers/upload",
        files={"file": ("sample.pdf", pdf_bytes, "application/pdf")},
    )
    assert r.status_code == 200, r.text
    paper = r.json()
    try:
        assert paper["id"]
        assert paper["title"] == pdf_meta["title"]
        assert paper["n_pages"] == pdf_meta["n_pages"]
        assert paper["source"] == "upload"
    finally:
        client.delete(f"/api/papers/{paper['id']}")


def test_uploaded_paper_appears_in_list_and_get(client, paper_id):
    listing = client.get("/api/papers")
    assert listing.status_code == 200
    ids = [p["id"] for p in listing.json()["papers"]]
    assert paper_id in ids

    got = client.get(f"/api/papers/{paper_id}")
    assert got.status_code == 200
    assert got.json()["id"] == paper_id


def test_patch_updates_title(client, paper_id):
    r = client.patch(f"/api/papers/{paper_id}", json={"title": "Renamed Paper"})
    assert r.status_code == 200
    assert r.json()["title"] == "Renamed Paper"
    assert client.get(f"/api/papers/{paper_id}").json()["title"] == "Renamed Paper"


def test_patch_updates_and_persists_direction_tags(client, paper_id):
    tags = ["Reinforcement Learning", "Robotics"]
    r = client.patch(f"/api/papers/{paper_id}", json={"tags": tags})
    assert r.status_code == 200
    assert r.json()["tags"] == tags

    listed = client.get("/api/papers").json()["papers"]
    paper = next(item for item in listed if item["id"] == paper_id)
    assert paper["tags"] == tags


def test_patch_updates_and_persists_reading_lens(client, paper_id):
    original = client.get(f"/api/papers/{paper_id}").json()
    assert original["reading_lens"] == "auto"

    response = client.patch(
        f"/api/papers/{paper_id}", json={"reading_lens": "humanities"}
    )
    assert response.status_code == 200
    assert response.json()["reading_lens"] == "humanities"

    listed = client.get("/api/papers").json()["papers"]
    paper = next(item for item in listed if item["id"] == paper_id)
    assert paper["reading_lens"] == "humanities"


def test_patch_rejects_invalid_reading_lens(client, paper_id):
    response = client.patch(
        f"/api/papers/{paper_id}", json={"reading_lens": "economics-ish"}
    )
    assert response.status_code == 422
    assert client.get(f"/api/papers/{paper_id}").json()["reading_lens"] == "auto"


def test_delete_removes_paper(client, pdf_bytes):
    pid = client.post(
        "/api/papers/upload",
        files={"file": ("sample.pdf", pdf_bytes, "application/pdf")},
    ).json()["id"]

    assert client.delete(f"/api/papers/{pid}").status_code == 200
    assert client.get(f"/api/papers/{pid}").status_code == 404


def test_get_unknown_paper_is_404(client):
    r = client.get("/api/papers/this-id-does-not-exist")
    assert r.status_code == 404


def test_upload_non_pdf_is_rejected(client):
    r = client.post(
        "/api/papers/upload",
        files={"file": ("notes.txt", b"just some text, not a pdf", "text/plain")},
    )
    assert r.status_code == 400
    assert "detail" in r.json()
