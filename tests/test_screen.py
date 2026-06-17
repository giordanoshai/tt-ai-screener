import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock


@pytest.fixture
def client():
    from web.app import app
    return TestClient(app)


@patch("web.app.get_conn")
def test_screen_returns_list(mock_conn, client):
    mock_con = MagicMock()
    mock_conn.return_value = mock_con

    mock_con.execute.side_effect = [
        MagicMock(fetchone=MagicMock(return_value=("2026-06-16",))),  # max date
        MagicMock(fetchall=MagicMock(return_value=[
            ("AAPL", "Apple Inc", "Technology", 195.0, 1.5, 55.0, 1.2, 0.02, "core"),
        ])),
    ]

    resp = client.post("/screen", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    assert data["results"][0]["ticker"] == "AAPL"


@patch("web.app.get_conn")
def test_screen_filters_by_sector(mock_conn, client):
    mock_con = MagicMock()
    mock_conn.return_value = mock_con

    mock_con.execute.side_effect = [
        MagicMock(fetchone=MagicMock(return_value=("2026-06-16",))),
        MagicMock(fetchall=MagicMock(return_value=[])),
    ]

    resp = client.post("/screen", json={"sectors": ["Technology"]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"] == []
    assert data["total"] == 0
