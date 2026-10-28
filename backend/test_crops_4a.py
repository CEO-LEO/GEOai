"""
test_crops_4a.py — Phase 4a: จำแนกชนิดพืช (label, โมเดล, ประเมินแบบ spatial, นำเข้าแปลงอ้างอิง, API ทาย/ยืนยัน)
"""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from conftest import ADMIN_HEADERS, auth_as, fake_verify_line_token

if "ee" not in sys.modules or not isinstance(sys.modules["ee"], MagicMock):
    _ee = MagicMock()
    _ee.EEException = Exception
    sys.modules["ee"] = _ee

import crops as crops_mod
from crop_classifier import evaluate, features, labels, model, references, sources

IDX = crops_mod.by_id()
NF = len(sources.feature_names())


def _synthetic(n_per_class=20, classes=("pomelo", "guava", "coconut_aromatic"), groups=5, seed=0):
    rng = np.random.default_rng(seed)
    X, y, g = [], [], []
    for k, c in enumerate(classes):
        center = np.zeros(NF)
        center[k * 3:(k + 1) * 3] = 3.0
        for i in range(n_per_class):
            X.append((center + rng.normal(0, 0.6, NF)).tolist())
            y.append(c)
            g.append(f"d70{i % groups:02d}")
    return X, y, g


class TestLabels:
    def test_crop_class(self):
        assert labels.crop_class(["pomelo"], IDX) == "pomelo"
        assert labels.crop_class(["pomelo", "guava"], IDX) == labels.MIXED
        assert labels.crop_class(["vegetables", "guava"], IDX) == "guava"
        assert labels.crop_class(["xyz"], IDX) is None
        assert labels.crop_class([], IDX) is None

    def test_officer_overrides_owner(self):
        fb = [{"role": "farmer", "answer": "yes", "crop_ids": ["guava"], "created_at": "2026-10-01"},
              {"role": "officer", "answer": "other", "crop_ids": ["pomelo"], "created_at": "2026-10-02"}]
        lab = labels.plot_label({}, ["guava"], fb, False, IDX)
        assert lab == {"cls": "pomelo", "weight": labels.W_OFFICER, "basis": "officer"}

    def test_officer_says_no_drops_plot(self):
        fb = [{"role": "officer", "answer": "no", "crop_ids": [], "created_at": "2026-10-02"}]
        assert labels.plot_label({}, ["guava"], fb, True, IDX) is None

    def test_owner_selected_with_photo_bonus(self):
        lab = labels.plot_label({}, ["rose_apple"], [], True, IDX)
        assert lab["cls"] == "rose_apple" and lab["weight"] == labels.W_OWNER + labels.W_PHOTO_BONUS
        assert lab["basis"] == "owner_selected"

    def test_no_crops_no_label(self):
        assert labels.plot_label({}, [], [], False, IDX) is None

    def test_fold_rare_needs_plots_and_districts(self):
        rows = ([{"cls": "pomelo", "group": f"d{i % 3}"} for i in range(12)]
                + [{"cls": "guava", "group": "d1"} for _ in range(15)]          # อำเภอเดียว → อื่น ๆ
                + [{"cls": "grape", "group": f"d{i}"} for i in range(4)])      # น้อยเกิน → อื่น ๆ
        out, summary = labels.fold_rare(rows)
        assert {r["cls"] for r in out} == {"pomelo", labels.OTHER}
        assert summary["guava"] == {"plots": 15, "districts": 1, "kept": False}

    def test_spatial_group(self):
        assert labels.spatial_group("7004", 13.5, 99.8) == "d7004"
        assert labels.spatial_group(None, 13.52, 99.81) == labels.spatial_group(None, 13.53, 99.82)


