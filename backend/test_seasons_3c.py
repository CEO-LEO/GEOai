"""
test_seasons_3c.py — Phase 3c: เทียบหน้าฝน/หน้าแล้งของแปลงเดียวกัน
"""

import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from conftest import auth_as, fake_verify_line_token

if "ee" not in sys.modules or not isinstance(sys.modules["ee"], MagicMock):
    _ee = MagicMock()
    _ee.EEException = Exception
    sys.modules["ee"] = _ee

import seasons

TODAY = datetime(2026, 10, 25, tzinfo=timezone.utc)


def _series(rainy_vv, dry_vv, wetter_this_year=0.0, orbit=1):
    out, t = [], TODAY - timedelta(days=730)
    while t <= TODAY:
        base = rainy_vv if t.month in seasons.RAINY_MONTHS else dry_vv
        if t.year == 2026 and t.month >= 5:
            base += wetter_this_year
        out.append((t, base, orbit))
        out.append((t, base - 3, orbit + 1))        # วงโคจรรอง (ควรถูกตัดทิ้ง)
        t += timedelta(days=6)
    return out


class TestSeasons:
    def test_keys_and_labels(self):
        assert seasons.season_key(datetime(2026, 7, 1)) == ("rainy", 2026)
        assert seasons.season_key(datetime(2025, 12, 1)) == ("dry", 2026)
        assert seasons.season_key(datetime(2026, 2, 1)) == ("dry", 2026)
        assert seasons.season_label("dry", 2026) == "หน้าแล้ง 2569 (พ.ย. 68–เม.ย. 69)"

    def test_wetter_rainy_season(self):
        out = seasons.summarize(_series(-7.0, -9.0, wetter_this_year=1.5), TODAY)
        assert out["status"] == "ok"
        assert any(s.startswith("หน้าฝน 2569 (ยังไม่จบฤดู) ชื้นกว่าหน้าฝน 2568") for s in out["sentences"])
        assert any("หน้าแล้ง 2569 ชื้นใกล้เคียงหน้าแล้ง 2568" in s for s in out["sentences"])
        assert all(0 <= m["index"] <= 100 for m in out["months"])

    def test_uses_dominant_orbit_only(self):
        out = seasons.summarize(_series(-7.0, -9.0), TODAY)
        assert max(m["vv"] for m in out["months"]) == -7.0     # ค่าวงโคจรรอง (-10/-12) ไม่ปน

    def test_flat_year_round(self):
        out = seasons.summarize(_series(-8.0, -8.0), TODAY)
        assert any("ชื้นใกล้เคียงกัน" in s for s in out["sentences"])

    def test_empty(self):
        assert seasons.summarize([], TODAY)["status"] == "insufficient"

    def test_sparse_months_not_used_for_extremes(self):
        s = [(TODAY - timedelta(days=d), -5.0 if d == 400 else -8.0, 1) for d in range(0, 700, 12)]
        out = seasons.summarize(s, TODAY)
        peak_line = next(x for x in out["sentences"] if x.startswith("เดือนที่ชื้นที่สุด"))
        spike_month = (TODAY - timedelta(days=400)).strftime("%Y-%m")
        spike_label = next(m["label"] for m in out["months"] if m["month"] == spike_month)
        assert spike_label not in peak_line.split("·")[0]        # เดือนที่มีภาพเดียวไม่ถูกเลือก


@pytest_asyncio.fixture
async def client():
    from middleware import reset_limits
    reset_limits()
    with patch("scheduler.create_scheduler", return_value=MagicMock()), \
         patch("auth.verify_line_token", side_effect=fake_verify_line_token):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac


@pytest.mark.asyncio
class TestSeasonsApi:
    async def test_endpoint_and_cache(self, client):
        import main
        main._seasons_cache.clear()
        plot = {"id": 5, "user_id": "U1", "lat": 13.5, "lng": 99.8, "polygon": None}
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=plot), \
             patch("risk_v2.s1_series", return_value=_series(-7, -9)) as s1, \
             patch("main.log_event", new_callable=AsyncMock):
            r1 = await client.get("/plots/U1/5/seasons", headers=auth_as("U1"))
            r2 = await client.get("/plots/U1/5/seasons", headers=auth_as("U1"))
        assert r1.status_code == 200 and r1.json()["status"] == "ok" and r2.json() == r1.json()
        assert s1.call_count == 1

    async def test_other_users_plot(self, client):
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value={"id": 5, "user_id": "U2"}):
            assert (await client.get("/plots/U1/5/seasons", headers=auth_as("U1"))).status_code == 404
