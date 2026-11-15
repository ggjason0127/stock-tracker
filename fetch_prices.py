#!/usr/bin/env python3
"""
每日抓取觀察清單股價，累積到 data/prices.json

台股：證交所 (TWSE) / 櫃買中心 (TPEx) 官方 OpenAPI 盤後資料
美股：Yahoo Finance chart API
回補歷史：python fetch_prices.py --backfill 365   (用 Yahoo 回補最近 N 天，台股也可)

只用 Python 標準函式庫，不需 pip install。
"""
import argparse
import datetime as dt
import json
import pathlib
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
WATCHLIST = ROOT / "watchlist.json"
OUT = ROOT / "data" / "prices.json"
TPE = dt.timezone(dt.timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (daily-stock-tracker)", "Accept": "application/json"}

TWSE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d"


# ---------- 小工具 ----------
def get_json(url, retries=3):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8-sig"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"抓取失敗 {url}: {last}")


def num(x):
    if x is None:
        return None
    s = str(x).replace(",", "").replace("+", "").strip()
    if s in ("", "-", "--", "---", "X", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def roc_to_iso(s):
    """'1141114' 或 '114/11/14' → '2025-11-14'"""
    if not s:
        return None
    s = str(s).strip().replace("/", "")
    if len(s) == 7:
        return f"{int(s[:3]) + 1911}-{s[3:5]}-{s[5:7]}"
    if len(s) == 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return None


def pick(row, *keys):
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    return None


def today_tpe():
    return dt.datetime.now(TPE).date().isoformat()


# ---------- 台股：官方盤後 ----------
def fetch_twse():
    rows = get_json(TWSE_URL)
    out = {}
    for r in rows:
        code = str(pick(r, "Code", "證券代號") or "").strip()
        if not code:
            continue
        out[code] = {
            "name": pick(r, "Name", "證券名稱"),
            "d": roc_to_iso(pick(r, "Date", "日期")) or today_tpe(),
            "o": num(pick(r, "OpeningPrice", "開盤價")),
            "h": num(pick(r, "HighestPrice", "最高價")),
            "l": num(pick(r, "LowestPrice", "最低價")),
            "c": num(pick(r, "ClosingPrice", "收盤價")),
            "chg": num(pick(r, "Change", "漲跌價差")),
            "v": num(pick(r, "TradeVolume", "成交股數")),
            "ex": "TWSE",
        }
    return out


def fetch_tpex():
    rows = get_json(TPEX_URL)
    out = {}
    for r in rows:
        code = str(pick(r, "SecuritiesCompanyCode", "代號") or "").strip()
        if not code:
            continue
        out[code] = {
            "name": pick(r, "CompanyName", "名稱"),
            "d": roc_to_iso(pick(r, "Date", "資料日期")) or today_tpe(),
            "o": num(pick(r, "Open", "開盤")),
            "h": num(pick(r, "High", "最高")),
            "l": num(pick(r, "Low", "最低")),
            "c": num(pick(r, "Close", "收盤")),
            "chg": num(pick(r, "Change", "漲跌")),
            "v": num(pick(r, "TradingShares", "成交股數")),
            "ex": "TPEx",
        }
    return out


# ---------- Yahoo（美股 / 回補） ----------
def fetch_yahoo(sym, rng="5d"):
    data = get_json(YAHOO_URL.format(sym=sym, rng=rng))
    res = (data.get("chart") or {}).get("result") or []
    if not res:
        raise RuntimeError(f"Yahoo 無資料 {sym}")
    res = res[0]
    ts = res.get("timestamp") or []
    q = (res.get("indicators") or {}).get("quote", [{}])[0]
    off = (res.get("meta") or {}).get("gmtoffset", 0)
    bars, prev = [], None
    for i, t in enumerate(ts):
        c = q.get("close", [None])[i]
        if c is None:
            continue
        d = dt.datetime.fromtimestamp(t + off, dt.timezone.utc).date().isoformat()
        bars.append({
            "d": d,
            "o": _r(q.get("open", [None])[i]),
            "h": _r(q.get("high", [None])[i]),
            "l": _r(q.get("low", [None])[i]),
            "c": _r(c),
            "chg": _r(c - prev) if prev is not None else None,
            "v": q.get("volume", [None])[i],
        })
        prev = c
    return bars


def _r(x):
    return None if x is None else round(float(x), 4)


# ---------- 合併 ----------
def merge(stock, bars):
    hist = {b["d"]: b for b in stock.get("history", [])}
    for b in bars:
        if b.get("c") is None:
            continue
        clean = {k: b.get(k) for k in ("d", "o", "h", "l", "c", "chg", "v")}
        hist[b["d"]] = clean
    stock["history"] = [hist[d] for d in sorted(hist)]
    # 沒有官方漲跌時，用前一日收盤補算
    h = stock["history"]
    for i in range(1, len(h)):
        if h[i].get("chg") is None and h[i - 1].get("c") is not None:
            h[i]["chg"] = round(h[i]["c"] - h[i - 1]["c"], 4)


# ---------- 給 Excel 模型用的 CSV ----------
def unified_dates(db):
    """每個市場的統一日 = 該市場所有股票中最新的交易日"""
    out = {}
    for st in db["stocks"].values():
        if st.get("hidden") or not st.get("history"):
            continue
        m = st.get("market", "TW")
        out[m] = max(out.get(m, ""), st["history"][-1]["d"])
    return out


def bar_on_or_before(hist, d):
    for i in range(len(hist) - 1, -1, -1):
        if hist[i]["d"] <= d:
            return hist[i], (hist[i - 1] if i else None)
    return None, None


def write_csvs(db):
    import csv
    gname = {g["id"]: g["name"] for g in db.get("groups", [])}
    ud = unified_dates(db)
    rows = []
    for code, st in sorted(db["stocks"].items(), key=lambda kv: kv[1].get("order", 9999)):
        if st.get("hidden") or not st.get("history"):
            continue
        m = st.get("market", "TW")
        b, prev = bar_on_or_before(st["history"], ud.get(m, ""))
        if not b:
            continue
        chg = b.get("chg")
        base = (b["c"] - chg) if chg is not None else (prev["c"] if prev else None)
        pct = round(chg / base * 100, 2) if chg is not None and base else ""
        rows.append({
            "代號": code, "名稱": st.get("name", ""), "市場": m, "交易所": st.get("exchange", ""),
            "報告": "、".join(gname.get(g, g) for g in st.get("groups", [])),
            "統一日": ud.get(m, ""), "資料日": b["d"], "是否統一日": "Y" if b["d"] == ud.get(m) else "N",
            "收盤價": b["c"], "漲跌": "" if chg is None else chg, "漲跌幅%": pct,
            "幣別": "TWD" if m == "TW" else "USD",
        })
    cols = ["代號", "名稱", "市場", "交易所", "報告", "統一日", "資料日", "是否統一日", "收盤價", "漲跌", "漲跌幅%", "幣別"]
    with open(OUT.parent / "latest.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    with open(OUT.parent / "history.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["代號", "名稱", "市場", "日期", "開盤", "最高", "最低", "收盤", "漲跌", "成交量"])
        for code, st in sorted(db["stocks"].items(), key=lambda kv: kv[1].get("order", 9999)):
            if st.get("hidden"):
                continue
            for b in st.get("history", []):
                w.writerow([code, st.get("name", ""), st.get("market", ""), b["d"], b.get("o"), b.get("h"),
                            b.get("l"), b["c"], b.get("chg"), b.get("v")])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", type=int, default=0, help="用 Yahoo 回補最近 N 天歷史")
    args = ap.parse_args()

    wl = json.loads(WATCHLIST.read_text(encoding="utf-8"))
    watch = wl["stocks"]
    db = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"stocks": {}}
    db.setdefault("stocks", {})

    tw_codes = [s for s in watch if s.get("market", "TW").upper() == "TW"]
    twse, tpex = {}, {}
    if tw_codes:
        try:
            twse = fetch_twse()
        except Exception as e:  # noqa: BLE001
            print("⚠ TWSE:", e, file=sys.stderr)
        try:
            tpex = fetch_tpex()
        except Exception as e:  # noqa: BLE001
            print("⚠ TPEx:", e, file=sys.stderr)

    rng = None
    if args.backfill > 0:
        rng = "5d" if args.backfill <= 5 else "1mo" if args.backfill <= 31 else \
              "3mo" if args.backfill <= 92 else "6mo" if args.backfill <= 183 else \
              "1y" if args.backfill <= 366 else "2y" if args.backfill <= 731 else "5y"

    errors = 0
    for s in watch:
        code = str(s["code"]).strip().upper()
        market = s.get("market", "TW").upper()
        st = db["stocks"].setdefault(code, {"history": []})
        st["market"] = market
        st["groups"] = s.get("groups", [])
        st["order"] = watch.index(s)
        if s.get("name"):
            st["name"] = s["name"]
        try:
            if market == "TW":
                row = twse.get(code) or tpex.get(code)
                if row:
                    st["exchange"] = row["ex"]
                    st.setdefault("name", row.get("name"))
                suffixes = [".TW"] if st.get("exchange") == "TWSE" else \
                           [".TWO"] if st.get("exchange") == "TPEx" else [".TW", ".TWO"]
                if rng or not row:  # 回補，或官方抓不到時改用 Yahoo
                    for suf in suffixes:
                        try:
                            merge(st, fetch_yahoo(code + suf, rng or "5d"))
                            st.setdefault("exchange", "TWSE" if suf == ".TW" else "TPEx")
                            break
                        except Exception:  # noqa: BLE001
                            continue
                if row:  # 官方盤後資料最後寫入，同日以官方為準
                    merge(st, [row])
            else:
                st["exchange"] = "US"
                merge(st, fetch_yahoo(code, rng or "5d"))
            last = st["history"][-1] if st["history"] else None
            print(f"✓ {code:<6} {st.get('name','')}  {last['d'] if last else '-'}  {last['c'] if last else '-'}")
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"✗ {code}: {e}", file=sys.stderr)
        time.sleep(0.5)

    # 從觀察清單移除的股票保留歷史，但標記為不顯示
    codes = {str(s["code"]).strip().upper() for s in watch}
    for c, st in db["stocks"].items():
        st["hidden"] = c not in codes

    db["groups"] = wl.get("groups", [])
    db["updated"] = dt.datetime.now(TPE).strftime("%Y-%m-%d %H:%M")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    write_csvs(db)
    print(f"已寫入 {OUT}（{len(codes)} 檔，{errors} 檔失敗）")
    if errors == len(watch):
        sys.exit(1)


if __name__ == "__main__":
    main()
