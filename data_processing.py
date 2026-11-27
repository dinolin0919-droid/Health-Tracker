"""
B｜Data processing 資料處理

負責：
  1. 熱量計算：
       詳細記錄：飲食 {種類: 份數} → 攝取熱量；運動 {種類: 分鐘} → 運動消耗
       快速記錄：每餐「沒吃／少／普通／多／大餐」→ 用每日消耗估算熱量
  2. 資料清理：標出不完整或異常的日子（今天還沒過完、手機沒記錄、體重異常跳動…）
  3. 每日消耗估算：公式（BMR × 活動係數 + 運動）和手機（靜態 + 活動熱量）兩種
  4. 體重校正：用過去 4 週的體重變化，算出「記錄的攝取要乘多少才對得上體重」
  5. 整理成一張表給 C、D、E 用，也可以匯出 CSV

────────────────────────────────────────
給其他組員的用法
────────────────────────────────────────
    from user_management import UserManager
    from data_processing import build_dataset, export_csv

    um = UserManager()
    rows = build_dataset(um, "test", start="2026-11-01")   # list[dict]，一天一列
    for r in rows:
        if r["usable"]:            # C、D 只用這些天
            r["date"], r["intake_corrected"], r["tdee_formula"], r["weight_smooth"]

    export_csv(um, "test")         # 存成 data/processed_test.csv（Excel 可直接開）

欄位說明見 COLUMNS。
單獨執行 `python data_processing.py` 會出現文字選單：每天量體重、快速記錄三餐（或同昨天），
想精確時再用詳細記錄；「資料品質與校正」可以看體重校正的結果。
"""

from __future__ import annotations

import csv
import json
import os
from datetime import date, timedelta
from statistics import median

from user_management import MEAL_LEVELS, MEAL_NAMES, DailyRecord, UserError, UserManager

# ───────────────────────── 熱量表 ─────────────────────────
# 種類: (每份大卡, 一份是多少)，數值為常見份量的約略值
FOOD_TABLE = {
    # 主食
    "白飯": (280, "1 碗 200g"), "糙米飯": (260, "1 碗 200g"), "滷肉飯": (400, "小碗"),
    "炒飯": (700, "1 盤"), "炒麵": (650, "1 盤"), "陽春麵": (350, "1 碗"),
    "牛肉麵": (650, "1 碗"), "水餃": (50, "1 顆"), "鍋貼": (60, "1 顆"),
    "吐司": (140, "1 片"), "饅頭": (280, "1 個"), "地瓜": (180, "1 條 200g"),
    "泡麵": (450, "1 包"),
    # 便當、小吃
    "雞腿便當": (850, "1 個"), "排骨便當": (900, "1 個"), "鯖魚便當": (750, "1 個"),
    "蛋餅": (250, "1 份"), "飯糰": (450, "1 個"), "御飯糰": (200, "1 個"),
    "燒餅油條": (500, "1 套"), "蔥油餅": (400, "1 片"), "雞排": (650, "1 片"),
    "鹽酥雞": (600, "1 份"), "臭豆腐": (500, "1 份"), "火鍋": (900, "1 套"),
    # 蛋白質
    "雞蛋": (75, "1 顆"), "茶葉蛋": (75, "1 顆"), "雞胸肉": (165, "100g"),
    "鮭魚": (200, "100g"), "豆腐": (90, "100g"), "牛排": (450, "200g"),
    # 蔬果
    "青菜": (80, "1 份 炒/燙"), "沙拉": (250, "1 份 含醬"), "香蕉": (100, "1 根"),
    "蘋果": (120, "1 顆"), "芭樂": (110, "1 顆"), "堅果": (180, "1 小把 30g"),
    # 飲料
    "珍珠奶茶": (650, "700ml 全糖"), "珍珠奶茶半糖": (500, "700ml"), "無糖豆漿": (170, "500ml"),
    "含糖豆漿": (260, "500ml"), "鮮奶": (150, "240ml"), "拿鐵": (200, "中杯"),
    "美式咖啡": (10, "1 杯"), "可樂": (140, "330ml"), "啤酒": (140, "330ml"),
}

