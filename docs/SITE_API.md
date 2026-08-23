# 動畫瘋站台 API 對照表（自我修復地圖）

> 這份文件的用途：**當動畫瘋改版、下載突然失敗時，快速定位是「哪一個步驟／哪一個端點」壞掉**，
> 並找到對應的解析程式碼位置，交給 AI 或自己修改。

下載一集動畫，程式會依序打幾支動畫瘋的內部 API。任何一支的網址、參數、或回傳格式改變，
都可能讓下載中斷。程式已經在每個「解析邊界」失敗時，把**原始回應**存到 `data/logs/diag/`，
並在 log 印出對應的**步驟代碼**（例如 `token-noad`）。本表就是「步驟代碼 → 端點 → 程式位置」的索引。

行號為撰寫當下（v0.1.1）的位置，改版後可能位移，以函式名稱為準。

---

## 1. 解析流程（Web 模式）

```
animeVideo.php?sn=          取得影片頁面 HTML，解析標題與集數列表
        │                   [核心] __get_src / __get_title / __get_episode_list
        ▼
ajax/getdeviceid.php        取得 deviceid（後續每支 API 都要帶）
        │
        ▼
ajax/token.php?adID=0       取得帳號資訊，判斷是否 VIP
        │
        ├─（非 VIP）ajax/videoCastcishu.php?s=194699   看廣告 → 等待 ad_time 秒 → &ad=end 跳過
        │            └─ ajax/token.php（迴圈檢查 time==1，確認廣告已看完）
        ▼
anime/v1/video_src.php     取得 master playlist 的網址（data.srcUseCases[].src.playlist）
        │
        ▼
master playlist (.m3u8)     列出各畫質對應的 chunklist 網址 → 解析成 {畫質: 網址}
        │                   [到這裡 get_m3u8_dict() 結束，網站健檢也檢查到這一步]
        ▼
chunklist (.m3u8)           某個畫質的分段清單，內含 AES-128 金鑰 URI + 各 ts 分段
        │                   [核心] download() 內
        ▼
key.m3u8key + *.ts          下載金鑰與所有 ts 分段 → ffmpeg 解密合併成 mp4
```

APP（mobile）模式走的是 `api.gamer.com.tw/mobile_app/anime/*`，端點不同但步驟一一對應（見第 3 節）。
由 `config.json` 的 `use_mobile_api` 決定走哪一套。

---

## 2. 各端點詳解

每一列：**步驟代碼**（診斷傾印用的名字）｜端點｜回應應有的樣子｜解析位置｜壞掉的症狀。

### 2.1 影片頁面 → `title-parse`
- **端點（Web）**：`GET https://ani.gamer.com.tw/animeVideo.php?sn=<sn>`（回傳 HTML）
- **端點（APP）**：`GET https://api.gamer.com.tw/mobile_app/anime/v4/video.php?sn=<sn>`（回傳 JSON）
- **請求位置**：`core/engine.py` `__get_src`（約 179）
- **回應應有的樣子**：
  - Web：HTML 內有 `<div class="anime_name"><h1>標題[集數]</h1>`；集數列表在 `<section class="season">` 內的 `<a>`。
  - APP：JSON `data.anime.title` 為標題，`data.anime.episodes` 為集數字典。
- **解析位置**：`__get_title`（約 187，Web 用 `soup.find('div','anime_name').h1.string`；APP 用 `data.anime.title`）、
  `__get_episode_list`（約 245）。
- **壞掉症狀**：log 出現「ERROR: 找不到影片標題」，傾印 `title-parse`。
  代表 sn 不存在、或頁面 HTML 結構／JSON 欄位改版（例如 `anime_name` class 改名）。

### 2.2 取得裝置 ID → `getdeviceid`
- **端點**：`GET https://ani.gamer.com.tw/ajax/getdeviceid.php`
- **請求＋解析位置**：`core/engine.py` `get_device_id`（約 431）
- **回應應有的樣子**：JSON `{"deviceid": "..."}`
- **壞掉症狀**：log「getdeviceid.php 回應中找不到 deviceid」，傾印 `getdeviceid`。
  代表這支 API 改版或回傳非 JSON（例如被擋、回了 HTML 錯誤頁）。

### 2.3 帳號權限（VIP 判定）→ `token`
- **端點**：`GET https://ani.gamer.com.tw/ajax/token.php?adID=0&sn=<sn>&device=<deviceid>&hash=<隨機>`
- **請求位置**：`gain_access`（約 458）
- **回應應有的樣子**：JSON 含 `vip`（布林）。若有 `error` 欄位代表需登入／分級限制。
- **解析位置**：`__get_m3u8_dict` 主流程（`error` 檢查約 567；`vip` 檢查約 573）
- **壞掉症狀**：log「token.php 回應中找不到 vip 欄位」，傾印 `token`。代表 token.php 回傳結構改版。

