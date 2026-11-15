# 每日股價追蹤網站

GitHub Pages 網站 + GitHub Actions 每日自動抓股價，免費、不需要自己的伺服器。

- 台股：證交所 / 櫃買中心 OpenAPI 盤後資料（自動判斷上市或上櫃）
- 美股：Yahoo Finance
- 依研究報告分頁（天鈺 4961 同業、資安類股、健策 3653），每個市場自動對齊「統一日」收盤價
- 每天產生 `data/latest.csv`，Excel 模型可直接連結，開檔即更新股價
- 個股走勢圖、MA5/20/60、歷史明細、複製到 Excel、下載 CSV

## 部署（約 5 分鐘）

1. 在 GitHub 建一個新 repo（例如 `stock-tracker`），把這個資料夾的所有檔案上傳（含 `.github` 資料夾）。
2. **Settings → Actions → General → Workflow permissions** 選 **Read and write permissions**，儲存。
3. **Settings → Pages**：Source 選 **Deploy from a branch**，Branch 選 `main` / `/ (root)`，儲存。
4. **Actions → 每日抓股價 → Run workflow**，回補天數填 `365`，按執行（第一次先回補一年歷史）。
5. 跑完後打開 `https://<你的帳號>.github.io/stock-tracker/` 就是網站。

之後自動更新：

| 時間（台北） | 內容 |
|---|---|
| 16:37 | 台股盤後收盤價 |
| 隔日 06:17 | 美股收盤價 |
| 每月 11 日 10:17 | 月營收（並重算今年／明年預估 EPS） |

（GitHub 排程尖峰時可能延遲 5–30 分鐘。）

## 讓研究報告（Excel 模型）的股價每天自動更新

每次抓完價格，會產生兩個檔：

| 檔案 | 內容 |
|---|---|
| `data/latest.csv` | 每檔一列：**統一日**收盤價、漲跌、漲跌幅、屬於哪份報告。台股、美股各自取該市場最新交易日；某檔當天沒成交會標 `是否統一日 = N` |
| `data/history.csv` | 全部歷史日資料 |

網址：`https://<你的帳號>.github.io/<repo>/data/latest.csv`

### Excel（Windows / Microsoft 365）

1. 在模型裡新增一個工作表 `Prices`。
2. **資料 → 取得資料 → 從其他來源 → 空白查詢**，開「進階編輯器」，貼上（換成你的網址）：

```
let
    Source = Csv.Document(Web.Contents("https://<你的帳號>.github.io/<repo>/data/latest.csv"),
             [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    Typed = Table.TransformColumnTypes(Headers, {
        {"代號", type text}, {"收盤價", type number}, {"漲跌", type number},
        {"漲跌幅%", type number}, {"統一日", type date}, {"資料日", type date}})
in
    Typed
```

3. 查詢命名為 `Prices`，「關閉並載入至」`Prices` 工作表（會變成名為 `Prices` 的表格）。
4. 查詢的「內容」勾選 **開啟檔案時重新整理**（也可設每 60 分鐘）。
5. 把模型裡原本手打的股價改成公式，例如 Peers 工作表 B 欄是代號：

```
收盤價     =XLOOKUP(TEXT(B7,"@"), Prices[代號], Prices[收盤價], "查無")
資料日     =XLOOKUP(TEXT(B7,"@"), Prices[代號], Prices[資料日])
台股統一日 =MAXIFS(Prices[統一日], Prices[市場], "TW")
美股統一日 =MAXIFS(Prices[統一日], Prices[市場], "US")
```

本益比、市值、預測股價等原本參照股價的公式都不用改，開檔就會用最新統一日價格重算。

### Google 試算表

`=IMPORTDATA("https://<你的帳號>.github.io/<repo>/data/latest.csv")`，再用 `XLOOKUP` 或 `VLOOKUP` 參照。

### 手動貼上

網站上按「複製價格」，會複製目前分頁（例如「資安類股」）的統一日收盤價，可直接貼進 Excel。

## 估值模型（網站「估值模型」分頁）

選任一檔股票，自動用當天股價算：

1. **目前本益比**：收盤價 ÷ 選定的 EPS（近四季／今年預估／明年預估）
2. **同業本益比**：同一份報告、同一市場的股票，用**同一種 EPS** 計算；虧損或超過上限（預設 40x）自動排除，可手動勾選
3. **信評式評分**：六個因子（−2～+2）× 權重，每 1 分調整倍數 10%
4. **三情境目標價**：同業 Q1／中位數／Q3 × 調整係數 × EPS，加上機率加權目標價
5. **敏感度表**：EPS ±20% × 各倍數