class TestModel:
    def test_fit_predict_and_json_roundtrip(self):
        import json
        X, y, _ = _synthetic()
        params = json.loads(json.dumps(model.fit_logreg(X, y)))
        p = model.predict_proba(params, X)
        assert np.allclose(p.sum(axis=1), 1.0)
        acc = np.mean([params["classes"][int(i)] == t for i, t in zip(p.argmax(axis=1), y)])
        assert acc > 0.9

    def test_missing_values_imputed(self):
        X, y, _ = _synthetic()
        X[0][5] = None
        params = model.fit_logreg(X, y)
        row = [None] * NF
        assert np.isfinite(model.predict_proba(params, [row])).all()

    def test_binary_case_gives_two_columns(self):
        X, y, _ = _synthetic(classes=("pomelo", "guava"))
        params = model.fit_logreg(X, y)
        assert model.predict_proba(params, X[:3]).shape == (3, 2)

    def test_decide(self):
        cls = ["pomelo", "guava", "vegetables"]
        assert model.decide(cls, [0.8, 0.15, 0.05], 0.9, IDX)["status"] == "ok"
        assert model.decide(cls, [0.45, 0.4, 0.15], 0.9, IDX)["status"] == "uncertain"
        assert model.decide(cls, [0.8, 0.15, 0.05], 0.1, IDX)["status"] == "not_orchard"
        assert model.decide(cls, [0.05, 0.15, 0.8], 0.1, IDX)["status"] == "ok"        # ผัก ต้นไม้น้อยได้
        d = model.decide(cls, [0.8, 0.15, 0.05], None, IDX)
        assert [t["crop_id"] for t in d["top"]] == cls and d["confidence"] == 0.8


class TestEvaluate:
    def test_spatial_cv_and_gate(self):
        X, y, g = _synthetic(n_per_class=20, groups=5)
        m = evaluate.spatial_cv(X, y, [1.0] * len(y), g)
        assert m["status"] == "ok" and m["groups"] == 5 and m["folds"] == 5
        assert m["logreg"]["macro_f1"] > 0.8 and "random_forest" in m
        assert sum(v["n"] for v in m["logreg"]["per_group"].values()) == 60
        ok, why = evaluate.passes_gate(m)
        assert ok, why

    def test_insufficient_single_group(self):
        X, y, _ = _synthetic(n_per_class=5)
        m = evaluate.spatial_cv(X, y, [1.0] * len(y), ["d1"] * len(y))
        assert m["status"] == "insufficient"
        assert evaluate.passes_gate(m)[0] is False

    def test_gate_rejects_small_dataset(self):
        X, y, g = _synthetic(n_per_class=6, groups=3)
        ok, why = evaluate.passes_gate(evaluate.spatial_cv(X, y, [1.0] * len(y), g, with_rf=False))
        assert not ok and any("≥ 50" in w for w in why)

    def test_report_hides_grid_cells(self):
        X, y, g = _synthetic(n_per_class=10, groups=3)
        g = [x if i % 2 else "g270_1997" for i, x in enumerate(g)]
        m = evaluate.spatial_cv(X, y, [1.0] * len(y), g, with_rf=False)
        md = evaluate.report_markdown("v1", m, {"sha256": "abc"}, {"7000": "เมืองราชบุรี"})
        assert "g270_1997" not in md and "นอกอำเภอที่รู้จัก (รวม)" in md and "เมืองราชบุรี" in md


class TestFeatures:
    def test_vector_order_and_missing(self):
        raw = {"emb": {f"A{i:02d}": 0.1 for i in range(64)},
               "s2": {f"ndvi_m{m:02d}": 0.5 + m / 100 for m in range(1, 13)},
               "s1": {"vv_m01": -8.0},                                # เดือนเดียว → สรุปไม่ได้
               "dw": {"dw_trees": 0.8, "dw_crops": 0.1, "dw_built": float("nan")}}
        v = features.to_vector(raw)
        names = sources.feature_names()
        assert len(v) == len(names) == NF
        assert v[names.index("ndvi_max")] == pytest.approx(0.62)
        assert v[names.index("vv_mean")] is None and v[names.index("dw_built")] is None

    def test_geom_hash_changes_with_boundary(self):
        a = features.geom_hash(13.5, 99.8, [[99.8, 13.5], [99.81, 13.5], [99.81, 13.51]])
        b = features.geom_hash(13.5, 99.8, [[99.8, 13.5], [99.81, 13.5], [99.81, 13.52]])
        assert a != b and features.geom_hash(13.5, 99.8, None) == features.geom_hash(13.5, 99.8, [])


