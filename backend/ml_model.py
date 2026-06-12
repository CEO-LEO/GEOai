"""
ml_model.py — Proof of Concept: Durian Yield Prediction (ML)
สร้างชุดข้อมูลจำลอง, เทรน RandomForestRegressor, บันทึก/โหลดโมเดล

ฟีเจอร์อินพุต:
  - ndvi         : ดัชนีสุขภาพพืช (0.0 – 1.0)
  - bsi_score    : Bare Soil Index หน้าดิน (-0.5 – 0.5)
  - elevation_diff: ความต่างระดับจากรอบข้าง (เมตร, ลบ = แอ่ง)

เป้าหมาย (Target):
  - actual_yield : ผลผลิตจริง (กก./ไร่/ปี)

รันเทรนโมเดลโดยตรง:
  python ml_model.py            → เทรนและบันทึก durian_yield_model.pkl
  python ml_model.py --predict  → ตัวอย่างพยากรณ์จากโมเดลที่บันทึกไว้
"""

import argparse
import logging
import os
import random

import joblib
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split

logger = logging.getLogger(__name__)

# ─── พาธของไฟล์โมเดล ─────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(_HERE, "durian_yield_model_v3.pkl")

# ─── ค่าคงที่โมเดล ────────────────────────────────────
N_SAMPLES      = 200
BASE_YIELD     = 1500       # กก./ไร่
RANDOM_STATE   = 42
N_ESTIMATORS   = 100


# ─────────────────────────────────────────────────────
# 1. สร้างชุดข้อมูลจำลอง
# ─────────────────────────────────────────────────────

def make_mock_dataset(n: int = N_SAMPLES, seed: int = RANDOM_STATE):
    """
    สร้างชุดข้อมูลสวนทุเรียนจำลอง n แถว
    ใช้ logic เดียวกับ predict_yield() ใน rule_engine.py
    แต่เพิ่ม noise เพื่อให้โมเดล ML ต้องเรียนรู้ pattern

    Returns
    -------
    X : np.ndarray  shape (n, 3)  — [ndvi, bsi_score, elevation_diff]
    y : np.ndarray  shape (n,)    — actual_yield (กก./ไร่)
    """
    rng = np.random.default_rng(seed)

    ndvi          = rng.uniform(0.15, 0.90, n)
    bsi_score     = rng.uniform(-0.3, 0.45, n)
    elevation_diff = rng.uniform(-4.0, 2.5, n)

    # v3: Soil Water-Air Balance features
    # soil_water_pct: proxy from VV SAR, range 15-75%
    soil_water_pct = rng.uniform(15.0, 75.0, n)
    swab_index_arr = np.clip((soil_water_pct - 45.0) / 45.0, -1.0, 1.0)

    # คำนวณ yield ฐานจาก logic เดิม + noise จริง ±15%
    yield_ = np.full(n, float(BASE_YIELD))

    # NDVI > 0.6 → +20%
    yield_ = np.where(ndvi > 0.6, yield_ * 1.20, yield_)

    # BSI สูง (> 0.2) → -15%
    yield_ = np.where(bsi_score > 0.2, yield_ * 0.85, yield_)

    # พื้นที่ต่ำ (elev_diff < -1.5) → -10%
    yield_ = np.where(elevation_diff < -1.5, yield_ * 0.90, yield_)

    # NDVI ต่ำมาก (<0.3) → ผลผลิตแย่กว่าอีก
    yield_ = np.where(ndvi < 0.3, yield_ * 0.65, yield_)

    # v3: SWAB effect — น้ำขังหรือแล้ง ลดผลผลิต
    yield_ = np.where(swab_index_arr >  0.35, yield_ * 0.75, yield_)  # น้ำขัง → -25%
    yield_ = np.where(swab_index_arr < -0.35, yield_ * 0.80, yield_)  # แล้ง → -20%
    yield_ = np.where(np.abs(swab_index_arr) <= 0.15, yield_ * 1.05, yield_)  # สมดุล → +5%

    # เพิ่ม noise ±15% เพื่อจำลองความผันแปรจากธรรมชาติ (อากาศ, โรค, แมลง)
    noise = rng.normal(1.0, 0.12, n)
    yield_ = np.clip(yield_ * noise, 100, 2500).round().astype(float)

    X = np.column_stack([ndvi, bsi_score, elevation_diff, swab_index_arr, soil_water_pct])
    y = yield_

    return X, y


# ─────────────────────────────────────────────────────
# 2. เทรนโมเดล RandomForest
# ─────────────────────────────────────────────────────

def train_model(save: bool = True) -> RandomForestRegressor:
    """
    สร้างข้อมูลจำลอง → เทรน RandomForestRegressor → (บันทึก)

    Returns
    -------
    model : RandomForestRegressor ที่เทรนแล้ว
    """
    logger.info(f"สร้างชุดข้อมูลจำลอง {N_SAMPLES} แถว...")
    X, y = make_mock_dataset(N_SAMPLES, RANDOM_STATE)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=RANDOM_STATE
    )

    logger.info(f"เทรนโมเดล RandomForest (n_estimators={N_ESTIMATORS})...")
    model = RandomForestRegressor(
        n_estimators=N_ESTIMATORS,
        max_depth=8,
        min_samples_leaf=3,
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)

    # ── ประเมินผล ──
    y_pred = model.predict(X_test)
    mae    = mean_absolute_error(y_test, y_pred)
    r2     = r2_score(y_test, y_pred)
    logger.info(f"ผลการเทรน — MAE: {mae:.1f} กก./ไร่ | R²: {r2:.3f}")

    # ── Feature importance ──
    features = ["ndvi", "bsi_score", "elevation_diff", "swab_index", "soil_water_pct"]
    importances = dict(zip(features, model.feature_importances_))
    logger.info(f"Feature importance: {importances}")

    if save:
        joblib.dump(model, MODEL_PATH)
        logger.info(f"บันทึกโมเดลที่ {MODEL_PATH}")

    return model