# 種類: MET（Compendium of Physical Activities 的常見值）
EXERCISE_MET = {
    "散步": 3.0, "快走": 4.3, "慢跑": 8.3, "跑步": 9.8, "騎腳踏車": 6.0,
    "飛輪": 8.5, "游泳": 6.0, "重訓": 5.0, "HIIT": 8.0, "有氧舞蹈": 7.3,
    "瑜伽": 2.5, "跳繩": 11.8, "爬樓梯": 8.0, "登山": 6.0, "羽球": 5.5,
    "桌球": 4.0, "籃球": 6.5, "家事": 3.3,
}

# 快速記錄：每一餐「普通」佔每日消耗的比例，以及各等級的倍數
MEAL_SHARE = {"早餐": 0.25, "午餐": 0.35, "晚餐": 0.30, "點心": 0.10}
MEAL_MULT = {"沒吃": 0.0, "少": 0.7, "普通": 1.0, "多": 1.35, "大餐": 1.8}
assert set(MEAL_SHARE) == set(MEAL_NAMES) and set(MEAL_MULT) == set(MEAL_LEVELS)

CALIB_WINDOW = 28          # 體重校正用過去幾天
KCAL_PER_KG = 7700

# build_dataset 輸出的欄位
COLUMNS = {
    "date": "日期",
    "weight_kg": "當天量的體重（沒量是 None）",
    "weight_clean": "體重，異常值已移除",
    "weight_smooth": "近 7 天體重平均（減少水分波動），C、D 建議用這個",
    "intake_kcal": "記錄的攝取熱量（食物清單＋快速記錄）",
    "intake_method": "記錄方式：foods（食物清單）/ meals（快速記錄）/ mixed / manual（直接填總數）",
    "intake_factor": "校正係數：用過去 28 天體重算出，記錄的攝取要乘上多少才對得上體重（資料不夠是 None）",
    "intake_corrected": "校正後的攝取 = intake_kcal × intake_factor（沒有係數就等於 intake_kcal），C、D 建議用這個",
    "exercise_kcal": "運動消耗（已扣除靜止時本來的消耗）",
    "steps": "步數（健康 app）",
    "active_kcal": "活動熱量（健康 app）",
    "basal_kcal": "靜態熱量（健康 app）",
    "bmr_formula": "基礎代謝，Mifflin-St Jeor 公式",
    "tdee_formula": "每日總消耗 = BMR × 活動係數 + 運動",
    "tdee_phone": "每日總消耗 = 靜態 + 活動熱量（手機估計，資料完整才有）",
    "balance_kcal": "攝取 − 每日總消耗（公式），負數 = 赤字",
    "flags": "資料問題，用「;」分隔",
    "usable": "能不能給模型用（有攝取熱量、而且沒有嚴重問題）",
}

FLAG_TEXT = {
    "today_partial": "今天還沒過完",
    "phone_incomplete": "手機紀錄不完整（靜態熱量太低）",
    "no_intake": "沒有飲食紀錄",
    "intake_implausible": "攝取熱量不合理（<500 或 >6000）",
    "weight_outlier": "體重跟前後幾天差太多",
    "unknown_food": "有不在熱量表裡的食物",
}
BLOCKING = {"today_partial", "no_intake", "intake_implausible"}   # 有這些就不給模型用


# ───────────────────────── 自訂食物、運動 ─────────────────────────
def load_tables(um: UserManager):
    """內建表 + 使用者自己加的（大家共用）。回傳 (food_table, exercise_met)"""
    foods, mets = dict(FOOD_TABLE), dict(EXERCISE_MET)
    for kind, target in (("foods", foods), ("exercises", mets)):
        for k, v in (um.storage.get(f"custom:{kind}") or {}).items():
            target[k] = tuple(v) if kind == "foods" else float(v)
    return foods, mets


def add_custom_food(um: UserManager, name: str, kcal: float, unit: str = "1 份"):
    _add_custom(um, "foods", name, [float(kcal), unit])


def add_custom_exercise(um: UserManager, name: str, met: float):
    _add_custom(um, "exercises", name, float(met))


def _add_custom(um, kind, name, value):
    name = name.strip()
    if not name or len(name) > 30:
        raise ValueError("名稱要 1～30 個字")
    data = um.storage.get(f"custom:{kind}") or {}
    data[name] = value
    um.storage.put(f"custom:{kind}", data)


def find_food(name: str, table: dict) -> list[str]:
    """完全相同就回傳 [它]；否則回傳名字裡有包含的候選"""
    if name in table:
        return [name]
    return [k for k in table if name in k or k in name]