class TestReferences:
    def test_parse(self):
        text = ("﻿ref,crop,lat,lng,district\n"
                "A1,ส้มโอ,13.52,99.95,7004\n"
                "A2,pomelo|guava,13.6,99.8,ดำเนินสะดวก\n"
                "A3,ทุเรียนเทศ,13.6,99.8,\n"
                "A4,ฝรั่ง,99.8,13.6,\n"
                "A1,ฝรั่ง,13.6,99.8,\n")
        rows, errors = references.parse_csv(text, "farmer_registry_test", "DPA test", "admin")
        assert [r["source_ref"] for r in rows] == ["A1", "A2"]
        assert rows[0]["crop_ids"] == ["pomelo"] and rows[1]["crop_ids"] == ["pomelo", "guava"]
        assert rows[0]["consent_basis"] == "DPA test"
        assert len(errors) == 3

    def test_missing_columns(self):
        rows, errors = references.parse_csv("a,b\n1,2\n", "s_x", "basis", "admin")
        assert rows == [] and "ref, crop, lat, lng" in errors[0]


@pytest_asyncio.fixture
async def client():
    from middleware import reset_limits
    reset_limits()
    with patch("scheduler.create_scheduler", return_value=MagicMock()), \
         patch("auth.verify_line_token", side_effect=fake_verify_line_token):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac


PLOT = {"id": 5, "user_id": "U1", "lat": 13.5, "lng": 99.8, "polygon": None}