### 2.4 廣告檢查（非 VIP）→ `token-noad`
- **看廣告端點**：`GET https://ani.gamer.com.tw/ajax/videoCastcishu.php?sn=<sn>&s=194699`（開始）、
  同址加 `&ad=end`（跳過）。`s=194699` 是寫死的魔術數字。
- **確認端點**：`GET https://ani.gamer.com.tw/ajax/token.php?sn=<sn>&device=<deviceid>&hash=<隨機>`
- **請求＋解析位置**：`start_ad`/`skip_ad`（約 475）、`check_no_ad`（約 493）
- **回應應有的樣子**：JSON 含 `time`；`time==1` 代表廣告已看完可下載。
- **壞掉症狀**：log「token.php 回應中沒有 time 欄位」，傾印 `token-noad`。
  訊息會提示：**可能是地區限制（IP 不被動畫瘋認可），也可能是 API 改版**。
  VIP 帳號通常不會走到這一步（VIP 直接下載）。

### 2.5 取得播放清單網址 → `m3u8-json`
- **端點（Web）**：`GET https://api.gamer.com.tw/anime/v1/video_src.php?videoSn=<sn>&deviceid=<deviceid>&deviceTypeUseCases=1`
- **端點（APP）**：`GET https://api.gamer.com.tw/mobile_app/anime/v3/m3u8.php?videoSn=<sn>&device=<deviceid>`
- **請求位置**：`get_playlist`
- **回應應有的樣子**：
  - Web JSON：`{"data":{"deviceid":"...","srcUseCases":[{"deviceType":1,"src":{"playlist":"<master playlist 網址>"}}]}}`
    取 `deviceType == 1` 那一筆的 `src.playlist`（找不到就退回第一筆）。
  - APP JSON：`data.src`。
- **解析位置**：`parse_playlist`
- **壞掉症狀**：log「播放清單 API 回應中找不到 src」，傾印 `m3u8-json`。代表回應結構改版。
  若 log 是「取得播放清單的 API 未回傳 JSON (HTTP xxx)」，代表**端點本身消失或被擋**，
  傾印檔會是原始 HTML —— 先看 HTTP 狀態碼。

> **2026-08-23 改版紀錄。** 舊的 Web 端點 `ani.gamer.com.tw/ajax/m3u8.php` **已被移除**，
> 對任何請求（含不帶參數）一律回 `404 File not found.`。網頁播放器改呼叫
> `api.gamer.com.tw/anime/v1/video_src.php`，三處都變了：
> `sn` → `videoSn`、`device` → `deviceid`、新增 `deviceTypeUseCases=1`，
> 回應也從扁平的 `{"src": ...}` 變成 `data.srcUseCases[]` 陣列。
> 症狀是每一集都在 `識別到VIP賬戶` 之後立刻 `JSONDecodeError`。
> 當時 `data/logs/diag/` 是**空的** —— 因為 `dump_diag` 只在「解析後找不到 src」時觸發，
> 而 404 的 HTML 在 `.json()` 就先拋例外了。現已改成在 `get_playlist` 內解析 JSON 並
> 於失敗時傾印，所以下次端點消失會留下原始回應。
> 注意 master playlist 網址本身仍是 `playlist_advance.m3u8?hdnts=...`（query string 形式）；
> 路徑片段形式的 `hdntl=.../` 只出現在**分段檔**網址裡，不影響 `url_prefix` 的組法。

### 2.6 解析 master playlist（各畫質）→ `playlist`
- **端點**：上一步 `src` 指向的 `.m3u8`（帶 `origin: https://ani.gamer.com.tw` 標頭，不帶 cookie）
- **請求＋解析位置**：`parse_playlist`（約 537-555）
- **回應應有的樣子**：標準 m3u8 master playlist，每個畫質兩行：
  `#EXT-X-STREAM-INF:...RESOLUTION=1920x1080` 後接一行 `.../chunklist....m3u8`。
  程式用正則抓 `=\d+x\d+` 取直向像素當 key、抓 `chunklist` 那行當畫質網址。
- **壞掉症狀**：log「master playlist 解析不到任何畫質」，傾印 `playlist`。
  代表 playlist 內部格式改變（解析度標記或 chunklist 命名規則變了）。

### 2.7 chunklist 的 AES-128 金鑰 → `chunklist-aeskey`
- **端點**：某畫質的 chunklist `.m3u8`（`get_m3u8_dict()` 產生的網址）
- **請求＋解析位置**：`download()` 內（約 727-737）
- **回應應有的樣子**：chunklist m3u8 內含 `#EXT-X-KEY:METHOD=AES-128,URI="<金鑰網址>"`，
  以及多行 `media_b....ts` 分段。程式用正則 `(?<=AES-128,URI=")(.*)(?=")` 抓金鑰網址。
