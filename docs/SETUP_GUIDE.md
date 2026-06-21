# AniGamerDownloader 設定指南

> 從零把巴哈姆特動畫瘋 VIP 帳號的動畫下載到本機的 SOP（原始碼／開發用）。
> 只想用免裝 Python 的可攜包或 Docker，見 [README](../README.md) 與 [docs/DESKTOP_BUILD.md](DESKTOP_BUILD.md)。

---

## 1. 安裝

```powershell
git clone https://github.com/lilasrepo/AniGamerDownloader.git
cd AniGamerDownloader

# 虛擬環境（Python 3.13）
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-venv.txt
.venv\Scripts\python -m pip install pywebview    # 只有桌面視窗需要
```

- **ffmpeg** 是硬性需求（解密 + 合併分段）：確保系統 `PATH` 上有 `ffmpeg`，或把 `ffmpeg.exe` 放到程式目錄。
- 設定 / cookie / 資料庫 / 日誌都在 `data\`；**首次啟動若無 `data\` 會自動建立並寫入預設值**（解析度 1080、dashboard 5000）。

---

## 2. 設定 VIP cookie

程式靠一份 `Cookie:` 標頭字串認證（決定畫質與 VIP 身分）。兩種設定方式：

- **（推薦）Web 介面**：啟動後到「設定」分頁的「帳號 Cookie」區塊貼上 cookie 與 UA、儲存。
- **手動**：cookie 單行存到 `data\cookie.txt`（UTF-8、結尾不換行），UA 填進 `data\config.json` 的 `ua`。

取得 cookie 的圖文步驟見[原專案說明](README.md#cookietxt)。重點：

- 用瀏覽器**無痕視窗**登入 <https://ani.gamer.com.tw>，勾「保持登入狀態」；`F12` → Network → 複製對 `ani.gamer.com.tw` 請求的 `Cookie:` 整串。
- **UA 必須與取 cookie 的同一瀏覽器一致**，否則 cookie 自動刷新會失效。
- 🚨 cookie 解析有**帳號被封鎖風險且不可解封**（[issue #207](https://github.com/miyouzi/aniGamerPlus/issues/207)）；建議用無痕取一份「專供本工具」的 cookie。
- **1080p 只有 VIP 串流才有**，非 VIP 會自動降畫質。
- 登入裝置可在 <https://home.gamer.com.tw/login_devices.php> 管理／登出。

---

## 3. 下載

**sn = 動畫網址 `?sn=` 後的數字**（`...animeVideo.php?sn=12345` → `12345`）。兩種方式：

1. **Web「手動任務」分頁**（daemon 開著時）：填 sn + 模式即可。
2. **一次性 CLI**：

```powershell
.venv\Scripts\python cli.py -s 12345            # 該 sn 那一集
.venv\Scripts\python cli.py -s 12345 -m all     # 整部（全集）
.venv\Scripts\python cli.py -s 12345 -e 2,5-8   # 指定集數
.venv\Scripts\python cli.py -s 12345 -i         # 只查資訊（驗證 cookie / VIP / 可用畫質）
```

常用旗標：`-m {single,latest,all,...}`、`-e 集數`、`-r {360,480,540,576,720,1080}`、`-d`（彈幕）、`-n`（不建資料夾）。
完整參數 `python cli.py -h` 或見[原專案說明](README.md)。CLI 一次性模式**不寫資料庫、會強制重抓**。

下載結構（`classify_bangumi` + `classify_season`）：

```
<下載目錄>\
└─ 作品名\
   └─ Season 2\
      └─ 【動畫瘋】作品名 第二季[01][1080P].mp4
```

季別子資料夾用英文 `Season N` / `Specials` / `Movie`，由標題的「第N季」解析；無季別資訊者落在 `作品名\Season 1\`。

---

## 4. 追番（自動巡檢）

- 編輯 `data\sn_list.txt`，每行一部：`sn 模式 <自訂資料夾名> # 註解`（模式只認 `all` / `latest` / `largest-sn`）。
- 啟動 daemon（`aniGamer.bat` 或 `python shells\desktop.py`）會讀 `sn_list.txt`、每 `check_frequency` 分鐘檢查新集、寫入 `data\aniGamer.db`。
- 要開機自動跑：用 **Windows 工作排程器**定時呼叫 `aniGamer.bat`。

---

## 5. 疑難排解

| 症狀 | 原因 / 解法 |
|---|---|
| `UnicodeEncodeError: 'cp950'...` | 終端非 UTF-8；入口已自動設 UTF-8，仍出錯就 `set PYTHONUTF8=1`。 |
| 檔案沒下到指定目錄 | `config.json` 的 `bangumi_dir`/`temp_dir` 路徑不存在會**靜默退回** `data\bangumi`、`data\temp`；確認路徑存在。 |
| 畫質只有 720p | cookie 沒設或非 VIP；用 `cli.py -s <sn> -i` 看是否識別到 VIP。 |
| 登入失效、變遊客 | 出現 `data\invalid_cookie.txt` 代表 cookie 被判失效；重抓放回 `data\cookie.txt`。 |
| 改了 cookie 沒用 | `ua` 沒對應到取 cookie 的瀏覽器；把該瀏覽器的 User-Agent 填回 `config.json` 的 `ua`。 |
| `ModuleNotFoundError` | 沒用 venv 跑；一定要用 `.venv\Scripts\python.exe`。 |
| 設定全變回預設 | config 讀取出錯時程式會重置成預設（`core/config.py`）；改 config 前先備份 `config.json`。 |

---

## 6. 重要檔案（都在 `data\`，已 `.gitignore`，不進版控）

| 路徑 | 用途 |
|---|---|
| `data\config.json` | 主設定 |
| `data\cookie.txt` | VIP cookie（機密） |
| `data\sn_list.txt` | 追番清單 |
| `data\aniGamer.db` | 下載紀錄 sqlite |
| `data\logs\` | 日誌 |
| `requirements-venv.txt` | 依賴清單 |
| `aniGamer.bat` | 主啟動器（桌面視窗 + 背景 daemon + 控制臺） |
