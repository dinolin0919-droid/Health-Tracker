"""
讀取 iPhone「健康」app 匯出的資料

取得檔案：健康 app → 右上角大頭貼 → 輸出所有健康資料 → 得到 export.zip
（中文介面的 iPhone 會叫做 輸出.zip，裡面是 輸出.xml，兩種都能讀）
用法：
    在 VS Code 直接按右上角 ▷ 執行          # 自動找同資料夾的 export.zip / 輸出.xml
    python read_health.py export.zip                 # 讀 zip，不用先解壓縮
    python read_health.py export.xml --days 60       # 只看最近 60 天
    python read_health.py export.zip --out health.json

會讀出：
    基本資料：年齡（由出生日期換算）、性別、最新身高、最新體重
    每天：體重、活動熱量、步數、飲食熱量（有同步的話）
    運動：體能訓練紀錄（種類、分鐘、消耗）

在其他程式裡也可以直接用：
    from read_health import load_health
    data = load_health("export.zip")
"""

import argparse
import json
import os
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime, timedelta

# 要讀的紀錄類型
HEIGHT = "HKQuantityTypeIdentifierHeight"
WEIGHT = "HKQuantityTypeIdentifierBodyMass"
ACTIVE = "HKQuantityTypeIdentifierActiveEnergyBurned"
STEPS = "HKQuantityTypeIdentifierStepCount"
DIET = "HKQuantityTypeIdentifierDietaryEnergyConsumed"
BASAL = "HKQuantityTypeIdentifierBasalEnergyBurned"
WANTED = {HEIGHT, WEIGHT, ACTIVE, STEPS, DIET, BASAL}

# 健康 app 的運動種類 → 中文（盡量對到 diet_tracker.py 的 EXERCISE_MET）
WORKOUT_NAME = {
    "Walking": "快走", "Running": "跑步", "Cycling": "騎腳踏車",
    "Swimming": "游泳", "TraditionalStrengthTraining": "重訓",
    "FunctionalStrengthTraining": "重訓", "Yoga": "瑜伽",
    "Badminton": "羽球", "Basketball": "籃球", "StairClimbing": "爬樓梯",
    "JumpRope": "跳繩", "Hiking": "登山", "HighIntensityIntervalTraining": "HIIT",
    "Elliptical": "滑步機", "Dance": "舞蹈", "TableTennis": "桌球",
}


# ---------- 單位換算 ----------
def to_cm(v, unit):
    return {"cm": v, "m": v * 100, "in": v * 2.54, "ft": v * 30.48}.get(unit, v)


def to_kg(v, unit):
    return {"kg": v, "g": v / 1000, "lb": v * 0.453592}.get(unit, v)


def to_kcal(v, unit):
    return v / 4.184 if unit == "kJ" else v   # kcal、Cal 都是大卡


def age_from(birth, today=None):
    today = today or date.today()
    y, m, d = map(int, birth.split("-"))
    return today.year - y - ((today.month, today.day) < (m, d))


