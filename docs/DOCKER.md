# Docker 部署指南（AniGamerDownloader 本地 fork）

把 AniGamerDownloader 的「背景下載 daemon + Web 控制臺」包成容器，跑在 **mac / Ubuntu / Synology NAS** 上。
與 Windows 桌面版共用同一套下載核心（`core/`）與服務層（`app/`），只是換成 headless 啟動。

> ⚠️ **僅限區域網路（LAN）使用。** 容器強制開啟 BasicAuth（帳號／密碼），但這只擋同網段的誤觸，
> **不是**對外網安全機制。請勿把這個 port 直接暴露到公網；若要遠端存取，請放在 VPN 或反向代理之後。

---

## 1. 它做了什麼

- `shells/docker/Dockerfile`：以 `python:3.13-slim` 為基底，裝 `ffmpeg`（解密＋合併分段的硬依賴）、
  `ffprobe`（資料庫深度驗證用）、`tini`（PID 1 訊號轉發）。多架構設計，**無任何架構專屬二進位**。
- `shells/docker/entrypoint.py`：headless 啟動。**先**把 env 覆寫寫進 `data/config.json`，**再** import
  `app.daemon` 並執行 `run_daemon()`（自動下載迴圈 + Web 控制臺）。
  - 強制 `host = 0.0.0.0`、`BasicAuth = True`。
  - **空密碼或預設佔位密碼（`admin` / `password` / `changeme`）會直接拒絕啟動**（exit code 78）。
- `data/` 走掛載 volume：`config.json` / `cookie.txt` / `aniGamer.db` / `logs/`／（預設）下載輸出都在裡面，
  容器重建也不會遺失。彈幕模板 `DanmuTemplate.ass` 由 entrypoint 在首次啟動時自動植入 volume。

---

## 2. 環境變數

| 變數 | 說明 | 預設 |
| --- | --- | --- |
| `ANIGAMER_WEB_PASSWORD` | BasicAuth 密碼，**必填**。空值或預設佔位密碼會被拒絕。 | 無（必填） |
| `ANIGAMER_WEB_USER` | BasicAuth 帳號 | `admin` |
| `ANIGAMER_WEB_PORT` | 容器**內**的 Web port | `5000` |
| `ANIGAMER_OUTPUT_DIR` | 容器內的下載輸出目錄 | `/app/data/bangumi`（落在 `/app/data` volume 內） |
| `ANIGAMER_RESOLUTION` | 下載解析度（`360`/`480`/`540`/`720`/`1080`） | 沿用 config |
| `ANIGAMER_CHECK_FREQ` | 檢查 `sn_list` 的頻率（分鐘） | 沿用 config |

> 其餘設定（多執行緒數、檔名前綴、彈幕等）首次啟動後可直接在 **Web 控制臺的「設定」頁**調整，
> 存進掛載的 `data/config.json`。

---

## 3. 建置多架構映像（buildx）

> 動畫瘋無法在 Windows 上跑 Docker，因此**請在 mac / Ubuntu / NAS / CI** 建置與實測。
> 以下指令的**建置 context 是 repo 根目錄**（這樣 `core/` `app/` `web/` `data/` 都看得到）。

一次性準備 buildx（多架構需要 QEMU 模擬器，Docker Desktop 已內建）：

```bash
docker buildx create --use --name anigamer-builder   # 只需做一次
docker buildx inspect --bootstrap
```

建置 `linux/amd64`（Intel/AMD NAS、x86 機器）+ `linux/arm64`（ARM 版 Synology、Apple Silicon、樹莓派）
單一 tag，並推到 registry（建議 GitHub Container Registry, GHCR）：

```bash
# 在 repo 根目錄執行
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -f shells/docker/Dockerfile \
  -t ghcr.io/<你的帳號>/anigamerdownloader:latest \
  --push .
```

> 多架構映像**必須 `--push` 到 registry**（buildx 無法把多平台映像存進本機 docker images）。
> 只想在本機單一架構測試，可改用 `--load` 並只指定當前平台：
> `docker buildx build --platform linux/amd64 -f shells/docker/Dockerfile -t anigamerdownloader:local --load .`

---

## 4. 執行

### 方式 A：docker compose（建議）

`shells/docker/docker-compose.yml` 已備好範例。在 **repo 根目錄**執行：

```bash
export ANIGAMER_WEB_PASSWORD='換成一組夠強的密碼'
docker compose -f shells/docker/docker-compose.yml up -d
```

- 預設把主機的 `./data` 掛到容器 `/app/data`，下載也落在 `./data/bangumi`。
- 想把下載輸出指到別的硬碟／NAS 共享，取消註解 compose 裡的 `- /volume1/anime:/output`，
  並設 `ANIGAMER_OUTPUT_DIR: "/output"`。

不想自己 build、直接拉 registry 映像：把 compose 裡的 `build:` 區塊換成
`image: ghcr.io/<你的帳號>/anigamerdownloader:latest` 即可。

### 方式 B：docker run

