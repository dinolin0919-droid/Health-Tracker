"""
模擬資料產生器：在真實資料累積夠之前，先給 C、D、E 開發和測試用

做法：用一個「看不見的真實身體」產生資料，再像真人一樣「不完美地記錄」下來。
  真實身體（比 C 組三個模型都複雜，所以沒有哪個模型會剛好猜中）：
    - 脂肪、瘦體組織兩個部分，依 Forbes 曲線分配（Hall 2011 的做法）
    - 開始節食時肝醣和水分會先掉 0.5～1 公斤
    - 代謝適應：赤字越久，身體消耗越少
  記錄方式（模仿真人）：
    - 飲食：--style meals 只用快速記錄（每餐少／普通／多）、foods 只用食物清單、
            mixed（預設）大約 65% 的天快速記錄、其他天食物清單
    - 飲食少記約 10%（研究上常見的低報），而且有些天忘了記
    - 體重每天有水分波動，週末後會偏重，大約 85% 的天有量，偶爾手滑打錯
    - 手機：步數、活動熱量、靜態熱量

情境（預設 12 週）：
    第 1～2 週維持飲食 → 第 3～10 週每天少吃約 500 大卡、每週快走 3 次 → 第 11～12 週稍微放鬆

用法：
    python simulate_data.py                     # 建立帳號 demo（密碼 demo1234）
    python simulate_data.py --user demo2 --seed 7 --weeks 16
    python simulate_data.py --force             # 帳號已存在時清掉重建紀錄
    python simulate_data.py --style meals       # 全部用快速記錄

產生的東西：
    data/ 裡的 demo 帳號：跟真人帳號一樣，用 A、B 的函式讀取
    data/simulated_truth_<帳號>.csv：每天的「真實」體重、脂肪、瘦體組織、真實攝取
        （真實體重另外存一份在資料庫，雲端上的 app 也看得到）
    要產生到雲端：python simulate_data.py --cloud（設定見 README.md）
        → D 組可以拿模型預測跟「量到的」和「真實的」體重各比一次
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import random
from datetime import date, timedelta

from data_processing import (EXERCISE_MET, FOOD_TABLE, bmr_mifflin, exercise_kcal,
                             recalc)
from user_management import (MEAL_LEVELS, MEAL_NAMES, DailyRecord, UserManager,
                             cloud_storage_from_secrets)

RHO_F, RHO_L = 9400, 1800          # 每公斤脂肪 / 瘦體組織的能量（大卡），Hall 2011
FORBES_C = 10.4 * RHO_L / RHO_F     # Forbes 分配常數

# 組餐用：(食物, 份數)
MEALS = {
    "早餐": [[("蛋餅", 1), ("無糖豆漿", 1)], [("飯糰", 1)], [("吐司", 2), ("雞蛋", 1), ("鮮奶", 1)],
             [("御飯糰", 1), ("茶葉蛋", 1)], [("燒餅油條", 1), ("無糖豆漿", 1)]],
    "午餐": [[("雞腿便當", 1)], [("排骨便當", 1)], [("鯖魚便當", 1)], [("牛肉麵", 1)],
             [("水餃", 10), ("青菜", 1)], [("滷肉飯", 1), ("青菜", 1), ("茶葉蛋", 1)]],
    "晚餐": [[("白飯", 1), ("雞胸肉", 1.5), ("青菜", 1)], [("炒飯", 1)], [("牛肉麵", 1)],
             [("鍋貼", 10)], [("糙米飯", 1), ("鮭魚", 1), ("青菜", 1)], [("火鍋", 1)]],
    "點心": [[("珍珠奶茶", 1)], [("香蕉", 1)], [("蘋果", 1)], [("堅果", 1)], [("雞排", 1)],
             [("拿鐵", 1)], [("可樂", 1)]],
}


def _kcal(items):
    return sum(FOOD_TABLE[f][0] * n for f, n in items)


def pick_foods(target: float, rng: random.Random) -> dict:
    """組出一天的飲食，總熱量盡量接近 target"""
    best, best_err = None, 1e9
    for _ in range(60):
        day = [rng.choice(MEALS[m]) for m in ("早餐", "午餐", "晚餐")]
        if rng.random() < 0.7:
            day.append(rng.choice(MEALS["點心"]))
        if rng.random() < 0.25:
            day.append(rng.choice(MEALS["點心"]))
        err = abs(_kcal([x for meal in day for x in meal]) - target)
        if err < best_err:
            best, best_err = day, err
    foods = {}
    for meal in best:
        for f, n in meal:
            foods[f] = foods.get(f, 0) + n
    # 微調份數讓總數更接近（例如白飯半碗）
    total = _kcal(foods.items())
    if "白飯" in foods or "糙米飯" in foods:
        rice = "白飯" if "白飯" in foods else "糙米飯"
        adj = round((target - total) / FOOD_TABLE[rice][0] * 2) / 2
        foods[rice] = max(0.5, foods[rice] + max(-0.5, min(1.0, adj)))
    return foods


def pick_meals(true_intake: float, maintenance: float, rng: random.Random) -> dict:
    """模擬一般人憑感覺選「少／普通／多」：跟自己平常比，而且會稍微往少的方向估"""
    ratio = true_intake / maintenance * 0.93          # 低報
    cuts = [(0.35, "沒吃"), (0.85, "少"), (1.17, "普通"), (1.55, "多")]

    def level(x):
        return next((lv for c, lv in cuts if x < c), "大餐")

    out = {}
    for m in MEAL_NAMES:
        if m == "早餐" and rng.random() < 0.1:
            out[m] = "沒吃"
        elif m == "點心":
            out[m] = level(rng.gauss(1.0, 0.3)) if rng.random() < 0.5 * ratio else "沒吃"
        else:
            out[m] = level(ratio * rng.gauss(1.0, 0.18) + rng.gauss(0, 0.08))
    assert set(out.values()) <= set(MEAL_LEVELS)
    return out


def simulate(um: UserManager, username="demo", weeks=12, seed=42, force=False, style="mixed",
             sex="女", birth="2000-09-19", height=165.0, weight0=68.0, fat_pct=0.32):
    rng = random.Random(seed)
    days = weeks * 7
    start = date.today() - timedelta(days=days)

    if um.exists(username):
        if not force:
            raise SystemExit(f"帳號 {username} 已經存在，要重建請加 --force")
        for r in um.get_records(username):
            um.delete_record(username, r.date)
        um.update_profile(username, sex=sex, birth_date=birth, height_cm=height,
                          initial_weight_kg=weight0, activity_level=2, goal_weight_kg=weight0 - 6,
                          display_name="模擬資料")
        print(f"清掉 {username} 舊的紀錄，重新產生")
    else:
        um.register(username, "demo1234", sex=sex, birth_date=birth, height_cm=height,
                    weight_kg=weight0, activity_level=2, goal_weight_kg=weight0 - 6,
                    display_name="模擬資料")
        um.delete_record(username, date.today().isoformat())   # 註冊時自動加的今天體重不要
        print(f"建立帳號 {username}（密碼 demo1234）")

    prof = um.get_profile(username)
    F = weight0 * fat_pct            # 脂肪
    L = weight0 - F                  # 瘦體組織（含水）
    glyco = 0.0                      # 肝醣 + 水分的偏移（公斤）
    adapt = 0.0                      # 代謝適應（大卡/天）
    pal = 1.4                        # 真實活動係數（跟使用者填的 1.375 不完全一樣）
    maintenance = bmr_mifflin(sex, weight0, height, prof.age) * pal

    truth_rows = []
    for i in range(days):
        d = start + timedelta(days=i)
        ds = d.isoformat()
        week = i // 7 + 1
        W = F + L
        age = prof.age

        # ── 情境：這天打算吃多少、要不要運動 ──
        if week <= 2:
            plan = maintenance
        elif week <= weeks - 2:
            plan = maintenance - 500
        else:
            plan = maintenance - 200
        if d.weekday() >= 5:                        # 週末吃比較多
            plan += rng.gauss(350, 150)
        true_intake = max(900, rng.gauss(plan, 180))
        exercises = {}
        if week > 2 and d.weekday() in (0, 2, 4) and rng.random() < 0.85:
            exercises["快走"] = rng.choice([30, 40, 45])
        if week > 4 and d.weekday() == 5 and rng.random() < 0.6:
            exercises["慢跑"] = rng.choice([20, 30])

        # ── 真實身體 ──
        ex_true = sum(exercise_kcal(EXERCISE_MET[k], W, m) for k, m in exercises.items())
        bmr_true = bmr_mifflin(sex, W, height, age) * rng.gauss(1.0, 0.02)
        ee = bmr_true * pal + ex_true - adapt + 0.1 * (true_intake - maintenance)  # 食物產熱
        balance = true_intake - ee
        target_adapt = max(0.0, -balance) * 0.15                   # 赤字越大、適應越多
        adapt += (target_adapt - adapt) / 21                       # 大約 3 週慢慢形成
        target_gly = -0.8 if plan < maintenance - 300 else (-0.3 if plan < maintenance else 0.0)
        glyco += (target_gly - glyco) / 4                          # 肝醣幾天內就變
        p = FORBES_C / (FORBES_C + F)
        F += (1 - p) * balance / RHO_F
        L += p * balance / RHO_L
        true_w = F + L + glyco

        # ── 量到的體重 ──
        water = rng.gauss(0, 0.35) + (0.4 if d.weekday() in (0, 1) else 0)   # 週末後偏重
        measured = round(true_w + water, 1) if rng.random() < 0.85 else None
        if measured is not None and rng.random() < 0.02:
            measured = round(measured + rng.choice([-1, 1]) * rng.uniform(2.5, 4), 1)  # 打錯

        # ── 記下來的飲食（少記約 10%，約 7% 的天忘了記，但不會連續兩天都忘）──
        forgot = rng.random() < 0.07 and not (truth_rows and truth_rows[-1]["logged_intake"] is None)
        quick = style == "meals" or (style == "mixed" and rng.random() < 0.65)
        foods, meals = {}, {}
        if not forgot and quick:
            meals = pick_meals(true_intake, maintenance, rng)
        elif not forgot:
            foods = pick_foods(true_intake * rng.gauss(0.9, 0.05), rng)

        # ── 手機 ──
        steps = max(500, int(rng.gauss(7500, 2200) + (4000 if exercises else 0)))
        active = steps * 0.035 * W / 60 + ex_true * 0.6
        basal = bmr_true * rng.gauss(1.0, 0.03)
        if rng.random() < 0.03:                                    # 手機沒電
            steps, active, basal = int(steps * 0.3), active * 0.3, basal * 0.3

        rec = DailyRecord(ds, weight_kg=measured, foods=foods, meals=meals, exercises=exercises,
                          steps=steps, active_kcal=round(active), basal_kcal=round(basal),
                          note="模擬資料", source="manual")
        rec = recalc(um, username, rec)
        um.save_record(username, rec, merge=False)

        truth_rows.append({"date": ds, "true_weight": round(true_w, 3), "fat_kg": round(F, 3),
                           "lean_kg": round(L, 3), "glycogen_water_kg": round(glyco, 3),
                           "true_intake": round(true_intake), "true_expenditure": round(ee),
                           "logged_intake": rec.intake_kcal,
                           "log_style": "meals" if meals else ("foods" if foods else "none"),
                           "measured_weight": measured,
                           "adaptation_kcal": round(adapt)})

    um.storage.put(f"truth:{username}", {r["date"]: r["true_weight"] for r in truth_rows})
    path = os.path.join(um.data_dir, f"simulated_truth_{username}.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(truth_rows[0]))
        w.writeheader()
        w.writerows(truth_rows)
    return truth_rows, path


def main():
    ap = argparse.ArgumentParser(description="產生模擬的飲食、運動、體重資料")
    ap.add_argument("--user", default="demo")
    ap.add_argument("--weeks", type=int, default=12)
    ap.add_argument("--seed", type=int, default=42, help="同一個 seed 每次產生一樣的資料")
    ap.add_argument("--force", action="store_true", help="帳號已存在時清掉重建")
    ap.add_argument("--style", choices=["mixed", "meals", "foods"], default="mixed",
                    help="飲食記錄方式（預設 mixed）")
    ap.add_argument("--cloud", action="store_true",
                    help="產生到雲端資料庫（讀 .streamlit/secrets.toml 或環境變數）")
    args = ap.parse_args()

    storage = cloud_storage_from_secrets() if args.cloud else None
    um = UserManager(storage=storage, cache=True)      # 放上雲端時可以少很多次連線
    rows, path = simulate(um, args.user, args.weeks, args.seed, args.force, args.style)
    first, last = rows[0], rows[-1]
    measured = [r for r in rows if r["measured_weight"] is not None]
    logged = [r for r in rows if r["logged_intake"]]
    under = sum(r["logged_intake"] for r in logged) / sum(r["true_intake"] for r in logged)
    print(f"\n產生 {len(rows)} 天（{first['date']} ～ {last['date']}）")
    print(f"真實體重：{first['true_weight']:.1f} → {last['true_weight']:.1f} kg"
          f"（脂肪 {first['fat_kg']:.1f} → {last['fat_kg']:.1f}）")
    quick = sum(r["log_style"] == "meals" for r in rows)
    print(f"有量體重 {len(measured)} 天、有記飲食 {len(logged)} 天（快速記錄 {quick} 天），"
          f"記下的熱量約是真實的 {under:.0%}")
    print(f"真實答案存在：{path}")
    print(f"\n用 data_processing.py 登入 {args.user}／demo1234，選「8 資料品質與校正」就能看到整理後的資料和校正係數。")


if __name__ == "__main__":
    main()