# ─────────────────────────────────────────────────────
# 3. พยากรณ์จากโมเดล .pkl
# ─────────────────────────────────────────────────────

def predict_from_ml(ndvi: float, bsi: float, elev_diff: float,
                    swab_index: float = 0.0,
                    soil_water_pct: float = 45.0) -> int:
    """
    โหลดโมเดลจาก durian_yield_model_v3.pkl และพยากรณ์ผลผลิต (v3)

    Parameters
    ----------
    ndvi          : ค่า NDVI ของแปลง (0.0 – 1.0)
    bsi           : ค่า Bare Soil Index (-0.5 – 0.5)
    elev_diff     : ความต่างระดับพื้นที่จากรอบข้าง (เมตร)
    swab_index    : SWAB Index (-1 – +1); 0 = สมดุล
    soil_water_pct: % น้ำในดิน (5 – 90)

    Returns
    -------
    int : ผลผลิตพยากรณ์ (กก./ไร่/ปี) หรือ -1 ถ้าโมเดลยังไม่พร้อม
    """
    if not os.path.exists(MODEL_PATH):
        logger.warning(
            f"ไม่พบไฟล์โมเดล {MODEL_PATH} — รัน 'python ml_model.py' ก่อน"
        )
        return -1

    try:
        model = joblib.load(MODEL_PATH)
        X = np.array([[ndvi, bsi, elev_diff, swab_index, soil_water_pct]])
        pred = model.predict(X)[0]
        return max(0, round(pred))
    except Exception as exc:
        logger.error(f"โหลด/ใช้โมเดลล้มเหลว: {exc}")
        return -1


FEATURE_LABELS_TH = {
    "ndvi":           "ความสมบูรณ์พืช (NDVI)",
    "bsi_score":      "ดินเปิดโล่ง (BSI)",
    "elevation_diff": "ระดับพื้นที่",
    "swab_index":     "สมดุลน้ำ-อากาศในดิน",
    "soil_water_pct": "ปริมาณน้ำในดิน",
}


def get_model_meta() -> dict:
    """
    ข้อมูลโมเดลสำหรับแสดงความแม่นยำ/ความสำคัญของฟีเจอร์ใน dashboard
    คืน {"available", "version", "type", "features": [{key,label,importance}], ...}
    """
    meta = {"available": False, "version": "v3", "type": "rule-based fallback",
            "features": []}
    if not os.path.exists(MODEL_PATH):
        return meta
    try:
        model = joblib.load(MODEL_PATH)
        features = ["ndvi", "bsi_score", "elevation_diff", "swab_index", "soil_water_pct"]
        meta["available"] = True
        meta["type"] = type(model).__name__
        if hasattr(model, "feature_importances_"):
            imp = [
                {"key": f, "label": FEATURE_LABELS_TH.get(f, f),
                 "importance": round(float(v), 3)}
                for f, v in zip(features, model.feature_importances_)
            ]
            imp.sort(key=lambda x: x["importance"], reverse=True)
            meta["features"] = imp
        if hasattr(model, "n_estimators"):
            meta["n_estimators"] = int(model.n_estimators)
    except Exception as exc:
        logger.error(f"get_model_meta ล้มเหลว: {exc}")
    return meta


# ─────────────────────────────────────────────────────
# 4. CLI entry point
# ─────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
    )

    parser = argparse.ArgumentParser(description="GEOai — Durian Yield ML Model")
    parser.add_argument(
        "--predict", action="store_true",
        help="ทดสอบพยากรณ์จากโมเดลที่บันทึกไว้ (ต้องเทรนก่อน)"
    )
    args = parser.parse_args()

    if args.predict:
        # ตัวอย่างพยากรณ์ 3 สถานการณ์
        scenarios = [
            ("สวนสมบูรณ์",                0.72, 0.05,  0.5,  0.0,  45.0),
            ("ดินเปิดโล่ง + แห้ง",        0.55, 0.30,  0.2, -0.3,  32.0),
            ("น้ำขัง + โทรม",           0.40, 0.25, -2.0,  0.5,  67.0),
        ]
        print("\n─── ตัวอย่างผลพยากรณ์จากโมเดล ML (v3) ───")
        for label, ndvi, bsi, elev, swab, water in scenarios:
            result = predict_from_ml(ndvi, bsi, elev, swab, water)
            if result >= 0:
                print(f"  {label:25s} NDVI={ndvi:.2f}  BSI={bsi:.2f}  elev={elev:+.1f}m"
                      f"  SWAB={swab:+.2f}  H2O={water:.0f}%"
                      f"  →  {result:,} กก./ไร่")
            else:
                print(f"  โมเดลยังไม่พร้อม — รัน: python ml_model.py ก่อน")
    else:
        # เทรนโมเดลและบันทึก
        print("\n─── เริ่มเทรนโมเดล Durian Yield Prediction ───")
        m = train_model(save=True)
        print(f"\nเสร็จสิ้น! โมเดลถูกบันทึกที่:\n  {MODEL_PATH}")
        print("\nทดสอบพยากรณ์ด้วย: python ml_model.py --predict")