```bash
docker run -d \
  --name anigamerdownloader \
  --restart unless-stopped \
  -p 5000:5000 \
  -e ANIGAMER_WEB_PASSWORD='換成一組夠強的密碼' \
  -v "$(pwd)/data:/app/data" \
  ghcr.io/<你的帳號>/anigamerdownloader:latest
```

啟動後瀏覽器開 `http://<主機IP>:5000`，會跳出 BasicAuth 帳密視窗。

---

## 5. 各平台掛載重點

### macOS / Ubuntu

- 直接用上面的指令即可。輸出建議掛到一個你有寫入權的目錄（例如 `-v ~/anime:/output` 搭配
  `ANIGAMER_OUTPUT_DIR=/output`）。
- Linux 上若遇到檔案 owner 問題，可加 `--user "$(id -u):$(id -g)"`（需確保掛載目錄該使用者可寫）。

### Synology NAS（DSM / Container Manager）

1. 先在 registry 推好多架構映像（步驟 3），DSM 會自動依機型抓對的架構（Intel 機抓 amd64、
   ARM 機抓 arm64）。
2. **Container Manager → 登入／映像**：下載 `ghcr.io/<你的帳號>/anigamerdownloader:latest`。
3. 建立容器時設定：
   - **磁碟區（Volume）**：把一個 NAS 共享資料夾掛到 `/app/data`（放設定／db／log），
     另一個下載夾掛到 `/output`。
   - **環境變數**：`ANIGAMER_WEB_PASSWORD`（必填）、`ANIGAMER_OUTPUT_DIR=/output`。
   - **連接埠**：本機 `5000` → 容器 `5000`（或改成你習慣的本機 port）。
4. 啟動後用同網段裝置開 `http://<NAS的IP>:5000`。

---

## 6. 貼上 cookie（用 GUI，不用編 cookie.txt）

容器內不方便手動編輯 `cookie.txt`，**改用 Web 控制臺貼上**：

1. 從瀏覽器（已登入動畫瘋、VIP 帳號）取得 `Cookie:` 標頭字串：
   開發者工具（F12）→ Network → 隨便點一個對 `ani.gamer.com.tw` 的請求 →
   Request Headers 裡的 `cookie:` 整行複製。
2. 在 Web 控制臺的**「設定」頁的 Cookie 區塊**貼上，並把對應的 **UA**（User-Agent）一起填上
   （cookie 與產生它的瀏覽器 UA 必須一致，否則自動刷新會失敗）。
3. 存檔。內容會寫進掛載 volume 的 `data/cookie.txt`。

> ⚠️ cookie 解析帶有帳號被鎖的風險（上游 issue #207）。請只在自己信任的環境貼上，
> 並避免在公開網路傳輸。

---

## 7. 反向代理 / 子路徑注意

前端同時用了相對路徑與根絕對路徑（如 `/uploadConfig`、`/batch/*`）。
因此 **Docker 版請掛在反向代理的「根路徑」**（例如 `anime.example.lan/` 而非
`example.lan/anigamer/`）。若硬掛在子路徑，根絕對路徑的 API 會失效。
（之後若需要子路徑支援，需另外加 `BASE_PATH` 參數化，非目前範圍。）

若用 Nginx 反向代理（仍限 LAN），最小設定：

```nginx
location / {
    proxy_pass http://127.0.0.1:5000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

---

## 8. 常見問題

- **容器一啟動就退出、log 顯示 `FATAL: ANIGAMER_WEB_PASSWORD is empty or a default placeholder`**：
  你沒設密碼，或用了 `admin`/`password`/`changeme`。設一組真正的強密碼即可。
- **網頁打不開**：確認 port mapping（`-p 主機:5000`）與防火牆；NAS 上確認容器在執行中。
- **下載失敗、log 提到 ffmpeg**：映像已內建 ffmpeg；若是自行改了基底映像，請確認 `ffmpeg` 在 PATH。
- **檔案存到容器裡而不是 NAS**：你沒設 `ANIGAMER_OUTPUT_DIR`，輸出落在 `/app/data/bangumi`
  （即掛載的 `./data/bangumi`）。要存到別的共享請設 `ANIGAMER_OUTPUT_DIR` 並掛上對應 volume。
- **舊檔名是 `E1`、新檔是 `E01`**：集數補零規則已更新（預設兩碼、破百三碼），舊下載不受影響；
  資料庫盤點的「檔案存在」比對可能把舊檔當缺漏，必要時手動改名或忽略。

---

## 9. 與 Windows 桌面版的關係

兩者共用 `core/`（純下載引擎）+ `app/`（daemon + Flask web）+ `web/`（同一套 SPA）。
差別只在外殼：

- **Docker**：`shells/docker/entrypoint.py`，headless、`0.0.0.0`、強制 BasicAuth。
- **Windows 桌面**：`shells/desktop.py`，pywebview 原生視窗、loopback、可攜（見 `docs/DESKTOP_BUILD.md`）。

所以「設定頁、Cookie 貼上、下載清單、資料庫盤點」等操作兩版完全相同。
