"""
retrain_model.py — Gap 5: Retrain RandomForestRegressor ด้วยข้อมูลจริง

รัน: python retrain_model.py
ต้องการ env vars: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
บันทึกโมเดลใหม่เป็น durian_yield_model_v4.pkl
"""

import os
import sys
import json
import logging
import pickle
from datetime import datetime

import httpx

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _headers():
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    return {
        "apikey":        key,
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json",
    }


def fetch_training_data(supabase_url: str) -> list[dict]:
    """ดึง analyses JOIN field_observations สำหรับเทรน"""
    # ดึง field_observations ทั้งหมด
    resp = httpx.get(
        f"{supabase_url}/rest/v1/field_observations",
        headers={**_headers(), "Prefer": ""},
        params={"select": "*", "order": "observation_date.desc"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Cannot fetch field_observations: {resp.status_code} {resp.text}")
    observations = resp.json()
    logger.info(f"Fetched {len(observations)} field observations")

    # ดึง analyses ที่ match plot_id
    plot_ids = list({str(o["plot_id"]) for o in observations})
    if not plot_ids:
        raise RuntimeError("No observations with plot_id found")

    rows = []
    for obs in observations:
        plot_id = obs["plot_id"]
        # ดึง analysis ล่าสุดของแปลงนี้ก่อนวันที่ observation
        resp2 = httpx.get(
            f"{supabase_url}/rest/v1/analyses",
            headers={**_headers(), "Prefer": ""},
            params={
                "plot_id":    f"eq.{plot_id}",
                "created_at": f"lte.{obs['observation_date']}",
                "order":      "created_at.desc",
                "limit":      "1",
                "select":     "ndvi_now,ndvi_change,soil_moisture_vv,elevation_diff,"
                              "surface_stability,fertilizer_level,bsi_score,"
                              "displacement_level,yield_estimated_kg",
            },
            timeout=15,
        )
        if resp2.status_code != 200 or not resp2.json():
            continue
        analysis = resp2.json()[0]

        # ดึง root_rot_history สำหรับแปลงนี้ (เคยเกิดรากเน่าหรือไม่)
        resp3 = httpx.get(
            f"{supabase_url}/rest/v1/field_observations",
            headers={**_headers(), "Prefer": ""},
            params={
                "plot_id":          f"eq.{plot_id}",
                "root_rot_occurred": "eq.true",
                "limit":            "1",
                "select":           "id",
            },
            timeout=10,
        )
        root_rot_history = len(resp3.json()) > 0 if resp3.status_code == 200 else False

        rows.append({
            "ndvi_now":          float(analysis.get("ndvi_now") or 0),
            "ndvi_change":       float(analysis.get("ndvi_change") or 0),
            "soil_moisture_vv":  float(analysis.get("soil_moisture_vv") or -15),
            "elevation_diff":    float(analysis.get("elevation_diff") or 0),
            "surface_stability": float(analysis.get("surface_stability") or 1),
            "bsi_score":         float(analysis.get("bsi_score") or 0),
            "displacement_high": 1 if analysis.get("displacement_level") == "high" else 0,
            "root_rot_history":  1 if root_rot_history else 0,
            "actual_yield_kg":   float(obs["actual_yield_kg"]),
        })

    logger.info(f"Assembled {len(rows)} training samples")
    return rows


def retrain(rows: list[dict]) -> None:
    try:
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.model_selection import cross_val_score
        import numpy as np
    except ImportError:
        logger.error("scikit-learn not installed — run: pip install scikit-learn")
        sys.exit(1)

    FEATURES = [
        "ndvi_now", "ndvi_change", "soil_moisture_vv", "elevation_diff",
        "surface_stability", "bsi_score", "displacement_high", "root_rot_history",
    ]

    X = [[r[f] for f in FEATURES] for r in rows]
    y = [r["actual_yield_kg"] for r in rows]

    import numpy as np
    X_arr = np.array(X)
    y_arr = np.array(y)

    model = RandomForestRegressor(n_estimators=100, random_state=42, n_jobs=-1)

    if len(rows) >= 10:
        scores = cross_val_score(model, X_arr, y_arr, cv=min(5, len(rows)),
                                 scoring="neg_mean_absolute_error")
        mae = -scores.mean()
        logger.info(f"Cross-val MAE: {mae:.1f} kg/rai")

    model.fit(X_arr, y_arr)

    out_path = os.path.join(os.path.dirname(__file__), "durian_yield_model_v4.pkl")
    meta = {
        "model": model,
        "features": FEATURES,
        "trained_at": datetime.utcnow().isoformat(),
        "n_samples": len(rows),
        "version": "v4",
    }
    with open(out_path, "wb") as f:
        pickle.dump(meta, f)

    logger.info(f"✅ Model saved → {out_path}  (n={len(rows)} samples)")
    logger.info("Feature importances:")
    for feat, imp in sorted(zip(FEATURES, model.feature_importances_),
                            key=lambda x: -x[1]):
        logger.info(f"  {feat:25s} {imp:.3f}")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    url = os.environ.get("SUPABASE_URL", "")
    if not url:
        logger.error("SUPABASE_URL not set")
        sys.exit(1)

    rows = fetch_training_data(url)
    if len(rows) < 5:
        logger.error(f"Not enough training data: {len(rows)} samples (need ≥ 5)")
        sys.exit(1)

    retrain(rows)
