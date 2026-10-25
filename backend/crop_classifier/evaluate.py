"""
evaluate.py — ประเมินแบบแบ่งเชิงพื้นที่ (spec 7.4) — ฟังก์ชันล้วน รันซ้ำได้

แบ่ง: GroupKFold ตามกลุ่มพื้นที่ (อำเภอ หรือกริด 5 กม. — labels.spatial_group) แปลงในกลุ่มเดียวกันอยู่ฝั่งเดียวกันเสมอ
      ไม่สุ่มรายพิกเซล/รายแปลง เพราะแปลงใกล้กันคล้ายกัน ทำให้แม่นเกินจริง
รายงาน: accuracy, macro-F1, F1 ต่อพืช, confusion matrix, ผลรายกลุ่มพื้นที่ + เทียบ random forest
เกณฑ์เปิดใช้ (decisions.md): แปลงที่ประเมิน ≥ 50 · macro-F1 ≥ 0.6 · ≥ 3 กลุ่มพื้นที่
"""

from collections import Counter, defaultdict

import numpy as np

from . import model

GATE_MIN_PLOTS = 50
GATE_MIN_MACRO_F1 = 0.6
GATE_MIN_GROUPS = 3
MAX_FOLDS = 5


def _f1_table(y_true: list[str], y_pred: list[str], classes: list[str]) -> dict:
    out = {}
    for c in classes:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == c and p == c)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != c and p == c)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == c and p != c)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        out[c] = {"f1": round(2 * prec * rec / (prec + rec), 3) if prec + rec else 0.0,
                  "precision": round(prec, 3), "recall": round(rec, 3), "support": tp + fn}
    return out


def _rf_predict(Xtr, ytr, wtr, Xte):
    from sklearn.ensemble import RandomForestClassifier
    mean = np.nanmean(Xtr, axis=0)
    mean = np.where(np.isnan(mean), 0.0, mean)
    rf = RandomForestClassifier(n_estimators=300, class_weight="balanced", min_samples_leaf=2, random_state=0)
    rf.fit(np.where(np.isnan(Xtr), mean, Xtr), ytr, sample_weight=wtr)
    return [str(p) for p in rf.predict(np.where(np.isnan(Xte), mean, Xte))]


def spatial_cv(X, y: list[str], w, groups: list[str], with_rf: bool = True) -> dict:
    """คืน dict ผลประเมิน (status "insufficient" ถ้าข้อมูลไม่พอแบ่ง)"""
    X, w = model.to_matrix(X), np.asarray(w, dtype=float)
    uniq = sorted(set(groups))
    classes = sorted(set(y))
    base = {"n": len(y), "groups": len(uniq), "classes": classes, "class_counts": dict(Counter(y))}
    if len(uniq) < 2 or len(classes) < 2:
        return {**base, "status": "insufficient"}
    from sklearn.model_selection import GroupKFold
    k = min(MAX_FOLDS, len(uniq))
    pred_lr, pred_rf, truth, grp = [None] * len(y), [None] * len(y), list(y), list(groups)
    for tr, te in GroupKFold(n_splits=k).split(X, y, groups):
        ytr = [y[i] for i in tr]
        if len(set(ytr)) < 2:
            for i in te:
                pred_lr[i] = pred_rf[i] = Counter(ytr).most_common(1)[0][0]
            continue
        params = model.fit_logreg(X[tr], ytr, w[tr])
        probs = model.predict_proba(params, X[te])
        for i, row in zip(te, probs):
            pred_lr[i] = params["classes"][int(np.argmax(row))]
        if with_rf:
            for i, p in zip(te, _rf_predict(X[tr], ytr, w[tr], X[te])):
                pred_rf[i] = p

    def summary(pred):
        f1 = _f1_table(truth, pred, classes)
        cm = {t: dict(Counter(p for tt, p in zip(truth, pred) if tt == t)) for t in classes}
        by_group = defaultdict(lambda: [0, 0])
        for g, t, p in zip(grp, truth, pred):
            by_group[g][0] += t == p
            by_group[g][1] += 1
        return {"accuracy": round(sum(t == p for t, p in zip(truth, pred)) / len(truth), 3),
                "macro_f1": round(float(np.mean([v["f1"] for v in f1.values()])), 3),
                "per_class": f1, "confusion": cm,
                "per_group": {g: {"correct": c, "n": n} for g, (c, n) in sorted(by_group.items())}}

    out = {**base, "status": "ok", "folds": k, "split": "GroupKFold ตามกลุ่มพื้นที่", "logreg": summary(pred_lr)}
    if with_rf:
        out["random_forest"] = summary(pred_rf)
    return out


