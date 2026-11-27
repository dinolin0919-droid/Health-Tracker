"""
E｜外觀主題：四種風格，使用者在「設定 → 外觀」自己選，存在個人資料裡

每個主題定義：底色、卡片色、文字色、主色、輔色、字體、圓角，以及圖表的線條顏色。
Streamlit 沒有「每個人不同主題」的功能，所以這裡用 CSS 蓋過預設樣式；
表格改用自己畫的 HTML 表格，深色主題下才不會出現白底的表格。
"""

from __future__ import annotations

import html

THEMES = {
    "fresh": {
        "name": "清爽健康", "desc": "白底、綠和淡藍，乾淨好讀",
        "bg": "#ffffff", "surface": "#f2f8f4", "line": "#d6e6dc", "ink": "#12261c", "muted": "#5b6f64",
        "primary": "#1f9d61", "on_primary": "#ffffff", "accent": "#2f80c2", "bad": "#d1495b",
        "font": "Noto Sans TC", "head_font": "Noto Sans TC", "head_weight": 900,
        "radius": "12px", "hero": "linear-gradient(120deg,#e3f5ea 0%,#e6f1fb 100%)",
        "chart": {"實際體重": "#12261c", "7 天平均": "#9bb5a6", "Wishnofsky": "#e07a5f",
                  "Antonetti": "#e9b44c", "Hall": "#2f80c2", "加權預測": "#1f9d61"},
    },
    "warm": {
        "name": "溫暖食物感", "desc": "米白、番茄紅、橘黃，像手帳",
        "bg": "#fbf6ee", "surface": "#f4e8d6", "line": "#e6d5bd", "ink": "#3b2a1e", "muted": "#86705c",
        "primary": "#d4502b", "on_primary": "#ffffff", "accent": "#e39a2d", "bad": "#b8322a",
        "font": "Noto Sans TC", "head_font": "Noto Serif TC", "head_weight": 900,
        "radius": "10px", "hero": "linear-gradient(120deg,#f7e3c8 0%,#f9d9cc 100%)",
        "chart": {"實際體重": "#3b2a1e", "7 天平均": "#b9a28a", "Wishnofsky": "#b8322a",
                  "Antonetti": "#e39a2d", "Hall": "#5f8a4f", "加權預測": "#d4502b"},
    },
    "dark": {
        "name": "深色科技感", "desc": "深色背景配亮色圖表，像儀表板",
        "bg": "#0e131d", "surface": "#171f2d", "line": "#273246", "ink": "#e9eef6", "muted": "#8d9ab0",
        "primary": "#3ccbf0", "on_primary": "#06202a", "accent": "#b6f25a", "bad": "#ff6b81",
        "font": "Noto Sans TC", "head_font": "Noto Sans TC", "head_weight": 700,
        "radius": "10px", "hero": "linear-gradient(120deg,#132235 0%,#1b1530 100%)",
        "chart": {"實際體重": "#e9eef6", "7 天平均": "#5d6b82", "Wishnofsky": "#ff6b81",
                  "Antonetti": "#ffc857", "Hall": "#b6f25a", "加權預測": "#3ccbf0"},
    },
    "cute": {
        "name": "可愛手繪", "desc": "圓角、粉嫩色，活潑有趣",
        "bg": "#fff7fa", "surface": "#ffe8f1", "line": "#f7cfe0", "ink": "#4a3350", "muted": "#9a7fa3",
        "primary": "#ff6f9f", "on_primary": "#ffffff", "accent": "#6cbcf5", "bad": "#e8556f",
        "font": "Huninn", "head_font": "Huninn", "head_weight": 400,
        "radius": "20px", "hero": "linear-gradient(120deg,#ffe3ee 0%,#e6f3ff 100%)",
        "chart": {"實際體重": "#4a3350", "7 天平均": "#c7a9cf", "Wishnofsky": "#ff8a65",
                  "Antonetti": "#ffd166", "Hall": "#6cbcf5", "加權預測": "#ff6f9f"},
    },
}
DEFAULT = "fresh"


def get(key: str | None) -> dict:
    return THEMES.get(key or DEFAULT, THEMES[DEFAULT])


