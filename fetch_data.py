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
    """KIWOOM 미국S&P500모멘텀 (0137V0) - 네이버 금융 현재가"""
    code = "0137V0"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://finance.naver.com",
    }

    # 방법 1: 네이버 금융 현재가 API
    try:
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        r = requests.get(url, headers=headers, timeout=10)
        # 현재가 파싱
        match = re.search(r'"now":\s*"?([\d,]+)"?', r.text)
        if match:
            price = int(match.group(1).replace(",", ""))
            if 1000 < price < 1000000:
                print(f"  KIWOOM(naver1): {price:,}원")
                return price
    except Exception as e:
        print(f"  방법1 오류: {e}")

    # 방법 2: 네이버 금융 시세 JSON
    try:
        url2 = f"https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"
        r2 = requests.get(url2, headers=headers, timeout=10)
        d = r2.json()
        price = int(d["datas"][0]["closePrice"].replace(",",""))
        if 1000 < price < 1000000:
            print(f"  KIWOOM(naver2): {price:,}원")
            return price
    except Exception as e:
        print(f"  방법2 오류: {e}")

    # 방법 3: 네이버 증권 검색 API
    try:
        url3 = f"https://m.stock.naver.com/api/stock/{code}/basic"
        r3 = requests.get(url3, headers=headers, timeout=10)
        d = r3.json()
        price = int(str(d.get("closePrice","0")).replace(",",""))
        if 1000 < price < 1000000:
            print(f"  KIWOOM(naver3): {price:,}원")
            return price
    except Exception as e:
        print(f"  방법3 오류: {e}")

    # 방법 4: 네이버 금융 모바일
    try:
        url4 = f"https://m.finance.naver.com/domestic/item.nhn?code={code}"
        r4 = requests.get(url4, headers=headers, timeout=10)
        match2 = re.search(r'<em[^>]*class="[^"]*price[^"]*"[^>]*>([\d,]+)</em>', r4.text)
        if not match2:
            match2 = re.search(r'([\d,]+)</em>', r4.text)
        if match2:
            price = int(match2.group(1).replace(",",""))
            if 1000 < price < 1000000:
                print(f"  KIWOOM(naver4): {price:,}원")
                return price
    except Exception as e:
        print(f"  방법4 오류: {e}")

    print(f"  KIWOOM: 모든 방법 실패 — None 반환")
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
print("KIWOOM ETF 수집...")
kiwoom_price = get_kiwoom_etf_price()
print("CNN F&G 수집...")
fg = get_cnn_fear_greed()

data = {
    "updated_utc":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "nasdaq": {
        "drawdown_pct":  qqq["drawdown_pct"],
        "rsi":           qqq["rsi"],
        "ma200_dev_pct": qqq["ma200_dev_pct"],
        "current_price": qqq["current_price"],
        "peak_price":    qqq["peak_price"],
    },
    "vix":          vix,
    "fear_greed":   fg,
    "etf_prices":   etf_prices,
    "usd_krw":      usd_krw,
    "kiwoom_price": kiwoom_price,
}

with open("docs/data.json", "w") as f:
    json.dump(data, f, indent=2)

print("\n✅ 저장 완료")
print(json.dumps(data, indent=2, ensure_ascii=False))
