	"""
📈 Stock Signal Telegram Bot — v4.0
======================================
Yangiliklar:
  ✅ Real vaqt narxlar (Polygon WebSocket)
  ✅ TradingView chart rasm + havola
  ✅ Support & Resistance chiziq (matplotlib)
  ✅ Halol aksiya filtri (qarz, alkohol, porno)
  ✅ O'zbek + ingliz tili
  ✅ Inline klaviatura (tugmalar)
  ✅ IPO, /news, /signal buyruqlari
  ✅ Yahoo, Finviz, Polygon manbalar

Kerakli paketlar:
  pip install requests schedule beautifulsoup4 lxml matplotlib mplfinance numpy pillow
"""

import requests
import schedule
import time
import logging
import threading
import re
import io
import numpy as np
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    MATPLOTLIB_OK = True
except ImportError:
    MATPLOTLIB_OK = False
    logging.warning("matplotlib o'rnatilmagan — chart rasmsiz ishlaydi")

logging.basicConfig(level=logging.INFO, format="%(asctime)s — %(message)s")

# ══════════════════════════════════════════════
#  🔧 SOZLAMALAR
# ══════════════════════════════════════════════

TELEGRAM_TOKEN   = "8644202231:AAGIgpy78-IDeLcKyjFiVMMn2WN7W9DeX7s"
TELEGRAM_CHAT_ID = "8644202231"
POLYGON_API_KEY  = "r5Vx6piDI5BbdWk1S6pqmDwmbx2ENxzt"

MIN_VOLUME_USD = 1_000_000
MIN_CHANGE_PCT = 2.0
MAX_CHANGE_PCT = 15.0
MIN_MARKET_CAP = 2_000_000_000

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Halol bo'lmagan sektorlar / kalit so'zlar
HARAM_KEYWORDS = [
    "alcohol", "beer", "wine", "spirits", "tobacco", "cannabis",
    "marijuana", "gambling", "casino", "betting", "lottery",
    "adult entertainment", "pornography", "weapons", "defense contractor",
    "pig", "pork", "brewery", "distillery",
    "constellation brands", "anheuser", "molson", "heineken",
    "mgm resorts", "las vegas sands", "wynn resorts", "penn gaming",
    "altria", "philip morris", "british american tobacco",
]

WATCHLIST = [
    "AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","AVGO",
    "JPM","V","UNH","XOM","LLY","MA","JNJ","PG",
    "HD","MRK","ABBV","CVX","CRM","BAC","COST","NFLX",
    "AMD","ADBE","WMT","TMO","ACN","ORCL","MCD","QCOM",
    "DIS","TXN","INTC","INTU","AMGN","CAT","GS","HOOD",
    "PLTR","SOFI","COIN","SQ","SNAP","UBER","LYFT","RBLX",
    "PYPL","SHOP","CRWD","DDOG","NET","SNOW","ARM","SMCI",
    "MU","LRCX","AMAT","MRVL","F","GM","WFC","C","MS",
    "BLK","SCHW","AXP","NKE","SBUX","ABNB","PINS","TWLO",
]

# ══════════════════════════════════════════════
#  📡 POLYGON — Real vaqt + tarix
# ══════════════════════════════════════════════

def get_realtime_price(ticker):
    """Real vaqt narxi (snapshot)."""
    url = (f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}"
           f"?apiKey={POLYGON_API_KEY}")
    try:
        r = requests.get(url, timeout=10)
        data = r.json().get("ticker", {})
        day  = data.get("day", {})
        prev = data.get("prevDay", {})
        last = data.get("lastTrade", {})
        return {
            "price":      last.get("p") or day.get("c", 0),
            "open":       day.get("o", 0),
            "high":       day.get("h", 0),
            "low":        day.get("l", 0),
            "close":      day.get("c", 0),
            "volume":     day.get("v", 0),
            "vwap":       day.get("vw", 0),
            "prev_close": prev.get("c", 0),
            "change_pct": data.get("todaysChangePerc", 0),
        }
    except Exception as e:
        logging.warning(f"Realtime price xato ({ticker}): {e}")
    return None


def get_candle_history(ticker, days=60):
    """So'nggi N kunlik sham (OHLCV) ma'lumotlari."""
    end   = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days+20)).strftime("%Y-%m-%d")
    url   = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day"
             f"/{start}/{end}?adjusted=true&sort=asc&limit=120&apiKey={POLYGON_API_KEY}")
    try:
        r    = requests.get(url, timeout=15)
        data = r.json().get("results", [])
        return data[-days:] if len(data) >= days else data
    except Exception as e:
        logging.warning(f"Candle history xato ({ticker}): {e}")
    return []


def get_stock_details(ticker):
    """Polygon kompaniya ma'lumotlari."""
    url = (f"https://api.polygon.io/v3/reference/tickers/{ticker}"
           f"?apiKey={POLYGON_API_KEY}")
    try:
        r   = requests.get(url, timeout=10)
        res = r.json().get("results", {})
        return {
            "name":        res.get("name", ticker),
            "sector":      res.get("sic_description", "N/A"),
            "market_cap":  res.get("market_cap", 0) or 0,
            "employees":   res.get("total_employees", 0) or 0,
            "website":     res.get("homepage_url", ""),
            "description": (res.get("description") or "")[:400],
        }
    except Exception:
        return {"name": ticker, "sector": "N/A", "market_cap": 0,
                "employees": 0, "website": "", "description": ""}


def get_technicals(ticker):
    """RSI, SMA50, SMA200."""
    result = {"rsi": None, "sma50": None, "sma200": None}
    for indicator, window, key in [
        ("rsi", 14, "rsi"), ("sma", 50, "sma50"), ("sma", 200, "sma200")
    ]:
        try:
            r = requests.get(
                f"https://api.polygon.io/v1/indicators/{indicator}/{ticker}"
                f"?timespan=day&window={window}&series_type=close&limit=1"
                f"&apiKey={POLYGON_API_KEY}", timeout=10)
            vals = r.json().get("results", {}).get("values", [])
            if vals:
                result[key] = round(vals[0]["value"], 2)
        except Exception:
            pass
        time.sleep(13)
    return result


