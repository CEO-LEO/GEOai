"""
test_validation_2d.py — Phase 2d: ยืนยันภาคสนาม จริง / ไม่จริง / ไม่แน่ใจ
"""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from conftest import ADMIN_HEADERS, auth_as, fake_verify_line_token

if "ee" not in sys.modules or not isinstance(sys.modules["ee"], MagicMock):
    _ee = MagicMock()
    _ee.EEException = Exception
    sys.modules["ee"] = _ee

import access
import validation


def _rows(problem, answers, role="farmer"):
    return [{"problem": problem, "answer": a, "role": role, "plot_id": i % 3} for i, a in enumerate(answers)]


class TestSummary:
    def test_small_sample_no_rate(self):
        s = validation.summarize(_rows("wet_spot", ["true", "true", "false", "unsure"]))
        b = s["by_problem"][0]
        assert (b["true"], b["false"], b["unsure"], b["decided"]) == (2, 1, 1, 3)
        assert b["match_rate"] is None and "ตัวอย่างยังน้อย" in b["text_th"]
        assert s["total"] == 4 and s["plots"] == 3

    def test_rate_when_enough(self):
        b = validation.summarize(_rows("dry_spot", ["true"] * 8 + ["false"] * 2, role="officer"))["by_problem"][0]
        assert b["match_rate"] == 0.8 and b["officer_true"] == 8
        assert b["text_th"] == "ตรงกับในแปลง 8 ใน 10 ครั้งที่ตอบชัด"


@pytest_asyncio.fixture
async def client():
    from middleware import reset_limits
    reset_limits()
    access.invalidate()
    with patch("scheduler.create_scheduler", return_value=MagicMock()), \
         patch("auth.verify_line_token", side_effect=fake_verify_line_token):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac


PLOT = {"id": 5, "user_id": "U1", "area_code": "7004", "created_by": "U1"}


@pytest.mark.asyncio
class TestValidationApi:
    async def test_point_validation(self, client):
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.insert_validation", new_callable=AsyncMock, return_value={"id": 1}) as ins, \
             patch("main.log_event", new_callable=AsyncMock):
            resp = await client.post("/plots/U1/5/validations", headers=auth_as("U1"), json={
                "scope": "point", "problem": "wet_spot", "severity": "medium", "answer": "true",
                "lat": 13.5, "lng": 99.9, "context": {"rel": 0.31, "big": "x" * 500, "nested": {"a": 1}}})
        assert resp.status_code == 200 and resp.json()["answer_th"] == "จริง"
        row = ins.await_args.args[0]
        assert row["role"] == "farmer" and row["scope"] == "point" and row["lat"] == 13.5
        assert len(row["context"]["big"]) == 60 and isinstance(row["context"]["nested"], str)

    async def test_point_needs_coordinates(self, client):
        resp = await client.post("/plots/U1/5/validations", headers=auth_as("U1"),
                                 json={"scope": "point", "problem": "wet_spot", "answer": "true"})
        assert resp.status_code == 422

    async def test_unknown_problem(self, client):
        resp = await client.post("/plots/U1/5/validations", headers=auth_as("U1"),
                                 json={"scope": "plot", "problem": "ufo", "answer": "true"})
        assert resp.status_code == 422

    async def test_other_users_plot_404(self, client):
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value={**PLOT, "user_id": "U2", "created_by": "U2"}), \
             patch("main.insert_validation", new_callable=AsyncMock) as ins:
            resp = await client.post("/plots/U1/5/validations", headers=auth_as("U1"),
                                     json={"scope": "plot", "problem": "wetter_than_usual", "answer": "false"})
        assert resp.status_code == 404
        ins.assert_not_called()

    async def test_officer_records_role(self, client):
        with patch("main.get_user_access", new_callable=AsyncMock,
                   return_value={"role": "officer", "areas": ["7004"], "share_with_officers": False}), \
             patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.get_share_flags", new_callable=AsyncMock, return_value={"U1": True}), \
             patch("main.log_audit", new_callable=AsyncMock), \
             patch("main.insert_validation", new_callable=AsyncMock, return_value={"id": 2}) as ins, \
             patch("main.log_event", new_callable=AsyncMock):
            resp = await client.post("/plots/Uoff/5/validations", headers=auth_as("Uoff"),
                                     json={"scope": "plot", "problem": "wetter_than_usual", "answer": "unsure"})
        assert resp.status_code == 200 and ins.await_args.args[0]["role"] == "officer"

    async def test_list(self, client):
        rows = [{"plot_id": 5, "user_id": "U1", "role": "farmer", "scope": "plot", "problem": "dry_spot",
                 "answer": "true", "created_at": "2026-10-25T00:00:00Z"}]
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.list_validations", new_callable=AsyncMock, return_value=rows):
            body = (await client.get("/plots/U1/5/validations", headers=auth_as("U1"))).json()
        assert body["summary"]["total"] == 1 and body["items"][0]["by_me"] is True
        assert "user_id" not in body["items"][0]

    async def test_admin_summary_and_export(self, client):
        rows = [{"id": 1, "plot_id": 5, "user_id": "U1", "role": "officer", "scope": "point", "problem": "wet_spot",
                 "answer": "false", "lat": 13.5, "lng": 99.9, "created_at": "2026-10-25T00:00:00Z"}]
        with patch("main.list_validations", new_callable=AsyncMock, return_value=rows), \
             patch("main.log_audit", new_callable=AsyncMock):
            s = (await client.get("/admin/validations", headers=ADMIN_HEADERS)).json()
            csv_resp = await client.get("/admin/validations/export.csv", headers=ADMIN_HEADERS)
        assert s["by_problem"][0]["officer_false"] == 1
        assert csv_resp.status_code == 200 and "U1" not in csv_resp.text and "13.5" in csv_resp.text

    async def test_admin_needs_key(self, client):
        assert (await client.get("/admin/validations")).status_code == 403


