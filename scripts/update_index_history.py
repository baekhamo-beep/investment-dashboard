"""
yfinance + Stooq 폴백으로 QQQ/SPY/KOSPI 종가 가져옴 (NaN 방어)
"""
import json
import math
import sys
from datetime import datetime, timezone, timedelta
from io import StringIO

import yfinance as yf
import requests
import pandas as pd

KST = timezone(timedelta(hours=9))
now_kst = datetime.now(KST)
ym = now_kst.strftime("%Y-%m")
print(f"갱신 대상 월: {ym}")
print(f"yfinance 버전: {yf.__version__}")

def is_valid(v):
    """None, NaN, 0 이하 모두 invalid"""
    if v is None: return False
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f) or f <= 0:
            return False
        return True
    except:
        return False

def fetch_yfinance(symbol):
    try:
        df = yf.download(symbol, period="10d", progress=False, auto_adjust=True)
        if df.empty: return None
        if isinstance(df.columns, pd.MultiIndex):
            close = df["Close"][symbol] if symbol in df["Close"].columns else df["Close"].iloc[:, 0]
        else:
            close = df["Close"]
        last = close.dropna()
        if last.empty: return None
        val = float(last.iloc[-1])
        return val if is_valid(val) else None
    except Exception as e:
        print(f"  yfinance err ({symbol}): {e}")
        return None

def fetch_stooq(stooq_symbol):
    try:
        url = f"https://stooq.com/q/d/l/?s={stooq_symbol}&i=d"
        r = requests.get(url, timeout=15)
        if r.status_code != 200 or len(r.text) < 50: return None
        df = pd.read_csv(StringIO(r.text))
        if df.empty or "Close" not in df.columns: return None
        df = df.dropna(subset=["Close"]).sort_values("Date")
        if df.empty: return None
        val = float(df["Close"].iloc[-1])
        return val if is_valid(val) else None
    except Exception as e:
        print(f"  stooq err ({stooq_symbol}): {e}")
        return None

TICKERS = {
    "qqq":   {"yf": "QQQ",   "stooq": "qqq.us"},
    "spy":   {"yf": "SPY",   "stooq": "spy.us"},
    "kospi": {"yf": "^KS11", "stooq": "^kospi"}
}

prices = {}
for key, sym in TICKERS.items():
    print(f"\n[{key}]")
    val = fetch_yfinance(sym["yf"])
    if val:
        prices[key] = round(val, 2)
        print(f"  ✓ yfinance: {prices[key]}")
        continue
    val = fetch_stooq(sym["stooq"])
    if val:
        prices[key] = round(val, 2)
        print(f"  ✓ stooq:    {prices[key]}")
        continue
    print(f"  ✗ 실패")

print(f"\n받은 가격: {prices}")

if len(prices) < 3:
    print("⚠ 가격 일부만 받음 - 갱신 중단")
    sys.exit(1)

# JSON 갱신
json_path = "docs/index_history.json"
with open(json_path, "r", encoding="utf-8") as f:
    data = json.load(f)

if "data" not in data:
    data["data"] = {}

old = data["data"].get(ym)

# 같은 달이면 새 값으로 덮어쓰기 (월말까지 갱신 누적)
data["data"][ym] = prices

if old == prices:
    print(f"변경 없음 ({ym})")
else:
    print(f"변경 {ym}: {old} -> {prices}")

# NaN 최종 방어
def has_nan(obj):
    if isinstance(obj, float): return math.isnan(obj) or math.isinf(obj)
    if isinstance(obj, dict): return any(has_nan(v) for v in obj.values())
    if isinstance(obj, list): return any(has_nan(v) for v in obj)
    return False

if has_nan(data):
    print("⚠ NaN 발견 - 저장 중단!")
    sys.exit(1)

data["data"] = dict(sorted(data["data"].items()))
data["last_updated_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

with open(json_path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")

print(f"\n✅ {json_path} 갱신 완료")