# ══════════════════════════════════════════════
#  💰 HALOLLIK TEKSHIRUVI
# ══════════════════════════════════════════════

def get_balance_sheet(ticker):
    """Qarz va aktiv nisbatini tekshirish."""
    url = (f"https://api.polygon.io/vX/reference/financials"
           f"?ticker={ticker}&timeframe=annual&limit=1&apiKey={POLYGON_API_KEY}")
    try:
        r    = requests.get(url, timeout=10)
        data = r.json().get("results", [])
        if data:
            bs = data[0].get("financials", {}).get("balance_sheet", {})
            total_assets      = bs.get("assets", {}).get("value", 0) or 0
            total_liabilities = bs.get("liabilities", {}).get("value", 0) or 0
            return {
                "assets":      total_assets,
                "liabilities": total_liabilities,
                "debt_ratio":  (total_liabilities / total_assets * 100) if total_assets > 0 else 0
            }
    except Exception as e:
        logging.warning(f"Balance sheet xato ({ticker}): {e}")
    return {"assets": 0, "liabilities": 0, "debt_ratio": 0}


def check_halal(ticker, details, balance):
    """
    Halol aksiya tekshiruvi:
      - Sektor/nom haram kalit so'zlardan xoli bo'lishi kerak
      - Qarz / aktiv nisbati 33% dan kam bo'lishi kerak (islomiy moliya standarti)
    """
    issues = []

    # Sektorni tekshirish
    sector_lower = (details.get("sector", "") + " " + details.get("description", "")).lower()
    name_lower   = details.get("name", "").lower()

    for kw in HARAM_KEYWORDS:
        if kw in sector_lower or kw in name_lower:
            issues.append(f"Haram sektor: {kw}")
            break

    # Qarz nisbatini tekshirish
    debt_ratio = balance.get("debt_ratio", 0)
    if debt_ratio > 33:
        issues.append(f"Qarz nisbati yuqori: {debt_ratio:.1f}% (33% dan oshmasligi kerak)")

    if issues:
        return False, issues
    return True, []


# ══════════════════════════════════════════════
#  📊 SUPPORT & RESISTANCE + CHART
# ══════════════════════════════════════════════

def calculate_support_resistance(candles, n=5):
    """
    So'nggi N ta sham asosida support va resistance darajalari.
    Pivot point usuli: lokkal min/maks topish.
    """
    if len(candles) < 10:
        return [], []

    highs  = np.array([c["h"] for c in candles])
    lows   = np.array([c["l"] for c in candles])
    closes = np.array([c["c"] for c in candles])

    supports    = []
    resistances = []

    # Lokkal minimumlar (support)
    for i in range(2, len(lows) - 2):
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and \
           lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            supports.append(round(lows[i], 2))

    # Lokkal maksimumlar (resistance)
    for i in range(2, len(highs) - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and \
           highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            resistances.append(round(highs[i], 2))

    # Eng yaqin 3 ta daraja
    current = closes[-1]
    supports    = sorted(set(supports),    reverse=True)
    resistances = sorted(set(resistances))

    near_sup = [s for s in supports    if s < current][:3]
    near_res = [r for r in resistances if r > current][:3]

    return near_sup, near_res


def draw_chart(ticker, candles, support_levels, resistance_levels, price_info):
    """Matplotlib bilan shamlar grafigi + S/R chiziqlari."""
    if not MATPLOTLIB_OK or len(candles) < 10:
        return None

    try:
        fig, (ax1, ax2) = plt.subplots(
            2, 1, figsize=(14, 8),
            gridspec_kw={"height_ratios": [3, 1]},
            facecolor="#0d1117"
        )
        fig.patch.set_facecolor("#0d1117")

        dates  = list(range(len(candles)))
        opens  = [c["o"] for c in candles]
        highs  = [c["h"] for c in candles]
        lows   = [c["l"] for c in candles]
        closes = [c["c"] for c in candles]
        vols   = [c["v"] for c in candles]

        # ── Shamlar ──
        for i, (o, h, l, c_) in enumerate(zip(opens, highs, lows, closes)):
            color = "#00c896" if c_ >= o else "#ff4757"
            # Sham tanasi
            ax1.bar(i, abs(c_ - o), bottom=min(o, c_),
                    color=color, width=0.7, zorder=3)
            # Sham dumchasi
            ax1.plot([i, i], [l, h], color=color, linewidth=0.8, zorder=2)

        # ── Support chiziqlari (yashil) ──
        for sup in support_levels:
            ax1.axhline(y=sup, color="#00c896", linewidth=1.2,
                        linestyle="--", alpha=0.8, zorder=4)
            ax1.text(len(candles) - 0.5, sup, f" S ${sup:.2f}",
                     color="#00c896", fontsize=7.5,
                     va="center", ha="left", zorder=5)

        # ── Resistance chiziqlari (qizil) ──
        for res in resistance_levels:
            ax1.axhline(y=res, color="#ff4757", linewidth=1.2,
                        linestyle="--", alpha=0.8, zorder=4)
            ax1.text(len(candles) - 0.5, res, f" R ${res:.2f}",
                     color="#ff4757", fontsize=7.5,
                     va="center", ha="left", zorder=5)

        # ── Joriy narx ──
        cur_price = price_info.get("price", closes[-1])
        ax1.axhline(y=cur_price, color="#f5a623", linewidth=1.5,
                    linestyle="-", alpha=0.9, zorder=4)
        ax1.text(0, cur_price, f"${cur_price:.2f} ◄",
                 color="#f5a623", fontsize=9, fontweight="bold",
                 va="center", zorder=5)

        # ── SMA 20 ──
        if len(closes) >= 20:
            sma20 = np.convolve(closes, np.ones(20)/20, mode="valid")
            ax1.plot(range(19, len(closes)), sma20,
                     color="#a29bfe", linewidth=1.2, label="SMA20", zorder=3)

        # ── SMA 50 ──
        if len(closes) >= 50:
            sma50 = np.convolve(closes, np.ones(50)/50, mode="valid")
            ax1.plot(range(49, len(closes)), sma50,
                     color="#fdcb6e", linewidth=1.2, label="SMA50", zorder=3)

        ax1.set_facecolor("#0d1117")
        ax1.tick_params(colors="#8b949e", labelsize=8)
        ax1.spines[:].set_color("#30363d")
        ax1.set_xlim(-1, len(candles) + 4)
        ax1.legend(loc="upper left", fontsize=8,
                   facecolor="#161b22", labelcolor="white", framealpha=0.8)
        change_pct = price_info.get("change_pct", 0)
        color_title = "#00c896" if change_pct >= 0 else "#ff4757"
        ax1.set_title(
            f"{ticker}  ${cur_price:.2f}  ({change_pct:+.2f}%)",
            color=color_title, fontsize=13, fontweight="bold", pad=10
        )

        # ── Hajm ustunlari ──
        for i, (o, c_, v) in enumerate(zip(opens, closes, vols)):
            color = "#00c896" if c_ >= o else "#ff4757"
            ax2.bar(i, v, color=color, width=0.7, alpha=0.7)

        ax2.set_facecolor("#0d1117")
        ax2.tick_params(colors="#8b949e", labelsize=7)
        ax2.spines[:].set_color("#30363d")
        ax2.set_xlim(-1, len(candles) + 4)
        ax2.set_ylabel("Hajm", color="#8b949e", fontsize=8)
        ax2.yaxis.set_major_formatter(
            matplotlib.ticker.FuncFormatter(
                lambda x, _: f"{x/1e6:.0f}M" if x >= 1e6 else f"{x/1e3:.0f}K"
            )
        )

        # Pastki info
        now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
        fig.text(0.01, 0.01, f"📊 {ticker} | 60 kunlik | {now_str} | t.me/StockSignalBot",
                 color="#484f58", fontsize=7)

        plt.tight_layout(pad=1.5)

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=130,
                    bbox_inches="tight", facecolor="#0d1117")
        buf.seek(0)
        plt.close(fig)
        return buf

    except Exception as e:
        logging.error(f"Chart xato ({ticker}): {e}")
        return None