def split_by_day(start, end):
    """把一段時間按日期切開，回傳 [(日期, 佔比), ...]；同一天就是 [(日期, 1.0)]"""
    if start[:10] == end[:10]:
        return [(start[:10], 1.0)]
    try:
        s = datetime.strptime(start[:19], "%Y-%m-%d %H:%M:%S")
        e = datetime.strptime(end[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return [(start[:10], 1.0)]
    total = (e - s).total_seconds()
    if total <= 0:
        return [(start[:10], 1.0)]
    out, cur = [], s
    while cur < e:
        nxt = min(e, datetime.combine(cur.date() + timedelta(days=1), datetime.min.time()))
        out.append((cur.date().isoformat(), (nxt - cur).total_seconds() / total))
        cur = nxt
    return out


# ---------- 開檔 ----------
EXPORT_NAMES = ("export.zip", "輸出.zip", "export.xml", "輸出.xml")


def find_export(path):
    """path 可以是檔案或資料夾；資料夾的話自動找裡面的匯出檔"""
    path = os.path.expanduser(path.strip().strip('"').strip("'").replace("\\ ", " "))
    if os.path.isdir(path):
        for n in EXPORT_NAMES:
            if os.path.isfile(os.path.join(path, n)):
                return os.path.join(path, n)
        others = sorted(n for n in os.listdir(path)
                        if n.lower().endswith((".xml", ".zip")) and "cda" not in n.lower())
        if others:
            return os.path.join(path, others[0])
        raise FileNotFoundError(f"資料夾「{path}」裡找不到 export.zip 或 輸出.xml")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"找不到檔案：{path}")
    return path


def open_export(path):
    """回傳 export.xml 的檔案物件；zip、資料夾也可以"""
    path = find_export(path)
    if zipfile.is_zipfile(path):
        z = zipfile.ZipFile(path)
        # 英文介面叫 export.xml，中文介面叫 輸出.xml；另一個 *_cda.xml 不是我們要的
        names = [n for n in z.namelist() if n.endswith(".xml") and "cda" not in n.lower()
                 and "/workout-routes/" not in n]
        if not names:
            raise FileNotFoundError("zip 裡找不到 export.xml（或 輸出.xml）")
        names.sort(key=lambda n: n.count("/"))
        return z.open(names[0])
    return open(path, "rb")


# ---------- 主要讀取 ----------
def load_health(path, days=None):
    """讀健康 app 匯出檔，回傳 dict。days=只保留最近幾天（None=全部）"""
    since = (date.today() - timedelta(days=days)).isoformat() if days else "0000"

    profile = {"birth": None, "age": None, "sex": None, "height": None, "weight": None}
    last_h = last_w = ""                         # 最新身高、體重的時間
    weights = {}                                 # {日期: 當天最後一筆體重}
    # 手機和手錶都會記步數、熱量，直接加會重複算。
    # 先按「日期 → 來源」分開加總，最後每天取最大的那個來源。
    per_source = {k: defaultdict(lambda: defaultdict(float)) for k in (ACTIVE, STEPS, DIET, BASAL)}
    workouts = []

    with open_export(path) as f:
        # 檔案可能有好幾百 MB，用 iterparse 一筆一筆讀，讀完就清掉
        for _, el in ET.iterparse(f, events=("end",)):
            tag = el.tag
            if tag == "Me":
                birth = el.get("HKCharacteristicTypeIdentifierDateOfBirth")
                sex = el.get("HKCharacteristicTypeIdentifierBiologicalSex", "")
                if birth:
                    profile["birth"] = birth
                    profile["age"] = age_from(birth)
                profile["sex"] = {"HKBiologicalSexMale": "男",
                                  "HKBiologicalSexFemale": "女"}.get(sex)

            elif tag == "Record" and el.get("type") in WANTED:
                t, unit, when = el.get("type"), el.get("unit"), el.get("startDate", "")
                try:
                    v = float(el.get("value"))
                except (TypeError, ValueError):
                    el.clear()
                    continue
                day = when[:10]
                if t == HEIGHT and when > last_h:
                    profile["height"], last_h = round(to_cm(v, unit), 1), when
                elif t == WEIGHT:
                    kg = round(to_kg(v, unit), 1)
                    if when > last_w:
                        profile["weight"], last_w = kg, when
                    if day >= since:
                        weights[day] = kg          # 檔案依時間排序，後面的會蓋掉前面的
                else:
                    src = el.get("sourceName", "")
                    val = to_kcal(v, unit) if t in (ACTIVE, DIET, BASAL) else v
                    # 有些紀錄橫跨好幾天（例如手機關機時補記），按時間比例分到每一天
                    for d, share in split_by_day(when, el.get("endDate", when)):
                        if d >= since:
                            per_source[t][d][src] += val * share
                el.clear()

            elif tag == "Workout":
                day = el.get("startDate", "")[:10]
                if day >= since:
                    kind = el.get("workoutActivityType", "").replace("HKWorkoutActivityType", "")
                    minutes = float(el.get("duration") or 0)
                    if el.get("durationUnit") == "hr":
                        minutes *= 60
                    elif el.get("durationUnit") == "s":
                        minutes /= 60
                    # 舊版 iOS 熱量寫在屬性，新版寫在 WorkoutStatistics 子元素
                    kcal = el.get("totalEnergyBurned")
                    kcal = to_kcal(float(kcal), el.get("totalEnergyBurnedUnit")) if kcal else 0
                    if not kcal:
                        for st in el.iter("WorkoutStatistics"):
                            if st.get("type") == ACTIVE and st.get("sum"):
                                kcal = to_kcal(float(st.get("sum")), st.get("unit"))
                    workouts.append({"day": day, "type": WORKOUT_NAME.get(kind, kind),
                                     "minutes": round(minutes), "kcal": round(kcal)})
                el.clear()

    def daily(t):
        return {d: round(max(srcs.values())) for d, srcs in sorted(per_source[t].items())}

    return {
        "profile": profile,
        "weights": dict(sorted(weights.items())),
        "active_kcal": daily(ACTIVE),
        "steps": daily(STEPS),
        "diet_kcal": daily(DIET),
        "basal_kcal": daily(BASAL),
        "workouts": sorted(workouts, key=lambda w: w["day"]),
    }


# ---------- 顯示 ----------
def rpad(s, n):
    """靠右對齊；中文字算兩格寬"""
    s = str(s)
    w = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)
    return " " * max(0, n - w) + s