@pytest.mark.asyncio
class TestCropApi:
    async def test_guess_collecting_when_disabled(self, client, monkeypatch):
        monkeypatch.delenv("CROP_GUESS_ENABLED", raising=False)
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.get_plot_crop_ids", new_callable=AsyncMock, return_value=["pomelo"]), \
             patch("main.get_user_privacy", new_callable=AsyncMock, return_value={"consent_photos_research": True}):
            r = await client.get("/plots/U1/5/crop-guess", headers=auth_as("U1"))
        assert r.status_code == 200
        j = r.json()
        assert j["status"] == "collecting" and j["current_names_th"] == ["ส้มโอ"] and j["owner_helps_training"] is True

    async def test_guess_with_active_model(self, client, monkeypatch):
        monkeypatch.setenv("CROP_GUESS_ENABLED", "1")
        X, y, _ = _synthetic()
        params = model.fit_logreg(X, y)
        m = {"version": "crop-t", "passes_gate": True, "params": params}
        feat = {"vals": X[0], "meta": {"tree_fraction": 0.9}, "geom_hash": "x"}
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.get_plot_crop_ids", new_callable=AsyncMock, return_value=[]), \
             patch("main.get_user_privacy", new_callable=AsyncMock, return_value={}), \
             patch("crop_classifier.store.get_active_model", new_callable=AsyncMock, return_value=m), \
             patch("crop_classifier.store.latest_prediction", new_callable=AsyncMock, return_value=None), \
             patch("crop_classifier.service.ensure_features", new_callable=AsyncMock, return_value=feat), \
             patch("crop_classifier.store.insert_prediction", new_callable=AsyncMock,
                   side_effect=lambda row: {**row, "id": 77}):
            j = (await client.get("/plots/U1/5/crop-guess", headers=auth_as("U1"))).json()
        assert j["status"] == "ok" and j["top"][0]["crop_id"] == "pomelo" and j["top"][0]["name_th"] == "ส้มโอ"
        assert j["prediction_id"] == 77 and "CC BY 4.0" in j["attribution_th"]

    async def test_guess_not_shown_for_model_failing_gate(self, client, monkeypatch):
        monkeypatch.setenv("CROP_GUESS_ENABLED", "1")
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.get_plot_crop_ids", new_callable=AsyncMock, return_value=[]), \
             patch("main.get_user_privacy", new_callable=AsyncMock, return_value={}), \
             patch("crop_classifier.store.get_active_model", new_callable=AsyncMock,
                   return_value={"version": "v", "passes_gate": False}):
            j = (await client.get("/plots/U1/5/crop-guess", headers=auth_as("U1"))).json()
        assert j["status"] == "collecting"

    async def test_feedback_other_sets_crops(self, client):
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.get_plot_crop_ids", new_callable=AsyncMock, return_value=[]), \
             patch("main.set_plot_crops", new_callable=AsyncMock, return_value=True) as sc, \
             patch("main.crop_store.insert_feedback", new_callable=AsyncMock, return_value={"id": 9}) as ins, \
             patch("main.log_event", new_callable=AsyncMock):
            r = await client.post("/plots/U1/5/crop-feedback", headers=auth_as("U1"),
                                  json={"answer": "other", "crop_ids": ["guava"]})
        assert r.status_code == 200 and r.json()["names_th"] == ["ฝรั่ง"]
        sc.assert_awaited_once_with(5, ["guava"], source="user")
        assert ins.await_args.args[0]["role"] == "farmer" and ins.await_args.args[0]["answer"] == "other"

    async def test_feedback_yes_needs_crops(self, client):
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT):
            r = await client.post("/plots/U1/5/crop-feedback", headers=auth_as("U1"), json={"answer": "yes"})
        assert r.status_code == 422

    async def test_feedback_no_does_not_touch_crops(self, client):
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value=PLOT), \
             patch("main.set_plot_crops", new_callable=AsyncMock) as sc, \
             patch("main.crop_store.insert_feedback", new_callable=AsyncMock, return_value={"id": 3}), \
             patch("main.log_event", new_callable=AsyncMock):
            r = await client.post("/plots/U1/5/crop-feedback", headers=auth_as("U1"),
                                  json={"answer": "no", "prediction_id": 4})
        assert r.status_code == 200 and sc.await_count == 0

    async def test_other_users_plot_hidden(self, client):
        with patch("main.get_plot_by_id", new_callable=AsyncMock, return_value={**PLOT, "user_id": "U2"}), \
             patch("main.get_user_access", new_callable=AsyncMock, return_value={"role": "farmer", "areas": []}):
            assert (await client.get("/plots/U1/5/crop-guess", headers=auth_as("U1"))).status_code == 404
            r = await client.post("/plots/U1/5/crop-feedback", headers=auth_as("U1"),
                                  json={"answer": "other", "crop_ids": ["guava"]})
            assert r.status_code == 404

    async def test_admin_requires_key_and_train_insufficient(self, client):
        assert (await client.get("/admin/crop-model")).status_code in (401, 403)
        ds = {"n": 0, "feature_set": "emb_phen_v1"}
        with patch("crop_classifier.service.build_dataset", new_callable=AsyncMock, return_value=([], ds)), \
             patch("main.log_audit", new_callable=AsyncMock):
            r = await client.post("/admin/crop-model/train", headers=ADMIN_HEADERS)
        assert r.status_code == 200 and r.json()["status"] == "insufficient"

    async def test_admin_activate_rejects_failing_model(self, client):
        with patch("crop_classifier.store.get_model", new_callable=AsyncMock,
                   return_value={"version": "v", "passes_gate": False}):
            r = await client.post("/admin/crop-model/activate?version=v", headers=ADMIN_HEADERS)
        assert r.status_code == 409

    async def test_admin_import_references(self, client):
        with patch("main.crop_store.upsert_reference_labels", new_callable=AsyncMock, return_value=1), \
             patch("main.log_audit", new_callable=AsyncMock):
            r = await client.post("/admin/crop-model/references", headers=ADMIN_HEADERS,
                                  json={"source": "farmer_registry_test", "consent_basis": "DPA ทดสอบ",
                                        "csv": "ref,crop,lat,lng\nA1,ส้มโอ,13.5,99.9\nA2,xx,13.5,99.9\n"})
        assert r.status_code == 200 and r.json()["imported"] == 1 and r.json()["error_count"] == 1


OFFICER = {"role": "officer", "areas": ["7004"]}
SURVEY = {"client_id": "abc12345-x", "lat": 13.52, "lng": 99.95, "accuracy_m": 8, "crop_ids": ["pomelo"],
          "owner_permission": True}


