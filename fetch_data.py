import json, os, requests, re
from datetime import datetime, timezone
import yfinance as yf

def calc_rsi(close, period=14):
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rsi   = 100 - 100 / (1 + gain / loss)
    return round(float(rsi.iloc[-1]), 1)

def get_nasdaq():
    df    = yf.download("QQQ", period="2y", interval="1d", progress=False, auto_adjust=True)
    close = df["Close"].dropna().squeeze()
    cur   = float(close.iloc[-1])
    peak  = float(close.max())
    ma200 = float(close.rolling(200).mean().iloc[-1])
    ma50  = float(close.rolling(50).mean().iloc[-1])  # SWITCH 전략용
    # 6개월 전 가격 (약 126 거래일)
    idx_6m = max(0, len(close) - 126)
    price_6m_ago = float(close.iloc[idx_6m])
    momentum_6m = round((cur / price_6m_ago - 1) * 100, 2)
    # SWITCH 전략용 20일/60일 수익률
    idx_20d = max(0, len(close) - 20)
    idx_60d = max(0, len(close) - 60)
    price_20d_ago = float(close.iloc[idx_20d])
    price_60d_ago = float(close.iloc[idx_60d])
    return_20d = round((cur / price_20d_ago - 1) * 100, 2)
    return_60d = round((cur / price_60d_ago - 1) * 100, 2)
    return {
        "drawdown_pct":   round((cur / peak - 1) * 100, 2),
        "rsi":            calc_rsi(close),
        "ma200_dev_pct":  round((cur / ma200 - 1) * 100, 2),
        "ma50_dev_pct":   round((cur / ma50 - 1) * 100, 2),    # SWITCH: 50일선 대비
        "return_20d_pct": return_20d,                           # SWITCH: 20일 수익률
        "return_60d_pct": return_60d,                           # SWITCH: 60일 수익률
        "current_price":  round(cur, 2),
        "peak_price":     round(peak, 2),
        "momentum_6m":    momentum_6m,
        "above_ma200":    cur > ma200,
        "above_ma50":     cur > ma50,
    }

def get_vix():
    df = yf.download("^VIX", period="5d", interval="1d", progress=False, auto_adjust=True)
    return round(float(df["Close"].dropna().squeeze().iloc[-1]), 2)

def get_etf_prices():
    tickers = ["QLD", "SPMO", "SCHD", "AVUV", "SGOV"]
    prices = {}
    for t in tickers:
        try:
            df = yf.download(t, period="2d", interval="1d", progress=False, auto_adjust=True)
            price = round(float(df["Close"].dropna().squeeze().iloc[-1]), 2)
            prices[t] = price
            print(f"  {t}: ${price}")
        except Exception as e:
            print(f"  {t} 오류: {e}")
            prices[t] = None
    return prices

def get_usd_krw():
    try:
        df = yf.download("USDKRW=X", period="2d", interval="1d", progress=False, auto_adjust=True)
        rate = round(float(df["Close"].dropna().squeeze().iloc[-1]), 1)
        print(f"  USD/KRW: {rate}")
        return rate
    except Exception as e:
        print(f"  환율 오류: {e}")
        return None

def get_kiwoom_etf_price():
    code = "0137V0"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://finance.naver.com",
    }
    try:
        url2 = f"https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"
        r2 = requests.get(url2, headers=headers, timeout=10)
        d = r2.json()
        price = int(d["datas"][0]["closePrice"].replace(",",""))
        if 1000 < price < 1000000:
            print(f"  KIWOOM(naver2): {price:,}원")
            return price
    except Exception as e:
        print(f"  KIWOOM 오류: {e}")
    return None

def get_cnn_fear_greed():
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.cnn.com/markets/fear-and-greed",
        }
        r = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata", headers=headers, timeout=15)
        d = r.json()
        score = round(float(d["fear_and_greed"]["score"]), 1)
        rating = d["fear_and_greed"]["rating"]
        print(f"  CNN F&G: {score} ({rating})")
        return {"value": score, "classification": rating,
                "prev_close": round(float(d["fear_and_greed"]["previous_close"]), 1),
                "prev_1w":    round(float(d["fear_and_greed"]["previous_1_week"]), 1),
                "prev_1m":    round(float(d["fear_and_greed"]["previous_1_month"]), 1),
                "source": "CNN Business"}
    except Exception as e:
        print(f"  CNN F&G 오류: {e}")
        try:
            r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
            d = r.json()
            return {"value": int(d["data"][0]["value"]), "classification": d["data"][0]["value_classification"], "source": "alternative.me (fallback)"}
        except:
            return {"value": None, "classification": "unavailable", "source": "unavailable"}

os.makedirs("docs", exist_ok=True)

print("나스닥(QQQ) 수집...")
qqq = get_nasdaq()
print("VIX 수집...")
vix = get_vix()
print("ETF 현재가 수집...")
etf_prices = get_etf_prices()
print("환율 수집...")
usd_krw = get_usd_krw()
print("KIWOOM ETF 수집...")
kiwoom_price = get_kiwoom_etf_price()
print("CNN F&G 수집...")
fg = get_cnn_fear_greed()

# MPS 자동 계산
mps_trend     = 1 if qqq["above_ma200"] else 0
mps_momentum  = 1 if qqq["momentum_6m"] > 0 else 0
mps_vix       = 1 if vix < 20 else 0
mps_total     = mps_trend + mps_momentum + mps_vix

data = {
    "updated_utc":   datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "nasdaq": {
        "drawdown_pct":  qqq["drawdown_pct"],
        "rsi":           qqq["rsi"],
        "ma200_dev_pct": qqq["ma200_dev_pct"],
        "current_price": qqq["current_price"],
        "peak_price":    qqq["peak_price"],
        "momentum_6m":   qqq["momentum_6m"],
        "above_ma200":   qqq["above_ma200"],
    },
    "vix":           vix,
    "fear_greed":    fg,
    "etf_prices":    etf_prices,
    "usd_krw":       usd_krw,
    "kiwoom_price":  kiwoom_price,
    "mps": {
        "trend":    mps_trend,
        "momentum": mps_momentum,
        "vix":      mps_vix,
        "total":    mps_total,
    },
}

with open("docs/data.json", "w") as f:
    json.dump(data, f, indent=2)

print(f"\n✅ 저장 완료 | MPS: {mps_total}점 (추세:{mps_trend} 모멘텀:{mps_momentum} VIX:{mps_vix})")
print(json.dumps(data, indent=2, ensure_ascii=False))
