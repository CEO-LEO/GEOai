"""
test_recommendations.py — Phase 1d: ตารางคำแนะนำ พืช × ปัญหา × ความรุนแรง, หน้า admin, "แห้งกว่าปกติ"
"""

import re
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from conftest import ADMIN_HEADERS, auth_as, fake_verify_line_token

if "ee" not in sys.modules or not isinstance(sys.modules["ee"], MagicMock):
    _ee = MagicMock()
    _ee.EEException = Exception
    sys.modules["ee"] = _ee

import recommendations as rec
import risk_v2

RULES = list(rec.seed_rules())


@pytest_asyncio.fixture
async def client():
    from middleware import reset_limits
    reset_limits()
    rec.invalidate()
    with patch("scheduler.create_scheduler", return_value=MagicMock()), \
         patch("auth.verify_line_token", side_effect=fake_verify_line_token):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac


class TestSeed:
    def test_all_drafts(self):
        assert RULES and all(r["status"] == "draft" for r in RULES)

    def test_no_chemicals_or_doses(self):
        """ตัดชื่อสารเคมี ปริมาณปุ๋ย/ธาตุอาหาร และตัวเลขที่ยังไม่ได้ตรวจ ออกจากข้อความที่เกษตรกรเห็น"""
        banned = re.compile(r"Metalaxyl|Phosphonate|เมทาแลกซิล|ฟอสโฟเนต|\(N\)|\(P\)|\(K\)|\(Ca\)|\(Mg\)|กก\.|ซม\.|\d")
        for r in RULES:
            text = " ".join([r["title_th"], r["reason_th"], *r["actions_th"]])
            assert not banned.search(text), r["key"]

    def test_every_point_problem_has_generic_rule(self):
        for problem, sev in rec.POINT_PROBLEMS:
            assert rec.recommend(RULES, [], None, problem, sev), (problem, sev)

    def test_dry_than_usual_rules_exist(self):
        assert rec.recommend(RULES, ["guava"], None, "drier_than_usual", "medium")["title"].startswith("แปลงนี้แห้งกว่าปกติ")


