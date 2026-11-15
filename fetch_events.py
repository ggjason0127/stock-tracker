#!/usr/bin/env python3
"""
自選股公司活動 → data/events.json（網站「月報」分頁）

台股
  重大訊息（證交所 t187ap04_L、櫃買中心）：法說會、股東會、董事會／財報、股利、庫藏股、增資、併購…
    ※ 這個資料只有「當天」的公告，所以每天收集、累積起來（保留約 180 天）
  除權除息預告表（證交所、櫃買中心）
美股
  Yahoo Finance 財報日期（calendarEvents）
只用 Python 標準函式庫。
"""
import datetime as dt
import json
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from fetch_estimates import TPE, Yahoo, find, get_json, http_get, UA_WEB  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "data" / "events.json"
KEEP_DAYS = 180

ANN_URLS = [("https://openapi.twse.com.tw/v1/opendata/t187ap04_L", "TWSE"),
            ("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O", "TPEx"),
            ("https://www.tpex.org.tw/openapi/v1/tpex_mopsfin_t187ap04_O", "TPEx")]
EXDIV_URLS = [("https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL", "TWSE"),
              ("https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost", "TPEx"),
              ("https://www.tpex.org.tw/openapi/v1/tpex_exright_daily", "TPEx")]

TYPES = [  # (類型, 關鍵字)；依序比對，先符合者為準
    ("法說會", ["法人說明會", "投資人說明會", "法說會", "業績說明會", "investor conference"]),
    ("股東會", ["股東常會", "股東臨時會", "股東會"]),
    ("股利", ["除權", "除息", "股利", "配息", "盈餘分配"]),
    ("財報", ["財務報告", "財務報表", "財報", "營運成果", "自結"]),
    ("庫藏股", ["庫藏股", "買回本公司股份"]),
    ("增資／募資", ["增資", "可轉換公司債", "私募", "公司債"]),
    ("併購", ["合併", "併購", "收購", "分割", "股份轉換"]),
    ("董事會", ["董事會"]),
    ("人事", ["董事長", "總經理", "財務主管", "會計主管", "發言人", "異動"]),
]


def classify(title):
    t = title or ""
    for name, kws in TYPES:
        if any(k.lower() in t.lower() for k in kws):
            return name
    return "重大訊息"


def roc_to_iso(s):
    s = re.sub(r"\D", "", str(s or ""))
    if len(s) == 7:
        return f"{int(s[:3]) + 1911:04d}-{s[3:5]}-{s[5:7]}"
    if len(s) == 8 and s.startswith(("19", "20")):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return None


DATE_RE = re.compile(r"(\d{2,4})\s*[年/.\-]\s*(\d{1,2})\s*[月/.\-]\s*(\d{1,2})\s*日?")


def first_date_after(text, labels):
    """在說明文字中找『召開日期／開會日期／除息交易日…』後面的日期"""
    for lab in labels:
        i = text.find(lab)
        if i >= 0:
            m = DATE_RE.search(text, i, i + 60)
            if m:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                y = y + 1911 if y < 1000 else y
                try:
                    return dt.date(y, mo, d).isoformat()
                except ValueError:
                    pass
    return None


EVENT_LABELS = {
    "法說會": ["召開法人說明會之日期", "說明會日期", "召開日期", "日期"],
    "股東會": ["股東會召開日期", "召開日期", "開會日期"],
    "股利": ["除息交易日", "除權交易日", "除權息交易日", "基準日"],
    "財報": ["董事會日期", "董事會決議日期", "事實發生日"],
    "董事會": ["董事會日期", "召開日期", "董事會決議日期"],
}


def clean(s, n=220):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[:n] + "…"


# ---------------- 台股重大訊息 ----------------
def fetch_announcements(codes):
    out, done = [], set()
    for url, ex in ANN_URLS:
        if ex in done:
            continue
        try:
            rows = get_json(url)
        except Exception as e:  # noqa: BLE001
            print(f"⚠ 重大訊息 {ex}: {e}", file=sys.stderr)
            continue
        done.add(ex)
        for r in rows:
            code = str(find(r, "公司代號", "SecuritiesCompanyCode", "CompanyCode") or "").strip()
            if code not in codes:
                continue
            title = clean(find(r, "主旨", "Subject"), 160)
            body = str(find(r, "說明", "Description") or "")
            typ = classify(title)
            ann = roc_to_iso(find(r, "發言日期", "AnnouncementDate", "出表日期"))
            fact = roc_to_iso(find(r, "事實發生日", "FactDate"))
            evd = first_date_after(body, EVENT_LABELS.get(typ, [])) if typ in EVENT_LABELS else None
            out.append({"code": code, "name": find(r, "公司名稱", "CompanyName"), "type": typ,
                        "date": evd or fact or ann, "announced": ann, "title": title,
                        "detail": clean(body), "src": f"{ex} 重大訊息"})
    return out


