"""
test_messaging.py — Phase 1b: ลดข้อความ LINE, ตรวจโควตา, log การส่ง, สแกนไม่หลุดเกิน 1,000
"""

import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from conftest import ADMIN_HEADERS, auth_as, fake_verify_line_token

if "ee" not in sys.modules or not isinstance(sys.modules["ee"], MagicMock):
    _ee = MagicMock()
    _ee.EEException = Exception
    sys.modules["ee"] = _ee


HIGH = {"overall_risk_level": "high", "ndvi_change": 0.0, "swab": {"swab_level": 3}}
OK = {"overall_risk_level": "ok", "ndvi_change": 0.0, "swab": {"swab_level": 0}}


def _resp(status, body=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body if body is not None else {}
    r.text = text
    return r


def _mock_client(get=None, post=None):
    c = AsyncMock()
    c.get = AsyncMock(side_effect=get)
    c.post = AsyncMock(side_effect=post)
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    return c


@pytest.fixture(autouse=True)
def _reset_quota():
    import line_quota
    line_quota.reset_for_tests()
    yield
    line_quota.reset_for_tests()


def _set_quota(quota, used):
    import line_quota, time
    line_quota._state.update(type="limited", quota=quota, used=used,
                             fetched_at=time.monotonic(), sent_since_fetch=0)


# ── โควตา ─────────────────────────────────────────────

class TestQuotaGuard:
    def test_unknown_quota_allows(self):
        import line_quota
        assert line_quota.is_allowed("low")

    def test_tiers(self):
        import line_quota
        _set_quota(300, 300 - 60)      # เหลือ 20%
        assert line_quota.is_allowed("critical")
        assert line_quota.is_allowed("normal")
        assert not line_quota.is_allowed("low")
        _set_quota(300, 300 - 20)      # เหลือ ~7%
        assert line_quota.is_allowed("critical")
        assert not line_quota.is_allowed("normal")
        _set_quota(300, 300)           # หมด
        assert not line_quota.is_allowed("critical")

    def test_health(self):
        import line_quota
        _set_quota(300, 100)
        assert line_quota.health()["health"] == "ok"
        _set_quota(300, 290)
        assert line_quota.health()["health"] == "warn"
        _set_quota(300, 300)
        h = line_quota.health()
        assert h["health"] == "critical" and h["remaining"] == 0

    def test_failure_makes_health_critical(self):
        import line_quota
        _set_quota(300, 10)
        line_quota.note_event("failed")
        assert line_quota.health()["health"] == "critical"


@pytest.mark.asyncio
class TestSendLineMessage:
    async def test_skipped_and_logged_when_quota_exhausted(self):
        import line_sender
        _set_quota(300, 300)
        client = _mock_client(post=AsyncMock())
        with patch("line_sender.httpx.AsyncClient", return_value=client), \
             patch("database.log_line_message", new_callable=AsyncMock) as log:
            ok = await line_sender.send_line_message("U1", "hi", kind="risk_alert", priority="critical")
        assert ok is False
        client.post.assert_not_called()
        assert log.call_args.args[3] == "skipped_quota"

    async def test_monthly_limit_429_marks_exhausted_without_retry(self):
        import line_quota, line_sender
        _set_quota(300, 100)

        async def post(url, headers=None, json=None):
            return _resp(429, text='{"message":"You have reached your monthly limit."}')

        client = _mock_client(post=post)
        with patch("line_sender.httpx.AsyncClient", return_value=client), \
             patch("line_sender.asyncio.sleep", new_callable=AsyncMock) as sleep, \
             patch("database.log_line_message", new_callable=AsyncMock) as log:
            ok = await line_sender.send_line_message("U1", "hi")
        assert ok is False
        assert client.post.call_count == 1
        sleep.assert_not_called()
        assert line_quota.remaining() == 0
        assert log.call_args.args[3] == "failed"
        assert line_quota.health()["health"] == "critical"

    async def test_sent_is_logged_and_counted(self):
        import line_quota, line_sender
        _set_quota(300, 100)
        client = _mock_client(post=AsyncMock(return_value=_resp(200)))
        with patch("line_sender.httpx.AsyncClient", return_value=client), \
             patch("database.log_line_message", new_callable=AsyncMock) as log:
            ok = await line_sender.send_line_message("U1", "hi", kind="weekly_digest", priority="low")
        assert ok is True
        assert line_quota.remaining() == 199
        assert log.call_args.args[1:4] == ("weekly_digest", "low", "sent")

    async def test_more_than_five_messages_truncated(self):
        import line_sender
        client = _mock_client(post=AsyncMock(return_value=_resp(200)))
        msgs = [{"type": "text", "text": str(i)} for i in range(8)]
        with patch("line_sender.httpx.AsyncClient", return_value=client), \
             patch("database.log_line_message", new_callable=AsyncMock):
            await line_sender.send_line_message("U1", "x", messages=msgs)
        assert len(client.post.call_args.kwargs["json"]["messages"]) == 5


# ── /analyze ไม่ push, /admin/messaging/status ─────────

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
class TestEndpoints:
    async def test_messaging_status_requires_admin(self, client):
        assert (await client.get("/admin/messaging/status", headers=auth_as("U1"))).status_code == 403

    async def test_messaging_status(self, client):
        _set_quota(300, 300)
        with patch("line_quota.refresh", new_callable=AsyncMock), \
             patch("main.get_recent_message_failures", new_callable=AsyncMock, return_value=[]):
            resp = await client.get("/admin/messaging/status", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["health"] == "critical" and body["remaining"] == 0


# ── การแจ้งเตือนในงานสแกน ─────────────────────────────

class TestAlertRules:
    def test_increase_only(self):
        from scheduler import _risk_increased_to_high
        assert _risk_increased_to_high(True, None)
        assert _risk_increased_to_high(True, "medium")
        assert _risk_increased_to_high(True, "ok")
        assert not _risk_increased_to_high(True, "high")
        assert not _risk_increased_to_high(False, "ok")

    def test_digest_day_is_monday_bangkok(self):
        from scheduler import _is_digest_day
        bkk = timezone(timedelta(hours=7))
        assert _is_digest_day(datetime(2026, 10, 19, 7, tzinfo=bkk))       # จันทร์
        assert not _is_digest_day(datetime(2026, 10, 20, 7, tzinfo=bkk))   # อังคาร
        # อาทิตย์ 23:30 UTC = จันทร์ 06:30 เวลาไทย
        assert _is_digest_day(datetime(2026, 10, 18, 23, 30, tzinfo=timezone.utc))


@pytest.mark.asyncio
class TestRainDedupe:
    async def test_rules(self):
        from scheduler import _rain_alert_is_new
        with patch("scheduler.get_last_sent_message", new_callable=AsyncMock, return_value=None):
            assert await _rain_alert_is_new(1, "warning")
        with patch("scheduler.get_last_sent_message", new_callable=AsyncMock,
                   return_value={"level": "warning"}):
            assert not await _rain_alert_is_new(1, "warning")
            assert not await _rain_alert_is_new(1, "watch")
            assert await _rain_alert_is_new(1, "critical")


def _scan_patches(prev_level, digest_users=frozenset(), monday=False, user_ids=("U1",)):
    plot = {"id": 7, "name": "แปลง A", "lat": 13.5, "lng": 99.8,
            "polygon": [[99.8, 13.5], [99.81, 13.5], [99.81, 13.51], [99.8, 13.5]]}
    return [
        patch("scheduler.delete_old_grid_snapshots", new_callable=AsyncMock),
        patch("scheduler.delete_old_plot_images", new_callable=AsyncMock),
        patch("scheduler.get_all_user_ids", new_callable=AsyncMock, return_value=list(user_ids)),
        patch("scheduler.get_notifiable_users", new_callable=AsyncMock, return_value=set(user_ids)),
        patch("scheduler.get_digest_users", new_callable=AsyncMock, return_value=set(digest_users)),
        patch("scheduler.get_user_plots", new_callable=AsyncMock, return_value=[plot]),
        patch("scheduler.get_plot_analysis_since", new_callable=AsyncMock, return_value=dict(HIGH)),
        patch("scheduler.get_previous_risk_level", new_callable=AsyncMock, return_value=prev_level),
        patch("scheduler.get_last_sent_message", new_callable=AsyncMock, return_value=None),
        patch("scheduler.get_swab_level_days_ago", new_callable=AsyncMock, return_value=None),
        patch("scheduler.build_plot_grid_image_url", new_callable=AsyncMock,
              return_value={"url": "https://img/x.png", "problem_points": []}),
        patch("scheduler.build_weekly_alert_flex", return_value={"type": "flex", "contents": {}}),
        patch("scheduler.build_result_flex", return_value={"type": "flex", "contents": {"type": "bubble"}}),
        patch("scheduler.build_daily_digest_message", return_value="สรุป"),
        patch("scheduler.format_message", return_value="msg"),
        patch("scheduler._is_digest_day", return_value=monday),
        # คำเตือนความเสี่ยงถูกหยุดชั่วคราวในโหมดกำลังปรับปรุง — test ตรรกะการเตือนจึงเปิดไว้
        patch("scheduler.risk_display.risk_alerts_enabled", return_value=True),
    ]


async def _run_scan(patches):
    from contextlib import ExitStack
    from scheduler import daily_scan_job
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        send = stack.enter_context(patch("scheduler.send_line_message", new_callable=AsyncMock,
                                         return_value=True))
        await daily_scan_job(hour=None)
        return send


@pytest.mark.asyncio
class TestDailyScan:
    async def test_no_alert_when_still_high(self):
        send = await _run_scan(_scan_patches(prev_level="high"))
        send.assert_not_called()

    async def test_alert_once_when_risk_increases(self):
        send = await _run_scan(_scan_patches(prev_level="medium"))
        assert send.call_count == 1
        kw = send.call_args.kwargs
        assert kw["kind"] == "risk_alert" and kw["priority"] == "critical" and kw["plot_id"] == 7

    async def test_digest_not_sent_on_other_days(self):
        send = await _run_scan(_scan_patches(prev_level="high", digest_users={"U1"}, monday=False))
        send.assert_not_called()

    async def test_weekly_digest_is_one_push_with_at_most_five_messages(self):
        send = await _run_scan(_scan_patches(prev_level="high", digest_users={"U1"}, monday=True))
        assert send.call_count == 1
        kw = send.call_args.kwargs
        assert kw["kind"] == "weekly_digest" and kw["priority"] == "low"
        assert 1 < len(kw["messages"]) <= 5

    async def test_scans_users_from_users_table(self):
        """ผู้ใช้มาจากตาราง users ทั้งหมด ไม่ใช่จากรายงานล่าสุด 1,000 แถว"""
        users = [f"U{i:04d}" for i in range(1200)]
        patches = _scan_patches(prev_level="high", user_ids=users)
        from contextlib import ExitStack
        from scheduler import daily_scan_job
        with ExitStack() as stack:
            mocks = [stack.enter_context(p) for p in patches]
            stack.enter_context(patch("scheduler.send_line_message", new_callable=AsyncMock))
            await daily_scan_job(hour=None)
        get_user_plots = next(m for p, m in zip(patches, mocks) if p.attribute == "get_user_plots")
        assert get_user_plots.call_count == 1200


@pytest.mark.asyncio
class TestFetchAllPagination:
    async def test_reads_every_page(self, monkeypatch):
        import database
        monkeypatch.setattr(database, "SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setattr(database, "SUPABASE_KEY", "k")
        pages = [[{"user_id": f"U{i}"} for i in range(1000)],
                 [{"user_id": f"V{i}"} for i in range(250)]]
        calls = []

        async def get(url, headers=None, params=None):
            calls.append(params["offset"])
            return _resp(200, pages[len(calls) - 1])

        with patch("database.httpx.AsyncClient", return_value=_mock_client(get=get)):
            ids = await database.get_all_user_ids()
        assert len(ids) == 1250
        assert calls == ["0", "1000"]


# ── ปุ่ม "ตรวจสอบ" ตอบด้วย reply ไม่ใช้ push ─────────

@pytest.mark.asyncio
class TestHistoryReply:
    async def test_cards_and_images_in_single_reply(self):
        import webhook
        plots = [{"id": i, "name": f"แปลง {i}", "polygon": [[0, 0], [1, 0], [1, 1], [0, 0]]}
                 for i in range(1, 6)]
        report = {"lat": 13.5, "lng": 99.8}
        event = {"replyToken": "r", "postback": {"data": "action=history"}, "source": {"userId": "U1"}}
        with patch("webhook.get_user_plots", new_callable=AsyncMock, return_value=plots), \
             patch("webhook.get_latest_report_by_plot", new_callable=AsyncMock, return_value=report), \
             patch("webhook.build_result_flex", return_value={"contents": {"type": "bubble"}}), \
             patch("rule_engine.format_message", return_value="m"), \
             patch("plot_image_service.build_plot_grid_image_url", new_callable=AsyncMock,
                   return_value={"url": "https://img/a.png",
                                 "problem_points": [{"grid_label": "A1", "label_th": "น้ำขังหนัก",
                                                     "position_desc": "ฝั่งเหนือของแปลง"}]}), \
             patch("webhook._start_loading", new_callable=AsyncMock), \
             patch("webhook._reply", new_callable=AsyncMock) as reply, \
             patch("line_sender.send_line_message", new_callable=AsyncMock) as push:
            await webhook._handle_postback(event)
        push.assert_not_called()
        msgs = reply.call_args.args[1]
        assert len(msgs) == 5
        assert msgs[0]["contents"]["type"] == "carousel"
        assert msgs[1]["type"] == "text" and "A1" in msgs[1]["text"] and "ในแอป" in msgs[1]["text"]
        assert [m["type"] for m in msgs[2:]] == ["image"] * 3


# ── รอบ 3: เตือนซ้ำครั้งเดียว, รวมเตือนฝน, แจ้งเฉพาะตอนสถานะเปลี่ยน ─────────────

@pytest.mark.asyncio
class TestRiskReminder:
    async def _needs(self, last):
        from scheduler import _needs_risk_reminder
        with patch("scheduler.get_last_sent_message", new_callable=AsyncMock, return_value=last):
            return await _needs_risk_reminder(7)

    async def test_reminder_after_seven_days_once(self):
        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        recent = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        assert await self._needs({"kind": "risk_alert", "created_at": old})
        assert not await self._needs({"kind": "risk_alert", "created_at": recent})
        assert not await self._needs({"kind": "risk_reminder", "created_at": old})   # เตือนซ้ำไปแล้ว
        assert not await self._needs(None)                                          # ไม่มี log

    async def test_scan_sends_reminder_when_still_high(self):
        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        patches = [p for p in _scan_patches(prev_level="high") if p.attribute != "get_last_sent_message"]
        patches.append(patch("scheduler.get_last_sent_message", new_callable=AsyncMock,
                             return_value={"kind": "risk_alert", "created_at": old}))
        send = await _run_scan(patches)
        assert send.call_count == 1
        assert send.call_args.kwargs["kind"] == "risk_reminder"
        assert send.call_args.kwargs["priority"] == "normal"


@pytest.mark.asyncio
class TestGroupedRainAlert:
    async def test_many_plots_one_push_with_per_plot_logs(self):
        from scheduler import _send_grouped_rain_alert
        poly = [[0, 0], [1, 0], [1, 1], [0, 0]]
        pending = [{"flex": {"type": "flex", "contents": {"type": "bubble", "i": i}}, "msg": f"m{i}",
                    "plot_id": i, "level": "critical" if i == 2 else "warning",
                    "polygon": poly, "name": f"P{i}"} for i in range(1, 6)]
        with patch("scheduler.build_plot_grid_image_url", new_callable=AsyncMock,
                   return_value={"url": "https://img/a.png", "problem_points": []}) as img, \
             patch("scheduler.send_line_message", new_callable=AsyncMock) as send:
            await _send_grouped_rain_alert("U1", pending)
        assert send.call_count == 1
        kw = send.call_args.kwargs
        assert kw["messages"][0]["contents"]["type"] == "carousel"
        assert len(kw["messages"][0]["contents"]["contents"]) == 5
        assert [m["type"] for m in kw["messages"][1:]] == ["image"] * 3
        assert img.call_count == 3
        assert kw["priority"] == "critical"
        assert kw["log_plots"] == [(i, "critical" if i == 2 else "warning") for i in range(1, 6)]

    async def test_single_plot_keeps_single_card(self):
        from scheduler import _send_grouped_rain_alert
        pending = [{"flex": {"type": "flex", "contents": {"type": "bubble"}}, "msg": "m",
                    "plot_id": 1, "level": "warning", "polygon": None, "name": "P1"}]
        with patch("scheduler.send_line_message", new_callable=AsyncMock) as send:
            await _send_grouped_rain_alert("U1", pending)
        msgs = send.call_args.kwargs["messages"]
        assert msgs == [pending[0]["flex"]]
        assert send.call_args.kwargs["priority"] == "normal"

    async def test_log_plots_writes_one_row_per_plot(self):
        import line_sender
        client = _mock_client(post=AsyncMock(return_value=_resp(200)))
        with patch("line_sender.httpx.AsyncClient", return_value=client), \
             patch("database.log_line_message", new_callable=AsyncMock) as log:
            await line_sender.send_line_message("U1", "x", kind="rain_alert",
                                                log_plots=[(1, "warning"), (2, "critical")])
        assert client.post.call_count == 1
        assert [(c.kwargs["plot_id"], c.kwargs["level"]) for c in log.call_args_list] == \
               [(1, "warning"), (2, "critical")]


class TestQuotaReset:
    def test_reset_date(self):
        from line_quota import quota_reset_date
        bkk = timezone(timedelta(hours=7))
        assert quota_reset_date(datetime(2026, 10, 17, tzinfo=bkk)) == "2026-11-01"
        assert quota_reset_date(datetime(2026, 12, 31, 23, tzinfo=bkk)) == "2027-01-01"

    def test_exhausted_reason_has_reset_date(self):
        import line_quota
        _set_quota(300, 300)
        h = line_quota.health()
        assert any("รีเซ็ต" in r for r in h["reasons"]) and h["quota_resets_on"]


@pytest.mark.asyncio
class TestHealthTracking:
    async def _status(self, client, stored):
        with patch("line_quota.refresh", new_callable=AsyncMock), \
             patch("main.get_recent_message_failures", new_callable=AsyncMock, return_value=[]), \
             patch("main.get_scheduler_last_run", new_callable=AsyncMock, return_value=stored), \
             patch("main.set_scheduler_last_run", new_callable=AsyncMock) as save:
            resp = await client.get("/admin/messaging/status?track=true", headers=ADMIN_HEADERS)
        return resp.json(), save

    async def test_notifies_once_when_problem_starts(self, client):
        _set_quota(300, 300)
        body, save = await self._status(client, {"last_run_hour": 0})        # เดิม ok
        assert body["changed"] is True and body["health"] == "critical"
        save.assert_called_once()
        assert save.call_args.args[2] == 2

    async def test_no_notify_while_problem_continues(self, client):
        _set_quota(300, 300)
        body, save = await self._status(client, {"last_run_hour": 2})        # เดิม critical
        assert body["changed"] is False
        save.assert_not_called()

    async def test_notifies_on_recovery(self, client):
        _set_quota(300, 100)
        body, save = await self._status(client, {"last_run_hour": 2})
        assert body["changed"] is True and body["health"] == "ok"
        assert save.call_args.args[2] == 0