def print_summary(data, show_days=7):
    p = data["profile"]
    print("=== 基本資料 ===")
    print(f"性別：{p['sex'] or '未設定'}")
    print(f"年齡：{p['age'] if p['age'] is not None else '未設定'}"
          + (f"（{p['birth']} 出生）" if p["birth"] else ""))
    print(f"身高：{p['height'] or '沒有紀錄'} cm")
    print(f"體重：{p['weight'] or '沒有紀錄'} kg")

    missing = [k for k, v in (("出生日期", p["birth"]), ("性別", p["sex"]),
                                  ("身高", p["height"]), ("體重", p["weight"])) if not v]
    if missing:
        print(f"\n※ 健康 app 裡沒有設定：{'、'.join(missing)}")
        print("  補法：健康 app → 右上角大頭貼 → 健康詳細資料 → 編輯（填出生日期、性別、身高、體重），")
        print("  體重也可以在「瀏覽 → 身體測量值 → 體重 → 加入資料」記錄，之後重新匯出一次。")

    cols = [("體重", data["weights"]), ("活動熱量", data["active_kcal"]),
            ("靜態熱量", data["basal_kcal"]), ("步數", data["steps"]), ("飲食熱量", data["diet_kcal"])]
    days = sorted(set().union(*[set(c[1]) for c in cols]))[-show_days:]
    if days:
        print(f"\n=== 最近 {len(days)} 天 ===")
        print("日期      " + "".join(rpad(name, 10) for name, _ in cols))
        for d in days:
            cells = []
            for name, col in cols:
                v = col.get(d)
                cells.append("—" if v is None else (f"{v:.1f}" if name == "體重" else str(v)))
            print(d + "".join(rpad(c, 10) for c in cells))

        print("\n=== 每日平均（不含今天）===")
        today = date.today().isoformat()
        for name, col in cols[1:4]:
            vals = [v for d, v in col.items() if d < today]
            if vals:
                print(f"{name}：{sum(vals) / len(vals):,.0f}（{len(vals)} 天）")
        bas = [v for d, v in data["basal_kcal"].items() if d < today]
        act = [v for d, v in data["active_kcal"].items() if d < today]
        if bas and act:
            print(f"手機估計的每日總消耗：約 {sum(bas)/len(bas) + sum(act)/len(act):,.0f} 大卡（靜態 + 活動）")

    recent = data["workouts"][-5:]
    if recent:
        print("\n=== 最近的運動 ===")
        for w in recent:
            print(f"{w['day']}  {w['type']}  {w['minutes']} 分鐘  {w['kcal']} 大卡")

    ws = list(data["weights"].items())
    if len(ws) >= 2:
        print(f"\n體重變化：{ws[0][0]} {ws[0][1]} kg → {ws[-1][0]} {ws[-1][1]} kg"
              f"（{ws[-1][1] - ws[0][1]:+.1f} kg）")


def main():
    ap = argparse.ArgumentParser(description="讀取 iPhone 健康 app 匯出資料")
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [os.path.join(here, n) for n in ("export.zip", "輸出.zip", "export.xml", "輸出.xml")]
    default = next((c for c in candidates if os.path.exists(c)), candidates[0])
    ap.add_argument("path", nargs="?", default=default,
                    help="export.zip / 輸出.zip / export.xml / 輸出.xml（不填就找同資料夾裡的）")
    ap.add_argument("--days", type=int, default=90, help="只讀最近幾天（預設 90，0=全部）")
    ap.add_argument("--out", help="另存成 JSON 檔")
    args = ap.parse_args()
    if not os.path.exists(args.path):
        print(f"找不到檔案：{args.path}")
        print("請把 export.zip 或 輸出.xml 放在和 read_health.py 同一個資料夾，或在指令後面寫出檔案路徑。")
        return

    print("讀取中，檔案大的話要等一下…")
    data = load_health(args.path, args.days or None)
    print_summary(data)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"\n已存到 {args.out}")


if __name__ == "__main__":
    main()
