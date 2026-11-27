"""
D｜Adaptive learning 自適應學習

負責：模型權重調整、加權預測、準確度評估。

做法
  1. 滾動回測：在過去的每一天 t「假裝站在那天」，讓三個模型用 t 之後實際吃的（校正後攝取）
     預測 t + h 天的體重，再跟那天的 7 天平均體重比，記下誤差。
  2. 調整權重：站在第 t 天時，只能用「答案已經揭曉」（目標日 ≤ t）的誤差，不能偷看未來。
       equal       三個模型平均
       inverse_mse 權重 ∝ 1 / 最近 window 次的均方誤差（誤差越小權重越大）
       hedge       權重 ∝ exp(−η × 打折後的累積誤差)，越近的誤差影響越大
       best        只用最近 window 次誤差最小的那一個模型
       bias_corrected  inverse_mse 之外，再扣掉最近 window 次加權預測的平均誤差

  實驗結果（8 組模擬資料 × 16 週，預測 14、28 天後，跟量到的體重比的平均 MAE）：
       平均分配 0.24、誤差倒數加權 0.25、指數加權 0.25、只用最準的 0.25～0.26、
       偏差校正 0.32～0.33（反而變差：偏差主要來自水分波動，追著它校正會放大雜訊）
       → app 預設用 inverse_mse；偏差校正留著當對照組。
  3. 評估：MAE（平均絕對誤差）、RMSE、偏差（平均高估或低估多少）。
     模擬資料另外跟「真實體重」比，排除體重計和水分的雜訊。

────────────────────────────────────────
給其他組員的用法
────────────────────────────────────────
    import adaptive as ad
    weights, info = ad.current_weights(um, "demo")        # {"hall": 0.45, ...}
    fc = ad.weighted_forecast(um, "demo", days=84)       # forecast 多一個 "weighted"
    report = ad.evaluate(um, "demo", horizon=14)          # 各模型、各方法的誤差

單獨執行 `python adaptive.py demo` 會印出評估報告。
"""

from __future__ import annotations

import csv
import math
import os
import sys
from datetime import date, timedelta

import data_processing as dp
import prediction_models as pm

METHODS = {
    "equal": "平均分配",
    "inverse_mse": "誤差倒數加權",
    "hedge": "指數加權",
    "best": "只用最近最準的",
    "bias_corrected": "誤差倒數加權＋偏差校正",
}
MODELS = list(pm.MODELS)
WINDOW = 21          # inverse_mse、best 看最近幾次回測
ETA = 2.0            # hedge 的學習率（誤差單位：公斤²）
DISCOUNT = 0.95      # hedge：每過一次回測，舊的誤差打幾折
MIN_COVERAGE = 0.7   # 預測期間至少 70% 的天要有飲食紀錄


def _shift(day: str, n: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=n)).isoformat()


# ───────────────────────── 1. 滾動回測 ─────────────────────────
def backtest(rows: list[dict], body: pm.Body, horizon: int = 14) -> list[dict]:
    """回傳每個起點的回測結果：
    {"origin", "target", "w0", "actual", "preds": {模型: 預測}, "errors": {模型: 預測 − 實際}}
    """
    by_date = {r["date"]: r for r in rows}
    out = []
    for r in rows:
        t, target = r["date"], _shift(r["date"], horizon)
        end = by_date.get(target)
        if r["weight_smooth"] is None or not end or end["weight_smooth"] is None:
            continue
        days = [by_date.get(_shift(t, i)) for i in range(horizon)]
        known = [d["intake_corrected"] for d in days
                 if d and d["usable"] and d["intake_corrected"] is not None]
        if len(known) < MIN_COVERAGE * horizon:
            continue
        fill = sum(known) / len(known)
        intakes = [d["intake_corrected"] if d and d["usable"] and d["intake_corrected"] is not None
                   else fill for d in days]
        exercises = [(d or {}).get("exercise_kcal") or 0 for d in days]
        preds = {m: pm.MODELS[m](r["weight_smooth"], intakes, exercises, body)[-1] for m in MODELS}
        actual = end["weight_smooth"]
        out.append({"origin": t, "target": target, "w0": r["weight_smooth"], "actual": actual,
                    "preds": preds, "errors": {m: preds[m] - actual for m in MODELS}})
    return out