# ───────────────────────── 熱量計算 ─────────────────────────
def intake_from_foods(foods: dict, table: dict) -> tuple[float, list[str]]:
    """回傳 (總大卡, 找不到的食物)"""
    total, unknown = 0.0, []
    for k, servings in foods.items():
        if k in table:
            total += table[k][0] * servings
        else:
            unknown.append(k)
    return total, unknown


def meal_kcal(meals: dict, base_tdee: float) -> float:
    """快速記錄的熱量：每日消耗 × 該餐比例 × 份量倍數"""
    return sum(base_tdee * MEAL_SHARE[m] * MEAL_MULT[lv] for m, lv in meals.items())


def suggest_meals(target_kcal: float, base: float) -> tuple[dict, float]:
    """用快速記錄的份量組出最接近 target_kcal 的三餐，回傳 ({餐別: 等級}, 大卡)。
    偏好：正餐盡量不要「沒吃」、少用「大餐」，點心可以不吃。"""
    import itertools
    best, best_score = None, 1e18
    for combo in itertools.product(MEAL_LEVELS, repeat=len(MEAL_NAMES)):
        meals = dict(zip(MEAL_NAMES, combo))
        kcal = meal_kcal(meals, base)
        score = abs(kcal - target_kcal)
        score += sum(150 for m, lv in meals.items() if m != "點心" and lv == "沒吃")
        score += sum(60 for lv in combo if lv == "大餐")
        score += 80 if meals["點心"] in ("多", "大餐") else 0              # 點心不建議吃多
        score += 20 * len({lv for m, lv in meals.items() if m != "點心"})   # 三餐份量越一致越好記
        if score < best_score:
            best, best_score = meals, score
    return best, meal_kcal(best, base)


def top_foods(um: UserManager, username: str, days: int = 14, n: int = 5) -> list[tuple[str, float, float]]:
    """最近 days 天「詳細記錄」裡熱量最高的食物：[(名稱, 總大卡, 每份大卡)]"""
    table, _ = load_tables(um)
    start = (date.today() - timedelta(days=days)).isoformat()
    total = {}
    for r in um.get_records(username, start=start):
        for f, q in r.foods.items():
            if f in table:
                total[f] = total.get(f, 0) + table[f][0] * q
    return [(f, kc, table[f][0]) for f, kc in sorted(total.items(), key=lambda x: -x[1])[:n]]


def base_tdee(um: UserManager, username: str, day: str) -> float:
    """「平常一天吃多少」的起始估計：BMR × 活動係數（不含運動）"""
    p = um.get_profile(username)
    return bmr_mifflin(p.sex, weight_on(um, username, day), p.height_cm,
                       _age_on(p.birth_date, day)) * p.activity_factor


def exercise_kcal(met: float, weight_kg: float, minutes: float) -> float:
    """(MET − 1) × 體重 × 小時：扣掉休息時本來就會燒的，避免跟 TDEE 重複算"""
    return max(0.0, (met - 1) * weight_kg * minutes / 60)


def exercise_from_dict(exercises: dict, weight_kg: float, mets: dict) -> tuple[float, list[str]]:
    total, unknown = 0.0, []
    for k, minutes in exercises.items():
        if k in mets:
            total += exercise_kcal(mets[k], weight_kg, minutes)
        else:
            unknown.append(k)
    return total, unknown


def bmr_mifflin(sex: str, weight_kg: float, height_cm: float, age: int) -> float:
    return 10 * weight_kg + 6.25 * height_cm - 5 * age + (5 if sex == "男" else -161)


def weight_on(um: UserManager, username: str, day: str) -> float:
    """那天（含）之前最近一次量的體重；都沒有就用註冊體重"""
    ws = [w for d, w in um.weight_history(username) if d <= day]
    return ws[-1] if ws else um.get_profile(username).initial_weight_kg


