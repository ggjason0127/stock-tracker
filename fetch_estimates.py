#!/usr/bin/env python3
"""
自動抓 EPS，寫入 data/estimates.json（給網站估值模型當預設值）

台股
  近四季 EPS  = 收盤價 ÷ 官方本益比（證交所 BWIBBU_ALL、櫃買中心本益比資料）
  今年預估 EPS = 全年營收推估 ×（近四季 EPS ÷ 同期近四季營收）
                 全年營收 = 已公布月營收 + 未公布月份（去年同月 × (1 + 近三個月年增率)）
  明年預估 EPS = 今年預估 × (1 + 今年營收成長率 ÷ 2)，上限 15%、下限 −5%
  月營收：每月 11 日抓一次（加 --revenue），證交所／櫃買中心 OpenAPI 最新一期，
         缺的歷史月份用公開資訊觀測站月營收彙總頁回補；平日只用已存的月營收重算
美股
  近四季 EPS、今年／明年預估 EPS：Yahoo Finance 分析師共識（非正式 API，可能失效）
  台股若 Yahoo 有分析師共識，也一併存下來供參考

只用 Python 標準函式庫。
"""
import datetime as dt
import html
import http.cookiejar
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
DATA = ROOT / "data"
TPE = dt.timezone(dt.timedelta(hours=8))
UA_API = {"User-Agent": "Mozilla/5.0 (daily-stock-tracker)", "Accept": "application/json"}
UA_WEB = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"}

TWSE_PE = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
TPEX_PE = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"
TWSE_REV = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
TPEX_REV = ["https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O",
            "https://www.tpex.org.tw/openapi/v1/tpex_mopsfin_t187ap05_O"]
MOPS_HIST = "https://mopsov.twse.com.tw/nas/t21/{mkt}/t21sc03_{roc}_{m}_{kind}.html"
BACKFILL_MONTHS = 26


# ---------------- 共用 ----------------
def http_get(url, headers=UA_API, opener=None, timeout=30, retries=2):
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with (opener.open(req, timeout=timeout) if opener else urllib.request.urlopen(req, timeout=timeout)) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (404, 400):
                raise
            last = e
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"{url}: {last}")


def get_json(url, **kw):
    return json.loads(http_get(url, **kw).decode("utf-8-sig"))


def num(x):
    if x is None:
        return None
    s = str(x).replace(",", "").replace("+", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def find(row, *patterns):
    """依欄位名稱的關鍵字找值（API 欄位名稱偶爾會改，用模糊比對）"""
    for p in patterns:
        if p in row:
            return row[p]
    for p in patterns:
        for k, v in row.items():
            if p.lower() in k.lower():
                return v
    return None


def ym_add(ym, k):
    y, m = map(int, ym.split("-"))
    m += k
    while m <= 0:
        m += 12; y -= 1
    while m > 12:
        m -= 12; y += 1
    return f"{y:04d}-{m:02d}"


def roc_ym(s):
    """'11510' / '115/10' → '2026-10'"""
    s = re.sub(r"\D", "", str(s or ""))
    if len(s) == 5:
        return f"{int(s[:3]) + 1911:04d}-{s[3:]}"
    if len(s) == 6 and s.startswith("20"):
        return f"{s[:4]}-{s[4:]}"
    return None


# ---------------- 台股：官方本益比 → 近四季 EPS ----------------
def fetch_pe():
    out = {}
    for url, ex in ((TWSE_PE, "TWSE"), (TPEX_PE, "TPEx")):
        try:
            for r in get_json(url):
                code = str(find(r, "Code", "SecuritiesCompanyCode", "證券代號", "股票代號") or "").strip()
                pe = num(find(r, "PEratio", "PriceEarningRatio", "PriceEarning", "本益比"))
                if code and pe and pe > 0:
                    out[code] = pe
        except Exception as e:  # noqa: BLE001
            print(f"⚠ 本益比 {ex}: {e}", file=sys.stderr)
    return out


# ---------------- 台股：月營收 ----------------
def fetch_rev_snapshot():
    """最新一期月營收（含上月、去年同月）"""
    rows = []
    try:
        rows += get_json(TWSE_REV)
    except Exception as e:  # noqa: BLE001
        print("⚠ 上市月營收:", e, file=sys.stderr)
    for u in TPEX_REV:
        try:
            rows += get_json(u)
            break
        except Exception as e:  # noqa: BLE001
            print("⚠ 上櫃月營收:", u, e, file=sys.stderr)
    out = {}
    for r in rows:
        code = str(find(r, "公司代號", "CompanyCode", "SecuritiesCompanyCode") or "").strip()
        ym = roc_ym(find(r, "資料年月", "DataYearMonth", "YearMonth"))
        if not code or not ym:
            continue
        cur = num(find(r, "營業收入-當月營收", "當月營收"))
        prev = num(find(r, "營業收入-上月營收", "上月營收"))
        ly = num(find(r, "營業收入-去年當月營收", "去年當月營收"))
        d = out.setdefault(code, {})
        if cur is not None: d[ym] = cur
        if prev is not None: d[ym_add(ym, -1)] = prev
        if ly is not None: d[ym_add(ym, -12)] = ly
    return out


def parse_mops_month(raw):
    text = raw.decode("cp950", errors="ignore")
    out = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.S | re.I):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S | re.I)]
        if len(cells) >= 5 and re.fullmatch(r"\d{4,6}[A-Z]?", cells[0]):
            v = num(cells[2])  # 代號、名稱、當月營收、上月營收、去年當月營收…
            if v is not None:
                out[cells[0]] = v
    return out