# ───────────────────────── 2. 權重 ─────────────────────────
def weights_at(records: list[dict], day: str, method: str) -> dict[str, float]:
    """站在 day 這天的權重：只用目標日 ≤ day 的回測（答案已經知道了）"""
    known = [r for r in records if r["target"] <= day]
    equal = {m: 1 / len(MODELS) for m in MODELS}
    if method == "equal" or not known:
        return equal
    if method == "bias_corrected":
        method = "inverse_mse"
    if method in ("inverse_mse", "best"):
        recent = known[-WINDOW:]
        mse = {m: sum(r["errors"][m] ** 2 for r in recent) / len(recent) for m in MODELS}
        if method == "best":
            best = min(mse, key=mse.get)
            return {m: float(m == best) for m in MODELS}
        inv = {m: 1 / max(v, 1e-4) for m, v in mse.items()}
        s = sum(inv.values())
        return {m: v / s for m, v in inv.items()}
    if method == "hedge":
        n = len(known)
        loss = {m: sum(DISCOUNT ** (n - 1 - i) * r["errors"][m] ** 2 for i, r in enumerate(known))
                for m in MODELS}
        lo = min(loss.values())
        ex = {m: math.exp(-ETA * (v - lo)) for m, v in loss.items()}
        s = sum(ex.values())
        return {m: v / s for m, v in ex.items()}
    raise ValueError(f"沒有這個方法：{method}")


def combine(preds: dict[str, float], weights: dict[str, float]) -> float:
    return sum(preds[m] * weights[m] for m in MODELS)


def bias_at(records: list[dict], day: str) -> float:
    """站在 day 這天，最近 window 次（答案已揭曉）inverse_mse 加權預測的平均誤差"""
    known = [r for r in records if r["target"] <= day][-WINDOW:]
    if not known:
        return 0.0
    errs = [combine(r["preds"], weights_at(records, r["origin"], "inverse_mse")) - r["actual"]
            for r in known]
    return sum(errs) / len(errs)


# ───────────────────────── 3. 評估 ─────────────────────────
def _metrics(errors: list[float]) -> dict:
    if not errors:
        return {"n": 0, "mae": None, "rmse": None, "bias": None}
    n = len(errors)
    return {"n": n, "mae": sum(abs(e) for e in errors) / n,
            "rmse": math.sqrt(sum(e * e for e in errors) / n), "bias": sum(errors) / n}


def load_truth(um, username: str) -> dict[str, float] | None:
    """模擬資料的真實體重（simulate_data.py 產生的），沒有就回傳 None"""
    stored = um.storage.get(f"truth:{username}")
    if stored:
        return {d: float(w) for d, w in stored.items()}
    path = os.path.join(um.data_dir, f"simulated_truth_{username}.csv")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8-sig") as f:
        return {r["date"]: float(r["true_weight"]) for r in csv.DictReader(f)}


def evaluate(um, username: str, horizon: int = 14, warmup: int = 7) -> dict:
    """完整評估。為了公平，所有方法都只在「暖身」之後的同一批起點上比較
    （至少已經有 warmup 次回測揭曉答案）。

    回傳 {
      "horizon", "records": 回測次數, "evaluated": 參與評估的次數,
      "models": {模型: 指標}, "methods": {方法: 指標},
      "truth_models"/"truth_methods": 跟真實體重比（只有模擬資料才有）,
      "history": [{"origin", "weights": {方法: {模型: 權重}}, "actual", "preds", "combined": {方法: 預測}}]
    }
    """
    rows = dp.build_dataset(um, username)
    records = backtest(rows, pm.body_of(um, username), horizon)
    truth = load_truth(um, username)
    history = []
    for r in records:
        known = sum(x["target"] <= r["origin"] for x in records)
        w = {meth: weights_at(records, r["origin"], meth) for meth in METHODS}
        combined = {meth: combine(r["preds"], w[meth]) for meth in METHODS}
        combined["bias_corrected"] -= bias_at(records, r["origin"])
        history.append({"origin": r["origin"], "target": r["target"], "actual": r["actual"],
                        "preds": r["preds"], "weights": w, "combined": combined,
                        "warmed_up": known >= warmup})
    ev = [h for h in history if h["warmed_up"]]
    res = {
        "horizon": horizon, "records": len(records), "evaluated": len(ev),
        "models": {m: _metrics([h["preds"][m] - h["actual"] for h in ev]) for m in MODELS},
        "methods": {k: _metrics([h["combined"][k] - h["actual"] for h in ev]) for k in METHODS},
        "history": history,
    }
    if truth:
        tv = [h for h in ev if h["target"] in truth]
        res["truth_models"] = {m: _metrics([h["preds"][m] - truth[h["target"]] for h in tv]) for m in MODELS}
        res["truth_methods"] = {k: _metrics([h["combined"][k] - truth[h["target"]] for h in tv])
                                for k in METHODS}
    return res