# ───────────────────────── 寫入：記錄飲食、運動 ─────────────────────────
def update_day(um: UserManager, username: str, day: str, *,
               add_foods: dict | None = None, add_exercises: dict | None = None,
               set_meals: dict | None = None, remove: list[str] | None = None) -> DailyRecord:
    """在某天加上（或移除）食物、運動，重新算熱量後存回 A。

    add_foods={"白飯": 1}：份數會「加上去」，不是覆蓋
    set_meals={"早餐": "少"}：快速記錄，同一餐再記一次會覆蓋
    remove=["白飯"]：從食物、運動、快速記錄裡移除（快速記錄用餐別，例如 "早餐"）
    規則：有 foods 或 meals 時，攝取熱量 = 兩者加總（覆蓋手動輸入的總數）；運動同理
    """
    rec = um.get_record(username, day) or DailyRecord(day)
    for k, v in (add_foods or {}).items():
        rec.foods[k] = round(rec.foods.get(k, 0) + float(v), 2)
    for k, v in (add_exercises or {}).items():
        rec.exercises[k] = round(rec.exercises.get(k, 0) + float(v), 1)
    rec.meals.update(set_meals or {})
    for k in remove or []:
        rec.foods.pop(k, None)
        rec.exercises.pop(k, None)
        rec.meals.pop(k, None)
    rec = recalc(um, username, rec)
    return um.save_record(username, rec, merge=False)


def recalc(um: UserManager, username: str, rec: DailyRecord) -> DailyRecord:
    """依 foods、meals、exercises 重算 intake_kcal、exercise_kcal（沒有明細的欄位不動）"""
    foods_t, mets = load_tables(um)
    if rec.foods or rec.meals:
        total = intake_from_foods(rec.foods, foods_t)[0]
        if rec.meals:
            total += meal_kcal(rec.meals, base_tdee(um, username, rec.date))
        rec.intake_kcal = round(total)
    if rec.exercises:
        w = rec.weight_kg or weight_on(um, username, rec.date)
        rec.exercise_kcal = round(exercise_from_dict(rec.exercises, w, mets)[0])
    return rec


def recalc_all(um: UserManager, username: str) -> int:
    """熱量表改過之後，把所有有明細的日子重算一次。回傳改了幾天"""
    n = 0
    for r in um.get_records(username):
        if r.foods or r.exercises or r.meals:
            before = (r.intake_kcal, r.exercise_kcal)
            r = recalc(um, username, r)
            if (r.intake_kcal, r.exercise_kcal) != before:
                um.save_record(username, r, merge=False)
                n += 1
    return n


def copy_day(um: UserManager, username: str, to_day: str, from_day: str | None = None) -> DailyRecord:
    """「同昨天」：把某天的飲食（食物清單＋快速記錄）複製到 to_day。
    from_day 不填就找 to_day 之前最近一天有記飲食的。運動不複製。"""
    if from_day is None:
        prev = [r for r in um.get_records(username, end=_shift(to_day, -1)) if r.foods or r.meals]
        if not prev:
            raise ValueError("之前沒有可以複製的飲食紀錄")
        src = prev[-1]
    else:
        src = um.get_record(username, from_day)
        if not src or not (src.foods or src.meals):
            raise ValueError(f"{from_day} 沒有飲食紀錄")
    rec = um.get_record(username, to_day) or DailyRecord(to_day)
    rec.foods, rec.meals = dict(src.foods), dict(src.meals)
    rec = recalc(um, username, rec)
    rec.note = (rec.note + f" 飲食同 {src.date}").strip()
    return um.save_record(username, rec, merge=False)


def _shift(day: str, n: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=n)).isoformat()