- **壞掉症狀**：log「chunklist 找不到 AES-128 金鑰 URI」，傾印 `chunklist-aeskey`。
  代表加密方式或 chunklist 格式改變。**注意：這一步發生在實際下載時，網站健檢不會走到（健檢只到 2.6）。**

---

## 3. APP（mobile）模式對照

`use_mobile_api = true` 時改走這些端點（`core/engine.py` 內以 `use_mobile_api` 分支）：

| 步驟 | Web | APP（mobile_app） |
|---|---|---|
| 影片頁面 | `animeVideo.php`（HTML） | `mobile_app/anime/v4/video.php`（JSON） |
| 播放清單 | `anime/v1/video_src.php`（2026-08 起，舊 `ajax/m3u8.php` 已移除） | `mobile_app/anime/v3/m3u8.php` |
| 廣告 | `ajax/videoCastcishu.php?s=194699` | `mobile_app/anime/v1/stat_ad.php` |

APP 模式的請求帶固定的 App 標頭（寫死）：
`User-Agent: Animad/1.16.16 (tw.com.gamer.android.animad; build:328; Android 9) okHttp/4.4.0`（`core/engine.py` 約 295）。
App 改版時這個 UA／版本號可能要跟著更新。

---

## 4. 周邊端點（非主下載流程）

| 功能 | 端點 | 位置 |
|---|---|---|
| 下載彈幕 | `POST https://ani.gamer.com.tw/ajax/danmuGet.php` | `core/danmu.py` 約 60 |
| 彈幕關鍵字 | `GET https://ani.gamer.com.tw/ajax/keywordGet.php` | `core/danmu.py` 約 77 |
| 匯入「我的動畫」 | `GET https://ani.gamer.com.tw/mygather.php` | `app/daemon.py` 約 902 |

---

## 5. Cookie 自動刷新契約（**高風險，勿隨意改**）

動畫瘋在部分回應會用 `set-cookie` 換發新的 `BAHARUNE`（付費識別）cookie，程式會偵測並寫回 `data/cookie.txt`。
關鍵邏輯在 `core/engine.py` 的 `__request`（約 360-414）：

- `set-cookie` 內出現 `deleted`：代表這次刷新的機會已被**其他執行緒搶先領走**（一次性），本次略過。
- 只有 `set-cookie` 內含 `BAHARUNE` 時才更新並寫檔；更新前後比對 `old_BAHARUNE`，避免無謂寫入。
- UA 必須與產生該 cookie 的瀏覽器一致（`ref`/`ua` 見約 288-289），否則刷新會失敗。

> ⚠️ **這段與帳號被鎖的風險直接相關（上游 issue #207）。修別的東西時，`__request` / `__request_json` /
> cookie 刷新這段（約 309-421）請保持原樣，不要順手重構。**

---

## 6. 歷史變動紀錄（drift log）

改版是常態，以下是已知會「漂移」的點，改版時先看這裡：

- **廣告時間 `ad_time`**：上游隨站台調整過 8s → 20s → 25s（`core/engine.py` 約 581-582 的註解）。
  程式會在通過廣告後把實測秒數學習寫回 `config.json` 的 `ads_time` / `mobile_ads_time`。
- **`s=194699`**：`videoCastcishu.php` 的寫死參數（約 479/486），來源不明的魔術數字，改版時可能失效。
- **APP UA `Animad/1.16.16 build:328`**：寫死（約 295），App 版本更新時可能要跟上。
- **集數標記位置**：上游多次因標題內版本標記位置變動而壞掉（issue #36 / #109），
  `__get_episode` 有多段 fallback。

---

## 7. 壞掉時的修復流程（SOP）

1. **跑健檢**：監控分頁按「網站健檢」，或指令 `python cli.py --doctor`（可加 sn）。
2. **看中斷步驟代碼**：健檢會回報 `first_broken`（例如 `token-noad`）與傾印檔路徑。
3. **打開原始回應**：到 `data/logs/diag/` 開最新那個檔（檔名含步驟代碼），看動畫瘋實際回了什麼。
4. **對照本表**：用步驟代碼找到第 2 節對應端點、預期格式、與解析程式位置。
5. **交給 AI 修**：把「本表該端點段落 + 那份原始回應 + 對應程式碼」一起貼給 AI，通常就能定位並修好解析。
6. **修完再跑一次健檢**確認全綠。

> 診斷傾印只保留最新 20 份、且**永不影響下載**（寫入失敗就略過）。`data/logs/diag/` 已被 gitignore，不會進版控。