class TestEngine:
    def test_crop_without_specific_rule_uses_generic(self):
        r = rec.recommend(RULES, ["mango"], "flat", "dry_spot", "medium")
        assert r["rule_ids"] == ["dry_spot.medium.*"]
        assert r["status"] == "draft" and "ร่าง" in r["status_th"]

    def test_vegetable_group_rule_preferred(self):
        r = rec.recommend(RULES, ["vegetables"], "vegetable_bed", "wet_spot", "medium")
        assert r["rule_ids"] == ["wet_spot.medium.group:vegetable"]

    def test_mixed_orchard_merges_and_dedupes(self):
        r = rec.recommend(RULES, ["mango", "vegetables"], "flat", "dry_spot", "medium")
        assert set(r["rule_ids"]) == {"dry_spot.medium.*", "dry_spot.medium.group:vegetable"}
        assert len(r["actions"]) == len(set(r["actions"]))
        assert r["rule_ids"][0] == "dry_spot.medium.group:vegetable"   # เฉพาะเจาะจงกว่าขึ้นก่อน

    def test_sensitive_crop_raises_wet_severity(self):
        """ทุเรียน waterlogging_sensitivity=high → ชื้น medium กลายเป็น high · สวนผสมใช้พืชที่อ่อนไหวที่สุด"""
        assert rec.recommend(RULES, ["durian"], None, "wet_spot", "medium")["severity"] == "high"
        assert rec.recommend(RULES, ["mango", "durian"], None, "wet_spot", "medium")["severity"] == "high"
        assert rec.recommend(RULES, ["mango"], None, "wet_spot", "medium")["severity"] == "medium"
        assert rec.recommend(RULES, ["durian"], None, "dry_spot", "medium")["severity"] == "medium"

    def test_raised_bed_skips_ditch_driven_problems(self):
        for p in ("wet_spot", "water_collect", "flow_through_rows"):
            assert rec.recommend(RULES, ["pomelo"], "raised_bed", p, "medium") is None
        assert rec.recommend(RULES, ["pomelo"], "raised_bed", "dry_spot", "medium")

    def test_missing_severity_falls_back_to_nearest(self):
        r = rec.recommend(RULES, [], None, "water_collect", "high")
        assert r and r["rule_ids"] == ["water_collect.medium.*"]

    def test_retired_rules_ignored(self):
        rules = [{**r, "status": "retired"} if r["key"] == "dry_spot.medium.*" else r for r in RULES]
        r = rec.recommend(rules, ["mango"], None, "dry_spot", "medium")
        assert r["rule_ids"] == ["dry_spot.high.*"]

    def test_reviewed_label(self):
        rules = [{**r, "status": "reviewed", "reviewed_by": "สนง.เกษตรจังหวัดราชบุรี"} for r in RULES]
        r = rec.recommend(rules, ["mango"], None, "dry_spot", "medium")
        assert r["status"] == "reviewed" and r["status_th"] == "ตรวจแล้วโดย สนง.เกษตรจังหวัดราชบุรี"

    def test_unknown_problem(self):
        assert rec.recommend(RULES, [], None, "aliens", "high") is None

    def test_no_problem_found(self):
        out = rec.for_plot(RULES, ["mango"], "flat", {"risk_v2": {"status": "ok", "z": 0.2}, "ndvi_change": 0.02})
        assert out["plot"] == [] and out["note"] is None
        assert "wet_spot" in out["points"]

    def test_point_only_plot_without_signals(self):
        out = rec.for_plot(RULES, [], None, None)
        assert out["plot"] == []

    def test_plot_problems(self):
        probs = rec.plot_problems({"risk_v2": {"status": "ok", "z": -2.5}, "ndvi_change": -0.15,
                                   "topsoil_risk_level": "high"})
        assert ("drier_than_usual", "high") in probs
        assert ("leaf_decline", "medium") in probs
        assert ("bare_soil", "high") in probs
        assert rec.plot_problems({"risk_v2": {"status": "high", "z": 2.4}}) == [("wetter_than_usual", "high")]

    def test_validate_rule(self):
        ok = rec.validate_rule({"crop": "durian", "problem": "wet_spot", "severity": "high",
                                "title_th": " t ", "actions_th": ["a", " ", "b"]})
        assert ok["title_th"] == "t" and ok["actions_th"] == ["a", "b"]
        for bad in ({"crop": "x"}, {"problem": "zzz"}, {"severity": "max"}, {"actions_th": []}):
            with pytest.raises(ValueError):
                rec.validate_rule({"crop": "*", "problem": "wet_spot", "severity": "high",
                                   "title_th": "t", "actions_th": ["a"], **bad})


class TestDrierThanUsual:
    def _series(self, cur_vv):
        today = datetime(2026, 10, 20, tzinfo=timezone.utc)
        s = []
        for y in (2023, 2024, 2025):
            for d in (-10, 0, 10):
                s.append((datetime(y, 10, 20, tzinfo=timezone.utc) + timedelta(days=d), -10.0 + (d / 10) * 0.3, 1))
        s += [(today - timedelta(days=2), cur_vv, 1), (today - timedelta(days=8), cur_vv, 1)]
        return s, today

    def test_dry_status(self):
        s, today = self._series(-12.0)
        r = risk_v2.compute_wetness_anomaly(s, today)
        assert r["status"] == "ok" and r["dry_status"] == "high"
        assert risk_v2.anomaly_label_th(r) == "แห้งกว่าปกติมาก"

    def test_labels(self):
        assert risk_v2.anomaly_label_th({"status": "medium", "z": 1.2}) == "ชื้นกว่าปกติ"
        assert risk_v2.anomaly_label_th({"status": "ok", "z": -1.3}) == "แห้งกว่าปกติ"
        assert risk_v2.anomaly_label_th({"status": "ok", "z": 0.1, "dry_status": "ok"}) == "ปกติ"
        assert risk_v2.anomaly_label_th({"status": "insufficient"}) is None