def backfill_revenue(store, codes, today):
    """用公開資訊觀測站月營收彙總頁回補最近 N 個月（每月只試一次，失敗 7 天後再試）"""
    meta = store.setdefault("_tried", {})
    last_pub = ym_add(f"{today.year:04d}-{today.month:02d}", -1)
    for k in range(BACKFILL_MONTHS):
        ym = ym_add(last_pub, -k)
        have = sum(1 for c in codes if ym in store.get(c, {}))
        if have >= len(codes):
            continue
        tried = meta.get(ym)
        if tried and (today - dt.date.fromisoformat(tried)).days < 7:
            continue
        y, m = map(int, ym.split("-"))
        got = {}
        for mkt in ("sii", "otc"):
            for kind in ("0", "1"):  # 0 國內公司、1 外國公司（-KY）
                url = MOPS_HIST.format(mkt=mkt, roc=y - 1911, m=m, kind=kind)
                try:
                    got.update(parse_mops_month(http_get(url, headers=UA_WEB, retries=1)))
                except Exception as e:  # noqa: BLE001
                    print(f"  ⚠ 月營收回補 {ym} {mkt}_{kind}: {e}", file=sys.stderr)
                time.sleep(0.6)
        for c in codes:
            if c in got:
                store.setdefault(c, {})[ym] = got[c]
        meta[ym] = today.isoformat()
        print(f"  月營收回補 {ym}: {sum(1 for c in codes if c in got)}/{len(codes)} 檔")


def ttm_quarter_end(today):
    """官方本益比用的「近四季」截止月（依財報公告期限推算）"""
    y, md = today.year, (today.month, today.day)
    if md >= (11, 15): return f"{y}-09"
    if md >= (8, 15):  return f"{y}-06"
    if md >= (5, 16):  return f"{y}-03"
    if md >= (4, 1):   return f"{y - 1}-12"
    return f"{y - 1}-09"


def estimate_from_revenue(rev, eps_ttm, today):
    qe = ttm_quarter_end(today)
    ttm_months = [ym_add(qe, -k) for k in range(12)]
    if eps_ttm is None or any(m not in rev for m in ttm_months):
        return None, "月營收資料不足 12 個月" if eps_ttm is not None else "缺近四季 EPS"
    ttm_rev = sum(rev[m] for m in ttm_months)
    if ttm_rev <= 0:
        return None, "近四季營收為 0"
    Y = today.year
    actual = sorted(m for m in rev if m.startswith(f"{Y}-"))
    last = actual[-1] if actual else f"{Y - 1}-12"
    # 近三個月年增率
    l3 = [ym_add(last, -k) for k in range(3)]
    if any(m not in rev or ym_add(m, -12) not in rev for m in l3):
        return None, "缺去年同期月營收"
    base = sum(rev[ym_add(m, -12)] for m in l3)
    g3 = sum(rev[m] for m in l3) / base - 1 if base else 0
    full = 0.0
    for mm in range(1, 13):
        m = f"{Y}-{mm:02d}"
        if m in rev:
            full += rev[m]
        elif ym_add(m, -12) in rev:
            full += rev[ym_add(m, -12)] * (1 + g3)
        else:
            return None, f"缺 {Y - 1} 年 {mm} 月營收"
    last_year = [f"{Y - 1}-{mm:02d}" for mm in range(1, 13)]
    if any(m not in rev for m in last_year):
        return None, f"缺 {Y - 1} 年完整營收"
    g_y = full / sum(rev[m] for m in last_year) - 1
    g_next = max(-0.05, min(0.15, g_y / 2))
    fy = full * eps_ttm / ttm_rev
    return {
        "fy26": round(fy, 2), "fy27": round(fy * (1 + g_next), 2),
        "detail": {"ttm_end": qe, "rev_last": last, "g3": round(g3, 4), "g_year": round(g_y, 4),
                   "g_next": round(g_next, 4), "rev_year_est": round(full), "rev_ttm": round(ttm_rev),
                   "eps_per_rev": eps_ttm / ttm_rev},
    }, None