class TestAccuracy:
    """ความแม่นยำจากเกษตรกรอาสา (แผนทุน TED Fund) — precision/recall + ช่วงความเชื่อมั่น"""

    def _row(self, problem, answer, **kw):
        return {"scope": "point", "problem": problem, "answer": answer, "user_id": kw.pop("user_id", "U1"),
                "role": "farmer", "plot_id": 1, **kw}

    def test_wilson_interval_widens_with_small_n(self):
        import validation
        lo, hi = validation.wilson(8, 10)
        assert 0.4 < lo < 0.5 and 0.9 < hi < 1.0
        lo2, hi2 = validation.wilson(80, 100)
        assert (hi2 - lo2) < (hi - lo)
        assert validation.wilson(0, 0) is None

    def test_precision_recall_and_hidden_rate_when_n_small(self):
        import validation
        rows = ([self._row("wet_spot", "true")] * 7 + [self._row("water_collect", "false")] * 3
                + [self._row("water_check_unflagged", "true", selection="random")] * 2
                + [self._row("water_check_unflagged", "false", selection="random")] * 8)
        a = validation.accuracy(rows)
        assert a["counts"] == {"tp": 7, "fp": 3, "fn": 2, "tn": 8}
        assert a["water_precision"]["rate"] == 0.7 and a["water_precision"]["n"] == 10
        assert a["water_recall"]["k"] == 7 and a["water_recall"]["n"] == 9
        assert a["water_recall"]["rate"] is None and a["water_recall"]["ci95"] is None     # n < 10 → ไม่โชว์เปอร์เซ็นต์

    def test_unsure_excluded_and_symptom_split_by_flag(self):
        import validation
        rows = [self._row("tree_symptom", "true", context={"flag": "wet_spot"}),
                self._row("tree_symptom", "false", context={"flag": "none"}, selection="random"),
                self._row("tree_symptom", "unsure", context={"flag": "none"}, selection="random"),
                self._row("wet_spot", "unsure", photo_client_id="p1")]
        a = validation.accuracy(rows)
        assert (a["symptom_in_flagged_wet"]["k"], a["symptom_in_flagged_wet"]["n"]) == (1, 1)
        assert (a["symptom_in_not_flagged"]["k"], a["symptom_in_not_flagged"]["n"]) == (0, 1)
        assert a["respondents"] == 1 and a["answers_with_photo"] == 1

    @pytest.mark.asyncio
    async def test_volunteer_questions_need_volunteer_consent(self, client):
        body = {"scope": "point", "problem": "tree_symptom", "answer": "true", "lat": 13.5, "lng": 99.9}
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT),              patch("main.get_user_privacy", new_callable=AsyncMock, return_value={"consent_volunteer_test": False}),              patch("main.insert_validation", new_callable=AsyncMock, return_value={"id": 1}) as ins:
            r = await client.post("/plots/U1/5/validations", headers=auth_as("U1"), json=body)
        assert r.status_code == 403
        ins.assert_not_called()


    def test_recall_ignores_user_chosen_points(self):
        import validation
        rows = [self._row("wet_spot", "true")] * 5 + [
            self._row("water_check_unflagged", "true"),                       # อาสาเลือกเอง → ไม่นับ
            self._row("water_check_unflagged", "true", selection="user"),
            self._row("water_check_unflagged", "false", selection="random")]
        a = validation.accuracy(rows)
        assert a["counts"] == {"tp": 5, "fp": 0, "fn": 0, "tn": 1}
        assert a["user_chosen_unflagged_excluded"] == 2

    def test_symptom_not_flagged_group_random_only(self):
        import validation
        rows = [self._row("tree_symptom", "true", context={"flag": "none"}),             # เลือกเอง → ไม่เข้ากลุ่มเทียบ
                self._row("tree_symptom", "true", context={"flag": "dry_spot"}, selection="random")]
        a = validation.accuracy(rows)
        assert (a["symptom_in_not_flagged"]["k"], a["symptom_in_not_flagged"]["n"]) == (1, 1)


