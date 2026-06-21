# AniGamerDownloader

巴哈姆特動畫瘋（[ani.gamer.com.tw](https://ani.gamer.com.tw)）自動下載工具：VIP 1080p、彈幕轉 `.ass`、
排程巡檢、單頁 Web 控制台。提供 **Windows 可攜桌面 App**、**Docker** 與**原始碼**三種使用方式。

> [miyouzi/aniGamerPlus](https://github.com/miyouzi/aniGamerPlus) 的衍生 fork（GPL-3.0），
> 重構為**共用下載核心 `core/` + 服務層 `app/` + 兩種外殼 `shells/`**，Web 介面改為框架無關的單頁 SPA。

---

## 功能

- **自動巡檢下載**：把作品的 `sn`（網址 `?sn=` 那串）加進清單，daemon 依設定頻率檢查新集數並下載；
  支援 single / latest / all / largest-sn 等模式。
- **VIP 畫質**：貼上 VIP 帳號 cookie 即可下 1080p（須搭配產生該 cookie 的同一瀏覽器 UA）。
- **彈幕**：可一併抓彈幕並轉成 `.ass`（樣式由 `data/DanmuTemplate.ass` 控制）。
- **單頁 Web 控制台**（5 個分頁）：
  - **設定** — 所有設定 + 帳號 Cookie 區塊（cookie 顯示為遮罩，不回傳明碼）。
  - **監控** — 進度條與待下載清單（輪詢，非 websocket）；批次列：暫停／繼續／立即檢查／停止程式。
  - **下載清單** — 管理 `sn_list`。
  - **資料庫** — 盤點 `aniGamer.db`、與磁碟比對（檔案存在／大小／選用 ffprobe 深檢）、單集／整部重置與立即下載。
  - **手動任務** — 一次性下載（不必加進排程）。
- **可攜**：所有路徑可在介面內設定；打包後 `data/`（設定／cookie／db／logs）放在 exe 旁，整夾帶走即保留。
- **單一實例鎖**（loopback `47763`）避免重複下載。

---

## 三種使用方式

| | 取得方式 | 適合 |
| --- | --- | --- |
| **Windows 可攜桌面 App** | [Releases](../../releases) 的 `AniGamerDownloader-windows-portable-*.zip` | 一般使用者，免裝 Python，獨立視窗（pywebview / WebView2，非瀏覽器分頁） |
| **Docker 映像** | `ghcr.io/lilasrepo/anigamerdownloader`（amd64 / arm64） | NAS / mac / Linux 背景常駐，LAN + 密碼 |
| **原始碼** | `git clone` 本 repo | 開發、自訂、在 Windows 直接跑 |

> 三種都需要 **ffmpeg**（解密 + 合併分段）。桌面包不綑綁 ffmpeg、Docker 映像已內含。

### 1. Windows 可攜桌面 App

1. 從 [Releases](../../releases) 下載 zip 並**整夾解壓**。
2. 放一份 `ffmpeg.exe` 到 `AniGamerDownloader.exe` 旁（來源：<https://www.gyan.dev/ffmpeg/builds/> 的 essentials build），或確保系統 `PATH` 上有 ffmpeg。
3. 雙擊 `AniGamerDownloader.exe` → 開出獨立視窗 → 「設定」分頁貼上 Cookie 與 UA、設定下載目錄 →「下載清單」加 sn →「監控」按「立即檢查」。

首次啟動會在 exe 旁自動建立 `data/` 並寫入預設 `config.json`。整夾可攜（不依賴系統 Python）。
自行打包見 [docs/DESKTOP_BUILD.md](docs/DESKTOP_BUILD.md)。

### 2. Docker

```bash
docker run -d --name anigamer \
  -p 5000:5000 \
  -e ANIGAMER_WEB_USER=admin \
  -e ANIGAMER_WEB_PASSWORD='use-a-strong-password' \
  -v /path/to/data:/app/data \
  -v /path/to/output:/app/data/bangumi \
  ghcr.io/lilasrepo/anigamerdownloader:latest
```

僅供 **LAN** 使用、**強制 BasicAuth**（`ANIGAMER_WEB_PASSWORD` 必填；空白或 `admin`/`changeme`
等預設值會拒絕啟動），請勿對外網暴露。其他環境變數：`ANIGAMER_WEB_USER`、`ANIGAMER_WEB_PORT`、
`ANIGAMER_OUTPUT_DIR`、`ANIGAMER_RESOLUTION`、`ANIGAMER_CHECK_FREQ`。設定 / cookie / db 都在掛載的
`/app/data`，cookie 可直接在 Web 介面「設定」分頁貼上。細節見 [shells/docker/](shells/docker/)。

### 3. 原始碼直接跑

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-venv.txt
.venv\Scripts\python -m pip install pywebview     # 桌面視窗才需要
.venv\Scripts\python shells\desktop.py            # 獨立視窗，內含 http://127.0.0.1:5000
```

需要系統 `PATH` 上有 `ffmpeg`。安裝、cookie、一次性 CLI 下載與追番排程的完整 SOP 見 [docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md)。

---

## 注意事項

- **Cookie 即帳號**：貼上登入後的 `Cookie:` 標頭，並填入**產生該 cookie 的同一瀏覽器 UA**。
  取得 cookie 的詳細步驟與格式見 [docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md)。
  Cookie 解析有帳號風險（見上游 [issue #207](https://github.com/miyouzi/aniGamerPlus/issues/207)），請自負風險。
- 你的 `config.json` / `cookie.txt` / `aniGamer.db` / `logs` 都在 `data/`，已被 `.gitignore` 排除、**不會進版控**；
  Web 介面也只回傳遮罩後的 cookie，不外送明碼。

## 免責聲明

- 本專案僅供**個人技術研究與學習實驗**用途，依 GPL-3.0 不負任何明示或默示之擔保（AS IS）。
- 透過本工具下載之內容，其著作權均屬**巴哈姆特動畫瘋及各該版權方**所有。使用者**不得**將下載內容用於任何**商業或營利**用途，亦**不得公開散布、重製、再上傳**或為其他侵害著作權之行為，僅限個人離線備份。
- 請自行遵守[巴哈姆特動畫瘋服務條款](https://ani.gamer.com.tw/)及所在地法律；使用本工具所生之一切後果（含帳號封鎖風險）概由使用者自行承擔。
- 本工具為非官方之第三方軟體，與官方**並無任何關聯**，亦未獲其授權。

## 授權

本專案以 **[GPL-3.0](LICENSE)** 釋出（衍生自 GPL-3.0 的上游，依條款必須維持同一授權）。

- 原始作品 Copyright © [Miyouzi](https://github.com/miyouzi/aniGamerPlus) 及貢獻者。
- 本 fork 之修改 Copyright © 2026 lilasrepo。

依 GPL-3.0，你可自由使用、修改、再散布，但衍生作品須同樣以 GPL-3.0 開源並保留上述著作權標示。
