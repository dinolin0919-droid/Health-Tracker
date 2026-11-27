"""
A｜User management 使用者管理

負責：註冊、登入、個人資料、歷史紀錄儲存。
也定義全組共用的資料格式（UserProfile、DailyRecord），B～E 都透過這個模組讀寫資料。

────────────────────────────────────────
給其他組員的用法
────────────────────────────────────────
    from user_management import UserManager, DailyRecord

    um = UserManager()                       # 資料存在 user_management.py 旁邊的 data/
    profile = um.login("dino", "密碼")       # 回傳 UserProfile

    profile.age, profile.sex, profile.height_cm, profile.activity_factor
    um.latest_weight("dino")                 # 最新體重（沒紀錄就用註冊時的體重）

    # B：算好熱量後存進去（同一天會合併，不會蓋掉其他欄位）
    um.save_record("dino", DailyRecord("2026-11-27", intake_kcal=1850,
                   exercise_kcal=220, foods={"白飯": 1.5}, exercises={"快走": 30}))

    # C、D、E：讀歷史紀錄
    for r in um.get_records("dino", start="2026-11-01"):
        r.date, r.weight_kg, r.intake_kcal, r.exercise_kcal
    um.get_rows("dino")                      # 一樣的資料，但是 list[dict]，方便做表或畫圖

單獨執行 `python user_management.py` 會出現文字選單，可以手動測試。
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime

# ───────────────────────── 常數 ─────────────────────────
ACTIVITY_LEVELS = {          # 日常活動量 → 活動係數（算 TDEE 用）
    1: ("久坐（辦公室、少走動）", 1.2),
    2: ("輕度（常站立走動）", 1.375),
    3: ("中度（勞動型工作）", 1.55),
    4: ("高度（重體力工作）", 1.725),
}
SEXES = ("男", "女")
THEME_KEYS = ("fresh", "warm", "dark", "cute")
MEAL_NAMES = ("早餐", "午餐", "晚餐", "點心")
MEAL_LEVELS = ("沒吃", "少", "普通", "多", "大餐")   # 快速記錄用，熱量換算在 B 模組
PBKDF2_ROUNDS = 200_000
USERNAME_RE = re.compile(r"^[\w一-鿿]{2,20}$")   # 英數、底線、中文，2～20 字


# ───────────────────────── 錯誤類型 ─────────────────────────
class UserError(Exception):
    """這個模組所有錯誤的共同父類別"""


class ValidationError(UserError, ValueError):
    """輸入的值不合理（例如身高 500 cm）"""


class UserExistsError(UserError):
    """註冊時帳號已存在"""


class UserNotFoundError(UserError):
    """找不到帳號"""


class AuthError(UserError):
    """密碼錯誤"""


# ───────────────────────── 驗證小工具 ─────────────────────────
def _check_range(name, value, lo, hi, allow_none=False):
    if value is None and allow_none:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{name} 必須是數字") from None
    if not lo <= v <= hi:
        raise ValidationError(f"{name} 必須在 {lo}～{hi} 之間")
    return v


def _check_date(name, value):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise ValidationError(f"{name} 格式要是 YYYY-MM-DD") from None


def _now():
    return datetime.now().isoformat(timespec="seconds")


# ───────────────────────── 共用資料格式 ─────────────────────────
@dataclass
class UserProfile:
    """個人資料。年齡用出生日期算，所以每年會自動更新。"""
    username: str
    display_name: str
    sex: str                      # "男" / "女"
    birth_date: str               # "YYYY-MM-DD"
    height_cm: float
    initial_weight_kg: float      # 註冊時的體重
    activity_level: int = 1       # 1～4，見 ACTIVITY_LEVELS
    goal_weight_kg: float | None = None
    registered_at: str = field(default_factory=_now)
    theme: str = "fresh"          # app 的外觀：fresh / warm / dark / cute（見 themes.py）
    plan: dict | None = None      # 目標規劃：{"target", "start", "end", "intake", "recorded", "exercise", "meals"}

    @classmethod
    def from_dict(cls, d: dict) -> "UserProfile":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    @property
    def age(self) -> int:
        b, t = date.fromisoformat(self.birth_date), date.today()
        return t.year - b.year - ((t.month, t.day) < (b.month, b.day))

    @property
    def activity_factor(self) -> float:
        return ACTIVITY_LEVELS[self.activity_level][1]

    def validate(self):
        if self.sex not in SEXES:
            raise ValidationError("性別要是「男」或「女」")
        self.birth_date = _check_date("出生日期", self.birth_date)
        if not 10 <= self.age <= 120:
            raise ValidationError("年齡要在 10～120 歲之間（請確認出生日期）")
        self.height_cm = _check_range("身高", self.height_cm, 100, 250)
        self.initial_weight_kg = _check_range("體重", self.initial_weight_kg, 25, 300)
        self.goal_weight_kg = _check_range("目標體重", self.goal_weight_kg, 25, 300, allow_none=True)
        if self.activity_level not in ACTIVITY_LEVELS:
            raise ValidationError("活動量要是 1～4")
        if self.plan is not None:
            if not isinstance(self.plan, dict) or not {"target", "end", "intake"} <= set(self.plan):
                raise ValidationError("計畫格式不對")
            _check_range("計畫目標體重", self.plan["target"], 25, 300)
            _check_date("計畫結束日", self.plan["end"])
        if self.theme not in THEME_KEYS:
            raise ValidationError(f"外觀要是 {'、'.join(THEME_KEYS)} 其中之一")
        self.display_name = (self.display_name or self.username).strip()[:30]
        return self


@dataclass
class DailyRecord:
    """一天一筆的紀錄，全組共用。沒有的欄位就留 None。

    手動輸入：foods、exercises（B 負責換算成 intake_kcal、exercise_kcal）
    快速記錄：meals {"早餐": "普通", "午餐": "多"}（B 負責換算）
    健康 app：steps、active_kcal、basal_kcal、diet_kcal（B 的 read_health 讀進來）
    """
    date: str                                   # "YYYY-MM-DD"
    weight_kg: float | None = None
    intake_kcal: float | None = None            # 當天總攝取
    exercise_kcal: float | None = None          # 當天運動消耗
    foods: dict = field(default_factory=dict)   # {種類: 份數}
    exercises: dict = field(default_factory=dict)  # {種類: 分鐘}
    meals: dict = field(default_factory=dict)   # {餐別: 份量等級}，見 MEAL_NAMES、MEAL_LEVELS
    steps: int | None = None
    active_kcal: float | None = None            # 健康 app 的活動熱量
    basal_kcal: float | None = None             # 健康 app 的靜態熱量
    note: str = ""
    source: str = "manual"                      # "manual" / "health_app" / "mixed"
    updated_at: str = field(default_factory=_now)

    def validate(self):
        self.date = _check_date("日期", self.date)
        self.weight_kg = _check_range("體重", self.weight_kg, 25, 300, allow_none=True)
        self.intake_kcal = _check_range("攝取熱量", self.intake_kcal, 0, 20000, allow_none=True)
        self.exercise_kcal = _check_range("運動消耗", self.exercise_kcal, 0, 10000, allow_none=True)
        self.active_kcal = _check_range("活動熱量", self.active_kcal, 0, 10000, allow_none=True)
        self.basal_kcal = _check_range("靜態熱量", self.basal_kcal, 0, 5000, allow_none=True)
        if self.steps is not None:
            self.steps = int(_check_range("步數", self.steps, 0, 200000))
        for name, d, hi in (("食物份數", self.foods, 100), ("運動分鐘", self.exercises, 1440)):
            if not isinstance(d, dict):
                raise ValidationError(f"{name} 要是 dict")
            for k, v in d.items():
                _check_range(f"{name}「{k}」", v, 0, hi)
        if not isinstance(self.meals, dict):
            raise ValidationError("meals 要是 dict")
        for k, v in self.meals.items():
            if k not in MEAL_NAMES:
                raise ValidationError(f"餐別要是 {'、'.join(MEAL_NAMES)}，不是「{k}」")
            if v not in MEAL_LEVELS:
                raise ValidationError(f"份量要是 {'、'.join(MEAL_LEVELS)}，不是「{v}」")
        return self

    def merged_with(self, newer: "DailyRecord") -> "DailyRecord":
        """同一天的兩筆合併：新的有值就用新的；foods、exercises、meals 逐項覆蓋"""
        out = DailyRecord(self.date)
        for f in fields(DailyRecord):
            old_v, new_v = getattr(self, f.name), getattr(newer, f.name)
            if f.name in ("foods", "exercises", "meals"):
                setattr(out, f.name, {**old_v, **new_v})
            elif f.name == "note":
                out.note = new_v or old_v
            else:
                setattr(out, f.name, new_v if new_v is not None else old_v)
        if self.source != newer.source:
            out.source = "mixed"
        out.updated_at = _now()
        return out

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


# ───────────────────────── 密碼 ─────────────────────────
def _hash_password(password: str, salt: bytes | None = None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return salt.hex(), digest.hex()


def _verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    _, digest = _hash_password(password, bytes.fromhex(salt_hex))
    return hmac.compare_digest(digest, hash_hex)


# ───────────────────────── 儲存方式 ─────────────────────────
class StorageError(UserError):
    """存取資料失敗（例如雲端資料庫連不上）"""


class LocalStorage:
    """存在電腦上的 JSON 檔（預設）。key 對應到檔案：
        account:<帳號>  → data/accounts/<帳號>.json   帳號、密碼雜湊
        user:<id>       → data/users/<id>.json        個人資料、每日紀錄、登入紀錄
        custom:<種類>   → data/custom_<種類>.json      自訂食物、運動
        truth:<帳號>    → data/truth/<帳號>.json       模擬資料的真實答案
    """
    DIRS = {"account": "accounts", "user": "users", "truth": "truth"}

    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self._migrate_old_accounts()

    def _path(self, key: str) -> str:
        ns, _, name = key.partition(":")
        if not name or "/" in name or "\\" in name or name in (".", "..") or os.sep in name:
            raise StorageError(f"不合法的 key：{key}")
        if ns == "custom":
            return os.path.join(self.root, f"custom_{name}.json")
        return os.path.join(self.root, self.DIRS.get(ns, ns), name + ".json")

    def get(self, key: str):
        path = self._path(key)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def put(self, key: str, obj):
        """先寫暫存檔再取代，避免當機時把檔案寫壞"""
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(path + ".tmp", path)

    def delete(self, key: str):
        path = self._path(key)
        if os.path.exists(path):
            os.remove(path)

    def keys(self, prefix: str) -> list[str]:
        """列出某一類的 key，例如 keys("account:")"""
        ns = prefix.rstrip(":")
        if ns == "custom":
            return sorted("custom:" + f[7:-5] for f in os.listdir(self.root)
                          if f.startswith("custom_") and f.endswith(".json"))
        folder = os.path.join(self.root, self.DIRS.get(ns, ns))
        if not os.path.isdir(folder):
            return []
        return sorted(f"{ns}:{f[:-5]}" for f in os.listdir(folder) if f.endswith(".json"))

    def _migrate_old_accounts(self):
        """舊版把所有帳號放在 data/accounts.json，拆成一人一個檔（舊檔改名保留）"""
        old = os.path.join(self.root, "accounts.json")
        if os.path.isfile(old):
            with open(old, encoding="utf-8") as f:
                for name, acc in json.load(f).items():
                    if self.get(f"account:{name}") is None:
                        self.put(f"account:{name}", acc)
            os.replace(old, old + ".migrated")


class SupabaseStorage:
    """存在 Supabase 雲端資料庫（放上 Streamlit Cloud 給大家用時）。

    需要一張表（在 Supabase 的 SQL Editor 執行一次）：
        create table public.kv (key text primary key, value jsonb not null,
                                updated_at timestamptz not null default now());
        alter table public.kv enable row level security;
        grant select, insert, update, delete on table public.kv to service_role;
    金鑰用 secret key（sb_secret_...），只放在伺服器端（Streamlit 的 Secrets），使用者看不到。
    """

    def __init__(self, url: str, key: str, table: str = "kv", session=None):
        import requests
        from urllib.parse import urlparse
        u = urlparse(url.strip())                   # 只留 https://xxxx.supabase.co，多貼的 /rest/v1 等路徑去掉
        self.base = f"{u.scheme}://{u.netloc}/rest/v1/{table}"
        key = key.strip().strip('"').strip("“”").strip()   # 多貼的空白、換行、引號去掉
        # 出錯時給人看的金鑰「外觀」（不含金鑰本身）：開頭、長度、有沒有怪字元
        odd = sorted({c for c in key if not (c.isascii() and (c.isalnum() or c in "_-."))})
        self.key_info = (f"金鑰開頭 {key[:10]}…，長度 {len(key)}（秘密金鑰應該是 41）"
                         + (f"，含有不該有的字元 {odd}" if odd else ""))
        self.http = session or requests.Session()
        self.http.headers.update({"apikey": key, "Authorization": f"Bearer {key}",
                                  "Content-Type": "application/json"})

    def _call(self, method, **kw):
        try:
            r = self.http.request(method, self.base, timeout=30, **kw)
        except Exception as e:
            raise StorageError(f"連不上雲端資料庫：{e}") from None
        if r.status_code >= 400:
            hints = {"PGRST125": "SUPABASE_URL 只要到 .supabase.co 為止",
                     "PGRST205": "找不到 kv 表，請到 Supabase 的 SQL Editor 執行建表指令（見 README.md）",
                     "PGRST106": "找不到 kv 表，請到 Supabase 的 SQL Editor 執行建表指令（見 README.md）",
                     "42P01": "找不到 kv 表，請到 Supabase 的 SQL Editor 執行建表指令（見 README.md）"}
            hint = next((v for k, v in hints.items() if k in r.text), "")
            if "42501" in r.text and "service_role" in r.text:
                hint = ("秘密金鑰還沒有 kv 表的權限，請到 SQL Editor 執行："
                        "grant select, insert, update, delete on table public.kv to service_role;")
            elif r.status_code in (401, 403) and not hint:
                hint = f"金鑰不對：SUPABASE_KEY 要用 sb_secret_ 開頭的那把；{self.key_info}"
            raise StorageError(f"雲端資料庫錯誤 {r.status_code}" + (f"（{hint}）" if hint else "")
                               + f"：{r.text[:200]}")
        return r

    def get(self, key: str):
        rows = self._call("GET", params={"key": f"eq.{key}", "select": "value"}).json()
        return rows[0]["value"] if rows else None

    def put(self, key: str, obj):
        self._call("POST", json={"key": key, "value": obj, "updated_at": _now()},
                   headers={"Prefer": "resolution=merge-duplicates,return=minimal"})

    def delete(self, key: str):
        self._call("DELETE", params={"key": f"eq.{key}"})

    def keys(self, prefix: str) -> list[str]:
        rows = self._call("GET", params={"key": f"like.{prefix}*", "select": "key"}).json()
        return sorted(r["key"] for r in rows)


def cloud_storage_from_secrets(folder: str | None = None) -> "SupabaseStorage":
    """給在電腦上執行的腳本用（simulate_data.py --cloud、migrate_to_cloud.py）：
    先看環境變數，沒有的話讀 .streamlit/secrets.toml（跟 app 用同一份設定）"""
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if not (url and key):
        folder = folder or os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(folder, ".streamlit", "secrets.toml")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                conf = dict(re.findall(r'^\s*(\w+)\s*=\s*"([^"]*)"', f.read(), re.M))
            url, key = conf.get("SUPABASE_URL"), conf.get("SUPABASE_KEY")
    if not (url and key):
        raise StorageError("找不到雲端設定：請設定 SUPABASE_URL、SUPABASE_KEY，"
                           "或建立 .streamlit/secrets.toml（見 README.md）")
    if not url.startswith("https://") or "貼上" in url or "貼上" in key:
        raise StorageError(".streamlit/secrets.toml 還沒填好：把引號裡的文字換成 Supabase 的 "
                           "Project URL（https:// 開頭）和 sb_secret_ 開頭的金鑰")
    return SupabaseStorage(url, key)


def get_storage(data_dir: str):
    """有設定環境變數 SUPABASE_URL、SUPABASE_KEY 就用雲端，不然存在電腦上"""
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if url and key:
        return SupabaseStorage(url, key)
    return LocalStorage(data_dir)


# ───────────────────────── 主類別 ─────────────────────────
class UserManager:
    """所有讀寫都經過這裡。資料存在哪裡由 storage 決定（見 get_storage）。

    cache=True：同一個 UserManager 讀過的資料記在記憶體裡，寫入時一起更新。
    app 每次畫面更新都會建一個新的，所以開著也沒關係；雲端時可以少很多次連線。
    一直開著的程式（例如文字選單）不要開，才看得到別的程式寫進去的資料。
    """

    def __init__(self, data_dir: str | None = None, storage=None, cache: bool = False):
        # 不指定的話，存在「這個 .py 檔旁邊」的 data 資料夾，
        # 這樣不管從哪個位置執行（例如 VS Code 沒開資料夾時是從 / 執行）都會存到同一個地方
        if data_dir is None:
            data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)       # 匯出 CSV 等檔案放這裡
        self.storage = storage or get_storage(data_dir)
        self.cache = cache
        self._cache: dict = {}

    @property
    def is_cloud(self) -> bool:
        return isinstance(self.storage, SupabaseStorage)

    # ── 讀寫（經過快取）──
    def _get(self, key):
        if self.cache and key in self._cache:
            return copy.deepcopy(self._cache[key])
        v = self.storage.get(key)
        if self.cache:
            self._cache[key] = copy.deepcopy(v)
        return v

    def _put(self, key, obj):
        self.storage.put(key, obj)
        if self.cache:
            self._cache[key] = copy.deepcopy(obj)

    def _delete(self, key):
        self.storage.delete(key)
        self._cache.pop(key, None)

    def _account(self, username: str) -> dict | None:
        username = (username or "").strip()
        if not USERNAME_RE.match(username):      # 不合格式的帳號一定不存在（也避免怪字元進到路徑）
            return None
        return self._get(f"account:{username}")

    def _user_key(self, username):
        acc = self._account(username)
        if not acc:
            raise UserNotFoundError(f"找不到帳號「{username}」")
        return f"user:{acc['user_id']}"

    def _load(self, username):
        return self._get(self._user_key(username))

    def _save(self, username, doc):
        self._put(self._user_key(username), doc)

    # ── 帳號 ──
    def exists(self, username: str) -> bool:
        return self._account(username) is not None

    def list_users(self) -> list[str]:
        return [k.split(":", 1)[1] for k in self.storage.keys("account:")]

    def register(self, username: str, password: str, *, sex: str, birth_date: str,
                 height_cm: float, weight_kg: float, activity_level: int = 1,
                 goal_weight_kg: float | None = None, display_name: str = "") -> UserProfile:
        """註冊。會自動記錄註冊時間，並把註冊體重存成當天的第一筆紀錄。"""
        username = (username or "").strip()
        if not USERNAME_RE.match(username):
            raise ValidationError("帳號要 2～20 個字，只能用英數字、底線或中文")
        if self.exists(username):
            raise UserExistsError(f"帳號「{username}」已經有人用了")
        if len(password or "") < 4:
            raise ValidationError("密碼至少 4 個字")

        profile = UserProfile(username, display_name, sex, birth_date, height_cm,
                              weight_kg, int(activity_level), goal_weight_kg).validate()
        salt, pw_hash = _hash_password(password)
        acc = {"user_id": uuid.uuid4().hex, "salt": salt,
               "password_hash": pw_hash, "created_at": profile.registered_at}
        first = DailyRecord(date.today().isoformat(), weight_kg=profile.initial_weight_kg,
                            note="註冊時體重")
        doc = {"profile": asdict(profile), "records": {first.date: first.to_dict()},
               "login_history": []}
        self._put(f"user:{acc['user_id']}", doc)          # 先存資料，再建帳號
        self._put(f"account:{username}", acc)
        return profile

    def login(self, username: str, password: str) -> UserProfile:
        """登入成功回傳 UserProfile，並記下登入時間；失敗丟 UserNotFoundError / AuthError"""
        name = (username or "").strip()
        acc = self._account(name)
        if not acc:
            raise UserNotFoundError(f"找不到帳號「{username}」")
        if not _verify_password(password or "", acc["salt"], acc["password_hash"]):
            raise AuthError("密碼錯誤")
        doc = self._load(name)
        doc["login_history"] = ([_now()] + doc.get("login_history", []))[:100]
        self._save(name, doc)
        return UserProfile.from_dict(doc["profile"])

    def change_password(self, username: str, old: str, new: str):
        acc = self._account(username)
        if not acc:
            raise UserNotFoundError(f"找不到帳號「{username}」")
        if not _verify_password(old, acc["salt"], acc["password_hash"]):
            raise AuthError("舊密碼錯誤")
        if len(new) < 4:
            raise ValidationError("密碼至少 4 個字")
        acc["salt"], acc["password_hash"] = _hash_password(new)
        self._put(f"account:{username.strip()}", acc)

    def delete_user(self, username: str, password: str):
        """刪除帳號和所有紀錄（要密碼確認，刪了就救不回來）"""
        acc = self._account(username)
        if not acc:
            raise UserNotFoundError(f"找不到帳號「{username}」")
        if not _verify_password(password or "", acc["salt"], acc["password_hash"]):
            raise AuthError("密碼錯誤")
        self._delete(f"user:{acc['user_id']}")
        self._delete(f"truth:{username.strip()}")
        self._delete(f"account:{username.strip()}")

    def login_history(self, username: str) -> list[str]:
        """登入時間，最新的在前面（最多保留 100 筆）"""
        return list(self._load(username).get("login_history", []))

    # ── 個人資料 ──
    def get_profile(self, username: str) -> UserProfile:
        return UserProfile.from_dict(self._load(username)["profile"])

    def update_profile(self, username: str, /, **changes) -> UserProfile:
        """例：update_profile("dino", height_cm=176, goal_weight_kg=65)"""
        doc = self._load(username)
        locked = {"username", "registered_at"}
        unknown = set(changes) - {f.name for f in fields(UserProfile)}
        if unknown or locked & set(changes):
            raise ValidationError(f"不能修改的欄位：{', '.join(sorted(unknown | (locked & set(changes))))}")
        profile = UserProfile.from_dict({**doc["profile"], **changes}).validate()
        doc["profile"] = asdict(profile)
        self._save(username, doc)
        return profile

    # ── 每日紀錄 ──
    def save_record(self, username: str, record: DailyRecord, merge: bool = True) -> DailyRecord:
        """存一天的紀錄。merge=True 時跟同一天已有的資料合併（預設）"""
        record.validate()
        doc = self._load(username)
        old = doc["records"].get(record.date)
        if old and merge:
            record = DailyRecord.from_dict(old).merged_with(record)
        doc["records"][record.date] = record.to_dict()
        self._save(username, doc)
        return record

    def get_record(self, username: str, day: str) -> DailyRecord | None:
        d = self._load(username)["records"].get(_check_date("日期", day))
        return DailyRecord.from_dict(d) if d else None

    def get_records(self, username: str, start: str | None = None,
                    end: str | None = None) -> list[DailyRecord]:
        """依日期排序；start、end 都包含在內，不填就是全部"""
        recs = self._load(username)["records"]
        s = _check_date("開始日期", start) if start else "0000-00-00"
        e = _check_date("結束日期", end) if end else "9999-99-99"
        return [DailyRecord.from_dict(recs[k]) for k in sorted(recs) if s <= k <= e]

    def get_rows(self, username: str, start: str | None = None, end: str | None = None) -> list[dict]:
        """跟 get_records 一樣，但回傳 list[dict]（可以直接丟給 pandas.DataFrame）"""
        return [r.to_dict() for r in self.get_records(username, start, end)]

    def delete_record(self, username: str, day: str) -> bool:
        doc = self._load(username)
        removed = doc["records"].pop(_check_date("日期", day), None) is not None
        if removed:
            self._save(username, doc)
        return removed

    def weight_history(self, username: str) -> list[tuple[str, float]]:
        """[(日期, 體重), ...]，只列有量體重的日子"""
        return [(r.date, r.weight_kg) for r in self.get_records(username) if r.weight_kg is not None]

    def latest_weight(self, username: str) -> float:
        ws = self.weight_history(username)
        return ws[-1][1] if ws else self.get_profile(username).initial_weight_kg

    # ── 從健康 app 匯入（搭配 B 的 read_health.load_health）──
    def import_health_data(self, username: str, health: dict, fill_profile: bool = True) -> dict:
        """把 read_health.load_health() 的結果存進來。

        - 個人資料只補「空白或不同」的身高、性別、出生日期（fill_profile=False 就不動）
        - 每日紀錄：體重、步數、活動熱量、靜態熱量、飲食熱量，跟手動資料合併
          （同一天同一欄位兩邊都有值時，以手動輸入的為準）
        回傳 {"days": 匯入幾天, "profile_updated": [改了哪些欄位], "skipped": [跳過的日子與原因]}
        """
        changed = []
        hp = health.get("profile") or {}
        if fill_profile:
            prof = self.get_profile(username)
            updates = {}
            if hp.get("sex") in SEXES and hp["sex"] != prof.sex:
                updates["sex"] = hp["sex"]
            if hp.get("birth") and hp["birth"] != prof.birth_date:
                updates["birth_date"] = hp["birth"]
            if hp.get("height") and abs(hp["height"] - prof.height_cm) >= 0.5:
                updates["height_cm"] = hp["height"]
            if updates:
                self.update_profile(username, **updates)
                changed = list(updates)

        by_day: dict[str, DailyRecord] = {}

        def rec(d):
            return by_day.setdefault(d, DailyRecord(d, source="health_app"))

        for d, v in (health.get("weights") or {}).items():
            rec(d).weight_kg = v
        for d, v in (health.get("steps") or {}).items():
            rec(d).steps = int(v)
        for d, v in (health.get("active_kcal") or {}).items():
            rec(d).active_kcal = v
        for d, v in (health.get("basal_kcal") or {}).items():
            rec(d).basal_kcal = v
        for d, v in (health.get("diet_kcal") or {}).items():
            rec(d).intake_kcal = v

        # 一次讀寫，避免幾百天各存一次檔；數值不合理的那天跳過，不影響其他天
        # 合併規則：手動輸入的值優先，健康 app 只補空白的欄位
        doc = self._load(username)
        skipped = []
        for d, r in sorted(by_day.items()):
            try:
                r.validate()
            except ValidationError as e:
                skipped.append(f"{d}：{e}")
                continue
            old = doc["records"].get(d)
            doc["records"][d] = (r.merged_with(DailyRecord.from_dict(old)) if old else r).to_dict()
        self._save(username, doc)
        return {"days": len(by_day) - len(skipped), "profile_updated": changed, "skipped": skipped}


# ───────────────────────── 文字選單（手動測試用）─────────────────────────
def _ask(prompt, cast=str, allow_empty=False):
    while True:
        s = input(prompt).strip()
        if not s and allow_empty:
            return None
        try:
            return cast(s)
        except (ValueError, ValidationError) as e:
            print(f"  格式不對：{e}")


def _ask_password(prompt="密碼："):
    try:
        import getpass
        return getpass.getpass(prompt)
    except Exception:            # 某些 IDE 的主控台不支援隱藏輸入
        return input(prompt)


def _ask_birth():
    """可以輸入出生日期，也可以只輸入年齡"""
    while True:
        s = input("出生日期 YYYY-MM-DD（或直接輸入年齡）：").strip()
        if s.isdigit() and len(s) <= 3:
            t = date.today()
            return date(t.year - int(s), 1, 1).isoformat()
        try:
            return _check_date("出生日期", s)
        except ValidationError as e:
            print(f"  {e}")


def _show_profile(um, p):
    print(f"\n帳號：{p.username}（{p.display_name}）  註冊於 {p.registered_at}")
    print(f"性別 {p.sex}｜{p.age} 歲｜身高 {p.height_cm:g} cm｜"
          f"最新體重 {um.latest_weight(p.username):g} kg"
          + (f"｜目標 {p.goal_weight_kg:g} kg" if p.goal_weight_kg else ""))
    print(f"活動量：{ACTIVITY_LEVELS[p.activity_level][0]}")


def _show_history(um, username, n=14):
    recs = um.get_records(username)[-n:]
    if not recs:
        print("還沒有紀錄")
        return
    print(f"\n{'日期':<12}{'體重':>7}{'攝取':>8}{'運動':>7}{'步數':>8}  來源")
    for r in recs:
        def f(v, fmt="{:.0f}"):
            return "—" if v is None else fmt.format(v)
        print(f"{r.date:<12}{f(r.weight_kg, '{:.1f}'):>8}{f(r.intake_kcal):>9}"
              f"{f(r.exercise_kcal):>8}{f(r.steps):>9}  {r.source}")


def _user_menu(um, p):
    while True:
        print(f"\n[{p.display_name}] 1個人資料 2修改資料 3記錄今天 4歷史紀錄 "
              f"5登入紀錄 6匯入健康app 7改密碼 8登出")
        c = input("> ").strip()
        try:
            if c == "1":
                _show_profile(um, p)
            elif c == "2":
                print("直接按 Enter 表示不改")
                ch = {}
                h = _ask(f"身高（{p.height_cm:g}）：", float, True)
                g = _ask(f"目標體重（{p.goal_weight_kg or '未設定'}）：", float, True)
                a = _ask(f"活動量 1～4（{p.activity_level}）：", int, True)
                if h is not None:
                    ch["height_cm"] = h
                if g is not None:
                    ch["goal_weight_kg"] = g
                if a is not None:
                    ch["activity_level"] = a
                if ch:
                    p = um.update_profile(p.username, **ch)
                    print("已更新")
            elif c == "3":
                w = _ask("今天體重 kg（Enter 跳過）：", float, True)
                i = _ask("今天總攝取大卡（Enter 跳過）：", float, True)
                e = _ask("今天運動消耗大卡（Enter 跳過）：", float, True)
                r = um.save_record(p.username, DailyRecord(date.today().isoformat(), w, i, e))
                print(f"已存：{r.date} 體重 {r.weight_kg} 攝取 {r.intake_kcal} 運動 {r.exercise_kcal}")
            elif c == "4":
                _show_history(um, p.username)
            elif c == "5":
                for t in um.login_history(p.username)[:10]:
                    print(" ", t.replace("T", " "))
            elif c == "6":
                try:
                    from read_health import find_export, load_health
                except ImportError:
                    print("找不到 read_health.py，請放在同一個資料夾")
                    continue
                here = os.path.dirname(os.path.abspath(__file__))
                path = input("健康 app 匯出檔或資料夾（直接按 Enter = 程式所在的資料夾）：").strip() or here
                if not os.path.isabs(os.path.expanduser(path.strip('"').strip("'"))) and not os.path.exists(path):
                    path = os.path.join(here, path)     # 只打檔名的話，到程式所在的資料夾找
                try:
                    path = find_export(path)
                    print(f"讀取 {os.path.basename(path)} 中…（檔案大的話要等一下）")
                    res = um.import_health_data(p.username, load_health(path, days=None))
                except Exception as e:                   # 檔案壞掉、格式不對等，都不要讓程式整個停掉
                    print(f"  ✗ 匯入失敗：{e}")
                    continue
                p = um.get_profile(p.username)
                print(f"匯入 {res['days']} 天" +
                      (f"，更新個人資料：{', '.join(res['profile_updated'])}" if res["profile_updated"] else ""))
                for s in res["skipped"]:
                    print(f"  跳過 {s}")
            elif c == "7":
                um.change_password(p.username, _ask_password("舊密碼："), _ask_password("新密碼："))
                print("密碼已更新")
            elif c == "8":
                return
        except (UserError, FileNotFoundError) as e:
            print(f"  ✗ {e}")


def main():
    um = UserManager()
    while True:
        c = input("\n1註冊 2登入 3離開：").strip()
        try:
            if c == "1":
                name = input("帳號：").strip()
                pw = _ask_password()
                sex = _ask("性別（男/女）：")
                birth = _ask_birth()
                h = _ask("身高 cm：", float)
                w = _ask("體重 kg：", float)
                for k, (label, _) in ACTIVITY_LEVELS.items():
                    print(f"  {k} {label}")
                a = _ask("日常活動量 1～4：", int)
                g = _ask("目標體重 kg（Enter 跳過）：", float, True)
                p = um.register(name, pw, sex=sex, birth_date=birth, height_cm=h,
                                weight_kg=w, activity_level=a, goal_weight_kg=g)
                print(f"註冊成功！註冊時間 {p.registered_at.replace('T', ' ')}")
                _user_menu(um, um.login(name, pw))
            elif c == "2":
                p = um.login(input("帳號：").strip(), _ask_password())
                hist = um.login_history(p.username)
                last = hist[1].replace("T", " ") if len(hist) > 1 else "第一次登入"
                print(f"歡迎回來，{p.display_name}！上次登入：{last}")
                _user_menu(um, p)
            elif c == "3":
                break
        except UserError as e:
            print(f"  ✗ {e}")


if __name__ == "__main__":
    main()
