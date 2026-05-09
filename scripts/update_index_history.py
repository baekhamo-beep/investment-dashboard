"""
yfinance로 QQQ/SPY/KOSPI 최신 가격 가져와서
docs/index_history.json의 현재 월 데이터 갱신
"""
import json
import os
from datetime import datetime, timezone, timedelta
import yfinance as yf

# KST 기준 현재 연월
KST = timezone(timedelta(hours=9))
now_kst = datetime.now(KST)
ym = now_kst.strftime("%Y-%m")
print(f"갱신 대상 월: {ym}")

# 티커 매핑
TICKERS = {
    "qqq": "QQQ",
    "spy": "SPY",
    "kospi": "^KS11"
}

# 최신 종가 가져오기
prices = {}
for key, symbol in TICKERS.items():
    try:
        t = yf.Ticker(symbol)
        # 최근 5일 데이터 받아서 마지막 종가
        hist = t.history(period="5d")
        if hist.empty:
            print(f"  {key} ({symbol}): 데이터 없음")
            continue
        last_close = float(hist["Close"].iloc[-1])
        last_date = hist.index[-1].strftime("%Y-%m-%d")
        prices[key] = round(last_close, 2)
        print(f"  {key} ({symbol}): {last_close:.2f} ({last_date})")
    except Exception as e:
        print(f"  {key} ({symbol}) 오류: {e}")

if len(prices) < 3:
    print("⚠ 가격 일부만 받음 - 갱신 중단 (데이터 일관성 보호)")
    exit(0)

# JSON 파일 로드
json_path = "docs/index_history.json"
with open(json_path, "r", encoding="utf-8") as f:
    data = json.load(f)

# 현재 월 데이터 업데이트 (덮어쓰기)
if "data" not in data:
    data["data"] = {}

old = data["data"].get(ym)
data["data"][ym] = prices

if old == prices:
    print(f"변경 없음 - 커밋 스킵 가능")
else:
    print(f"변경: {old} -> {prices}")

# 정렬해서 저장 (연월 순)
data["data"] = dict(sorted(data["data"].items()))
data["last_updated_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

with open(json_path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")

print(f"✅ {json_path} 갱신 완료")