# ───────────────────────── 清理與整理 ─────────────────────────
def build_dataset(um: UserManager, username: str, start: str | None = None,
                  end: str | None = None) -> list[dict]:
    """一天一列的整理後資料（欄位見 COLUMNS）。中間沒紀錄的日子不會補列。"""
    prof = um.get_profile(username)
    foods_t, _ = load_tables(um)
    recs = um.get_records(username)          # 全部讀，算移動平均和中位數時需要前後資料
    today = date.today().isoformat()

    # 手機靜態熱量的「正常值」：用中位數，關機補記的日子會遠低於它
    basals = [r.basal_kcal for r in recs if r.basal_kcal and r.date < today]
    basal_med = median(basals) if basals else None

    # 體重異常：跟前後 3 天內其他體重的中位數差超過 2 公斤
    weights = [(r.date, r.weight_kg) for r in recs if r.weight_kg is not None]
    outliers = set()
    for d, w in weights:
        near = [w2 for d2, w2 in weights if d2 != d and abs(_days(d2, d)) <= 3]
        if near and abs(w - median(near)) > 2.0:
            outliers.add(d)
    clean_w = [(d, w) for d, w in weights if d not in outliers]

    rows, last_w = [], prof.initial_weight_kg
    for r in recs:
        flags = []
        if r.date == today:
            flags.append("today_partial")
        if basal_med and r.basal_kcal is not None and r.basal_kcal < 0.6 * basal_med:
            flags.append("phone_incomplete")
        if r.intake_kcal is None:
            flags.append("no_intake")
        elif not 500 <= r.intake_kcal <= 6000:
            flags.append("intake_implausible")
        if r.date in outliers:
            flags.append("weight_outlier")
        if r.foods and intake_from_foods(r.foods, foods_t)[1]:
            flags.append("unknown_food")

        w_clean = None if r.date in outliers else r.weight_kg
        if w_clean is not None:
            last_w = w_clean
        week = [w for d, w in clean_w if 0 <= _days(r.date, d) <= 6]
        bmr = bmr_mifflin(prof.sex, last_w, prof.height_cm, _age_on(prof.birth_date, r.date))
        ex = r.exercise_kcal or 0
        tdee_f = bmr * prof.activity_factor + ex
        phone_ok = (r.basal_kcal is not None and r.active_kcal is not None
                    and not {"today_partial", "phone_incomplete"} & set(flags))

        rows.append({
            "date": r.date,
            "weight_kg": r.weight_kg,
            "weight_clean": w_clean,
            "weight_smooth": round(sum(week) / len(week), 2) if week else None,
            "intake_kcal": r.intake_kcal,
            "intake_method": _method(r),
            "intake_factor": None,
            "intake_corrected": r.intake_kcal,
            "exercise_kcal": r.exercise_kcal,
            "steps": r.steps,
            "active_kcal": r.active_kcal,
            "basal_kcal": r.basal_kcal,
            "bmr_formula": round(bmr),
            "tdee_formula": round(tdee_f),
            "tdee_phone": round(r.basal_kcal + r.active_kcal) if phone_ok else None,
            "balance_kcal": round(r.intake_kcal - tdee_f) if r.intake_kcal is not None else None,
            "flags": ";".join(flags),
            "usable": not (BLOCKING & set(flags)),
        })

    # 體重校正：每一天的係數只用「那天以前」的資料算，D 組驗證時不會偷看到未來
    by_date = {x["date"]: x for x in rows}
    for x in rows:
        k = intake_factor(by_date, x["date"])
        x["intake_factor"] = k
        if k is not None and x["intake_kcal"] is not None:
            x["intake_corrected"] = round(x["intake_kcal"] * k)
    return [x for x in rows if not (start and x["date"] < start) and not (end and x["date"] > end)]


def _method(r: DailyRecord):
    if r.foods and r.meals:
        return "mixed"
    if r.foods:
        return "foods"
    if r.meals:
        return "meals"
    return "manual" if r.intake_kcal is not None else None


def intake_factor(by_date: dict, day: str, window: int = CALIB_WINDOW) -> float | None:
    """用 day 之前 window 天的資料，算「記錄的攝取 × k − 消耗 = 體重變化 × 7700」裡的 k。

    k > 1：記錄的比實際少（少記了），或實際消耗比公式低
    k < 1：記錄的比實際多，或實際消耗比公式高
    資料不夠（體重、飲食紀錄太少）就回傳 None。
    """
    end, start = _shift(day, -1), _shift(day, -1 - window)
    ws = by_date.get(start, {}).get("weight_smooth")
    we = by_date.get(end, {}).get("weight_smooth")
    if ws is None or we is None:
        return None
    present = [by_date[d] for d in (_shift(start, i) for i in range(1, window + 1)) if d in by_date]
    intakes = [x["intake_kcal"] for x in present if x["usable"] and x["intake_kcal"] is not None]
    if len(present) < 0.8 * window or len(intakes) < 0.5 * window:
        return None
    tdee_total = sum(x["tdee_formula"] for x in present) / len(present) * window
    k = (KCAL_PER_KG * (we - ws) + tdee_total) / (sum(intakes) / len(intakes) * window)
    return round(min(2.0, max(0.5, k)), 3)


def _days(a: str, b: str) -> int:
    """a − b 的天數"""
    return (date.fromisoformat(a) - date.fromisoformat(b)).days


def _age_on(birth: str, day: str) -> int:
    b, d = date.fromisoformat(birth), date.fromisoformat(day)
    return d.year - b.year - ((d.month, d.day) < (b.month, b.day))