# ---------------- 台股除權息預告 ----------------
def fetch_exdiv(codes):
    out = []
    for url, ex in EXDIV_URLS:
        try:
            rows = get_json(url)
        except Exception as e:  # noqa: BLE001
            print(f"⚠ 除權息預告 {ex} {url.rsplit('/', 1)[-1]}: {e}", file=sys.stderr)
            continue
        for r in rows:
            code = str(find(r, "Code", "股票代號", "SecuritiesCompanyCode", "代號") or "").strip()
            if code not in codes:
                continue
            d = roc_to_iso(find(r, "Date", "除權除息日期", "除權息日期", "ExRightsExDividendDate", "ExDate"))
            kind = find(r, "Exdividend", "除權息", "權/息", "ExRightOrDividend") or "除權息"
            cash = find(r, "CashDividend", "現金股利", "息值")
            stock = find(r, "StockDividendRatio", "無償配股率")
            det = "　".join(x for x in [f"現金股利 {cash}" if cash not in (None, "", "0", "0.0") else "",
                                        f"無償配股率 {stock}" if stock not in (None, "", "0", "0.0") else ""] if x)
            if d:
                out.append({"code": code, "name": find(r, "Name", "名稱", "股票名稱", "CompanyName"), "type": "股利",
                            "date": d, "announced": None, "title": f"{kind}交易日", "detail": det,
                            "src": f"{ex} 除權除息預告"})
    return out


# ---------------- 美股財報日 ----------------
def fetch_us_earnings(codes):
    out = []
    if not codes:
        return out
    y = Yahoo()
    try:
        y.login()
    except Exception as e:  # noqa: BLE001
        print("⚠ Yahoo 登入失敗，略過美股財報日:", e, file=sys.stderr)
        return out
    import urllib.parse
    for c in codes:
        url = (f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{c}"
               f"?modules=calendarEvents&crumb={urllib.parse.quote(y.crumb)}")
        try:
            res = json.loads(http_get(url, headers=UA_WEB, opener=y.opener))["quoteSummary"]["result"][0]
            ce = (res.get("calendarEvents") or {}).get("earnings") or {}
            dates = [x.get("fmt") or dt.datetime.utcfromtimestamp(x["raw"]).date().isoformat()
                     for x in ce.get("earningsDate", []) if x]
            if dates:
                avg = (ce.get("earningsAverage") or {}).get("raw")
                rev = (ce.get("revenueAverage") or {}).get("fmt")
                det = "　".join(x for x in [f"EPS 預估 {avg}" if avg is not None else "", f"營收預估 {rev}" if rev else "",
                                            "（日期區間，公司尚未確認）" if len(dates) > 1 and dates[0] != dates[-1] else ""] if x)
                out.append({"code": c, "name": None, "type": "財報", "date": dates[0], "announced": None,
                            "title": "財報公布", "detail": det, "src": "Yahoo"})
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠ Yahoo 財報日 {c}: {e}", file=sys.stderr)
        time.sleep(0.4)
    return out


def main():
    today = dt.datetime.now(TPE).date()
    wl = json.loads((ROOT / "watchlist.json").read_text(encoding="utf-8"))["stocks"]
    names = {s["code"]: s.get("name") for s in wl}
    tw = {s["code"] for s in wl if s.get("market", "TW") == "TW"}
    us = [s["code"] for s in wl if s.get("market") == "US"]

    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"events": []}
    # 預告型資料（除權息、美股財報日）每次重抓、整批替換；重大訊息累積
    keep = [e for e in old.get("events", []) if "重大訊息" in (e.get("src") or "")]
    new = fetch_announcements(tw) + fetch_exdiv(tw) + fetch_us_earnings(us)

    seen, events = set(), []
    for e in new + keep:
        e["name"] = e.get("name") or names.get(e["code"])
        k = (e["code"], e.get("type"), e.get("date"), e.get("title"))
        if k in seen:
            continue
        seen.add(k)
        d = e.get("date") or e.get("announced")
        if d and (today - dt.date.fromisoformat(d)).days > KEEP_DAYS:
            continue
        events.append(e)
    events.sort(key=lambda e: (e.get("date") or "", e["code"]))
    OUT.write_text(json.dumps({"updated": dt.datetime.now(TPE).strftime("%Y-%m-%d %H:%M"), "events": events},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    up = [e for e in events if (e.get("date") or "") >= today.isoformat()]
    print(f"已寫入 {OUT}：共 {len(events)} 筆，未來活動 {len(up)} 筆（本次新抓 {len(new)} 筆）")


if __name__ == "__main__":
    main()
