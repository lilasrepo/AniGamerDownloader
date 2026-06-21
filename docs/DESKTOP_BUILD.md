# Windows 可攜桌面版 — 建置與使用

把 AniGamerDownloader 打包成一個**獨立視窗 App**（pywebview / WebView2，非瀏覽器分頁），
且**不需要在目標電腦安裝 Python**即可執行。整個 `dist\AniGamerDownloader\` 資料夾就是可攜單位，
丟隨身碟、在任何 Windows 上都能直接跑，不假設 `F:` 槽，所有路徑可在介面內設定。

> 只能在 **Windows** 上打包與實測——PyInstaller 不跨平台。

---

## 一、運作模型（為什麼這樣設計）

- `shells\desktop.py` 是桌面外殼，採**子程序模型**：父程序開 pywebview 原生視窗，下載 daemon + web
  服務跑在一個**隱藏的子程序**。二者分開是必要的——pywebview 的 WebView2 訊息迴圈會卡住主執行緒、
  餓死 gevent 的 web 服務（同一行程內會白屏）。視窗關閉時父程序會 `taskkill /T` 收掉子程序樹。
- **可攜路徑模型**：打包後 `core\config.py` 以 `sys.frozen` 偵測自己是 exe，於是 `data\`、`web\`
  都到 **exe 旁邊**找；輸出/暫存路徑不存在時自動退回 exe 旁的 `data\bangumi`、`data\temp`。
- **ffmpeg 定位**：外殼先找 exe 旁的 `ffmpeg.exe`，再找系統 `PATH`。
- **WebView2**：pywebview 在 Windows 用 Edge WebView2 後端。bootstrapper 已綑綁，乾淨機器首次
  啟動會自動補裝 runtime。

---

## 二、前置需求

專案 venv（`.venv`，Python 3.13）已安裝 `pyinstaller` 與 `pywebview`
（後者會帶入 `pythonnet`/`clr`、`clr_loader`——WebView2 後端需要）。若缺：

```bat
.venv\Scripts\python.exe -m pip install pyinstaller pywebview
```

> 這兩個只在打包/桌面外殼用，故不在 `requirements-venv.txt`（那是 daemon 的執行時相依）。

---

## 三、一鍵打包

於 repo 根目錄：

```bat
scripts\build_desktop.bat
```

腳本會：

1. 若缺，下載 **WebView2 Evergreen bootstrapper**（微軟官方 ~2MB）到 `shells\webview2\`。
2. 以 PyInstaller **onedir**（`--contents-directory .`，扁平版面）打包 `shells\desktop.py`，
   並 `--collect-all` 帶入 `webview` / `pythonnet` / `clr_loader` / `gevent`。
3. 產出 `dist\AniGamerDownloader\`，最上層**只有兩項**：`AniGamerDownloader.exe` 與 `_internal\`
   （Python runtime、相依 DLL、`web\`、`data\DanmuTemplate.ass`、`webview2\` 全收在
   `_internal\` 裡）。首次執行後最上層才會多一個 `data\`（你的 config/cookie/db/logs）。

```
dist\AniGamerDownloader\
  AniGamerDownloader.exe        ← 執行檔
  ffmpeg.exe              ← 你放的（下載需要，見第四節）
  data\                   ← 你的設定/紀錄（首次執行後出現，可攜時帶走）
    config.json  cookie.txt  aniGamer.db  logs\
  _internal\              ← 相依檔，平時不用碰（web/、彈幕樣板、webview2、DLL…）
```

`dist\AniGamerDownloader\` 即可攜：複製整個資料夾到隨身碟/任意 Windows 即可執行，**不依賴系統 Python**。

---

## 四、重要：ffmpeg 需自備

本可攜包**只綑綁 WebView2，不綑綁 ffmpeg**（體積與 LGPL/GPL 授權考量）。
下載流程要解密 + 合併分段，**沒有 ffmpeg 無法完成下載**。請擇一：

- 把 `ffmpeg.exe` 放到 **`AniGamerDownloader.exe` 同一個資料夾**（最 portable，推薦），或
- 確保目標電腦的系統 `PATH` 上有 `ffmpeg`。

ffmpeg 來源：https://www.gyan.dev/ffmpeg/builds/ 的 essentials build，取 `bin\ffmpeg.exe`。

---

## 五、從隨身碟執行

1. 複製**整個** `dist\AniGamerDownloader\` 資料夾（不要只複製 exe——onedir 需要旁邊的相依檔）。
2. 雙擊 `AniGamerDownloader.exe` → 開出獨立 App 視窗。
3. 第一次啟動：到「**設定**」分頁 →「帳號 Cookie」區塊貼 cookie、填「請求UA」→ 設「下載目錄」
   →「下載清單」加 sn →「監控」按「立即檢查」。
4. `data\`（`config.json` / `cookie.txt` / `aniGamer.db` / `logs`）會建立在 **exe 旁**；
   可攜時整夾帶走即保留設定與紀錄。

> 可攜驗證：把資料夾複製到**另一個路徑**再跑一次，仍應正常（所有路徑相對於 exe 旁）。

---

## 六、疑難排解

| 症狀 | 原因 / 解法 |
| --- | --- |
| 視窗白屏 / 閃退 / 報缺 WebView2 | 手動雙擊 `webview2\MicrosoftEdgeWebview2Setup.exe` 補裝 runtime，再開一次 |
| 下載一開始就報 ffmpeg 找不到 | `ffmpeg.exe` 沒放進資料夾、也不在 `PATH`；放一份到 exe 旁 |
| 啟動沒反應 / 視窗未出現 | 看 `data\logs\desktop-child.log`（子程序開機日誌） |
| 下載畫質只有 720p 以下 | Cookie 非 VIP，或 UA 與產生 cookie 的瀏覽器不符；重貼 cookie 與對應 UA |
| 「已有一個 AniGamerDownloader 正在執行」 | 單一實例鎖（loopback 47763）；先關掉另一個實例再開 |

---

## 七、原始碼直接跑（開發用，不打包）

```bat
.venv\Scripts\python.exe -m pip install pywebview
set PYTHONUTF8=1 && .venv\Scripts\python.exe shells\desktop.py
```

效果與打包版相同（同一個外殼），只是路徑相對於 repo 根而非 exe 旁。
