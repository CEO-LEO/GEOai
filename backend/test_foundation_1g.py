"""
test_foundation_1g.py — Phase 1g: ข้อมูลตัวอย่างราชบุรี, /health
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

if "ee" not in sys.modules or not isinstance(sys.modules["ee"], MagicMock):
    _ee = MagicMock()
    _ee.EEException = Exception
    sys.modules["ee"] = _ee

import crops
import database

DEMO = json.loads((Path(__file__).parent / "config" / "demo_plots_ratchaburi.json").read_text(encoding="utf-8"))


class TestDemoData:
    def test_eight_ratchaburi_plots_with_known_crops(self):
        assert len(DEMO["plots"]) == 8
        for p in DEMO["plots"]:
            assert 13.2 < p["lat"] < 13.95 and 99.3 < p["lng"] < 100.1      # อยู่ในราชบุรี
            assert crops.validate_ids(p["crops"]) == p["crops"]
            assert p["plot_type"] in crops.PLOT_TYPES
            assert p["name"].startswith("ตัวอย่าง")
        assert {p["plot_type"] for p in DEMO["plots"]} == set(crops.PLOT_TYPES)
        assert any(len(p["crops"]) > 1 for p in DEMO["plots"])          # มีสวนผสม

    def test_dev_mode_demo_reports_in_ratchaburi(self):
        database.seed_demo_data()
        assert len(database._demo_reports) == 8
        assert all(13.2 < r["lat"] < 13.95 for r in database._demo_reports)

    def test_seed_sql_matches_config(self):
        sql = (Path(__file__).parent.parent / "supabase" / "seed_demo_ratchaburi.sql").read_text(encoding="utf-8")
        assert "ห้ามรันบน production" in sql
        assert sql.count("INSERT INTO plots") == 8


@pytest_asyncio.fixture
async def client():
    with patch("scheduler.create_scheduler", return_value=MagicMock()):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac


@pytest.mark.asyncio
async def test_health_reports_text_layout(client):
    body = (await client.get("/health")).json()
    assert body["text_layout"] in ("raqm", "basic", "unavailable")
