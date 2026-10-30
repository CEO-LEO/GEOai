"""
test_cloudrun.py — ย้ายไป Google Cloud Run (2026-10-30)
- งานตามเวลาแบบรอจบ (?wait=true) — Cloud Run ลด CPU หลังตอบคำขอแล้ว งาน background จะไม่จบ
- ผู้ใช้จำลองสำหรับ dev ถูกปิดบน Cloud Run (env K_SERVICE) เหมือนบน Render (env RENDER)
"""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from conftest import ADMIN_HEADERS

if "ee" not in sys.modules or not isinstance(sys.modules["ee"], MagicMock):
    _ee = MagicMock()
    _ee.EEException = Exception
    sys.modules["ee"] = _ee


@pytest_asyncio.fixture
async def client():
    from middleware import reset_limits
    reset_limits()
    with patch("scheduler.create_scheduler", return_value=MagicMock()):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac


@pytest.mark.asyncio
class TestTriggerWait:
    async def test_wait_runs_job_before_responding(self, client):
        with patch("main.run_job_with_catchup", new_callable=AsyncMock) as run:
            r = await client.post("/admin/trigger/daily-scan?hour=6&wait=true", headers=ADMIN_HEADERS)
        assert r.status_code == 200 and r.json()["status"] == "done" and r.json()["job"] == "daily_scan"
        run.assert_awaited_once()
        assert run.await_args.args[1:] == ("daily_scan", 6)

    async def test_default_still_background(self, client):
        with patch("main.run_job_with_catchup", new_callable=AsyncMock) as run:
            r = await client.post("/admin/trigger/rain-alert?hour=7", headers=ADMIN_HEADERS)
        assert r.json()["status"] == "started"
        assert run.await_args.args[1:] == ("rain_alert", 7)      # background task รันหลังตอบ (ใน test client รอจบ)

    async def test_requires_admin_key(self, client):
        assert (await client.post("/admin/trigger/daily-scan?wait=true")).status_code in (401, 403)


class TestDevAuthOffOnCloudRun:
    def test_k_service_disables_dev_user(self, monkeypatch):
        import auth
        monkeypatch.setenv("IAMROOT_DEV_AUTH_USER", "dev-user")
        assert auth.dev_auth_user() == "dev-user"
        monkeypatch.setenv("K_SERVICE", "iamroot")
        assert auth.dev_auth_user() is None
