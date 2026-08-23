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
.venv\Scripts\python -m pip install -r shells\requirements-desktop.txt   # 桌面視窗需要 (pywebview + 打包工具)
```

- **ffmpeg** 是硬性需求（解密 + 合併分段）：確保系統 `PATH` 上有 `ffmpeg`，或把 `ffmpeg.exe` 放到程式目錄。
- 設定 / cookie / 資料庫 / 日誌都在 `data\`；**首次啟動若無 `data\` 會自動建立並寫入預設值**（解析度 1080、dashboard 5000）。

---

## 2. 設定 VIP cookie

程式靠一份 `Cookie:` 標頭字串認證（決定畫質與 VIP 身分）。三種設定方式：

- **（桌面版最推薦）介面內登入**：「設定」分頁 →「帳號 Cookie」→「**開啟登入視窗（無痕）**」。
  會開一個獨立的無痕視窗，登入完成後自動擷取並寫入 cookie，接著自動跑一次帳號健檢。
  優點：能拿到瀏覽器 JavaScript 讀不到的 **HttpOnly** cookie（`BAHARUNE` 等），
  且登入視窗直接套用 `config.json` 的 `ua`，**不會有 UA 不一致的問題**。
  只在 pywebview 桌面視窗可用；用瀏覽器連 dashboard 或 Docker 版看不到這顆按鈕。
- **Web 介面貼上**：到「設定」分頁的「帳號 Cookie」區塊貼上 cookie 與 UA、儲存。
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

### 啟動時不會自動連線
預設（`start_paused: true`）程式**開啟後不會對外發出任何請求**，包含網站與 GitHub 版本檢查，
「監控」分頁會顯示 **待命中（尚未連線）**。要開始巡檢，按「**繼續**」或「**立即檢查**」
（立即檢查會一併解除暫停）。想恢復舊行為就到「設定」分頁關掉「啟動時不連線」。

### 畫質下限（老番也抓得到）
`download_resolution` 是**想要**的畫質，`min_resolution`（預設 720）是**最低可接受**的畫質。
兩者之間有落差時會自動改用可用範圍內最好的那一個：

| 作品 | 站上提供 | 結果 |
|---|---|---|
| Re:0 第四季 | …/1080P | 1080P 下載 |
| KERORO 軍曹（2004） | 360/540/720P | **720P 下載** |
| 只有 480P 的老番 | 480/360P | 標記**已跳過**，不再重試 |
| cookie 失效變遊客 | 360P | 標記**已跳過**（正是要擋的情況） |

下限不是多餘的：cookie 過期變成遊客時站上只會給低畫質，沒有下限就會**默默存下 360P 並在資料庫標記完成**，
之後很難發現。下限把「這部本來就只有 SD」和「你的登入壞了」分開。

「已跳過」是終端狀態，daemon 不會再重新解析它 —— 這也順便根除了舊版每輪重新解析整部老番的流量浪費。
要重來的話，到「資料庫」分頁按**重置**即可。

### 連續失敗會自動停手
`auto_pause_after_failures`（預設 5，填 0 關閉）：連續這麼多個任務失敗就自動暫停批次，
徽章顯示 **已自動暫停（連續失敗 N）**，滑鼠移上去可看最後一次失敗原因。
中間只要有一集下載成功就會歸零。
這是為了避免網站改版、cookie 失效或被限流時，daemon 整晚全速重試而讓帳號被判異常。
排除問題後按「繼續」即可（會一併把計數歸零）。

---

## 5. 疑難排解

| 症狀 | 原因 / 解法 |
|---|---|
| `UnicodeEncodeError: 'cp950'...` | 終端非 UTF-8；入口已自動設 UTF-8，仍出錯就 `set PYTHONUTF8=1`。 |
| 檔案沒下到指定目錄 | `config.json` 的 `bangumi_dir`/`temp_dir` 路徑不存在時會退回 `data\bangumi`、`data\temp`，並在日誌印**一次紅色警告**；照警告建立原資料夾，或到「設定」分頁改成實際路徑。 |
| 之前正常、突然整批下載失敗 | 可能是動畫瘋改版。到「監控」分頁按「網站健檢」（或 `python cli.py --doctor`）看是哪一步壞掉，再對照 [docs/SITE_API.md](SITE_API.md) 修復。 |
| 徽章顯示「已自動暫停（連續失敗 N）」 | 失敗保護生效，已停止繼續請求。先跑「網站健檢」與「帳號健檢」找出原因（多半是改版或 cookie 失效），修好後按「繼續」。 |
| 開了程式卻沒動靜 | 正常：預設啟動不連線。到「監控」分頁按「繼續」或「立即檢查」。 |
| 某部老番一直「已跳過」 | 該作品的最高畫質低於 `min_resolution`。資料庫分頁會顯示實際偵測到的畫質；接受的話把「最低可接受畫質」調低再按重置。 |
| 整批突然都變「已跳過」 | 不是作品的問題，是 **cookie 失效變遊客**（遊客只看得到低畫質）。跑「帳號健檢」確認，重新登入後按重置。 |
| 畫質只有 720p | cookie 沒設或非 VIP；用 `cli.py -s <sn> -i` 看是否識別到 VIP。 |
| 登入失效、變遊客 | 出現 `data\invalid_cookie.txt` 代表 cookie 被判失效；桌面版按「開啟登入視窗（無痕）」重登即可，或重抓放回 `data\cookie.txt`。可用「監控」分頁的「**帳號健檢**」確認伺服器是否仍接受此帳號、是否為 VIP。 |
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

---

## 7. 更新既有安裝

**狀態全在 `data\`**，更新只換程式碼、不動 `data\`：

- **原始碼**：`git pull` → `.venv\Scripts\python -m pip install -r requirements-venv.txt -r shells\requirements-desktop.txt` → 重新啟動 `aniGamer.bat`。
- **可攜桌面包 / Docker**：見 [README 的「更新」段](../README.md#更新)。

啟動時若 GitHub 有新版，控制台日誌會提示（僅通知、不自動更新）；比對的是 `lilasrepo/AniGamerDownloader` 的最新 Release。

### 發佈新版（維護者流程）

CI 會擋「程式內版本 ≠ 標籤」的發佈，因此順序固定：

1. 更新 `core/config.py` 的 `AniGamerDownloader_version`（例如 `v0.1.2`），使其**與即將打的標籤一致**。
2. commit。
3. 打標籤並推送：`git tag v0.1.2 && git push origin v0.1.2`。
4. GitHub Actions 先跑 `version-guard`（比對程式內版本與標籤，不一致直接失敗），再建 Windows 可攜包 + Docker 映像並建立 Release。
5. Release 說明可事後用 `gh release edit <tag> --notes-file ...` 補上。

> 更新檢查用**版本 tuple** 比較，標籤請維持數字式 `vX.Y.Z`。

---

## 8. 搬遷到新電腦

1. **新機安裝**：照 §1 裝好——**Python 3.13**、venv、`requirements-venv.txt` + `shells\requirements-desktop.txt`、以及 `ffmpeg`。
2. **舊機關閉**：先停掉舊機的程式，避免兩邊同時下載。
3. **還原資料**（兩種擇一）：
   - **備份 zip（推薦）**：在舊機控制台「設定」分頁按「備份資料 (zip)」，把 zip 內的 4 個檔解壓到新機的 `data\`。
   - **手動複製**：把舊機 `data\` 的 `config.json`、`cookie.txt`、`sn_list.txt`、`aniGamer.db` 複製到新機 `data\`。
4. **cookie 綁 UA、不綁機器**：cookie 可跨機沿用，但 `config.json` 的 `ua` 必須是**當初取 cookie 的瀏覽器 UA**；換了瀏覽器就重取一份。
5. **下載目錄**：若新機沒有舊的下載碟（例如舊機是 `F:\capture`），啟動時日誌會印**一次紅色警告**並暫時退回 `data\bangumi`；到「設定」分頁改成新機實際路徑，或先把資料夾建好。
6. **驗收**：`.venv\Scripts\python cli.py -s <sn> -i` 能識別 VIP、列出可用畫質，代表 cookie 與環境正常；也可用「監控」分頁的「網站健檢」。

> 備份 zip **內含 cookie（帳號機密）**，傳輸與保存請小心，勿放到雲端公開資料夾或聊天群組。
