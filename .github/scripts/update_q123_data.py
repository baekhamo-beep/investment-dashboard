#!/usr/bin/env python3
"""
Q123 시그널 데이터 갱신 스크립트 (GitHub Actions 환경용)

기능:
  - Yahoo Finance (또는 Stooq 폴백)에서 QQQ 일별 종가 가져오기
  - SMA50, SMA200, RET63, RET126 정확 계산
  - 모드(BEAR/NORMAL/BOOST) 자동 판단
  - docs/q123_data.json 저장 (커밋/푸시는 Actions yml이 처리)

수동 실행:
  python update_q123_data.py
"""

import json
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

OUTPUT_PATH = Path("docs/q123_data.json")

# ── BOOST 트리거 임계값 (M63 전략) ──
THRESHOLDS = {
    "boost_ret63_min": 10,
    "boost_ret126_min": 0,
    "boost_peak_stop_pct": -8,
    "boost_max_hold_days": 63,
    "cooldown_days": 21,
}


def fetch_yahoo():
    """Yahoo Finance v8 chart API에서 QQQ 1년치 일별 데이터"""
    url = "https://query1.finance.yahoo.com/v8/finance/chart/QQQ?range=1y&interval=1d"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read())
    result = data["chart"]["result"][0]
    closes = result["indicators"]["quote"][0]["close"]
    timestamps = result["timestamp"]
    # None 필터링
    pairs = [(t, c) for t, c in zip(timestamps, closes) if c is not None]
    return pairs, "Yahoo Finance v8"


def fetch_stooq():
    """Stooq.com CSV에서 QQQ 일별 데이터 (Yahoo 폴백)"""
    import time as _time
    url = "https://stooq.com/q/d/l/?s=qqq.us&i=d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        csv_text = r.read().decode()
    lines = csv_text.strip().split("\n")
    header = lines[0].split(",")
    date_idx = header.index("Date")
    close_idx = header.index("Close")
    pairs = []
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) <= max(date_idx, close_idx):
            continue
        try:
            ts = int(_time.mktime(_time.strptime(parts[date_idx], "%Y-%m-%d")))
            close = float(parts[close_idx])
            pairs.append((ts, close))
        except (ValueError, IndexError):
            continue
    return pairs, "Stooq.com"


def fetch_qqq_history():
    """일별 QQQ 데이터를 받아옴. Yahoo → Stooq 순으로 시도."""
    for fetcher, name in [(fetch_yahoo, "Yahoo"), (fetch_stooq, "Stooq")]:
        try:
            print(f"📡 {name} 시도 중...")
            pairs, source = fetcher()
            if len(pairs) >= 200:
                print(f"   ✅ {source}: {len(pairs)}일 받음")
                return pairs, source
            else:
                print(f"   ⚠ {name}: 데이터 부족 ({len(pairs)}일)")
        except Exception as e:
            print(f"   ❌ {name} 실패: {e}")
    raise RuntimeError("Yahoo와 Stooq 모두 실패")


def safe_ret(prices, days_back):
    """N거래일 전 대비 수익률 (%)"""
    if len(prices) < days_back + 1:
        return None
    return (prices[-1] / prices[-(days_back + 1)] - 1) * 100


