from fastapi.testclient import TestClient

from src.api import app


client = TestClient(app)


def test_recalculate_requires_admin_auth_header(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")

    response = client.post("/api/ratings/recalculate", json={"dry_run": True})

    assert response.status_code == 401
    assert response.json()["detail"] == "Admin authorization required"


def test_reset_rejects_invalid_admin_token(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")

    response = client.post(
        "/api/ratings/reset/agent-123",
        headers={"Authorization": "Bearer wrong-key"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid admin token"
