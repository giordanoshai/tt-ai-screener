import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from datetime import date


@pytest.fixture
def client():
    from web.app import app
    return TestClient(app)


@patch("web.app.get_conn")
def test_ticker_detail_from_cache(mock_conn, client):
    mock_con = MagicMock()
    mock_conn.return_value = mock_con

    fund_row = (28.5, 7.2, 45.0, 1.5, 3000000, 0.125, 0.15, 0.46, 160.0, 3.5, date.today().isoformat())
    news_rows = [("Test headline", "summary", "src", "2026-06-16T10:00:00", "positive")]
    meta_row = ("Apple Inc", "Technology", "Semiconductors")

    mock_con.execute.side_effect = [
        MagicMock(fetchone=MagicMock(return_value=fund_row)),  # fundamentals
        MagicMock(fetchall=MagicMock(return_value=news_rows)),  # news
        MagicMock(fetchone=MagicMock(return_value=meta_row)),   # meta
    ]

    resp = client.get("/ticker/AAPL")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ticker"] == "AAPL"
    assert data["fundamentals"]["pe_ratio"] == 28.5
    assert len(data["news"]) == 1


@patch("data_sources.registry.ProviderRegistry", side_effect=ImportError("no key"))
@patch("web.app.get_conn")
def test_ticker_detail_no_data(mock_conn, mock_registry, client):
    mock_con = MagicMock()
    mock_conn.return_value = mock_con

    mock_con.execute.side_effect = [
        MagicMock(fetchone=MagicMock(return_value=None)),  # no fundamentals
        MagicMock(fetchall=MagicMock(return_value=[])),     # no news
        MagicMock(fetchone=MagicMock(return_value=None)),   # no meta
    ]

    resp = client.get("/ticker/FAKE")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ticker"] == "FAKE"
    assert data["fundamentals"] is None
    assert data["news"] == []