同業有效本益比不足 2 檔時，改用 `manualPE` 手動倍數（保守／基準／樂觀）。

**EPS 自動抓取（`fetch_estimates.py`，每次排程跑完股價後執行）**

| EPS | 台股 | 美股 |
|---|---|---|
| 近四季 | 收盤價 ÷ 證交所／櫃買中心官方本益比 | Yahoo trailing EPS |
| 今年預估 | 月營收推估：全年營收（已公布月份＋未公布月份用去年同月 ×（1＋近三月年增率））×（近四季 EPS ÷ 同期近四季營收） | Yahoo 分析師共識 |
| 明年預估 | 今年預估 ×（1＋今年營收成長率 ÷ 2），上限 15%、下限 −5% | Yahoo 分析師共識 |

- 月營收：每天抓官方 OpenAPI 最新一期；第一次執行會從公開資訊觀測站回補約 26 個月（`data/revenue.json`）。
- 台股若 Yahoo 有分析師共識，網站會另外顯示，可一鍵套用。
- 網站上每個 EPS 旁邊會標來源：官方／月營收推估／分析師共識／手動。灰字是自動值，自己輸入的數字會覆蓋它，按「改用自動」可恢復。
- 推估假設獲利率不變，成長快但毛利下滑的公司會偏高；Yahoo 是非正式 API，可能失效（失效時只會少了美股預估，不影響股價）。

**輸入在哪裡改**
- 網站上直接改 EPS、評分、機率、同業 EPS，會存在這台裝置的瀏覽器。
- 按「匯出設定」→ 複製 → 貼到 repo 的 `fundamentals.json` 並 commit，所有裝置就同步。
- 財報公布後更新 EPS 即可；股價每天自動更新，目標價和本益比會跟著變。

## 月報（網站「月報」分頁）

依報告分頁顯示：

- **本月重點**：未來 30 天活動數、最近一項活動、月營收年增率最高／最低
- **📅 即將舉行**：法說會、股東會、除權息、董事會／財報、美股財報日（依日期排序，顯示倒數天數）
- **📈 月營收**：最新一個月的當月營收、月增率、年增率、累計年增率
- **📰 近期重大訊息**：自選股最近的重大訊息

| 資料 | 來源 | 更新 |
|---|---|---|
| 法說會、股東會、董事會、庫藏股等 | 證交所／櫃買中心「重大訊息」OpenAPI | 每天收集（官方只提供當天公告，所以要逐日累積，保留 180 天） |
| 除權息 | 證交所／櫃買中心「除權除息預告表」 | 每天 |
| 美股財報日 | Yahoo Finance | 每天 |
| 月營收 | 證交所／櫃買中心 | 每月 11 日（`fetch_estimates.py --revenue`） |

重大訊息從部署當天開始累積，之前已公告的法說會不會出現；活動日期由公告內文自動判讀，請以公司公告為準。

## 修改追蹤的股票

編輯 `watchlist.json`。目前分三份報告：`fiti`（天鈺 4961 同業）、`cyber`（資安類股）、`jentech`（健策 3653）。新增一檔：

```json
{ "code": "2454", "name": "聯發科", "market": "TW", "groups": ["fiti"] }
{ "code": "AAPL", "name": "Apple",  "market": "US", "groups": ["cyber"] }
```

新增一份報告：在 `groups` 加 `{ "id": "xxx", "name": "報告名稱" }`，股票的 `groups` 填 `"xxx"`；一檔可屬於多份報告。

新加的股票下次排程會自動回補一年股價和月營收，不用手動回補；想馬上看到就到 Actions 手動按一次 Run workflow。移除的股票歷史會保留，只是不顯示。

## 本機測試

```bash
python fetch_prices.py --backfill 30   # 只用標準函式庫，不需 pip
python -m http.server                  # 打開 http://localhost:8000
```

## 注意

- repo 若設為 Public，網站和資料任何人都看得到（只有股價，不含持股）。想私有需 GitHub Pro 才能用 Private Pages。
- Yahoo 偶爾會擋雲端 IP；台股以官方資料為主，不受影響。