def calculate_signals(pairs):
    """일별 종가에서 모든 시그널 계산"""
    timestamps = [p[0] for p in pairs]
    closes = [p[1] for p in pairs]

    current_price = closes[-1]
    latest_ts = timestamps[-1]
    latest_date = datetime.fromtimestamp(latest_ts, tz=timezone.utc).strftime("%Y-%m-%d")

    sma50 = sum(closes[-50:]) / 50
    sma200 = sum(closes[-200:]) / 200

    ret20 = safe_ret(closes, 20)
    ret63 = safe_ret(closes, 63)
    ret126 = safe_ret(closes, 126)

    peak = max(closes)
    drawdown = (current_price / peak - 1) * 100

    above_sma50 = current_price > sma50
    above_sma200 = current_price > sma200
    alignment = sma50 > sma200 and above_sma50 and above_sma200

    # 모드 자동 판단
    if not above_sma200:
        mode, asset = "BEAR", "QQQ"
        bear_cond, boost_cond = True, False
    elif (alignment
          and ret63 is not None and ret63 >= THRESHOLDS["boost_ret63_min"]
          and ret126 is not None and ret126 >= THRESHOLDS["boost_ret126_min"]):
        mode, asset = "BOOST", "TQQQ"
        bear_cond, boost_cond = False, True
    else:
        mode, asset = "NORMAL", "QLD"
        bear_cond, boost_cond = False, False

    return {
        "as_of": latest_date,
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "qqq": {
            "close": round(current_price, 2),
            "ath": round(peak, 2),
            "drawdown_pct": round(drawdown, 2),
        },
        "sma": {
            "sma50": round(sma50, 2),
            "sma200": round(sma200, 2),
            "above_sma50": above_sma50,
            "above_sma200": above_sma200,
            "alignment": alignment,
        },
        "returns": {
            "ret20": round(ret20, 2) if ret20 is not None else None,
            "ret63": round(ret63, 2) if ret63 is not None else None,
            "ret126": round(ret126, 2) if ret126 is not None else None,
        },
        "mode": {
            "current": mode,
            "asset": asset,
            "bear_condition": bear_cond,
            "boost_condition": boost_cond,
        },
        "thresholds": THRESHOLDS,
    }


def main():
    print("=" * 60)
    print("Q123 시그널 데이터 갱신")
    print("=" * 60)

    # 1) 일별 종가 받기
    print("\n[1/3] QQQ 일별 종가 수집")
    try:
        pairs, source = fetch_qqq_history()
    except Exception as e:
        print(f"\n❌ 모든 데이터 소스 실패: {e}")
        sys.exit(1)

    # 2) 시그널 계산
    print("\n[2/3] 시그널 계산")
    payload = calculate_signals(pairs)
    payload["source"] = source

    print(f"   기준일:  {payload['as_of']}")
    print(f"   QQQ:     ${payload['qqq']['close']}")
    print(f"   ATH:     ${payload['qqq']['ath']} (낙폭 {payload['qqq']['drawdown_pct']:+.2f}%)")
    print(f"   SMA50:   ${payload['sma']['sma50']}")
    print(f"   SMA200:  ${payload['sma']['sma200']}")
    print(f"   정배열:  {'✓' if payload['sma']['alignment'] else '✗'}")
    print(f"   RET63:   {payload['returns']['ret63']:+.2f}%")
    print(f"   RET126:  {payload['returns']['ret126']:+.2f}%")
    print(f"   → 모드:  {payload['mode']['current']} ({payload['mode']['asset']} 100%)")

    # 3) 파일 저장
    print("\n[3/3] docs/q123_data.json 저장")
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # 기존 파일과 비교 (불필요한 커밋 방지)
    new_content = json.dumps(payload, indent=2, ensure_ascii=False)
    if OUTPUT_PATH.exists():
        try:
            old = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
            # updated_utc만 다르고 실질 데이터가 같으면 변경 없음
            old_signature = {k: old.get(k) for k in ("qqq", "sma", "returns", "mode")}
            new_signature = {k: payload.get(k) for k in ("qqq", "sma", "returns", "mode")}
            if old_signature == new_signature:
                print("   ⏸  실질 데이터 동일 — 파일 갱신 스킵")
                # updated_utc만 다르면 안 쓰는 게 깔끔 (커밋 안 일어남)
                return
        except Exception:
            pass

    OUTPUT_PATH.write_text(new_content, encoding="utf-8")
    print(f"   ✅ {len(new_content.encode('utf-8')):,} bytes 저장")
    print(f"\n🎉 완료")


if __name__ == "__main__":
    main()
