import json, os, requests
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
    return {
        "drawdown_pct":  round((cur / peak - 1) * 100, 2),
        "rsi":           calc_rsi(close),
        "ma200_dev_pct": round((cur / ma200 - 1) * 100, 2),
        "current_price": round(cur, 2),
        "peak_price":    round(peak, 2),
    }

def get_vix():
    df = yf.download("^VIX", period="5d", interval="1d", progress=False, auto_adjust=True)
    return round(float(df["Close"].dropna().squeeze().iloc[-1]), 2)

def get_etf_prices():
    """ETF 현재가 수집"""
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
    """달러/원 환율"""
    try:
        df = yf.download("USDKRW=X", period="2d", interval="1d", progress=False, auto_adjust=True)
        rate = round(float(df["Close"].dropna().squeeze().iloc[-1]), 1)
        print(f"  USD/KRW: {rate}")
        return rate
    except Exception as e:
        print(f"  환율 오류: {e}")
        return None

def get_cnn_fear_greed():
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.cnn.com/markets/fear-and-greed",
        }
        url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
        r = requests.get(url, headers=headers, timeout=15)
        d = r.json()
        score = round(float(d["fear_and_greed"]["score"]), 1)
        rating = d["fear_and_greed"]["rating"]
        prev_close = round(float(d["fear_and_greed"]["previous_close"]), 1)
        prev_1w    = round(float(d["fear_and_greed"]["previous_1_week"]), 1)
        prev_1m    = round(float(d["fear_and_greed"]["previous_1_month"]), 1)
        print(f"  CNN F&G: {score} ({rating})")
        return {"value": score, "classification": rating, "prev_close": prev_close, "prev_1w": prev_1w, "prev_1m": prev_1m, "source": "CNN Business"}
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
print("CNN F&G 수집...")
fg = get_cnn_fear_greed()

data = {
    "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "nasdaq": {
        "drawdown_pct":  qqq["drawdown_pct"],
        "rsi":           qqq["rsi"],
        "ma200_dev_pct": qqq["ma200_dev_pct"],
        "current_price": qqq["current_price"],
        "peak_price":    qqq["peak_price"],
    },
    "vix":        vix,
    "fear_greed": fg,
    "etf_prices": etf_prices,
    "usd_krw":    usd_krw,
}

with open("docs/data.json", "w") as f:
    json.dump(data, f, indent=2)

print("\n✅ 저장 완료")
print(json.dumps(data, indent=2, ensure_ascii=False))
