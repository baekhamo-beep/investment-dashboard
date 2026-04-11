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

def get_fg():
    try:
        d = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10).json()
        return {"value": int(d["data"][0]["value"]), "classification": d["data"][0]["value_classification"]}
    except:
        return {"value": None, "classification": "unavailable"}

os.makedirs("docs", exist_ok=True)
data = {
    "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "nasdaq":      get_nasdaq(),
    "vix":         get_vix(),
    "fear_greed":  get_fg(),
}
with open("docs/data.json", "w") as f:
    json.dump(data, f, indent=2)
print(json.dumps(data, indent=2))