# ══════════════════════════════════════════════
#  📺 TRADINGVIEW
# ══════════════════════════════════════════════

def get_tradingview_rating(ticker):
    """TradingView texnik signal reytingi."""
    url     = "https://scanner.tradingview.com/america/scan"
    payload = {
        "symbols": {"tickers": [f"NASDAQ:{ticker}", f"NYSE:{ticker}", f"AMEX:{ticker}"]},
        "columns": ["Recommend.All", "Recommend.MA", "Recommend.Other",
                    "RSI", "MACD.macd", "MACD.signal",
                    "Stoch.K", "ADX", "volume"]
    }
    result = {
        "rating": "N/A", "ma_rating": "N/A", "oscillator": "N/A",
        "tv_rsi": "N/A", "macd": "N/A", "stoch": "N/A", "adx": "N/A",
    }
    try:
        r    = requests.post(url, json=payload, headers=HEADERS, timeout=12)
        data = r.json().get("data", [])
        if data:
            row = data[0].get("d", [])

            def label(val):
                if val is None: return "N/A"
                if val >= 0.5:  return "💪 KUCHLI SOTIB OL"
                if val >= 0.1:  return "🟢 SOTIB OL"
                if val > -0.1:  return "⚪ NEYTRAL"
                if val > -0.5:  return "🔴 SOT"
                return "💥 KUCHLI SOT"

            if len(row) >= 9:
                result["rating"]      = label(row[0])
                result["ma_rating"]   = label(row[1])
                result["oscillator"]  = label(row[2])
                result["tv_rsi"]      = round(row[3], 1) if row[3] else "N/A"
                if row[4] is not None and row[5] is not None:
                    result["macd"] = ("🟢 Yuqoriga (bullish)" if row[4] > row[5]
                                      else "🔴 Pastga (bearish)")
                result["stoch"] = round(row[6], 1) if row[6] else "N/A"
                result["adx"]   = round(row[7], 1) if row[7] else "N/A"
    except Exception as e:
        logging.warning(f"TradingView xato ({ticker}): {e}")
    return result


def get_tradingview_link(ticker):
    """TradingView sahifa havolasi."""
    return f"https://www.tradingview.com/chart/?symbol={ticker}"


# ══════════════════════════════════════════════
#  📊 YAHOO FINANCE
# ══════════════════════════════════════════════

def get_yahoo_fundamentals(ticker):
    url    = f"https://finance.yahoo.com/quote/{ticker}/"
    result = {
        "pe_ratio": "N/A", "eps": "N/A", "dividend": "N/A",
        "week52_high": "N/A", "week52_low": "N/A",
        "avg_volume": "N/A", "beta": "N/A",
        "forward_pe": "N/A", "peg": "N/A",
    }
    try:
        r    = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "lxml")
        fields = {
            "PE_RATIO":           "pe_ratio",
            "EPS_RATIO":          "eps",
            "FIFTY_TWO_WK_HIGH":  "week52_high",
            "FIFTY_TWO_WK_LOW":   "week52_low",
            "AVERAGE_VOLUME_3MONTH": "avg_volume",
            "BETA_3Y":            "beta",
            "DIVIDEND_AND_YIELD": "dividend",
            "FORWARD_PE_RATIO":   "forward_pe",
        }
        for field, key in fields.items():
            el = soup.find("fin-streamer", {"data-field": field})
            if el:
                result[key] = el.get_text(strip=True)
    except Exception as e:
        logging.warning(f"Yahoo fundamentals xato ({ticker}): {e}")
    return result


def get_yahoo_news(ticker=None, count=5):
    url  = (f"https://finance.yahoo.com/quote/{ticker}/news/"
            if ticker else "https://finance.yahoo.com/news/")
    news = []
    try:
        r    = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "lxml")
        for art in soup.find_all("h3", limit=count * 2):
            a = art.find("a")
            if a and len(a.get_text(strip=True)) > 15:
                title = a.get_text(strip=True)
                href  = a.get("href", "")
                if href and not href.startswith("http"):
                    href = "https://finance.yahoo.com" + href
                news.append({"title": title, "url": href, "source": "Yahoo Finance"})
            if len(news) >= count:
                break
    except Exception as e:
        logging.warning(f"Yahoo news xato: {e}")
    return news