import validation_sampling as vs


def _grid_cands(rows=20, cols=20):
    return vs.normalize_candidates([{"lat": 13.5 + r * 0.0001, "lng": 99.8 + c * 0.0001, "flag": "none"}
                                    for r in range(rows) for c in range(cols)])


class TestSampling:
    def test_same_seed_same_points_and_order_independent(self):
        pts = [{"lat": 13.5 + r * 0.0001, "lng": 99.8 + c * 0.0001} for r in range(10) for c in range(10)]
        a = vs.draw(vs.normalize_candidates(pts), seed=42)
        b = vs.draw(vs.normalize_candidates(list(reversed(pts))), seed=42)
        assert a == b and len(a) == 5
        assert a != vs.draw(vs.normalize_candidates(pts), seed=43)

    def test_stratified_covers_whole_plot(self):
        cands = _grid_cands()
        for seed in range(30):
            pts = vs.draw(cands, seed, 5)
            assert [p["stratum"] for p in pts] == [1, 2, 3, 4, 5]
            assert len({(p["lat"], p["lng"]) for p in pts}) == 5
            assert max(p["lat"] for p in pts) - min(p["lat"] for p in pts) > 0.0005   # ครอบคลุมทิศเหนือ-ใต้ของแปลง

    def test_hash_stable_and_small_n_clamped(self):
        c = _grid_cands(3, 3)
        assert vs.candidates_hash(c) == vs.candidates_hash(list(c))
        assert len(vs.draw(c, 1, 50)) == len(c)


POLY = [[99.8, 13.5], [99.802, 13.5], [99.802, 13.502], [99.8, 13.502], [99.8, 13.5]]
SAMPLE_PLOT = {**PLOT, "polygon": POLY, "plot_type": "flat"}


def _cand_body(n=6):
    return {"points": [{"lat": 13.5 + 0.0003 * i, "lng": 99.8 + 0.0003 * i} for i in range(n)]}


