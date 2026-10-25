"""
model.py — โมเดลหลายคลาสตัวเดียว (spec 7.3 ห้ามแยกโมเดลต่อพืชแบบ yes/no) + การตัดสิน "ไม่แน่ใจ"

เทรน: sklearn LogisticRegression (multinomial, class_weight="balanced" จัดการคลาสไม่สมดุล) บน feature ที่ standardize แล้ว
เก็บ: พารามิเตอร์เป็น JSON (mean/scale/coef/intercept/classes) — ไม่ใช้ pickle เพราะโหลด pickle จาก DB = รันโค้ดได้
ทาย: numpy ล้วน (softmax) ไม่ต้องโหลด sklearn ตอนใช้งาน
"""

import numpy as np

ALGORITHM = "logreg_v1"
P_MIN = 0.5            # ความน่าจะเป็นอันดับ 1 ต่ำกว่านี้ = ไม่แน่ใจ
MARGIN_MIN = 0.15      # อันดับ 1 ห่างอันดับ 2 น้อยกว่านี้ = ไม่แน่ใจ
TREE_MIN = 0.3         # ต้นไม้ในขอบเขต (Dynamic World) น้อยกว่านี้ แต่ทายเป็นไม้ผล = "ไม่น่าใช่สวนไม้ผล"


def fit_logreg(X: np.ndarray, y: list[str], w: np.ndarray | None = None, C: float = 0.5) -> dict:
    from sklearn.linear_model import LogisticRegression
    X = to_matrix(X)
    mean = np.nanmean(X, axis=0)
    mean = np.where(np.isnan(mean), 0.0, mean)            # feature ที่ไม่มีค่าเลย → 0 (ไม่มีผลหลัง scale)
    X = np.where(np.isnan(X), mean, X)                     # ค่าที่หาย (เดือนไม่มีภาพ) = ค่าเฉลี่ยชุดฝึก
    scale = X.std(axis=0)
    scale[scale < 1e-9] = 1.0
    clf = LogisticRegression(C=C, class_weight="balanced", max_iter=2000)
    clf.fit((X - mean) / scale, y, sample_weight=w)
    coef, intercept = clf.coef_, clf.intercept_
    if len(clf.classes_) == 2:          # sklearn เก็บแบบ binary เป็นแถวเดียว → แปลงเป็น 2 แถวให้ softmax ใช้ได้
        coef = np.vstack([-coef[0] / 2, coef[0] / 2])
        intercept = np.array([-intercept[0] / 2, intercept[0] / 2])
    return {"algorithm": ALGORITHM, "classes": [str(c) for c in clf.classes_], "C": C,
            "mean": mean.round(6).tolist(), "scale": scale.round(6).tolist(),
            "coef": np.round(coef, 6).tolist(), "intercept": np.round(intercept, 6).tolist()}


def to_matrix(X) -> np.ndarray:
    """แถวของ feature (มี None ได้) → float matrix ที่ None = NaN"""
    return np.atleast_2d(np.array([[np.nan if v is None else v for v in row] for row in X], dtype=float))


def predict_proba(params: dict, X) -> np.ndarray:
    mean = np.array(params["mean"])
    X = to_matrix(X)
    X = np.where(np.isnan(X), mean, X)
    z = ((X - mean) / np.array(params["scale"])) @ np.array(params["coef"]).T + np.array(params["intercept"])
    z -= z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def decide(classes: list[str], probs, tree_fraction: float | None, crop_index: dict[str, dict]) -> dict:
    """ความน่าจะเป็นต่อคลาส → {"status": ok|uncertain|not_orchard, "top": [{crop_id, p}] ≤ 3, "confidence"}"""
    order = np.argsort(-np.asarray(probs))
    top = [{"crop_id": classes[i], "p": round(float(probs[i]), 3)} for i in order[:3]]
    p1 = top[0]["p"]
    p2 = top[1]["p"] if len(top) > 1 else 0.0
    status = "ok"
    is_fruit = crop_index.get(top[0]["crop_id"], {}).get("group") == "fruit_tree" or top[0]["crop_id"] == "mixed"
    if tree_fraction is not None and tree_fraction < TREE_MIN and is_fruit:
        status = "not_orchard"
    elif p1 < P_MIN or p1 - p2 < MARGIN_MIN or top[0]["crop_id"] == "other":
        status = "uncertain"
    return {"status": status, "top": top, "confidence": round(p1, 3)}