# ───────────────────────── 4. 今天的權重、加權預測 ─────────────────────────
def current_weights(um, username: str, method: str = "inverse_mse",
                    horizon: int = 28) -> tuple[dict[str, float], dict]:
    """今天該用的權重。回傳 (權重, {"based_on": 用了幾次回測, "method", "bias", "horizon"})
    資料不夠做 horizon 天的回測時，自動改用 14、7 天；都不夠就平均分配（based_on = 0）。"""
    rows = dp.build_dataset(um, username)
    body = pm.body_of(um, username)
    today = date.today().isoformat()
    for h in dict.fromkeys((horizon, 14, 7)):
        records = backtest(rows, body, h)
        known = [r for r in records if r["target"] <= today]
        if known:
            horizon = h
            break
    used = min(len(known), WINDOW) if method in ("inverse_mse", "best", "bias_corrected") else len(known)
    bias = bias_at(records, today) if method == "bias_corrected" else 0.0
    return weights_at(records, today, method), {"based_on": used, "method": method,
                                                "bias": bias, "horizon": horizon}


def weighted_forecast(um, username: str, days: int = 84, method: str = "inverse_mse",
                      intake: float | None = None, exercise: float | None = None) -> dict:
    """pm.forecast 的結果，多加 "weighted"（加權預測）和 "weights"。

    偏差校正：回測量到的是「每 horizon 天」的偏差，換算成每天的速率往後累加，
    但最多只校正 horizon 天的量（偏差多半來自短期的水分、記錄習慣，不會無限累積）。
    """
    fc = pm.forecast(um, username, days=days, intake=intake, exercise=exercise)
    w, info = current_weights(um, username, method)
    rate, cap = info["bias"] / info["horizon"], info["horizon"]
    fc["weighted"] = [sum(fc[m][i] * w[m] for m in MODELS) - rate * min(i, cap)
                      for i in range(days + 1)]
    fc["weights"], fc["weights_info"] = w, info
    return fc


# ───────────────────────── 文字報告 ─────────────────────────
def print_report(um, username: str, horizon: int = 14):
    res = evaluate(um, username, horizon)
    print(f"\n=== {username}：預測 {horizon} 天後的體重 ===")
    print(f"回測 {res['records']} 次，暖身後參與評估 {res['evaluated']} 次")
    if not res["evaluated"]:
        print("資料還不夠。需要連續幾週的飲食紀錄和體重。")
        return

    def table(title, models, methods):
        print(f"\n{title}")
        print(f"  {'':<14}{'MAE':>7}{'RMSE':>7}{'偏差':>7}")
        for name, m in [(pm.MODEL_NAMES[k], v) for k, v in models.items()] + \
                       [("加權：" + METHODS[k], v) for k, v in methods.items()]:
            print(f"  {name:<14}{m['mae']:>7.2f}{m['rmse']:>7.2f}{m['bias']:>+7.2f}")

    table("跟量到的體重（7 天平均）比，單位 kg：", res["models"], res["methods"])
    if "truth_models" in res:
        table("跟模擬的真實體重比，單位 kg：", res["truth_models"], res["truth_methods"])
    w, info = current_weights(um, username, method="inverse_mse", horizon=horizon)
    print("\n今天的權重：" + "、".join(
        f"{pm.MODEL_NAMES[m]} {v:.0%}" for m, v in sorted(w.items(), key=lambda x: -x[1]))
          + f"（依最近 {info['based_on']} 次、預測 {info['horizon']} 天後的回測）")


if __name__ == "__main__":
    from user_management import UserManager
    print_report(UserManager(), sys.argv[1] if len(sys.argv) > 1 else "demo",
                 int(sys.argv[2]) if len(sys.argv) > 2 else 14)
