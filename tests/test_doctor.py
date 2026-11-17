import json
from pathlib import Path

"""In-app Doctor environment and self-check API."""


def test_doctor_reports_local_installation_without_exposing_secrets(client):
    response = client.post("/api/doctor")
    assert response.status_code == 200, response.text
    report = response.json()

    assert report["status"] in {"pass", "warning", "fail"}
    root = Path(__file__).resolve().parent.parent
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    assert report["version"] == package["version"]
    assert report["summary"]["passed"] >= 1
    assert report["system"]["paper_count"] >= 0
    assert report["system"]["plugin_count"] >= 0
    assert {item["id"] for item in report["checks"]} == {
        "runtime",
        "frontend",
        "data_dir",
        "cache_dir",
        "database",
        "disk",
        "provider",
        "provider_runtime",
        "plugins",
    }
    assert all(item["status"] in {"pass", "warning", "fail"} for item in report["checks"])
    rendered = response.text.lower()
    assert "api_key" not in rendered
    assert "sk-ant-" not in rendered


def test_doctor_reflects_the_latest_provider_connection_state(client, monkeypatch):
    from app.routers import doctor

    monkeypatch.setattr(
        doctor.registry,
        "provider_statuses",
        lambda: {
            "local_claude": {
                "status": "connected",
                "connected": True,
                "error": "",
                "checked_at": 1,
            }
        },
    )
    report = client.post("/api/doctor").json()
    provider = next(item for item in report["checks"] if item["id"] == "provider")
    assert provider == {
        "id": "provider",
        "status": "pass",
        "detail": "local_claude connected",
    }