@pytest.mark.asyncio
class TestApi:
    async def test_plot_recommendations_requires_owner(self, client):
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value={"id": 5, "user_id": "U2"}):
            resp = await client.get("/recommendations/plot/U1/5", headers=auth_as("U1"))
        assert resp.status_code == 404

    async def test_plot_recommendations(self, client):
        with patch("main.get_plot_by_id", new_callable=AsyncMock,
                   return_value={"id": 5, "user_id": "U1", "plot_type": "raised_bed"}), \
             patch("main.get_plot_crop_ids", new_callable=AsyncMock, return_value=["pomelo"]), \
             patch("main.get_latest_plot_signals", new_callable=AsyncMock,
                   return_value={"risk_v2": {"status": "ok", "z": -1.4}, "ndvi_change": 0.0}), \
             patch("database.list_recommendation_rules", new_callable=AsyncMock, return_value=None):
            resp = await client.get("/recommendations/plot/U1/5", headers=auth_as("U1"))
        assert resp.status_code == 200
        body = resp.json()
        assert [r["problem"] for r in body["plot"]] == ["drier_than_usual"]
        assert "wet_spot" not in body["points"] and "dry_spot" in body["points"]   # สวนยกร่อง
        assert body["status_label_th"] == "แห้งกว่าปกติ"
        assert body["note"] == rec.DRAFT_LABEL_TH

    async def test_admin_requires_key(self, client):
        assert (await client.get("/admin/recommendations")).status_code == 403
        assert (await client.put("/admin/recommendations/1", json={})).status_code == 403

    async def test_admin_list_falls_back_to_seed(self, client):
        with patch("main.list_recommendation_rules", new_callable=AsyncMock, return_value=None):
            resp = await client.get("/admin/recommendations", headers=ADMIN_HEADERS)
        assert resp.status_code == 200 and resp.json()["source"] == "seed"

    async def test_edit_resets_to_draft(self, client):
        saved = {"id": 3, "status": "draft"}
        with patch("main.save_recommendation_rule", new_callable=AsyncMock, return_value=saved) as save:
            resp = await client.put("/admin/recommendations/3", headers=ADMIN_HEADERS, json={
                "crop": "*", "problem": "dry_spot", "severity": "medium", "title_th": "ใหม่",
                "actions_th": ["ให้น้ำ"], "editor": "ทดสอบ"})
        assert resp.status_code == 200
        fields = save.await_args.args[1]
        assert fields["status"] == "draft" and fields["reviewed_by"] is None
        assert save.await_args.args[2] == "edit"

    async def test_edit_rejects_invalid(self, client):
        with patch("main.save_recommendation_rule", new_callable=AsyncMock) as save:
            resp = await client.put("/admin/recommendations/3", headers=ADMIN_HEADERS, json={
                "crop": "*", "problem": "nope", "severity": "medium", "title_th": "x",
                "actions_th": ["a"], "editor": "ทดสอบ"})
        assert resp.status_code == 422
        save.assert_not_called()

    async def test_approve_records_reviewer(self, client):
        with patch("main.save_recommendation_rule", new_callable=AsyncMock, return_value={"id": 3}) as save:
            resp = await client.post("/admin/recommendations/3/status", headers=ADMIN_HEADERS,
                                     json={"status": "reviewed", "editor": "นายสมชาย"})
        assert resp.status_code == 200
        fields = save.await_args.args[1]
        assert fields["status"] == "reviewed" and fields["reviewed_by"] == "นายสมชาย"
        assert save.await_args.args[2] == "approve"

    async def test_export_csv(self, client):
        with patch("main.list_recommendation_rules", new_callable=AsyncMock, return_value=None):
            resp = await client.get("/admin/recommendations/export.csv", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.text.startswith("﻿รหัส") and "ร่าง" in resp.text