def passes_gate(metrics: dict) -> tuple[bool, list[str]]:
    """เกณฑ์เปิดใช้โมเดลกับผู้ใช้ — คืน (ผ่านไหม, เหตุผลที่ไม่ผ่าน)"""
    why = []
    if metrics.get("status") != "ok":
        why.append("ข้อมูลไม่พอสำหรับประเมิน")
        return False, why
    if metrics["n"] < GATE_MIN_PLOTS:
        why.append(f"แปลงที่มีชนิดพืชยืนยัน {metrics['n']} (ต้อง ≥ {GATE_MIN_PLOTS})")
    if metrics["groups"] < GATE_MIN_GROUPS:
        why.append(f"กลุ่มพื้นที่ {metrics['groups']} (ต้อง ≥ {GATE_MIN_GROUPS})")
    if metrics["logreg"]["macro_f1"] < GATE_MIN_MACRO_F1:
        why.append(f"macro-F1 {metrics['logreg']['macro_f1']} (ต้อง ≥ {GATE_MIN_MACRO_F1})")
    return not why, why


def report_markdown(version: str, metrics: dict, dataset: dict, names_th: dict[str, str]) -> str:
    """รายงานผลประเมินสำหรับเก็บใน docs/ — ตัวเลขมาพร้อมจำนวนตัวอย่างเสมอ"""
    ok, why = passes_gate(metrics)
    lines = [f"# ผลประเมินโมเดลจำแนกพืช {version}", "",
             f"ชุดข้อมูล: {metrics.get('n', 0)} แปลง · {metrics.get('groups', 0)} กลุ่มพื้นที่ · sha256 `{dataset.get('sha256', '-')[:12]}`",
             f"เกณฑ์เปิดใช้: {'ผ่าน' if ok else 'ไม่ผ่าน — ' + ' · '.join(why)}", ""]
    if metrics.get("status") != "ok":
        lines.append("ยังประเมินไม่ได้ (ต้องมีอย่างน้อย 2 คลาส ใน 2 กลุ่มพื้นที่)")
        return "\n".join(lines) + "\n"
    lines += [f"แบ่งข้อมูล: {metrics['split']} ({metrics['folds']} fold)", "",
              "| โมเดล | accuracy | macro-F1 |", "|---|---|---|"]
    for key, label in (("logreg", "Logistic regression (ใช้จริง)"), ("random_forest", "Random forest (เทียบ)")):
        if key in metrics:
            lines.append(f"| {label} | {metrics[key]['accuracy']} | {metrics[key]['macro_f1']} |")
    lines += ["", "| พืช | ตัวอย่าง | F1 | precision | recall |", "|---|---|---|---|---|"]
    for c, v in metrics["logreg"]["per_class"].items():
        lines.append(f"| {names_th.get(c, c)} | {v['support']} | {v['f1']} | {v['precision']} | {v['recall']} |")
    cls = metrics["classes"]
    lines += ["", "Confusion matrix (แถว = จริง, คอลัมน์ = ทาย)", "",
              "| จริง \\ ทาย | " + " | ".join(names_th.get(c, c) for c in cls) + " |", "|---" * (len(cls) + 1) + "|"]
    for t in cls:
        lines.append(f"| {names_th.get(t, t)} | " + " | ".join(str(metrics["logreg"]["confusion"][t].get(p, 0)) for p in cls) + " |")
    # รายอำเภอ · กลุ่มกริด (แปลงนอกอำเภอที่รู้จัก) รวมเป็นแถวเดียว — ไม่เปิดเผยตำแหน่งช่องกริดในรายงาน
    rows: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for g, v in metrics["logreg"]["per_group"].items():
        key = names_th.get(g[1:], g[1:]) if g.startswith("d") else "นอกอำเภอที่รู้จัก (รวม)"
        rows[key][0] += v["correct"]
        rows[key][1] += v["n"]
    lines += ["", "| อำเภอ | ถูก / ทั้งหมด |", "|---|---|"]
    for k, (c, n) in rows.items():
        lines.append(f"| {k} | {c} / {n} |")
    return "\n".join(lines) + "\n"
