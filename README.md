# 熱量帳本

每天量體重、隨手記三餐，用體重變化自動校正記錄的熱量，再用三個文獻模型預測體重，並依照各模型最近準不準自動調整權重。

> 這是學生專題，所有數字都是估算，不是醫療建議。

## 檔案

| 組別 | 檔案 | 內容 |
|---|---|---|
| A 使用者管理 | `user_management.py` | 註冊、登入、個人資料、紀錄儲存（電腦上的檔案或 Supabase 雲端） |
| B 資料處理 | `data_processing.py`、`read_health.py` | 熱量換算、快速記錄、資料清理、體重校正、匯入 iPhone 健康 app |
| C 預測模型 | `prediction_models.py` | Wishnofsky（1958）、Antonetti 型（1973）、Hall 簡化版（2011） |
| D 自適應學習 | `adaptive.py` | 滾動回測、模型權重、準確度評估 |
| E 介面 | `app.py`、`themes.py` | Streamlit 網頁 app、四種外觀主題（使用者在「設定 → 外觀」自己選） |
| 其他 | `simulate_data.py` | 產生模擬資料（`demo` 帳號） |
| | `migrate_to_cloud.py` | 把電腦上的帳號複製到雲端 |
| | `test_*.py` | 自動測試：`python -m unittest discover` |

## 在自己的電腦上跑

```bash
pip install -r requirements.txt
streamlit run app.py
```

資料會存在旁邊的 `data/` 資料夾。

## 放上網路給大家用

會用到三個免費服務：**GitHub** 放程式、**Supabase** 存資料、**Streamlit Community Cloud** 跑 app。整個流程大約 20 分鐘。

### 1. Supabase：建立資料庫

1. 到 <https://supabase.com> 註冊，按 **New project**。
   - Region 建議選美國東部（US East），因為 Streamlit 的主機在美國，選近一點速度比較快。
   - Database password 自己設一個，記下來就好，app 用不到它。
2. 專案建好後，左邊點 **SQL Editor**，貼上下面這段，按 **Run**：

   ```sql
   create table public.kv (
     key text primary key,
     value jsonb not null,
     updated_at timestamptz not null default now()
   );
   alter table public.kv enable row level security;
   grant select, insert, update, delete on table public.kv to service_role;
   ```

   `enable row level security` 會鎖住這張表，最後一行再只開放給 secret key（也就是你的 app），所以公開的 publishable key 讀不到任何資料。

3. 左邊點 **Project Settings → API Keys**，記下兩個東西：
   - **Project URL**：像 `https://abcdefgh.supabase.co`（在 Data API 或 Project Settings 的頁面上）
   - **Secret key**：`sb_secret_` 開頭的那一把。**不要**用 publishable key，也**絕對不要**把 secret key 貼到 GitHub 或傳給別人。

### 2. GitHub：上傳程式

1. 到 <https://github.com> 註冊，右上角 **+ → New repository**，取個名字（例如 `calorie-diary`），按 **Create repository**。
2. 在新的 repository 頁面按 **uploading an existing file**，把下面這些檔案拖進去，再按 **Commit changes**：

   ```
   app.py  themes.py  user_management.py  data_processing.py  read_health.py
   prediction_models.py  adaptive.py  simulate_data.py  migrate_to_cloud.py
   requirements.txt  README.md
   ```

   **不要上傳**：`data/` 資料夾、`輸出.xml`、`export_cda.xml`、`想法/`、`.streamlit/`。這些是個人健康資料或金鑰。用 git 指令上傳的話，`.gitignore` 會自動排除它們。

### 3. Streamlit Community Cloud：上線

1. 到 <https://share.streamlit.io>，用 GitHub 帳號登入，允許它讀取你的 repository。
2. 按 **Create app → Deploy a public app from GitHub**：
   - Repository：剛剛建立的那個
   - Branch：`main`
   - Main file path：`app.py`
   - App URL：可以自己取，例如 `calorie-diary-tw`
3. 按 **Advanced settings**，在 **Secrets** 貼上（換成你自己的值）：

   ```toml
   SUPABASE_URL = "https://abcdefgh.supabase.co"
   SUPABASE_KEY = "sb_secret_xxxxxxxxxxxxxxxx"
   ```

4. 按 **Deploy**，等幾分鐘。完成後會得到一個網址，例如 `https://calorie-diary-tw.streamlit.app`，把它傳給大家就能用了。手機打開後可以「加入主畫面」。

之後只要更新 GitHub 上的檔案，app 會自動跟著更新。

### 4.（選擇性）把 demo 帳號或自己的資料放上雲端

在電腦上這個資料夾裡建立 `.streamlit/secrets.toml`，內容跟上面的 Secrets 一樣（這個檔案已經被 `.gitignore` 排除，不會被上傳），然後：

```bash
python simulate_data.py --cloud        # 在雲端建立 demo 帳號（密碼 demo1234）
python migrate_to_cloud.py test        # 把電腦上的 test 帳號複製上去
```

注意：建立 `.streamlit/secrets.toml` 之後，在電腦上執行 `streamlit run app.py` 也會改連雲端資料庫。想回到只存在電腦上，把這個檔案改名就好。

## 注意事項

- **免費方案的限制**
  - Supabase 免費專案如果一週內資料庫活動太少，會被自動暫停（暫停前會先寄信通知），到 Supabase 後台按 **Resume project** 就能恢復。資料庫上限 500 MB，一個人記一年大約只用 0.15 MB。
  - Streamlit 的 app 一段時間沒人用會休眠，有人打開時會自動喚醒，要等大約 30 秒。
  - 免費方案沒有自動備份。
- **隱私**
  - 這是健康資料，資料存在美國的伺服器上。請只邀請知情、同意的人使用，不要幫別人上傳他的資料。
  - 密碼經過 PBKDF2 加密雜湊，連管理者也看不到明碼。
  - 每個人只能看到自己的紀錄，也可以在 app 的「設定 → 刪除帳號」刪除所有資料。
  - 因為管理者（持有 Supabase 帳號的人）可以在 Supabase 後台看到資料庫內容，請在使用說明裡告訴大家這件事。
- **金鑰外洩怎麼辦**：如果不小心把 secret key 貼到 GitHub 或其他地方，馬上到 Supabase 的 **API Keys** 頁面建立一把新的 secret key，把 Streamlit Secrets 改成新的，再刪掉舊的。
