"""Timestamps are sent as explicit UTC; profile and display preferences."""
import base64
from datetime import datetime

from fastapi.testclient import TestClient

from app.main import app


def test_timestamps_are_explicit_utc():
    with TestClient(app) as c:
        sid = c.post("/api/sessions", json={}).json()["id"]
        chat = c.get(f"/api/sessions/{sid}").json()
        for field in ("created_at", "updated_at"):
            assert chat[field].endswith(("Z", "+00:00")), chat[field]  # ISO 8601, explicitly UTC
            assert datetime.fromisoformat(chat[field]).utcoffset().total_seconds() == 0
        listed = next(s for s in c.get("/api/sessions").json() if s["id"] == sid)
        assert listed["updated_at"].endswith(("Z", "+00:00"))
        assert c.get(f"/api/sessions/{sid}/runs").status_code == 200


def test_profile_and_preferences():
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
    with TestClient(app) as c:
        p = c.get("/api/profile").json()
        assert p["timezone"] == "auto" and p["hour12"] is False and p["avatar_url"] is None

        p = c.put("/api/profile", json={"name": "Joe", "email": "joe@example.com",
                                        "timezone": "America/New_York", "hour12": True}).json()
        assert (p["name"], p["timezone"], p["hour12"]) == ("Joe", "America/New_York", True)
        assert c.put("/api/profile", json={"timezone": "Mars/Olympus"}).status_code == 422
        assert c.put("/api/profile", json={"email": "not-an-email"}).status_code == 422
        assert c.put("/api/profile", json={"timezone": ""}).json()["timezone"] == "auto"
        assert c.get("/api/profile").json()["name"] == "Joe"  # partial updates keep other fields

        up = c.post("/api/profile/avatar", files={"file": ("me.png", png, "image/png")})
        assert up.status_code == 200 and up.json()["avatar_url"].startswith("/api/profile/avatar")
        assert c.get("/api/profile/avatar").content == png
        assert c.post("/api/profile/avatar", files={"file": ("me.bmp", b"x", "image/bmp")}).status_code == 415
        assert c.post("/api/profile/avatar", files={"file": ("big.png", b"0" * (2 * 1024 * 1024 + 1), "image/png")}).status_code == 413
        assert c.delete("/api/profile/avatar").json()["avatar_url"] is None
        assert c.get("/api/profile/avatar").status_code == 404
