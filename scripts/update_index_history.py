"""
yfinance + Stooq 폴백으로 QQQ/SPY/KOSPI 최신 종가 가져옴
"""
import json
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

# ===== 1차 시도: yfinance =====
def fetch_yfinance(symbol):
    try:
        # download 함수가 더 안정적
        df = yf.download(symbol, period="10d", progress=False, auto_adjust=True)
        if df.empty:
            return None, None
        # 멀티 컬럼 처리 (최신 yfinance는 (Close, QQQ) 형태)
        if isinstance(df.columns, pd.MultiIndex):
            close = df["Close"][symbol] if symbol in df["Close"].columns else df["Close"].iloc[:, 0]
        else:
            close = df["Close"]
        last_close = float(close.iloc[-1])
        last_date = close.index[-1].strftime("%Y-%m-%d")
        return round(last_close, 2), last_date
    except Exception as e:
        print(f"  yfinance 오류 ({symbol}): {e}")
        return None, None

# ===== 2차 시도: Stooq (CSV 다운로드) =====
def fetch_stooq(stooq_symbol):
    try:
        url = f"https://stooq.com/q/d/l/?s={stooq_symbol}&i=d"
        r = requests.get(url, timeout=15)
        if r.status_code != 200 or len(r.text) < 50:
            return None, None
        df = pd.read_csv(StringIO(r.text))
        if df.empty or "Close" not in df.columns:
            return None, None
        df = df.dropna(subset=["Close"]).sort_values("Date")
        last_close = float(df["Close"].iloc[-1])
        last_date = str(df["Date"].iloc[-1])
        return round(last_close, 2), last_date
    except Exception as e:
        print(f"  stooq 오류 ({stooq_symbol}): {e}")
        return None, None

# 티커 매핑: (yfinance, stooq)
TICKERS = {
    "qqq":   {"yf": "QQQ",   "stooq": "qqq.us"},
    "spy":   {"yf": "SPY",   "stooq": "spy.us"},
    "kospi": {"yf": "^KS11", "stooq": "^kospi"}
}

prices = {}
for key, sym in TICKERS.items():
    print(f"\n[{key}] 시도 중...")
    # 1차: yfinance
    val, dt = fetch_yfinance(sym["yf"])
    if val:
        prices[key] = val
        print(f"  ✓ yfinance: {val} ({dt})")
        continue
    # 2차: stooq
    val, dt = fetch_stooq(sym["stooq"])
    if val:
        prices[key] = val
        print(f"  ✓ stooq:    {val} ({dt})")
        continue
    print(f"  ✗ 모두 실패")

print(f"\n받은 가격: {prices}")

if len(prices) < 3:
    print("⚠ 가격 일부만 받음 - 갱신 중단 (데이터 일관성 보호)")
    sys.exit(1)  # 실패로 처리 (재실행 알림 받기 쉬움)

# ===== JSON 갱신 =====
json_path = "docs/index_history.json"
with open(json_path, "r", encoding="utf-8") as f:
    data = json.load(f)

if "data" not in data:
    data["data"] = {}

old = data["data"].get(ym)
data["data"][ym] = prices

if old == prices:
    print(f"\n변경 없음 ({ym})")
else:
    print(f"\n변경 감지 {ym}: {old} -> {prices}")

data["data"] = dict(sorted(data["data"].items()))
data["last_updated_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

with open(json_path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")

print(f"\n✅ {json_path} 갱신 완료")