# ══════════════════════════════════════════════
#  📰 FINVIZ
# ══════════════════════════════════════════════

def get_finviz_data(ticker):
    url    = f"https://finviz.com/quote.ashx?t={ticker}"
    result = {
        "industry": "N/A", "country": "N/A",
        "short_float": "N/A", "inst_own": "N/A",
        "insider_own": "N/A", "target_price": "N/A",
        "recommendation": "N/A", "news": [],
    }
    try:
        r    = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "lxml")

        # Fundamental jadval
        cells  = soup.select("td.snapshot-td2")
        labels = soup.select("td.snapshot-td2-cp")
        data   = {l.get_text(strip=True): c.get_text(strip=True)
                  for l, c in zip(labels, cells)}
        result.update({
            "industry":       data.get("Industry", "N/A"),
            "country":        data.get("Country",  "N/A"),
            "short_float":    data.get("Short Float", "N/A"),
            "inst_own":       data.get("Inst Own",  "N/A"),
            "insider_own":    data.get("Insider Own", "N/A"),
            "target_price":   data.get("Target Price", "N/A"),
            "recommendation": data.get("Recom", "N/A"),
        })

        # Yangiliklar
        for row in soup.select("table.fullview-news-outer tr")[:5]:
            a = row.find("a", class_="tab-link-news")
            if a:
                result["news"].append({
                    "title":  a.get_text(strip=True),
                    "url":    a.get("href", ""),
                    "source": "Finviz",
                })
    except Exception as e:
        logging.warning(f"Finviz xato ({ticker}): {e}")
    return result


# ══════════════════════════════════════════════
#  🚀 IPO
# ══════════════════════════════════════════════

def get_upcoming_ipos():
    ipos = []
    try:
        r    = requests.get("https://api.nasdaq.com/api/ipo/calendar",
                            headers=HEADERS, timeout=15)
        data = r.json().get("data", {})

        upcoming = (data.get("upcoming", {})
                       .get("upcomingTable", {}).get("rows", []))
        for ipo in upcoming[:8]:
            ipos.append({
                "name":    ipo.get("companyName", "N/A"),
                "ticker":  ipo.get("proposedTickerSymbol", "N/A"),
                "exchange":ipo.get("proposedExchange", "N/A"),
                "price":   (ipo.get("priceRangeLow","") + "–" +
                            ipo.get("priceRangeHigh","")),
                "shares":  ipo.get("sharesOffered", "N/A"),
                "date":    ipo.get("expectedPriceDate", "N/A"),
                "status":  "🗓 Kutilmoqda",
            })

        recent = (data.get("recent", {})
                     .get("recentTable", {}).get("rows", []))
        for ipo in recent[:5]:
            pct = ipo.get("pctChange", "0") or "0"
            ipos.append({
                "name":    ipo.get("companyName", "N/A"),
                "ticker":  ipo.get("proposedTickerSymbol", "N/A"),
                "exchange":ipo.get("proposedExchange", "N/A"),
                "ipo_price": ipo.get("ipoPrice", "N/A"),
                "current": ipo.get("currentPrice", "N/A"),
                "return":  pct,
                "date":    ipo.get("pricedDate", "N/A"),
                "status":  "🆕 Yangi",
            })
    except Exception as e:
        logging.warning(f"IPO xato: {e}")
    return ipos


def build_ipo_message(ipos):
    if not ipos:
        return "📋 IPO ma'lumotlari topilmadi."
    now   = datetime.now().strftime("%d.%m.%Y %H:%M")
    lines = [
        "━━━━━━━━━━━━━━━━━━━━",
        f"🚀 *IPO YANGILIKLARI*",
        f"🕐 {now}",
        "━━━━━━━━━━━━━━━━━━━━\n",
    ]
    for ipo in ipos:
        if "Kutilmoqda" in ipo["status"]:
            lines.append(
                f"🗓 *{ipo['name']}* (`{ipo['ticker']}`)\n"
                f"  🏦 Birja:    {ipo['exchange']}\n"
                f"  💰 Narx:     ${ipo['price']}\n"
                f"  📊 Aksiyalar:{ipo['shares']}\n"
                f"  📅 Sana:     {ipo['date']}\n"
            )
        else:
            pct   = float(ipo.get("return","0").replace("%","").replace("+","") or 0)
            emoji = "🟢" if pct >= 0 else "🔴"
            lines.append(
                f"{emoji} *{ipo['name']}* (`{ipo['ticker']}`)\n"
                f"  💰 IPO narx: ${ipo.get('ipo_price','N/A')}\n"
                f"  📈 Hozir:    ${ipo.get('current','N/A')}\n"
                f"  📊 Daromad:  {ipo.get('return','N/A')}%\n"
                f"  📅 Sana:     {ipo['date']}\n"
            )
    lines.append("⚠️ _Bu ma'lumot moliyaviy maslahat emas._")
    return "\n".join(lines)


# ══════════════════════════════════════════════
#  📰 BOZOR YANGILIKLARI
# ══════════════════════════════════════════════

