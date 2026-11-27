"""
C｜Prediction models 體重預測

三個模型，同一個介面：給「起始體重」和「每天的攝取、運動」，回傳每天的預測體重。

  1. Wishnofsky（1958）靜態模型：7700 大卡 ≈ 1 公斤，消耗固定在起始值
         W(t) = W0 + Σ(EI − EE0) / 7700
  2. Antonetti 型（1973）動態能量平衡：消耗隨體重線性變小
         dW/dt = (EI − EE(W)) / 7700，EE(W) = PAL × (10W + 6.25H − 5A + s) + 運動
         EI 固定時的解：W(t) = −β/α + (W0 + β/α)·e^{αt}
  3. Hall（2011）簡化版：脂肪、瘦體組織兩個部分，加上食物產熱與代謝適應
         dF/dt = (1−p)·G / ρF，dL/dt = p·G / ρL，p = C / (C + F)（Forbes 分配）
     參數來自 Hall et al., Lancet 2011;378:826–37。完整版還有肝醣、細胞外水分，這裡省略。

────────────────────────────────────────
給其他組員的用法
────────────────────────────────────────
    from prediction_models import MODELS, Body, forecast, hindcast

    body = Body(sex="女", age=26, height_cm=175, pal=1.375)
    w = MODELS["hall"](60.0, intakes=[1600] * 84, exercises=[150] * 84, body=body)
    w[0] = 起始體重，w[84] = 84 天後

    forecast(um, "test", days=84)        # 從最新體重往後預測（E 組畫圖用）
    hindcast(um, "demo", start, end)     # 用過去實際吃的去「重演」，跟真實體重比（D 組用）
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

RHO = 7700                 # 靜態模型：每公斤體重的能量
RHO_F, RHO_L = 9400, 1800  # 每公斤脂肪 / 瘦體組織的能量（Hall 2011）
ETA_F, ETA_L = 180, 230    # 合成脂肪 / 瘦體組織的額外成本
GAMMA_F, GAMMA_L = 3.2, 22  # 脂肪 / 瘦體組織每公斤每天的代謝
BETA_TEF, BETA_AT = 0.1, 0.14  # 食物產熱、代謝適應
FORBES_C = 10.4 * RHO_L / RHO_F

MODEL_NAMES = {
    "wishnofsky": "Wishnofsky 靜態模型",
    "antonetti": "Antonetti 動態模型",
    "hall": "Hall 生理模型（簡化）",
}


@dataclass
class Body:
    sex: str           # "男" / "女"
    age: int
    height_cm: float
    pal: float         # 活動係數（不含另外記錄的運動）

    def rmr(self, w: float) -> float:
        """靜止代謝，Mifflin-St Jeor"""
        return 10 * w + 6.25 * self.height_cm - 5 * self.age + (5 if self.sex == "男" else -161)

    def tdee(self, w: float) -> float:
        return self.rmr(w) * self.pal

    def fat_fraction(self, w: float) -> float:
        """用 BMI 估體脂率（Deurenberg 1991），Hall 模型需要起始脂肪量"""
        bmi = w / (self.height_cm / 100) ** 2
        bf = 1.2 * bmi + 0.23 * self.age - 10.8 * (self.sex == "男") - 5.4
        return min(0.6, max(0.05, bf / 100))


def _check(intakes, exercises):
    exercises = list(exercises) if exercises is not None else [0.0] * len(intakes)
    if len(exercises) != len(intakes):
        raise ValueError("intakes 和 exercises 的天數要一樣")
    return list(intakes), exercises


# ───────────────────────── 1. Wishnofsky ─────────────────────────
def wishnofsky(w0: float, intakes, exercises=None, body: Body = None) -> list[float]:
    intakes, exercises = _check(intakes, exercises)
    ee0 = body.tdee(w0)                      # 消耗固定在起始值，不會因為變瘦而減少
    out, w = [w0], w0
    for ei, ex in zip(intakes, exercises):
        w += (ei - ee0 - ex) / RHO
        out.append(w)
    return out


# ───────────────────────── 2. Antonetti 型 ─────────────────────────
def antonetti(w0: float, intakes, exercises=None, body: Body = None) -> list[float]:
    """dW/dt = αW + β，每天用解析解前進一天（攝取每天不同也可以）"""
    intakes, exercises = _check(intakes, exercises)
    alpha = -10 * body.pal / RHO                          # 每多 1 公斤，消耗多 10×PAL 大卡
    const = (6.25 * body.height_cm - 5 * body.age + (5 if body.sex == "男" else -161)) * body.pal
    out, w = [w0], w0
    for ei, ex in zip(intakes, exercises):
        beta = (ei - const - ex) / RHO
        w = -beta / alpha + (w + beta / alpha) * math.exp(alpha)
        out.append(w)
    return out


# ───────────────────────── 3. Hall 簡化版 ─────────────────────────
def hall(w0: float, intakes, exercises=None, body: Body = None,
         fat0: float | None = None, return_parts: bool = False):
    """脂肪 F、瘦體組織 L 兩個部分，每天前進一步。

    假設起始時是「維持體重」的狀態：當時的攝取 EI0 = 公式算的每日消耗。
    消耗 EE = K + γF·F + γL·L + δ·W + β_TEF·EI + β_AT·(EI − EI0) + 運動 + 合成組織的成本
    """
    intakes, exercises = _check(intakes, exercises)
    F = fat0 if fat0 is not None else w0 * body.fat_fraction(w0)
    L = w0 - F
    rmr0, ei0 = body.rmr(w0), body.tdee(w0)
    K = rmr0 - GAMMA_F * F - GAMMA_L * L                   # 讓起始的靜止代謝 = Mifflin
    delta = (ei0 * (1 - BETA_TEF) - rmr0) / w0             # 日常活動每公斤的消耗

    out, parts = [w0], [(F, L)]
    for ei, ex in zip(intakes, exercises):
        w = F + L
        ee_base = (K + GAMMA_F * F + GAMMA_L * L + delta * w + BETA_TEF * ei
                   + BETA_AT * (ei - ei0) + ex * w / w0)
        p = FORBES_C / (FORBES_C + F)
        c = ETA_F * (1 - p) / RHO_F + ETA_L * p / RHO_L    # 合成／分解組織的額外成本
        g = (ei - ee_base) / (1 + c)
        F = max(0.5, F + (1 - p) * g / RHO_F)
        L = L + p * g / RHO_L
        out.append(F + L)
        parts.append((F, L))
    return (out, parts) if return_parts else out


MODELS = {"wishnofsky": wishnofsky, "antonetti": antonetti, "hall": hall}


def predict_all(w0, intakes, exercises, body) -> dict[str, list[float]]:
    return {name: f(w0, intakes, exercises, body) for name, f in MODELS.items()}


def days_to_reach(weights: list[float], target: float) -> int | None:
    """第幾天會到 target（往下或往上都可以）；到不了回傳 None"""
    w0 = weights[0]
    for i, w in enumerate(weights):
        if (target < w0 and w <= target) or (target > w0 and w >= target):
            return i
    return None


# ───────────────────────── 接上 A、B 的資料 ─────────────────────────
def body_of(um, username: str) -> Body:
    p = um.get_profile(username)
    return Body(p.sex, p.age, p.height_cm, p.activity_factor)


def recent_averages(rows: list[dict], days: int = 14) -> tuple[float | None, float]:
    """最近 days 天、可用日子的平均（校正後攝取, 運動）"""
    use = [r for r in rows[-days * 2:] if r["usable"] and r["intake_corrected"] is not None][-days:]
    if not use:
        return None, 0.0
    ei = sum(r["intake_corrected"] for r in use) / len(use)
    ex = sum(r["exercise_kcal"] or 0 for r in use) / len(use)
    return ei, ex


def forecast(um, username: str, days: int = 84, intake: float | None = None,
             exercise: float | None = None) -> dict:
    """從最新的體重（7 天平均）往後預測 days 天。

    intake、exercise 不給的話，用最近 14 天的平均（校正後攝取）；
    都沒有飲食紀錄時，用公式的每日消耗（＝維持體重）。
    回傳 {"start": 日期, "w0": 起始體重, "intake": ..., "exercise": ..., "dates": [...],
          "wishnofsky": [...], "antonetti": [...], "hall": [...]}
    """
    from data_processing import build_dataset
    rows = build_dataset(um, username)
    body = body_of(um, username)
    smooth = [r for r in rows if r["weight_smooth"] is not None]
    w0 = smooth[-1]["weight_smooth"] if smooth else um.latest_weight(username)
    avg_ei, avg_ex = recent_averages(rows)
    ei = intake if intake is not None else (avg_ei if avg_ei is not None else body.tdee(w0))
    ex = exercise if exercise is not None else avg_ex
    start = date.today()
    res = predict_all(w0, [ei] * days, [ex] * days, body)
    res.update(start=start.isoformat(), w0=w0, intake=ei, exercise=ex,
               intake_source="custom" if intake is not None else ("recent" if avg_ei else "maintenance"),
               dates=[(start + timedelta(days=i)).isoformat() for i in range(days + 1)])
    return res


def hindcast(um, username: str, start: str, end: str) -> dict:
    """用 start～end 實際「校正後攝取」和運動去重演，跟量到的體重比。

    沒記飲食的日子用期間平均補上。起點用 start 那天的 7 天平均體重。
    回傳 {"dates", "actual"(量到的，沒量是 None), "smooth", 各模型預測, "mae"{模型: 平均絕對誤差}}
    """
    from data_processing import build_dataset
    rows = [r for r in build_dataset(um, username, start=start, end=end)]
    if len(rows) < 2:
        raise ValueError("這段期間的資料太少")
    by_date = {r["date"]: r for r in rows}
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    dates = [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    known = [r["intake_corrected"] for r in rows if r["usable"] and r["intake_corrected"] is not None]
    if not known:
        raise ValueError("這段期間沒有飲食紀錄")
    fill = sum(known) / len(known)

    def ei_of(d):
        r = by_date.get(d)
        return r["intake_corrected"] if r and r["usable"] and r["intake_corrected"] is not None else fill

    intakes = [ei_of(d) for d in dates[:-1]]
    exercises = [(by_date.get(d) or {}).get("exercise_kcal") or 0 for d in dates[:-1]]
    first = by_date.get(start) or rows[0]
    w0 = first["weight_smooth"] or first["weight_clean"]
    if w0 is None:
        raise ValueError("起始日附近沒有體重紀錄")
    res = predict_all(w0, intakes, exercises, body_of(um, username))
    actual = [(by_date.get(d) or {}).get("weight_clean") for d in dates]
    smooth = [(by_date.get(d) or {}).get("weight_smooth") for d in dates]
    mae = {}
    for name in MODELS:
        errs = [abs(p - a) for p, a in zip(res[name], actual) if a is not None]
        mae[name] = sum(errs) / len(errs) if errs else None
    res.update(dates=dates, actual=actual, smooth=smooth, mae=mae, w0=w0)
    return res