@pytest.mark.asyncio
class TestSampleApi:
    async def test_draw_records_seed_and_reuses(self, client):
        saved = {}

        async def _insert(row):
            saved.update(row)
            return {**row, "id": 9, "created_at": "2026-11-05T00:00:00Z"}
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=SAMPLE_PLOT), \
             patch("main.get_user_privacy", new_callable=AsyncMock, return_value={"consent_volunteer_test": True}), \
             patch("main.latest_validation_sample", new_callable=AsyncMock, return_value=None), \
             patch("main.insert_validation_sample", side_effect=_insert), \
             patch("main.list_validations", new_callable=AsyncMock, return_value=[]):
            r = await client.post("/plots/U1/5/validation-sample", headers=auth_as("U1"), json=_cand_body())
        assert r.status_code == 200
        j = r.json()
        assert j["sample_id"] == 9 and j["reused"] is False and len(j["points"]) == 5
        assert saved["seed"] == j["seed"] and saved["method"] == vs.METHOD and saved["candidates_n"] == 6
        assert len(saved["candidates"]) == 6                       # เก็บจุดตั้งต้นทั้งหมดไว้ตรวจย้อน
        again = {**saved, "id": 9, "created_at": "x"}
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=SAMPLE_PLOT), \
             patch("main.get_user_privacy", new_callable=AsyncMock, return_value={"consent_volunteer_test": True}), \
             patch("main.latest_validation_sample", new_callable=AsyncMock, return_value=again), \
             patch("main.insert_validation_sample", new_callable=AsyncMock) as ins, \
             patch("main.list_validations", new_callable=AsyncMock, return_value=[]):
            r2 = await client.post("/plots/U1/5/validation-sample", headers=auth_as("U1"), json=_cand_body())
        assert r2.json()["reused"] is True and r2.json()["points"] == j["points"]
        ins.assert_not_called()

    async def test_requires_volunteer_consent_and_rejects_raised_bed_and_outside(self, client):
        base = dict(headers=auth_as("U1"), json=_cand_body())
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=SAMPLE_PLOT), \
             patch("main.get_user_privacy", new_callable=AsyncMock, return_value={"consent_volunteer_test": False}):
            assert (await client.post("/plots/U1/5/validation-sample", **base)).status_code == 403
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value={**SAMPLE_PLOT, "plot_type": "raised_bed"}), \
             patch("main.get_user_privacy", new_callable=AsyncMock, return_value={"consent_volunteer_test": True}):
            assert (await client.post("/plots/U1/5/validation-sample", **base)).status_code == 422
        far = {"points": [{"lat": 14.5, "lng": 99.8 + 0.0003 * i} for i in range(6)]}
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=SAMPLE_PLOT), \
             patch("main.get_user_privacy", new_callable=AsyncMock, return_value={"consent_volunteer_test": True}):
            assert (await client.post("/plots/U1/5/validation-sample", headers=auth_as("U1"), json=far)).status_code == 422

    async def test_answer_marked_random_only_for_drawn_point(self, client):
        sample = {"id": 9, "plot_id": 5, "user_id": "U1", "points": [{"lat": 13.5003, "lng": 99.8003, "stratum": 1}]}
        body = {"scope": "point", "problem": "water_check_unflagged", "answer": "true", "lat": 13.5003, "lng": 99.8003,
                "sample_id": 9}
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.get_user_privacy", new_callable=AsyncMock, return_value={"consent_volunteer_test": True}), \
             patch("main.get_validation_sample", new_callable=AsyncMock, return_value=sample), \
             patch("main.insert_validation", new_callable=AsyncMock, return_value={"id": 1}) as ins, \
             patch("main.log_event", new_callable=AsyncMock):
            ok = await client.post("/plots/U1/5/validations", headers=auth_as("U1"), json=body)
            bad = await client.post("/plots/U1/5/validations", headers=auth_as("U1"),
                                    json={**body, "lat": 13.6, "lng": 99.9})
        assert ok.status_code == 200 and ins.await_args.args[0]["selection"] == "random" and ins.await_args.args[0]["sample_id"] == 9
        assert bad.status_code == 422
        # ไม่ส่ง sample_id = จุดที่ผู้ใช้เลือกเอง
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.get_user_privacy", new_callable=AsyncMock, return_value={"consent_volunteer_test": True}), \
             patch("main.insert_validation", new_callable=AsyncMock, return_value={"id": 2}) as ins2, \
             patch("main.log_event", new_callable=AsyncMock):
            await client.post("/plots/U1/5/validations", headers=auth_as("U1"), json={**body, "sample_id": None})
        assert ins2.await_args.args[0]["selection"] == "user"

    async def test_admin_summary_includes_sampling(self, client):
        samples = [{"id": 1, "plot_id": 5, "seed": 123, "method": vs.METHOD, "points": [{"lat": 1, "lng": 2}] * 5}]
        rows = [{"id": 1, "plot_id": 5, "user_id": "U1", "role": "farmer", "scope": "point", "problem": "water_check_unflagged",
                 "answer": "false", "lat": 13.5, "lng": 99.9, "selection": "random", "sample_id": 1, "created_at": "x"}]
        with patch("main.list_validations", new_callable=AsyncMock, return_value=rows), \
             patch("main.list_validation_samples", new_callable=AsyncMock, return_value=samples):
            s = (await client.get("/admin/validations", headers=ADMIN_HEADERS)).json()
        assert s["sampling"]["samples"] == 1 and s["sampling"]["points_drawn"] == 5 and s["sampling"]["seeds_recorded"]
        assert s["sampling"]["points_answered"] == 1 and "งูไล่" in s["sampling"]["method_th"]
