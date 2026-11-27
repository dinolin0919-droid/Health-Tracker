"""
E｜介面：把 A（使用者管理）、B（資料處理）、C（預測模型）、D（自適應學習）包成像 app 的網頁

執行：
    streamlit run app.py
    （第一次要先安裝：pip install -r requirements.txt）

電腦會自動開瀏覽器，資料存在旁邊的 data/ 資料夾。
放上 Streamlit Community Cloud 給大家用：在 Secrets 設定 SUPABASE_URL、SUPABASE_KEY，
資料就會改存到 Supabase 雲端資料庫（步驟見 README.md）。
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

import adaptive as ad
import data_processing as dp
import prediction_models as pm
from user_management import (ACTIVITY_LEVELS, MEAL_LEVELS, MEAL_NAMES, DailyRecord,
                             StorageError, UserError, UserManager)

st.set_page_config(page_title="熱量帳本", page_icon="⚖️", layout="centered")

# Streamlit Cloud 的 Secrets → 環境變數（本機沒有 secrets.toml 時跳過）
try:
    for _k in ("SUPABASE_URL", "SUPABASE_KEY"):
        if _k in st.secrets and not os.environ.get(_k):
            os.environ[_k] = str(st.secrets[_k])
except Exception:
    pass

# 新舊版 Streamlit 的「填滿寬度」寫法不同
_NEW = tuple(int(x) for x in st.__version__.split(".")[:2]) >= (1, 46)
WIDE = {"width": "stretch"} if _NEW else {"use_container_width": True}

COLORS = {"實際體重": "#1f1f1f", "7 天平均": "#8a8a8a",
          "Wishnofsky": "#d1495b", "Antonetti": "#edae49", "Hall": "#00798c",
          "加權預測": "#5b3fa0"}
MODEL_LABEL = {"wishnofsky": "Wishnofsky", "antonetti": "Antonetti", "hall": "Hall"}

st.markdown("""<style>
.block-container {padding-top: 2rem; padding-bottom: 4rem;}
div[data-testid="stMetricValue"] {font-size: 1.6rem;}
div[role="radiogroup"] {gap: 0.2rem 0.8rem;}
.cards {display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin: 0.2rem 0 0.8rem;}
.card {border: 1px solid rgba(128,128,128,.28); border-radius: 10px; padding: 10px 12px;}
.card .lab {font-size: .85rem; opacity: .75;}
.card .val {font-size: 1.55rem; font-weight: 700; line-height: 1.2; font-variant-numeric: tabular-nums;}
.card .sub {font-size: .75rem; opacity: .65;}
@media (max-width: 640px) {.cards {grid-template-columns: repeat(2, minmax(0, 1fr));}}
</style>""", unsafe_allow_html=True)

um = UserManager(os.environ.get("KCAL_DATA_DIR"), cache=True)   # 測試時可以指定別的資料夾


# ───────────────────────── 小工具 ─────────────────────────
def flash(msg: str):
    st.session_state["flash"] = msg


def show_flash():
    msg = st.session_state.pop("flash", None)
    if msg:
        st.success(msg)


def fmt(v, unit="", digits=0):
    return "—" if v is None else f"{v:,.{digits}f}{unit}"


def dataset(user) -> list[dict]:
    return dp.build_dataset(um, user)


# ───────────────────────── 登入、註冊 ─────────────────────────
def login_page():
    st.title("熱量帳本")
    st.caption("每天量體重、隨手記三餐，讓體重變化幫你校正，預測接下來會怎麼走。")
    t1, t2 = st.tabs(["登入", "註冊"])
    with t1:
        with st.form("login"):
            name = st.text_input("帳號")
            pw = st.text_input("密碼", type="password")
            if st.form_submit_button("登入", type="primary", **WIDE):
                try:
                    um.login(name, pw)
                    st.session_state["user"] = name.strip()
                    st.rerun()
                except UserError as e:
                    st.error(str(e))
        if um.exists("demo"):
            st.caption("想先看看：帳號 demo、密碼 demo1234（12 週模擬資料）")
    if um.is_cloud:
        st.caption("隱私：資料存在雲端資料庫（伺服器在美國），密碼經過加密雜湊，"
                   "只有登入的本人看得到自己的紀錄；可以隨時在「設定」刪除帳號和所有資料。"
                   "這是學生專題，請不要當成醫療建議。")
    else:
        st.caption("資料存在這台電腦的 data 資料夾。")
    with t2:
        with st.form("register"):
            name = st.text_input("帳號（2～20 字，英數或中文）")
            pw = st.text_input("密碼（至少 4 字）", type="password")
            c1, c2 = st.columns(2)
            sex = c1.radio("性別", ["女", "男"], horizontal=True)
            birth = c2.date_input("出生日期", value=date(2000, 1, 1),
                                  min_value=date(1920, 1, 1), max_value=date.today())
            c1, c2, c3 = st.columns(3)
            h = c1.number_input("身高 cm", 100.0, 250.0, 165.0, 0.5)
            w = c2.number_input("體重 kg", 25.0, 300.0, 60.0, 0.1)
            g = c3.number_input("目標體重 kg", 25.0, 300.0, 55.0, 0.1)
            act = st.selectbox("日常活動量", list(ACTIVITY_LEVELS),
                               format_func=lambda k: ACTIVITY_LEVELS[k][0], index=1)
            st.caption("運動會另外記錄，所以活動量選久坐或輕度就好，以免重複計算。")
            if st.form_submit_button("註冊", type="primary", **WIDE):
                try:
                    um.register(name, pw, sex=sex, birth_date=birth.isoformat(), height_cm=h,
                                weight_kg=w, activity_level=act, goal_weight_kg=g)
                    um.login(name, pw)
                    st.session_state["user"] = name.strip()
                    flash("註冊成功！先到「今天」記錄三餐吧。")
                    st.rerun()
                except UserError as e:
                    st.error(str(e))


# ───────────────────────── 今天 ─────────────────────────
def today_tab(user, rows):
    today = date.today().isoformat()
    rec = um.get_record(user, today) or DailyRecord(today)
    row = next((r for r in rows if r["date"] == today), None)
    body = pm.body_of(um, user)
    w_now = um.latest_weight(user)
    tdee = body.tdee(w_now) + (rec.exercise_kcal or 0)
    eat = rec.intake_kcal
    k = next((r["intake_factor"] for r in reversed(rows) if r["intake_factor"]), None)
    eat_adj = eat * k if (eat is not None and k) else eat

    bal = (eat_adj - tdee) if eat_adj is not None else None
    # 自己排版的小卡片：手機上也是兩欄（Streamlit 的 columns 在手機上會變成一欄）
    cards = [("攝取", fmt(eat), "依你的記錄換算"),
             ("校正後", fmt(eat_adj), f"× {k:.2f}（依體重校正）" if k else "資料夠了才會校正"),
             ("今天消耗", fmt(tdee), "BMR × 活動係數 + 運動"),
             ("收支", fmt(bal, digits=0) if bal is None else f"{bal:+,.0f}",
              "赤字，會瘦" if bal is not None and bal < 0 else ("盈餘" if bal is not None else "記了三餐才算"))]
    st.markdown('<div class="cards">' + "".join(
        f'<div class="card"><div class="lab">{a}</div><div class="val">{b}</div><div class="sub">{c}</div></div>'
        for a, b, c in cards) + "</div>", unsafe_allow_html=True)

    # 體重
    st.subheader("體重")
    with st.form("weight", border=False):
        c1, c2 = st.columns([3, 1], vertical_alignment="bottom") if _NEW else st.columns([3, 1])
        w = c1.number_input("今天幾公斤（起床、上完廁所後量最準）", 25.0, 300.0,
                            float(rec.weight_kg or w_now), 0.1)
        if c2.form_submit_button("記錄", type="primary", **WIDE):
            um.save_record(user, DailyRecord(today, weight_kg=w))
            flash(f"已記錄 {w:g} kg")
            st.rerun()

    # 快速記錄
    st.subheader("三餐")
    with st.form("meals", border=False):
        choice = {}
        for m in MEAL_NAMES:
            default = rec.meals.get(m, "沒吃" if m == "點心" else "普通")
            choice[m] = st.radio(m, MEAL_LEVELS, index=MEAL_LEVELS.index(default),
                                 horizontal=True, key=f"meal_{m}")
        c1, c2 = st.columns(2)
        save = c1.form_submit_button("儲存三餐", type="primary", **WIDE)
        same = c2.form_submit_button("跟上次一樣", **WIDE)
    if save:
        dp.update_day(um, user, today, set_meals=choice)
        flash("三餐已儲存")
        st.rerun()
    if same:
        try:
            r = dp.copy_day(um, user, today)
            flash(f"已複製 {r.note.split('同 ')[-1]} 的飲食")
            st.rerun()
        except ValueError as e:
            st.warning(str(e))

    foods_t, mets = dp.load_tables(um)
    with st.expander("詳細記錄食物（想精確時用）"):
        with st.form("food", border=False):
            names = sorted(foods_t)
            f = st.selectbox("食物", names, format_func=lambda n: f"{n}（{foods_t[n][1]}，{foods_t[n][0]:g} 大卡）")
            q = st.number_input("份數", 0.5, 20.0, 1.0, 0.5)
            if st.form_submit_button("加入", **WIDE):
                dp.update_day(um, user, today, add_foods={f: q})
                flash(f"已加入 {f} {q:g} 份")
                st.rerun()
        with st.form("custom_food", border=False):
            st.caption("清單裡沒有的食物")
            c1, c2, c3 = st.columns([2, 1, 1])
            n = c1.text_input("名稱")
            kc = c2.number_input("每份大卡", 0, 3000, 300, 10)
            q2 = c3.number_input("份數 ", 0.5, 20.0, 1.0, 0.5)
            if st.form_submit_button("新增並加入", **WIDE) and n.strip():
                dp.add_custom_food(um, n.strip(), kc)
                dp.update_day(um, user, today, add_foods={n.strip(): q2})
                flash(f"已加入 {n.strip()}")
                st.rerun()

    with st.expander("運動"):
        with st.form("exercise", border=False):
            e = st.selectbox("項目", list(mets))
            mins = st.number_input("分鐘", 5, 600, 30, 5)
            st.caption(f"約消耗 {dp.exercise_kcal(mets[e], w_now, mins):.0f} 大卡（已扣除靜止時本來的消耗）")
            if st.form_submit_button("加入運動", **WIDE):
                dp.update_day(um, user, today, add_exercises={e: mins})
                flash(f"已加入 {e} {mins} 分鐘")
                st.rerun()

    items = ([(n, f"{n} {v:g} 份") for n, v in rec.foods.items()]
             + [(n, f"{n} {v:g} 分鐘") for n, v in rec.exercises.items()])
    if items:
        st.subheader("今天的食物與運動")
        st.markdown("\n".join(f"- {label}" for _, label in items))
        with st.expander("刪除一項"):
            labels = {label: key for key, label in items}
            pick = st.selectbox("要刪除的項目", list(labels), label_visibility="collapsed")
            if st.button("刪除", key="del_item"):
                dp.update_day(um, user, today, remove=[labels[pick]])
                flash(f"已刪除 {pick}")
                st.rerun()
    if row and row["flags"]:
        st.caption("今天還沒過完，晚一點再看收支會比較準。")


# ───────────────────────── 趨勢與預測 ─────────────────────────
def trend_tab(user, rows):
    prof = um.get_profile(user)
    weeks = st.radio("預測多久", [4, 8, 12, 26], index=2, horizontal=True,
                     format_func=lambda x: f"{x} 週")
    avg_ei, avg_ex = pm.recent_averages(rows)
    mode = st.radio("假設之後每天吃", ["跟最近 14 天一樣", "自己設定"], horizontal=True)
    intake = exercise = None
    if mode == "自己設定" or avg_ei is None:
        if avg_ei is None and mode != "自己設定":
            st.info("還沒有足夠的飲食紀錄，先用「維持體重」的熱量預測；也可以自己設定。")
        c1, c2 = st.columns(2)
        base = avg_ei or pm.body_of(um, user).tdee(um.latest_weight(user))
        intake = c1.number_input("每天攝取（大卡）", 800, 5000, int(round(base, -1)), 50)
        exercise = c2.number_input("每天運動（大卡）", 0, 2000, int(round(avg_ex, -1)), 10)
    fc = ad.weighted_forecast(um, user, days=weeks * 7, intake=intake, exercise=exercise)

    # 圖
    hist = [r for r in rows if r["date"] >= (date.today() - timedelta(days=60)).isoformat()]
    recs = [{"日期": r["date"], "體重": r["weight_clean"], "線": "實際體重"} for r in hist if r["weight_clean"]]
    recs += [{"日期": r["date"], "體重": r["weight_smooth"], "線": "7 天平均"} for r in hist if r["weight_smooth"]]
    for key, label in MODEL_LABEL.items():
        recs += [{"日期": d, "體重": w, "線": label} for d, w in zip(fc["dates"], fc[key])]
    recs += [{"日期": d, "體重": w, "線": "加權預測"} for d, w in zip(fc["dates"], fc["weighted"])]
    df = pd.DataFrame(recs)
    df["日期"] = pd.to_datetime(df["日期"])
    order = [n for n in COLORS if n in set(df["線"])]
    color = alt.Color("線:N", scale=alt.Scale(domain=order, range=[COLORS[n] for n in order]),
                      legend=alt.Legend(orient="bottom", title=None, columns=3))
    lo, hi = df["體重"].min(), df["體重"].max()
    if prof.goal_weight_kg:
        lo, hi = min(lo, prof.goal_weight_kg), max(hi, prof.goal_weight_kg)
    y = alt.Y("體重:Q", scale=alt.Scale(domain=[lo - 0.5, hi + 0.5]), title="kg")
    x = alt.X("日期:T", title=None, axis=alt.Axis(format="%m/%d"))
    base = alt.Chart(df)
    layers = [
        base.transform_filter(alt.datum.線 == "實際體重").mark_circle(size=28).encode(x, y, color),
        base.transform_filter(alt.datum.線 == "7 天平均").mark_line(strokeWidth=2).encode(x, y, color),
        base.transform_filter(alt.FieldOneOfPredicate("線", list(MODEL_LABEL.values())))
            .mark_line(strokeWidth=2, strokeDash=[6, 3]).encode(x, y, color),
        base.transform_filter(alt.datum.線 == "加權預測").mark_line(strokeWidth=4).encode(x, y, color),
    ]
    if prof.goal_weight_kg:
        layers.append(alt.Chart(pd.DataFrame({"目標": [prof.goal_weight_kg]}))
                      .mark_rule(color="#00798c", strokeDash=[2, 4]).encode(y="目標:Q"))
    st.altair_chart(alt.layer(*layers).properties(height=340), **WIDE)

    src = {"recent": "最近 14 天平均（校正後）", "custom": "自己設定", "maintenance": "維持體重的熱量"}
    st.caption(f"從 {fc['w0']:.1f} kg 開始，每天吃 {fc['intake']:,.0f}、運動 {fc['exercise']:,.0f} 大卡"
               f"（{src[fc['intake_source']]}）")
    wi = fc["weights_info"]
    wtxt = "、".join(f"{MODEL_LABEL[m]} {v:.0%}" for m, v in sorted(fc["weights"].items(), key=lambda x: -x[1]))
    if wi["based_on"]:
        st.caption(f"加權預測的權重：{wtxt}（依最近 {wi['based_on']} 次回測、預測 {wi['horizon']} 天後誤差自動調整）")
    else:
        st.caption("加權預測：資料還不夠回測，先平均三個模型。記錄滿幾週後會自動調整權重。")
    table = []
    for key, label in list(MODEL_LABEL.items()) + [("weighted", "加權預測")]:
        end = fc[key][-1]
        reach = pm.days_to_reach(fc[key], prof.goal_weight_kg) if prof.goal_weight_kg else None
        table.append({"模型": label, f"{weeks} 週後": f"{end:.1f} kg",
                      "變化": f"{end - fc['w0']:+.1f} kg",
                      "到達目標": (f"{(date.today() + timedelta(days=reach)):%m/%d}（{reach} 天）"
                               if reach else "預測期間內不會")})
    st.dataframe(pd.DataFrame(table), hide_index=True, **WIDE)
    with st.expander("三個模型有什麼不同？"):
        st.markdown(
            "- **Wishnofsky**：最簡單，7,700 大卡 = 1 公斤，假設消耗不變，所以時間越長越會高估減重。\n"
            "- **Antonetti**：變瘦時消耗跟著變少，體重會慢慢趨近一個平衡點。\n"
            "- **Hall**：分開算脂肪和肌肉，再加上代謝適應，通常最保守。\n\n"
            "短期內三者差不多，時間拉長才看得出差別。")

    with st.expander("模型回顧：拿過去的資料比一比"):
        usable = [r for r in rows if r["usable"]]
        if len(usable) < 14:
            st.info("至少要 2 週的飲食紀錄才能回顧。")
            return
        first = date.fromisoformat(usable[0]["date"])
        last = date.today() - timedelta(days=1)
        start = st.date_input("從哪天開始", value=max(first, last - timedelta(days=42)),
                              min_value=first, max_value=last - timedelta(days=7))
        try:
            hc = pm.hindcast(um, user, start.isoformat(), last.isoformat())
        except ValueError as e:
            st.warning(str(e))
            return
        recs = [{"日期": d, "體重": w, "線": "實際體重"} for d, w in zip(hc["dates"], hc["actual"]) if w]
        for key, label in MODEL_LABEL.items():
            recs += [{"日期": d, "體重": w, "線": label} for d, w in zip(hc["dates"], hc[key])]
        d2 = pd.DataFrame(recs)
        d2["日期"] = pd.to_datetime(d2["日期"])
        b2 = alt.Chart(d2)
        st.altair_chart(alt.layer(
            b2.transform_filter(alt.datum.線 == "實際體重").mark_circle(size=28).encode(x, alt.Y("體重:Q", scale=alt.Scale(zero=False), title="kg"), color),
            b2.transform_filter(alt.datum.線 != "實際體重").mark_line(strokeWidth=2).encode(x, alt.Y("體重:Q", scale=alt.Scale(zero=False)), color),
        ).properties(height=280), **WIDE)
        mae = sorted(((v, MODEL_LABEL[k]) for k, v in hc["mae"].items() if v is not None))
        st.write("平均誤差（越小越準）：" + "、".join(f"**{n}** {v:.2f} kg" for v, n in mae))
        st.caption("用這段期間實際記錄的飲食（校正後）去重演，跟量到的體重比較。")


# ───────────────────────── 準確度（D 組）─────────────────────────
def accuracy_tab(user):
    st.caption("在過去每一天「假裝站在那天」預測未來，等日子到了再對答案。權重只用已經揭曉的答案調整，不會偷看未來。")
    hz = st.radio("預測幾天後", [7, 14, 28], index=1, horizontal=True, format_func=lambda x: f"{x} 天")
    res = ad.evaluate(um, user, hz)
    if not res["evaluated"]:
        st.info(f"資料還不夠：需要連續幾週、大部分天有記飲食，而且有量體重（目前可回測 {res['records']} 次）。")
        return
    st.write(f"回測 **{res['records']}** 次，暖身後參與評估 **{res['evaluated']}** 次。")

    def table(models, methods):
        out = [{"預測方式": pm.MODEL_NAMES[k], "MAE": v["mae"], "RMSE": v["rmse"], "偏差": v["bias"]}
               for k, v in models.items()]
        out += [{"預測方式": "加權：" + ad.METHODS[k], "MAE": v["mae"], "RMSE": v["rmse"], "偏差": v["bias"]}
                for k, v in methods.items()]
        df = pd.DataFrame(out).sort_values("MAE")
        st.dataframe(df.style.format({"MAE": "{:.2f}", "RMSE": "{:.2f}", "偏差": "{:+.2f}"}),
                     hide_index=True, **WIDE)

    st.subheader("跟量到的體重比")
    table(res["models"], res["methods"])
    st.caption("單位 kg。MAE＝平均差多少；RMSE 對大錯誤比較敏感；偏差＞0 表示平均高估。量到的體重用 7 天平均。")
    if "truth_models" in res:
        st.subheader("跟模擬的真實體重比")
        table(res["truth_models"], res["truth_methods"])
        st.caption("只有模擬資料才有「真實體重」，可以看出量體重的雜訊會不會讓我們挑錯模型。")

    st.subheader("權重怎麼變化（誤差倒數加權）")
    wdf = pd.DataFrame([{"日期": h["origin"], "模型": MODEL_LABEL[m], "權重": h["weights"]["inverse_mse"][m]}
                        for h in res["history"] for m in ad.MODELS])
    wdf["日期"] = pd.to_datetime(wdf["日期"])
    st.altair_chart(alt.Chart(wdf).mark_area().encode(
        alt.X("日期:T", title=None, axis=alt.Axis(format="%m/%d")),
        alt.Y("權重:Q", stack="normalize", axis=alt.Axis(format="%"), title=None),
        alt.Color("模型:N", scale=alt.Scale(domain=list(MODEL_LABEL.values()),
                                          range=[COLORS[n] for n in MODEL_LABEL.values()]),
                  legend=alt.Legend(orient="bottom", title=None, columns=3)),
    ).properties(height=220), **WIDE)


# ───────────────────────── 紀錄 ─────────────────────────
def records_tab(user, rows):
    cols = {"date": "日期", "weight_kg": "體重", "weight_smooth": "7天平均", "intake_kcal": "攝取",
            "intake_corrected": "校正後", "exercise_kcal": "運動", "tdee_formula": "消耗",
            "balance_kcal": "收支", "steps": "步數", "intake_method": "方式"}
    df = pd.DataFrame(rows[::-1])
    if df.empty:
        st.info("還沒有紀錄")
        return
    st.dataframe(df[list(cols)].rename(columns=cols).head(90), hide_index=True, **WIDE)
    st.caption(f"顯示最近 90 天，共 {len(df)} 天")
    full = pd.DataFrame(rows)[list(dp.COLUMNS)]
    st.download_button("下載全部（CSV，Excel 可開）", full.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"{user}_熱量帳本.csv", mime="text/csv", **WIDE)


# ───────────────────────── 資料品質 ─────────────────────────
def quality_tab(user, rows):
    q = dp.quality_report(rows)
    c = st.columns(3)
    c[0].metric("紀錄天數", q["days"])
    c[1].metric("可給模型用", q["usable"])
    c[2].metric("有量體重", q["weight_days"])
    k = q["latest_factor"]
    st.subheader("校正係數")
    if k is None:
        st.info("資料還不夠：需要過去 4 週裡至少一半的天有記飲食，而且頭尾都有量體重。")
    else:
        st.metric("目前", f"{k:.2f}")
        if abs(k - 1) < 0.05:
            st.write("記錄和體重變化很一致。")
        elif k > 1:
            st.write(f"依體重變化，實際攝取大約比記錄多 **{k - 1:.0%}**（可能少記了，或實際消耗比公式低）。")
        else:
            st.write(f"依體重變化，實際攝取大約比記錄少 **{1 - k:.0%}**（可能記多了，或實際消耗比公式高）。")
        ks = pd.DataFrame([{"日期": r["date"], "校正係數": r["intake_factor"]} for r in rows if r["intake_factor"]])
        ks["日期"] = pd.to_datetime(ks["日期"])
        st.altair_chart(alt.Chart(ks).mark_line(color="#00798c").encode(
            alt.X("日期:T", title=None, axis=alt.Axis(format="%m/%d")),
            alt.Y("校正係數:Q", scale=alt.Scale(zero=False))).properties(height=180), **WIDE)
    if q["flags"]:
        st.subheader("資料問題")
        st.dataframe(pd.DataFrame([{"問題": dp.FLAG_TEXT.get(f, f), "天數": n}
                                   for f, n in sorted(q["flags"].items(), key=lambda x: -x[1])]),
                     hide_index=True, **WIDE)
    if q["usable"] < 14 or q["weight_days"] < 7:
        st.warning("至少要 2 週的飲食紀錄、7 次以上的體重，分析才比較有意義。")


# ───────────────────────── 設定 ─────────────────────────
def settings_tab(user):
    p = um.get_profile(user)
    st.write(f"**{p.display_name}**（{p.sex}，{p.age} 歲）・註冊於 {p.registered_at[:10]}")
    with st.form("profile"):
        c1, c2 = st.columns(2)
        h = c1.number_input("身高 cm", 100.0, 250.0, float(p.height_cm), 0.5)
        g = c2.number_input("目標體重 kg", 25.0, 300.0, float(p.goal_weight_kg or p.initial_weight_kg), 0.1)
        act = st.selectbox("日常活動量", list(ACTIVITY_LEVELS), index=p.activity_level - 1,
                           format_func=lambda k: ACTIVITY_LEVELS[k][0])
        if st.form_submit_button("儲存", type="primary"):
            try:
                um.update_profile(user, height_cm=h, goal_weight_kg=g, activity_level=act)
                dp.recalc_all(um, user)
                flash("個人資料已更新")
                st.rerun()
            except UserError as e:
                st.error(str(e))

    with st.expander("從 iPhone 健康 app 匯入"):
        st.caption("健康 app → 右上角大頭貼 → 輸出所有健康資料，把得到的 export.zip（或解壓後的 輸出.xml）上傳。"
                   "建議上傳 zip，檔案比較小。")
        up = st.file_uploader("選擇檔案", type=["zip", "xml"])
        path = None
        if not um.is_cloud:
            here = os.path.dirname(os.path.abspath(__file__))
            local = st.text_input("或輸入電腦上的檔案／資料夾", value=here)
        if st.button("匯入"):
            import tempfile
            from read_health import find_export, load_health
            try:
                if up is not None:
                    suffix = ".zip" if up.name.lower().endswith(".zip") else ".xml"
                    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                        f.write(up.getbuffer())
                        path = f.name
                elif not um.is_cloud:
                    path = find_export(local)
                else:
                    st.warning("請先選擇檔案")
                    st.stop()
                with st.spinner("讀取中，檔案大的話要等一下…"):
                    res = um.import_health_data(user, load_health(path, days=None))
                flash(f"匯入 {res['days']} 天" + (f"，更新：{', '.join(res['profile_updated'])}"
                                                 if res["profile_updated"] else ""))
                st.rerun()
            except Exception as e:
                if type(e).__name__ in ("RerunException", "StopException"):
                    raise
                st.error(f"匯入失敗：{e}")
            finally:
                if up is not None and path and os.path.exists(path):
                    os.remove(path)

    with st.expander("改密碼"):
        with st.form("pw"):
            old = st.text_input("舊密碼", type="password")
            new = st.text_input("新密碼", type="password")
            if st.form_submit_button("更新密碼"):
                try:
                    um.change_password(user, old, new)
                    st.success("密碼已更新")
                except UserError as e:
                    st.error(str(e))

    with st.expander("刪除帳號"):
        st.write("會刪除你的帳號和**所有紀錄**，刪了就救不回來。")
        with st.form("delete"):
            pw = st.text_input("輸入密碼確認", type="password")
            sure = st.checkbox("我確定要刪除")
            if st.form_submit_button("永久刪除"):
                if not sure:
                    st.warning("請先勾選「我確定要刪除」")
                else:
                    try:
                        um.delete_user(user, pw)
                        st.session_state.pop("user", None)
                        flash("帳號和所有資料都已刪除")
                        st.rerun()
                    except UserError as e:
                        st.error(str(e))

    if st.button("登出", **WIDE):
        st.session_state.pop("user", None)
        st.rerun()


# ───────────────────────── 主程式 ─────────────────────────
def main():
    user = st.session_state.get("user")
    if not user or not um.exists(user):
        show_flash()
        login_page()
        return
    p = um.get_profile(user)
    st.title("熱量帳本")
    st.caption(f"{p.display_name}・{date.today():%Y/%m/%d}")
    show_flash()
    rows = dataset(user)
    tabs = st.tabs(["今天", "趨勢與預測", "準確度", "紀錄", "資料品質", "設定"])
    with tabs[0]:
        today_tab(user, rows)
    with tabs[1]:
        trend_tab(user, rows)
    with tabs[2]:
        accuracy_tab(user)
    with tabs[3]:
        records_tab(user, rows)
    with tabs[4]:
        quality_tab(user, rows)
    with tabs[5]:
        settings_tab(user)


try:
    main()
except StorageError as e:
    st.error(f"資料庫暫時連不上，請稍後再試。（{e}）")