def css(key: str | None) -> str:
    t = get(key)
    fonts = sorted({t["font"], t["head_font"], "Noto Sans TC"})
    families = "&".join(f"family={f.replace(' ', '+')}:wght@400;500;700;900" if f != "Huninn"
                        else "family=Huninn" for f in fonts)
    dark = key == "dark"
    out = f"""
<style>
@import url('https://fonts.googleapis.com/css2?{families}&display=swap');
:root {{
  --bg:{t['bg']}; --surface:{t['surface']}; --line:{t['line']}; --ink:{t['ink']}; --muted:{t['muted']};
  --primary:{t['primary']}; --on-primary:{t['on_primary']}; --accent:{t['accent']}; --bad:{t['bad']};
  --r:{t['radius']}; --font:'{t['font']}','Noto Sans TC',system-ui,sans-serif;
  --head:'{t['head_font']}','Noto Sans TC',system-ui,sans-serif;
}}
.stApp, [data-testid="stAppViewContainer"] {{ background: var(--bg); color: var(--ink); }}
[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding-top: 1.6rem; padding-bottom: 4rem; }}
html, body, .stApp, .stApp p, .stApp label, .stApp li, .stApp span, .stApp div, .stApp input,
.stApp button, .stApp textarea {{ font-family: var(--font); }}
.stApp [data-testid="stIconMaterial"], .stApp .material-symbols-rounded {{ font-family: 'Material Symbols Rounded'; }}
.stApp h1, .stApp h2, .stApp h3 {{ font-family: var(--head); color: var(--ink); font-weight: {t['head_weight']};
  letter-spacing: 0; }}
.stApp p, .stApp li, .stApp label, [data-testid="stWidgetLabel"] p, [data-testid="stMarkdownContainer"] {{ color: var(--ink); }}
[data-testid="stCaptionContainer"], .stApp small {{ color: var(--muted) !important; }}

/* 頁首 */
.hero {{ background: {t['hero']}; border: 1px solid var(--line); border-radius: var(--r);
  padding: 18px 20px; margin-bottom: 14px; display: flex; align-items: center; gap: 14px; }}
.hero .logo {{ width: 46px; height: 46px; border-radius: calc(var(--r) * .8); background: var(--primary);
  color: var(--on-primary); display: grid; place-items: center; font-size: 22px; font-weight: 900;
  font-family: var(--head); flex: none; }}
.hero .t {{ font-family: var(--head); font-weight: {t['head_weight']}; font-size: 1.55rem; line-height: 1.1;
  color: var(--ink); }}
.hero .s {{ color: var(--muted); font-size: .85rem; margin-top: 2px; }}

/* 分頁（同時支援新版 react-aria 和舊版 baseweb 的結構）*/
.stTabs [role="tablist"] {{ gap: 4px; border-bottom: 1px solid var(--line); }}
.stTabs [role="tab"] {{ background: transparent; border-radius: var(--r) var(--r) 0 0; padding: 6px 12px; }}
.stTabs [role="tab"] p {{ color: var(--muted); font-weight: 500; }}
.stTabs [role="tab"][aria-selected="true"] {{ background: var(--surface); }}
.stTabs [role="tab"][aria-selected="true"] p {{ color: var(--primary); font-weight: 700; }}
.stTabs .react-aria-SelectionIndicator, .stTabs [data-baseweb="tab-highlight"] {{ background: var(--primary) !important; }}
.stTabs [data-baseweb="tab-border"] {{ display: none; }}
/* 按鈕 */
.stButton button, .stFormSubmitButton button, .stDownloadButton button {{
  border-radius: var(--r); border: 1.5px solid var(--line); background: var(--surface); color: var(--ink);
  font-weight: 700; min-height: 44px; }}
.stButton button:hover, .stFormSubmitButton button:hover, .stDownloadButton button:hover {{
  border-color: var(--primary); color: var(--primary); }}
.stButton button[kind="primary"], .stFormSubmitButton button[kind="primaryFormSubmit"],
.stButton button[data-testid="stBaseButton-primary"],
.stFormSubmitButton button[data-testid="stBaseButton-primaryFormSubmit"] {{
  background: var(--primary); border-color: var(--primary); color: var(--on-primary); }}
.stButton button[kind="primary"]:hover, .stFormSubmitButton button[kind="primaryFormSubmit"]:hover,
.stFormSubmitButton button[data-testid="stBaseButton-primaryFormSubmit"]:hover {{
  filter: brightness(1.06); color: var(--on-primary); }}
.stButton button p, .stFormSubmitButton button p, .stDownloadButton button p {{ color: inherit; }}

/* 輸入框、下拉選單 */
.stTextInput input, .stNumberInput input, .stDateInput input, .stTextArea textarea {{
  background: var(--surface) !important; color: var(--ink) !important; }}
[data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="select"] > div {{
  background: var(--surface) !important; border-color: var(--line) !important; border-radius: var(--r) !important; }}
[data-baseweb="select"] * {{ color: var(--ink); }}
[data-testid="stSelectbox"] [role="group"], .react-aria-ComboBox [role="group"], .react-aria-Select button {{
  background: var(--surface) !important; border-color: var(--line) !important; border-radius: var(--r) !important; }}
[data-testid="stSelectbox"] input, [data-testid="stSelectbox"] span, [data-testid="stSelectbox"] svg {{
  color: var(--ink) !important; fill: var(--ink); background: transparent !important; }}
.react-aria-Popover, .react-aria-ListBox, [role="listbox"] {{ background: var(--surface) !important; color: var(--ink) !important; }}
[role="listbox"] [role="option"] {{ color: var(--ink) !important; }}
[role="listbox"] [role="option"][data-focused="true"], [role="listbox"] [role="option"]:hover {{
  background: color-mix(in srgb, var(--primary) 18%, var(--surface)) !important; }}
[data-baseweb="popover"] ul, [data-baseweb="popover"] li, [data-baseweb="calendar"] {{
  background: var(--surface) !important; color: var(--ink) !important; }}
.stNumberInput button {{ background: var(--surface); color: var(--ink); border-color: var(--line); }}

/* 點選份量的選項做成小膠囊（隱藏原本的圓點）*/
[data-testid="stRadioGroup"], .stRadio [role="radiogroup"] {{ gap: 6px !important; flex-wrap: wrap; }}
[data-testid="stRadioOption"], .stRadio [role="radiogroup"] label[data-baseweb="radio"] {{
  background: var(--surface); border: 1.5px solid var(--line); border-radius: 999px;
  padding: 5px 14px !important; margin: 0 !important; cursor: pointer; }}
[data-testid="stRadioOption"] > div > div:first-child:not([data-testid]),
.stRadio label[data-baseweb="radio"] > div:first-child {{ display: none !important; }}
[data-testid="stRadioOption"] p, .stRadio label[data-baseweb="radio"] p {{ margin: 0; color: var(--ink); }}
[data-testid="stRadioOption"][data-selected="true"], [data-testid="stRadioOption"]:has(input:checked),
.stRadio label[data-baseweb="radio"]:has(input:checked) {{
  background: var(--primary); border-color: var(--primary); }}
[data-testid="stRadioOption"][data-selected="true"] p, [data-testid="stRadioOption"]:has(input:checked) p,
.stRadio label[data-baseweb="radio"]:has(input:checked) p {{ color: var(--on-primary) !important; font-weight: 700; }}
[data-testid="stRadioOption"]:hover {{ border-color: var(--primary); }}
/* 滑桿 */
[data-testid="stSlider"] [role="slider"], [data-testid="stSlider"] div:has(> div > input[type="range"]) {{
  background: var(--primary) !important; border-color: var(--primary) !important; box-shadow: none !important; }}
[data-testid="stSlider"] [data-testid="stSliderThumbValue"], [data-testid="stSlider"] [data-testid="stThumbValue"] {{
  color: var(--primary) !important; }}
[data-testid="stSlider"] [data-testid="stSliderTickBar"] {{ color: var(--muted); }}
/* 外框、展開區、表單 */
[data-testid="stForm"] {{ border: 1px solid var(--line); border-radius: var(--r); background: transparent; }}
[data-testid="stExpander"] details {{ border: 1px solid var(--line); border-radius: var(--r); background: var(--bg); }}
[data-testid="stExpander"] summary p {{ color: var(--ink); font-weight: 700; }}
[data-testid="stFileUploaderDropzone"] {{ background: var(--surface); border-radius: var(--r); }}
[data-testid="stAlert"] {{ border-radius: var(--r); }}
hr {{ border-color: var(--line); }}

/* 今天的四張小卡 */
.cards {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin: .2rem 0 1rem; }}
.card {{ background: var(--surface); border: 1px solid var(--line); border-radius: var(--r); padding: 12px 14px; }}
.card .lab {{ font-size: .82rem; color: var(--muted); }}
.card .val {{ font-size: 1.6rem; font-weight: 800; line-height: 1.2; color: var(--ink);
  font-variant-numeric: tabular-nums; }}
.card .sub {{ font-size: .74rem; color: var(--muted); }}
.card.good .val {{ color: var(--primary); }}
.card.bad .val {{ color: var(--bad); }}
@media (max-width: 640px) {{ .cards {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}

/* 自己畫的表格 */
.tbl-wrap {{ overflow-x: auto; border: 1px solid var(--line); border-radius: var(--r); margin: .3rem 0 .6rem; }}
.tbl {{ width: 100%; border-collapse: collapse; font-size: .9rem; color: var(--ink); }}
.tbl th {{ background: var(--surface); color: var(--muted); font-weight: 700; text-align: left;
  padding: 8px 10px; white-space: nowrap; border-bottom: 1px solid var(--line) !important; }}
.tbl-wrap table {{ margin: 0; border: 0 !important; }}
.tbl th, .tbl td {{ border-left: 0 !important; border-right: 0 !important; border-top: 0 !important; }}
.tbl td {{ padding: 7px 10px; border-bottom: 1px solid var(--line) !important; white-space: nowrap; background: transparent; }}
.tbl tr:last-child td {{ border-bottom: 0; }}
.tbl td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.tbl tr.hi td {{ background: color-mix(in srgb, var(--primary) 10%, var(--bg)); font-weight: 700; }}

/* 外觀選擇的色票 */
.swatches {{ display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 8px; margin: .3rem 0 .4rem; }}
.sw {{ border-radius: 12px; padding: 10px; border: 2px solid transparent; font-size: .8rem; }}
.sw.on {{ border-color: var(--primary); }}
.sw .dots {{ display: flex; gap: 4px; margin-top: 6px; }}
.sw .dots span {{ width: 14px; height: 14px; border-radius: 50%; display: inline-block; }}
@media (max-width: 640px) {{ .swatches {{ grid-template-columns: repeat(2, minmax(0,1fr)); }} }}
{'' if not dark else '''
[data-testid="stAlert"] p { color: var(--ink); }
.stApp a { color: var(--primary); }
'''}
</style>"""
    # Streamlit 的 Markdown 遇到空白行會把 <style> 切斷，所以把空白行全部拿掉
    return "\n".join(line for line in out.splitlines() if line.strip())