def quality_report(rows: list[dict]) -> dict:
    """整理資料品質：幾天能用、各種問題各幾天、有幾天量體重"""
    counts = {}
    for r in rows:
        for f in filter(None, r["flags"].split(";")):
            counts[f] = counts.get(f, 0) + 1
    return {
        "days": len(rows),
        "usable": sum(r["usable"] for r in rows),
        "weight_days": sum(r["weight_clean"] is not None for r in rows),
        "flags": counts,
        "first": rows[0]["date"] if rows else None,
        "latest_factor": next((r["intake_factor"] for r in reversed(rows)
                               if r["intake_factor"] is not None), None),
        "last": rows[-1]["date"] if rows else None,
    }


def export_csv(um: UserManager, username: str, path: str | None = None, **kw) -> str:
    """存成 CSV（utf-8-sig，Excel 開中文不會亂碼）。回傳檔案路徑"""
    rows = build_dataset(um, username, **kw)
    path = path or os.path.join(um.data_dir, f"processed_{username}.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(COLUMNS))
        w.writeheader()
        w.writerows(rows)
    return path


# ───────────────────────── 文字選單 ─────────────────────────
def _input_items(prompt_kind, table, um, today, username):
    """一行一筆「種類 數量」，Enter 結束"""
    is_food = prompt_kind == "食物"
    unit = "份數" if is_food else "分鐘"
    print(f"一行輸入「{prompt_kind} {unit}」，例：{'白飯 1.5' if is_food else '快走 30'}；直接按 Enter 結束")
    items = {}
    while True:
        line = input("  > ").strip()
        if not line:
            return items
        parts = line.rsplit(maxsplit=1)
        if len(parts) != 2:
            print(f"  格式：{prompt_kind} {unit}")
            continue
        name, qty = parts
        try:
            qty = float(qty)
            if qty <= 0:
                raise ValueError
        except ValueError:
            print(f"  {unit}要是大於 0 的數字")
            continue
        if is_food:
            cands = find_food(name, table)
            if len(cands) == 1 and cands[0] != name:
                if input(f"  是「{cands[0]}」嗎？(y/n) ").strip().lower() == "y":
                    name = cands[0]
            elif len(cands) > 1 and name not in cands:
                print("  你是指：" + "、".join(cands[:8]) + "？請重新輸入")
                continue
            if name not in table:
                try:
                    kcal = float(input(f"  熱量表沒有「{name}」，每份幾大卡？ "))
                    u = input("  一份是多少（例：1 碗，可按 Enter 跳過）：").strip() or "1 份"
                    add_custom_food(um, name, kcal, u)
                    table[name] = (kcal, u)
                except ValueError:
                    print("  沒加入")
                    continue
            print(f"    {name} {qty:g} 份 × {table[name][0]:g} = {table[name][0] * qty:.0f} 大卡")
        else:
            if name not in table:
                print("  可選：" + "、".join(table))
                try:
                    met = float(input(f"  或輸入「{name}」的 MET 強度（不知道就填 5）："))
                    add_custom_exercise(um, name, met)
                    table[name] = met
                except ValueError:
                    continue
            w = weight_on(um, username, today)
            print(f"    {name} {qty:g} 分鐘 ≈ {exercise_kcal(table[name], w, qty):.0f} 大卡")
        items[name] = items.get(name, 0) + qty


def _show_day(rec):
    print(f"\n{rec.date}：攝取 {rec.intake_kcal or 0:.0f} 大卡，運動 {rec.exercise_kcal or 0:.0f} 大卡"
          + (f"，體重 {rec.weight_kg:g} kg" if rec.weight_kg else ""))
    for k, v in rec.meals.items():
        print(f"  吃  {k}：{v}")
    for k, v in rec.foods.items():
        print(f"  吃  {k} {v:g} 份")
    for k, v in rec.exercises.items():
        print(f"  動  {k} {v:g} 分鐘")


def _quick_meals():
    """每餐選份量，回傳 {餐別: 等級}"""
    print("每餐選份量：0 沒吃  1 少  2 普通  3 多  4 大餐（直接 Enter：正餐=普通、點心=沒吃；輸入 - 表示這餐不記）")
    out = {}
    for m in MEAL_NAMES:
        default = "沒吃" if m == "點心" else "普通"
        while True:
            s = input(f"  {m}：").strip()
            if s == "-":
                break
            if s == "":
                out[m] = default
                break
            if s in "01234" and len(s) == 1:
                out[m] = MEAL_LEVELS[int(s)]
                break
            if s in MEAL_LEVELS:
                out[m] = s
                break
            print("  請輸入 0～4")
    return out


def _menu(um, username):
    while True:
        today = date.today().isoformat()
        print(f"\n[{username}] 1量體重 2快速記錄飲食 3同昨天 4詳細記錄飲食 5記錄運動 "
              f"6今天明細 7刪除一項 8資料品質與校正 9匯出CSV 0登出")
        c = input("> ").strip()
        try:
            foods_t, mets = load_tables(um)
            if c == "1":
                w = float(input("今天體重 kg（起床、上完廁所後量最準）："))
                um.save_record(username, DailyRecord(today, weight_kg=w))
                print(f"已記錄 {w:g} kg")
            elif c == "2":
                meals = _quick_meals()
                if meals:
                    _show_day(update_day(um, username, today, set_meals=meals))
            elif c == "3":
                rec = copy_day(um, username, today)
                print(rec.note)
                _show_day(rec)
            elif c == "4":
                items = _input_items("食物", foods_t, um, today, username)
                if items:
                    _show_day(update_day(um, username, today, add_foods=items))
            elif c == "5":
                items = _input_items("運動", mets, um, today, username)
                if items:
                    _show_day(update_day(um, username, today, add_exercises=items))
            elif c == "6":
                rec = um.get_record(username, today)
                _show_day(rec) if rec else print("今天還沒有紀錄")
            elif c == "7":
                rec = um.get_record(username, today)
                if not rec or not (rec.foods or rec.exercises or rec.meals):
                    print("今天沒有可以刪的項目")
                    continue
                _show_day(rec)
                name = input("要刪哪一項（輸入名稱或餐別）：").strip()
                if name in rec.foods or name in rec.exercises or name in rec.meals:
                    _show_day(update_day(um, username, today, remove=[name]))
            elif c == "8":
                _report(um, username)
            elif c == "9":
                print("已存到", export_csv(um, username))
            elif c == "0":
                return
        except (UserError, ValueError) as e:
            print(f"  ✗ {e}")


def _report(um, username):
    rows = build_dataset(um, username)
    q = quality_report(rows)
    print(f"\n共 {q['days']} 天（{q['first']} ～ {q['last']}）")
    print(f"可以給模型用：{q['usable']} 天｜有量體重：{q['weight_days']} 天")
    for f, n in sorted(q["flags"].items(), key=lambda x: -x[1]):
        print(f"  {FLAG_TEXT.get(f, f)}：{n} 天")
    k = q["latest_factor"]
    if k is None:
        print("\n校正係數：資料還不夠（需要過去 4 週裡至少一半的天有記飲食，而且頭尾都有量體重）")
    else:
        if abs(k - 1) < 0.05:
            msg = "記錄和體重變化很一致"
        elif k > 1:
            msg = f"依體重變化，實際攝取大約比記錄多 {k - 1:.0%}（可能少記了，或實際消耗比公式低）"
        else:
            msg = f"依體重變化，實際攝取大約比記錄少 {1 - k:.0%}（可能記多了，或實際消耗比公式高）"
        print(f"\n校正係數 {k:.2f}：{msg}")
    if q["usable"] < 14 or q["weight_days"] < 7:
        print("※ 至少要 2 週的飲食紀錄、7 次以上的體重，分析才比較有意義。")
    print(f"\n{'日期':<12}{'記錄攝取':>8}{'校正後':>7}{'公式消耗':>8}{'體重':>7}  問題")
    for r in rows[-7:]:
        def f(v, fmt="{:.0f}"):
            return "—" if v is None else fmt.format(v)
        probs = "、".join(FLAG_TEXT[x] for x in r["flags"].split(";") if x)
        print(f"{r['date']:<12}{f(r['intake_kcal']):>10}{f(r['intake_corrected']):>9}"
              f"{f(r['tdee_formula']):>10}{f(r['weight_kg'], '{:.1f}'):>9}  {probs}")


def main():
    um = UserManager()
    from user_management import _ask_password
    while True:
        name = input("\n帳號（直接 Enter 離開）：").strip()
        if not name:
            break
        try:
            um.login(name, _ask_password())
        except UserError as e:
            print(f"  ✗ {e}（還沒有帳號的話，先執行 user_management.py 註冊）")
            continue
        _menu(um, name)


if __name__ == "__main__":
    main()