# ---------------- Yahoo 分析師共識 ----------------
class Yahoo:
    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.crumb = None

    def login(self):
        try:
            http_get("https://fc.yahoo.com", headers=UA_WEB, opener=self.opener, retries=0)
        except Exception:  # noqa: BLE001  （回 404 也會給 cookie）
            pass
        c = http_get("https://query2.finance.yahoo.com/v1/test/getcrumb", headers=UA_WEB, opener=self.opener).decode().strip()
        if not c or "<" in c:
            raise RuntimeError("取得 crumb 失敗")
        self.crumb = c

    def estimates(self, sym):
        url = (f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{sym}"
               f"?modules=earningsTrend,defaultKeyStatistics&crumb={urllib.parse.quote(self.crumb)}")
        res = json.loads(http_get(url, headers=UA_WEB, opener=self.opener))["quoteSummary"]["result"][0]
        out = {}
        for t in (res.get("earningsTrend") or {}).get("trend", []):
            key = {"0y": "fy26", "+1y": "fy27"}.get(t.get("period"))
            ee = t.get("earningsEstimate") or {}
            avg = (ee.get("avg") or {}).get("raw")
            if key and avg is not None:
                out[key] = {"v": round(avg, 2), "n": (ee.get("numberOfAnalysts") or {}).get("raw"),
                            "end": t.get("endDate")}
        te = ((res.get("defaultKeyStatistics") or {}).get("trailingEps") or {}).get("raw")
        if te is not None:
            out["ttm"] = {"v": round(te, 2)}
        return out


# ---------------- 主程式 ----------------
def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--revenue", action="store_true", help="抓最新月營收並回補歷史（每月一次）")
    args = ap.parse_args()
    today = dt.datetime.now(TPE).date()
    wl = json.loads((ROOT / "watchlist.json").read_text(encoding="utf-8"))["stocks"]
    prices = json.loads((DATA / "prices.json").read_text(encoding="utf-8"))["stocks"]
    rev_path = DATA / "revenue.json"
    revs = json.loads(rev_path.read_text(encoding="utf-8")) if rev_path.exists() else {}
    tw = [s["code"] for s in wl if s.get("market", "TW") == "TW"]
    us = [s["code"] for s in wl if s.get("market") == "US"]

    # 台股
    pe = fetch_pe() if tw else {}
    need_rev = args.revenue or any(c not in revs.get("_codes", []) for c in tw)  # 第一次或新加股票時也抓
    if tw and need_rev:
        print("抓月營收…")
        for c, d in fetch_rev_snapshot().items():
            if c in tw:
                revs.setdefault(c, {}).update(d)
        backfill_revenue(revs, tw, today)
        revs["_codes"] = sorted(set(revs.get("_codes", [])) | set(tw))

    yahoo = Yahoo()
    try:
        yahoo.login()
    except Exception as e:  # noqa: BLE001
        yahoo = None
        print("⚠ Yahoo 登入失敗，略過分析師共識:", e, file=sys.stderr)

    out = {"updated": dt.datetime.now(TPE).strftime("%Y-%m-%d %H:%M"), "year": today.year, "stocks": {}}
    for c in tw + us:
        st = prices.get(c, {})
        hist = st.get("history") or []
        close = hist[-1]["c"] if hist else None
        e = {}
        if c in tw:
            if c in pe and close:
                e["ttm"] = {"v": round(close / pe[c], 2), "src": "官方", "asof": hist[-1]["d"],
                            "note": f"收盤 {close} ÷ 官方本益比 {pe[c]}"}
            est, why = estimate_from_revenue(revs.get(c, {}), e.get("ttm", {}).get("v"), today)
            if est:
                d = est["detail"]
                e["fy26"] = {"v": est["fy26"], "src": "月營收推估",
                             "note": f"營收至 {d['rev_last']}，近三月年增 {d['g3']:+.1%}，全年營收年增 {d['g_year']:+.1%}"}
                e["fy27"] = {"v": est["fy27"], "src": "月營收推估", "note": f"今年預估 × (1 {d['g_next']:+.1%})"}
                e["rev"] = d
            elif why:
                e["why"] = why
        cons = {}
        if yahoo:
            sym = c if c in us else c + (".TWO" if st.get("exchange") == "TPEx" else ".TW")
            try:
                cons = yahoo.estimates(sym)
            except Exception as ex:  # noqa: BLE001
                print(f"  ⚠ Yahoo {sym}: {ex}", file=sys.stderr)
            time.sleep(0.4)
        if c in us:
            for k in ("ttm", "fy26", "fy27"):
                if k in cons:
                    e[k] = {**cons[k], "src": "分析師共識" if k != "ttm" else "Yahoo"}
        elif cons.get("fy26") or cons.get("fy27"):
            e["cons"] = {k: cons[k] for k in ("fy26", "fy27") if k in cons}
        out["stocks"][c] = e
        print(f"{c:<6} " + "  ".join(f"{k}={e[k]['v']}({e[k]['src']})" for k in ("ttm", "fy26", "fy27") if k in e)
              + (f"  [{e['why']}]" if e.get("why") else ""))

    rev_path.write_text(json.dumps(revs, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    (DATA / "estimates.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("已寫入 data/estimates.json")


if __name__ == "__main__":
    main()