def get_market_news():
    news = get_yahoo_news(count=5)
    try:
        r    = requests.get("https://finviz.com/news.ashx",
                            headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.select("a.tab-link")[:5]:
            title = a.get_text(strip=True)
            if len(title) > 20:
                news.append({"title": title,
                             "url":   a.get("href",""),
                             "source":"Finviz"})
    except Exception as e:
        logging.warning(f"Finviz bozor yangiliklari: {e}")
    return news[:10]


def build_news_message(news_list):
    if not news_list:
        return "📰 Yangiliklar topilmadi."
    now   = datetime.now().strftime("%d.%m.%Y %H:%M")
    lines = [
        "━━━━━━━━━━━━━━━━━━━━",
        "📰 *MOLIYAVIY YANGILIKLAR*",
        f"🕐 {now}",
        "━━━━━━━━━━━━━━━━━━━━\n",
    ]
    for i, n in enumerate(news_list, 1):
        title = n["title"][:75]
        src   = n.get("source","")
        url   = n.get("url","")
        lines.append(f"{i}\\. [{title}]({url})\n   📌 _{src}_\n")
    lines.append("⚠️ _Bu ma'lumot moliyaviy maslahat emas._")
    return "\n".join(lines)


# ══════════════════════════════════════════════
#  🎯 SIGNAL KUCHI
# ══════════════════════════════════════════════

def signal_strength(change_pct, volume_usd, rsi, tv_rating):
    score = 0
    if abs(change_pct) > 5:   score += 2
    elif abs(change_pct) > 3: score += 1
    if volume_usd > 50_000_000:  score += 2
    elif volume_usd > 10_000_000: score += 1
    if rsi:
        if change_pct > 0 and 40 < rsi < 70: score += 1
        elif change_pct < 0 and 30 < rsi < 60: score += 1
    if "KUCHLI SOTIB OL" in str(tv_rating): score += 2
    elif "SOTIB OL" in str(tv_rating):      score += 1
    if score >= 5:   return "💪💪 JUDA KUCHLI", "🟢🟢🟢🟢🟢"
    elif score >= 4: return "💪 KUCHLI",        "🟢🟢🟢🟢⚪"
    elif score >= 3: return "👍 O'RTA-YUQORI",  "🟢🟢🟢⚪⚪"
    elif score >= 2: return "👌 O'RTA",         "🟢🟢⚪⚪⚪"
    else:            return "🤔 ZAIF",          "🟢⚪⚪⚪⚪"


# ══════════════════════════════════════════════
#  📤 TELEGRAM YUBORISH
# ══════════════════════════════════════════════

def send_telegram(text, chat_id=None, reply_markup=None):
    cid     = chat_id or TELEGRAM_CHAT_ID
    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    chunks  = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for i, chunk in enumerate(chunks):
        payload = {
            "chat_id":                  cid,
            "text":                     chunk,
            "parse_mode":               "MarkdownV2",
            "disable_web_page_preview": True,
        }
        if reply_markup and i == len(chunks) - 1:
            payload["reply_markup"] = reply_markup
        try:
            r = requests.post(url, json=payload, timeout=15)
            if r.status_code != 200:
                # MarkdownV2 ishlamasa oddiy matn yuboramiz
                payload["parse_mode"] = "HTML"
                r2 = requests.post(url, json=payload, timeout=15)
                if r2.status_code != 200:
                    payload.pop("parse_mode")
                    requests.post(url, json=payload, timeout=15)
        except Exception as e:
            logging.error(f"Telegram yuborish xatosi: {e}")
        time.sleep(0.4)


def send_photo(image_buf, caption, chat_id=None, reply_markup=None):
    """Rasm yuborish."""
    cid = chat_id or TELEGRAM_CHAT_ID
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    try:
        files   = {"photo": ("chart.png", image_buf, "image/png")}
        payload = {
            "chat_id":    cid,
            "caption":    caption[:1024],
            "parse_mode": "HTML",
        }
        if reply_markup:
            import json
            payload["reply_markup"] = json.dumps(reply_markup)
        requests.post(url, data=payload, files=files, timeout=30)
    except Exception as e:
        logging.error(f"Rasm yuborish xatosi: {e}")


def main_keyboard():
    """Asosiy inline klaviatura."""
    return {
        "inline_keyboard": [
            [
                {"text": "📊 Signal",   "callback_data": "signal"},
                {"text": "🚀 IPO",      "callback_data": "ipo"},
                {"text": "📰 Yangilik", "callback_data": "news"},
            ],
            [
                {"text": "✅ Halol aksiyalar", "callback_data": "halal"},
                {"text": "📈 TOP o'sish",      "callback_data": "top"},
            ],
        ]
    }


def ticker_keyboard(ticker):
    """Aksiya uchun tugmalar."""
    tv_link = get_tradingview_link(ticker)
    return {
        "inline_keyboard": [
            [
                {"text": "📺 TradingView",  "url": tv_link},
                {"text": "📊 Yahoo Chart",
                 "url": f"https://finance.yahoo.com/chart/{ticker}"},
            ],
            [
                {"text": "🔄 Yangilash", "callback_data": f"refresh_{ticker}"},
                {"text": "📰 Yangiliklar","callback_data": f"news_{ticker}"},
            ],
        ]
    }


def escape_md(text):
    """MarkdownV2 uchun maxsus belgilarni ekranlashtirish."""
    chars = r"\_*[]()~`>#+-=|{}.!"
    return re.sub(f"([{re.escape(chars)}])", r"\\\1", str(text))


# ══════════════════════════════════════════════
#  📋 CHUQUR TAHLIL XABARI
# ══════════════════════════════════════════════

def build_deep_analysis(ticker, price, details, tech, yahoo, finviz,
                        tv, support, resistance, halal_ok, halal_issues, balance):
    c          = price.get("price", 0)
    o          = price.get("open",  0)
    h          = price.get("high",  0)
    l          = price.get("low",   0)
    v          = price.get("volume",0)
    vw         = price.get("vwap",  c)
    change_pct = price.get("change_pct", 0)
    volume_usd = v * (c or 1)
    cap_b      = details["market_cap"] / 1e9

    rsi       = tech.get("rsi")
    sma50     = tech.get("sma50")
    sma200    = tech.get("sma200")
    tv_rating = tv.get("rating","N/A")

    direction = "📈 O'SISH / Rising" if change_pct >= 0 else "📉 TUSHISH / Falling"
    sig_dir   = "🟢 SOTIB OL / BUY"  if change_pct >= 0 else "🔴 SOT / SELL"
    strength, stars = signal_strength(change_pct, volume_usd, rsi, tv_rating)

    halal_line = ("✅ HALOL / Halal ✅" if halal_ok
                  else "❌ HALOL EMAS / Not Halal ❌\n" +
                       "\n".join(f"   ⚠️ {i}" for i in halal_issues))

    now = datetime.now().strftime("%d.%m.%Y %H:%M")

    # Support / Resistance
    sup_str = " | ".join([f"${s}" for s in support[:3]])    or "N/A"
    res_str = " | ".join([f"${r}" for r in resistance[:3]]) or "N/A"

    # Kompaniya tavsifi
    desc = details.get("description","")
    desc_uz = desc[:300] + "..." if len(desc) > 300 else desc

    msg = (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 *{ticker}* — {details['name']}\n"
        f"🏭 {details['sector']} | {finviz.get('industry','N/A')}\n"
        f"🌍 {finviz.get('country','N/A')}\n"
        f"🕐 {now} | ⚡ REAL VAQT\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"

        f"📝 *KOMPANIYA HAQIDA / About*\n"
        f"_{desc_uz}_\n\n"

        f"💰 *NARX / Price*\n"
        f"  💵 Joriy / Current: *${c:.2f}*\n"
        f"  📊 O'zgarish / Change: *{change_pct:+.2f}%* {direction}\n"
        f"  🔓 Ochilish / Open:  ${o:.2f}\n"
        f"  🔺 Yuqori / High:    ${h:.2f}\n"
        f"  🔻 Past / Low:       ${l:.2f}\n"
        f"  📍 VWAP:             ${vw:.2f}\n"
        f"  📅 52H max: ${yahoo.get('week52_high','N/A')}"
        f" | min: ${yahoo.get('week52_low','N/A')}\n\n"

        f"📦 *HAJM / Volume*\n"
        f"  💫 Bugungi: *${volume_usd/1e6:.1f} mln*\n"
        f"  📊 O'rtacha / Avg: {yahoo.get('avg_volume','N/A')}\n\n"

        f"🏦 *FUNDAMENTAL*\n"
        f"  🏢 Bozor kap / Market Cap: *${cap_b:.1f} mlrd*\n"
        f"  💳 Qarz nisbati / Debt ratio: {balance.get('debt_ratio',0):.1f}%\n"
        f"  📈 P/E: {yahoo.get('pe_ratio','N/A')}"
        f" | Forward P/E: {yahoo.get('forward_pe','N/A')}\n"
        f"  💵 EPS: {yahoo.get('eps','N/A')}"
        f" | Beta: {yahoo.get('beta','N/A')}\n"
        f"  💰 Dividend: {yahoo.get('dividend','N/A')}\n"
        f"  👥 Xodimlar / Employees: {details['employees']:,}\n"
        f"  🏛 Inst. egalik: {finviz.get('inst_own','N/A')}"
        f" | Insider: {finviz.get('insider_own','N/A')}\n\n"

        f"📐 *TEXNIK TAHLIL / Technical*\n"
        f"  RSI(14):  {rsi or 'N/A'}"
        f" {'⚠️ Overbought' if rsi and rsi>70 else '✅ Oversold' if rsi and rsi<30 else '✅ Normal'}\n"
        f"  SMA50:    ${sma50 or 'N/A'}"
        f" {'✅ Yuqorida' if sma50 and c>sma50 else '⚠️ Pastda'}\n"
        f"  SMA200:   ${sma200 or 'N/A'}"
        f" {'✅ Bull trend' if sma200 and c>sma200 else '⚠️ Bear trend'}\n\n"

        f"📺 *TRADINGVIEW SIGNAL*\n"
        f"  Umumiy / Overall: {tv.get('rating','N/A')}\n"
        f"  MA signal:        {tv.get('ma_rating','N/A')}\n"
        f"  Oscillator:       {tv.get('oscillator','N/A')}\n"
        f"  MACD:             {tv.get('macd','N/A')}\n"
        f"  Stochastic:       {tv.get('stoch','N/A')}\n"
        f"  ADX:              {tv.get('adx','N/A')}\n\n"

        f"📏 *SUPPORT & RESISTANCE*\n"
        f"  🟢 Support (qo'llab-quvvatlash):\n"
        f"     {sup_str}\n"
        f"  🔴 Resistance (qarshilik):\n"
        f"     {res_str}\n\n"

        f"🎯 *YAKUNIY SIGNAL / Final Signal*\n"
        f"  {sig_dir}\n"
        f"  Kuch / Strength: {strength}\n"
        f"  {stars}\n"
        f"  🎯 Narx maqsadi / Target: ${finviz.get('target_price','N/A')}\n"
        f"  👨‍💼 Analitik / Analyst: {finviz.get('recommendation','N/A')}\n\n"

        f"☪️ *HALOLLIK / Halal Status*\n"
        f"  {halal_line}\n\n"

        f"⚠️ _Bu tahlil axborot uchun, moliyaviy maslahat emas._\n"
        f"_This is for information only, not financial advice._"
    )
    return msg


# ══════════════════════════════════════════════
#  🔍 QO'LDA TAHLIL
# ══════════════════════════════════════════════

def handle_ticker_query(ticker, chat_id):
    ticker = ticker.upper().strip()
    send_telegram(
        f"🔍 *{ticker}* tahlil boshlanmoqda\\.\\.\\.\n"
        f"⏳ Barcha manbalar yuklanmoqda:\n"
        f"📡 Polygon • 📺 TradingView • 📊 Yahoo • 📰 Finviz",
        chat_id
    )

    # 1. Real vaqt narxi
    price = get_realtime_price(ticker)
    if not price or price.get("price", 0) == 0:
        send_telegram(f"❌ *{ticker}* topilmadi\\. Ticker to'g'riligini tekshiring\\.", chat_id)
        return
    time.sleep(13)

    # 2. Kompaniya ma'lumotlari
    details = get_stock_details(ticker)
    time.sleep(13)

    # 3. Texnik ko'rsatkichlar
    tech = get_technicals(ticker)  # ~39 sek
    time.sleep(2)

    # 4. Sham tarixi + S/R
    candles = get_candle_history(ticker, days=60)
    support, resistance = calculate_support_resistance(candles)
    time.sleep(13)

    # 5. TradingView
    tv = get_tradingview_rating(ticker)
    time.sleep(2)

    # 6. Yahoo Finance
    yahoo = get_yahoo_fundamentals(ticker)
    time.sleep(3)

    # 7. Finviz
    finviz = get_finviz_data(ticker)
    time.sleep(2)

    # 8. Balans (halollik uchun)
    balance = get_balance_sheet(ticker)
    time.sleep(13)

    # 9. Halollik tekshiruvi
    halal_ok, halal_issues = check_halal(ticker, details, balance)

    # 10. Chart rasmini yaratish
    chart_buf = draw_chart(ticker, candles, support, resistance, price)

    # 11. Xabar yuborish
    caption = (
        f"<b>{ticker}</b> — {details['name']}\n"
        f"💵 ${price.get('price',0):.2f}  "
        f"({price.get('change_pct',0):+.2f}%)\n"
        f"🟢 Support: {' | '.join([f'${s}' for s in support[:2]]) or 'N/A'}\n"
        f"🔴 Resistance: {' | '.join([f'${r}' for r in resistance[:2]]) or 'N/A'}\n"
        f"📺 TradingView: {tv.get('rating','N/A')}"
    )

    if chart_buf:
        send_photo(chart_buf, caption, chat_id, ticker_keyboard(ticker))
    else:
        # Rasm bo'lmasa havola yuboramiz
        tv_link = get_tradingview_link(ticker)
        send_telegram(
            f"📺 [TradingView Chart — {ticker}]({tv_link})",
            chat_id
        )

    # 12. To'liq tahlil matni
    text = build_deep_analysis(
        ticker, price, details, tech, yahoo, finviz,
        tv, support, resistance, halal_ok, halal_issues, balance
    )
    send_telegram(text, chat_id, ticker_keyboard(ticker))

    # 13. Aksiyaga oid yangiliklar
    all_news = finviz.get("news", []) + get_yahoo_news(ticker, count=3)
    if all_news:
        news_lines = [
            "━━━━━━━━━━━━━━━━━━━━",
            f"📰 *{ticker} YANGILIKLARI / News*\n",
        ]
        for i, n in enumerate(all_news[:6], 1):
            title = n["title"][:70]
            src   = n.get("source","")
            url   = n.get("url","")
            news_lines.append(f"{i}\\. [{escape_md(title)}]({url})\n   📌 _{src}_\n")
        send_telegram("\n".join(news_lines), chat_id)


# ══════════════════════════════════════════════
#  🤖 AVTOMATIK SKREENER
# ══════════════════════════════════════════════

def run_screener(halal_only=False):
    logging.info(f"🔍 Avtomatik skreener {'(halol)' if halal_only else ''}...")
    signals = []

    for ticker in WATCHLIST:
        price = get_realtime_price(ticker)
        if not price or price.get("price", 0) == 0:
            time.sleep(13)
            continue

        change_pct = price.get("change_pct", 0)
        volume     = price.get("volume", 0)
        c          = price.get("price", 0)
        volume_usd = volume * c

        if volume_usd < MIN_VOLUME_USD:
            time.sleep(13)
            continue
        if not (MIN_CHANGE_PCT <= abs(change_pct) <= MAX_CHANGE_PCT):
            time.sleep(13)
            continue

        details = get_stock_details(ticker)
        time.sleep(13)

        if details["market_cap"] < MIN_MARKET_CAP:
            continue

        tv = get_tradingview_rating(ticker)
        time.sleep(2)

        if halal_only:
            balance = get_balance_sheet(ticker)
            time.sleep(13)
            halal_ok, _ = check_halal(ticker, details, balance)
            if not halal_ok:
                continue

        reason_parts = []
        if abs(change_pct) > 5:           reason_parts.append("Kuchli harakat")
        if volume_usd > 10_000_000:       reason_parts.append("Yuqori hajm")
        if details["market_cap"] > 10e9:  reason_parts.append("Katta kapital")
        if "SOTIB OL" in tv.get("rating",""):
            reason_parts.append("TV: BUY signal")

        signals.append({
            "ticker":     ticker,
            "name":       details["name"],
            "signal":     "BUY" if change_pct > 0 else "SELL",
            "close":      c,
            "change":     change_pct,
            "volume_usd": volume_usd,
            "market_cap": details["market_cap"],
            "tv_rating":  tv.get("rating","N/A"),
            "reason":     " + ".join(reason_parts) or "Mezon bajarildi",
        })
        logging.info(f"  {'BUY' if change_pct>0 else 'SELL'} {ticker} {change_pct:+.1f}%")
        time.sleep(13)

    if signals:
        signals.sort(key=lambda x: abs(x["change"]), reverse=True)
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        tag = "HALOL " if halal_only else ""
        lines = [
            "━━━━━━━━━━━━━━━━━━━━",
            f"🤖 *AVTOMATIK {tag}SIGNAL*",
            f"🕐 {now}",
            f"Topilgan: *{len(signals)} ta aksiya*",
            "━━━━━━━━━━━━━━━━━━━━\n",
        ]
        for s in signals[:10]:
            emoji  = "🟢" if s["signal"] == "BUY" else "🔴"
            action = "SOTIB OL / BUY" if s["signal"] == "BUY" else "SOT / SELL"
            cap_b  = s["market_cap"] / 1e9
            lines.append(
                f"{emoji} *{s['ticker']}* — {s['name']}\n"
                f"  💵 ${s['close']:.2f}  ({s['change']:+.1f}%)\n"
                f"  📦 ${s['volume_usd']/1e6:.1f} mln\n"
                f"  🏦 ${cap_b:.1f} mlrd\n"
                f"  📺 TV: {s['tv_rating']}\n"
                f"  🎯 *{action}*\n"
                f"  ⚡ {s['reason']}\n"
            )
        lines.append("⚠️ _Moliyaviy maslahat emas / Not financial advice_")
        send_telegram("\n".join(lines), reply_markup=main_keyboard())
    else:
        send_telegram("📊 Hozir shart bajargan aksiya topilmadi\\.")


# ══════════════════════════════════════════════
#  👂 TELEGRAM POLLING
# ══════════════════════════════════════════════

def poll_messages():
    offset = None
    logging.info("👂 Xabarlar tinglanmoqda...")

    while True:
        try:
            url    = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            params = {"timeout": 30, "offset": offset, "allowed_updates": ["message","callback_query"]}
            r      = requests.get(url, params=params, timeout=35)
            updates = r.json().get("result", [])

            for upd in updates:
                offset = upd["update_id"] + 1

                # ── Callback (tugma bosilganda) ──
                if "callback_query" in upd:
                    cb      = upd["callback_query"]
                    data    = cb.get("data","")
                    chat_id = str(cb["message"]["chat"]["id"])

                    # Callback ga javob (loading animatsiyasini to'xtatish)
                    requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
                        json={"callback_query_id": cb["id"], "text": "⏳ Yuklanmoqda..."},
                        timeout=5
                    )

                    if data == "signal":
                        threading.Thread(target=run_screener, daemon=True).start()
                    elif data == "ipo":
                        threading.Thread(
                            target=lambda c=chat_id: send_telegram(
                                build_ipo_message(get_upcoming_ipos()), c
                            ), daemon=True
                        ).start()
                    elif data == "news":
                        threading.Thread(
                            target=lambda c=chat_id: send_telegram(
                                build_news_message(get_market_news()), c
                            ), daemon=True
                        ).start()
                    elif data == "halal":
                        threading.Thread(
                            target=lambda c=chat_id: run_screener(halal_only=True),
                            daemon=True
                        ).start()
                    elif data == "top":
                        threading.Thread(
                            target=lambda c=chat_id: run_screener(halal_only=False),
                            daemon=True
                        ).start()
                    elif data.startswith("refresh_"):
                        tkr = data.replace("refresh_","")
                        threading.Thread(
                            target=handle_ticker_query, args=(tkr, chat_id), daemon=True
                        ).start()
                    elif data.startswith("news_"):
                        tkr = data.replace("news_","")
                        threading.Thread(
                            target=lambda t=tkr, c=chat_id: send_telegram(
                                build_news_message(get_yahoo_news(t, count=5)), c
                            ), daemon=True
                        ).start()
                    continue

                # ── Oddiy xabar ──
                msg     = upd.get("message", {})
                text    = msg.get("text","").strip()
                chat_id = str(msg.get("chat",{}).get("id",""))

                if not text or not chat_id:
                    continue

                logging.info(f"📩 '{text}' — {chat_id}")

                if text in ("/start", "/help"):
                    send_telegram(
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "👋 *Stock Signal Bot v4\\.0*\n"
                        "📡 Polygon \\| TV \\| Yahoo \\| Finviz\n"
                        "━━━━━━━━━━━━━━━━━━━━\n\n"
                        "📌 *Buyruqlar / Commands:*\n"
                        "  `/signal` — Hozir signallarni tekshir\n"
                        "  `/ipo`    — Yaqinlashayotgan IPOlar\n"
                        "  `/news`   — Moliyaviy yangiliklar\n"
                        "  `/halal`  — Faqat halol aksiyalar\n\n"
                        "📊 *Aksiya tahlili:*\n"
                        "  Ticker yozing: `AAPL`, `NVDA`, `TSLA`\n\n"
                        "🔄 Bot har 4 soatda avtomatik signal yuboradi\n"
                        "☪️ Halol aksiya filtri mavjud\n\n"
                        "⬇️ Quyidagi tugmalardan foydalaning:",
                        chat_id,
                        reply_markup=main_keyboard()
                    )

                elif text == "/signal":
                    send_telegram("🔍 Signal tekshirilmoqda\\.\\.\\.", chat_id)
                    threading.Thread(target=run_screener, daemon=True).start()

                elif text == "/ipo":
                    send_telegram("🚀 IPO ma'lumotlari yuklanmoqda\\.\\.\\.", chat_id)
                    threading.Thread(
                        target=lambda c=chat_id: send_telegram(
                            build_ipo_message(get_upcoming_ipos()), c
                        ), daemon=True
                    ).start()

                elif text == "/news":
                    send_telegram("📰 Yangiliklar yuklanmoqda\\.\\.\\.", chat_id)
                    threading.Thread(
                        target=lambda c=chat_id: send_telegram(
                            build_news_message(get_market_news()), c
                        ), daemon=True
                    ).start()

                elif text == "/halal":
                    send_telegram("☪️ Halol aksiyalar tekshirilmoqda\\.\\.\\.", chat_id)
                    threading.Thread(
                        target=lambda: run_screener(halal_only=True), daemon=True
                    ).start()

                elif text.startswith("/"):
                    send_telegram("❓ Noma'lum buyruq\\. /help yozing\\.", chat_id)

                elif re.match(r'^[A-Za-z]{1,6}$', text):
                    threading.Thread(
                        target=handle_ticker_query,
                        args=(text, chat_id),
                        daemon=True
                    ).start()

                else:
                    send_telegram(
                        "💡 Ticker yozing: `AAPL`, `NVDA`\n"
                        "Yoki quyidagi tugmalardan foydalaning:",
                        chat_id,
                        reply_markup=main_keyboard()
                    )

        except Exception as e:
            logging.error(f"Polling xato: {e}")
            time.sleep(5)