@pytest.mark.asyncio
class TestCropSurvey:
    async def test_farmer_forbidden(self, client):
        with patch("main.get_user_access", new_callable=AsyncMock, return_value={"role": "farmer", "areas": []}):
            r = await client.post("/officer/crop-survey", headers=auth_as("USV1"), json=SURVEY)
        assert r.status_code == 403

    async def test_needs_permission_and_accuracy(self, client):
        with patch("main.get_user_access", new_callable=AsyncMock, return_value=OFFICER):
            r1 = await client.post("/officer/crop-survey", headers=auth_as("USV2"), json={**SURVEY, "owner_permission": False})
            r2 = await client.post("/officer/crop-survey", headers=auth_as("USV2"), json={**SURVEY, "accuracy_m": 80})
            r3 = await client.post("/officer/crop-survey", headers=auth_as("USV2"), json={**SURVEY, "lat": 40.0})
        assert r1.status_code == 422 and "อนุญาต" in r1.json()["detail"]
        assert r2.status_code == 422 and "GPS" in r2.json()["detail"]
        assert r3.status_code == 422

    async def test_saves_reference_label(self, client):
        with patch("main.get_user_access", new_callable=AsyncMock, return_value=OFFICER), \
             patch("main.areas.lookup_point", return_value={"area_code": "7004", "area_name": "อ.ดำเนินสะดวก จ.ราชบุรี"}), \
             patch("main.crop_store.upsert_survey", new_callable=AsyncMock, return_value={"id": 11}) as up, \
             patch("main.log_event", new_callable=AsyncMock):
            r = await client.post("/officer/crop-survey", headers=auth_as("USV3"), json=SURVEY)
        assert r.status_code == 200 and r.json()["names_th"] == ["ส้มโอ"]
        row = up.await_args.args[0]
        assert row["source"] == labels.SURVEY_SOURCE and row["source_ref"] == SURVEY["client_id"]
        assert row["area_code"] == "7004" and row["imported_by"] == "USV3" and row["consent_basis"]

    async def test_list_and_delete_own_only(self, client):
        with patch("main.get_user_access", new_callable=AsyncMock, return_value=OFFICER), \
             patch("main.crop_store.list_surveys", new_callable=AsyncMock,
                   return_value=[{"id": 1, "crop_ids": ["guava"], "area_code": "7004", "lat": 13.5, "lng": 99.9,
                                  "imported_at": "2026-10-28T03:00:00Z"}]), \
             patch("main.crop_store.count_surveys", new_callable=AsyncMock, return_value=1), \
             patch("main.crop_store.delete_survey", new_callable=AsyncMock, return_value=False) as de:
            j = (await client.get("/officer/crop-survey", headers=auth_as("USV4"))).json()
            r = await client.delete("/officer/crop-survey/99", headers=auth_as("USV4"))
        assert j["total"] == 1 and j["items"][0]["names_th"] == ["ฝรั่ง"]
        assert r.status_code == 404 and de.await_args.args == (99, "USV4")

    async def test_survey_weight_in_dataset(self):
        from crop_classifier import service, store
        inp = {"plots": [], "feedback": [], "geo_photo_plots": set(), "consented_users": set(),
               "references": [{"id": 1, "source": "officer_survey", "crop_ids": ["pomelo"], "lat": 13.5, "lng": 99.8,
                               "area_code": "7004"},
                              {"id": 2, "source": "farmer_registry_x", "crop_ids": ["guava"], "lat": 13.6, "lng": 99.8,
                               "area_code": None}]}
        feats = [{"plot_id": None, "ref_id": i, "geom_hash": "h", "vals": [0.1] * NF, "meta": {}} for i in (1, 2)]
        with patch("gee_analysis._gee_ready", True), \
             patch("crop_classifier.features.latest_embedding_year", return_value=2025), \
             patch.object(store, "training_inputs", new_callable=AsyncMock, return_value=inp), \
             patch.object(store, "list_features", new_callable=AsyncMock, return_value=feats):
            rows, ds = await service.build_dataset()
        w = {r["orig_cls"]: (r["weight"], r["basis"]) for r in rows}
        assert w["pomelo"] == (labels.W_SURVEY, "officer_survey") and w["guava"] == (labels.W_REFERENCE, "reference")
        assert ds["by_basis"] == {"officer_survey": 1, "reference": 1}