def hero(title: str, subtitle: str) -> str:
    return (f'<div class="hero"><div class="logo">熱</div><div><div class="t">{html.escape(title)}</div>'
            f'<div class="s">{html.escape(subtitle)}</div></div></div>')


def swatches(current: str | None) -> str:
    out = []
    for k, t in THEMES.items():
        dots = "".join(f'<span style="background:{c}"></span>'
                       for c in (t["bg"], t["primary"], t["accent"], t["ink"]))
        out.append(f'<div class="sw{" on" if k == (current or DEFAULT) else ""}" '
                   f'style="background:{t["surface"]};color:{t["ink"]};font-family:\'{t["font"]}\',sans-serif">'
                   f'<b>{html.escape(t["name"])}</b><div style="opacity:.75">{html.escape(t["desc"])}</div>'
                   f'<div class="dots">{dots}</div></div>')
    return '<div class="swatches">' + "".join(out) + "</div>"


def table(rows: list[dict], num_cols: set[str] | None = None, highlight: str | None = None) -> str:
    """list[dict] → HTML 表格（數字靠右；highlight 是要特別標出來的那一列第一欄的值）"""
    if not rows:
        return ""
    cols = list(rows[0])
    num_cols = num_cols or set()
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in cols)
    body = []
    for r in rows:
        cls = ' class="hi"' if highlight is not None and str(r[cols[0]]) == highlight else ""
        tds = "".join(f'<td class="num">{html.escape(str(r[c]))}</td>' if c in num_cols
                      else f"<td>{html.escape(str(r[c]))}</td>" for c in cols)
        body.append(f"<tr{cls}>{tds}</tr>")
    return f'<div class="tbl-wrap"><table class="tbl"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def style_chart(chart, key: str | None):
    """Altair 圖表套主題：透明背景、軸線和文字顏色"""
    t = get(key)
    return (chart.configure(background="transparent", font=t["font"])
            .configure_view(stroke=None)
            .configure_axis(labelColor=t["muted"], titleColor=t["muted"], gridColor=t["line"],
                            domainColor=t["line"], tickColor=t["line"], labelFont=t["font"])
            .configure_legend(labelColor=t["ink"], labelFont=t["font"]))