# ══════════════════════════════════════════════
#  🚀 MAIN
# ══════════════════════════════════════════════

def main():
    logging.info("🤖 Stock Signal Bot v4.0 ishga tushdi")

    schedule.every(4).hours.do(run_screener)
    schedule.every().day.at("09:30").do(run_screener)
    schedule.every().day.at("16:05").do(run_screener)
    schedule.every().day.at("08:00").do(
        lambda: send_telegram(build_news_message(get_market_news()))
    )

    threading.Thread(
        target=lambda: [
            (schedule.run_pending(), time.sleep(60))
            for _ in iter(int, 1)
        ],
        daemon=True
    ).start()

    send_telegram(
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 *Stock Signal Bot v4\\.0 ishga tushdi\\!*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "✅ Real vaqt narxlar\n"
        "✅ Chart \\+ Support/Resistance\n"
        "✅ TradingView signal\n"
        "✅ Yahoo Finance \\+ Finviz\n"
        "✅ IPO kalendarі\n"
        "✅ Halol aksiya filtri ☪️\n"
        "✅ O'zbek \\+ ingliz tili\n\n"
        "💡 Sinab ko'ring: `AAPL` yozing\\!",
        reply_markup=main_keyboard()
    )

    poll_messages()


if __name__ == "__main__":
    main()
