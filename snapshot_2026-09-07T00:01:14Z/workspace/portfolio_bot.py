import os
import re
import sys
import time
import json
import urllib.parse
import requests

# =====================================================================
# CONFIGURATION  (move secrets to env vars: see note at bottom)
# =====================================================================

# 1. Gemini (used ONLY as a last-resort fallback)
GEMINI_API_KEY = "AIzaSyDw94ydHB_THTgyBg27rHxrRinm6_W04eo"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent"

# 2. Telegram
TELEGRAM_BOT_TOKEN = "8426200610:AAHAzHUeWfmt6t4omAENGjSuKQHIS9HnhNI"
TELEGRAM_CHAT_ID = "300115334"
TELEGRAM_API_URL_MESSAGE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
TELEGRAM_API_URL_DOCUMENT = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"

# 3. Output file (saved next to this script — works on Windows & Linux)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE_PATH = os.path.join(SCRIPT_DIR, "result.txt")

# 4. Your portfolio (edit these to match your holdings)
USER_GOLD_G = 27.5    # grams of physical gold
USER_SILVER_G = 62.20   # grams of physical silver

# Local (Iranian) silver price — manual, since Digikala can't be auto-scraped.
# Price of a 1 oz (31.1035g) Parsis silver bar on Digikala = 25,000,000 Toman.
# Edit USER_SILVER_LOCAL_BAR_TOMAN when the shop price changes.
# Set USER_SILVER_LOCAL_BAR_TOMAN = 0.0 to ignore local price and use XAG/USD only.
USER_SILVER_LOCAL_BAR_TOMAN = 25000000.0   # Toman for one 31.1035g bar
USER_SILVER_LOCAL_BAR_G = 31.1035          # grams per bar (1 troy oz)

# Constants & fallbacks
OUNCE_TO_GRAM = 31.1035
RETRY_ATTEMPTS = 3
FALLBACK_GOLD_USD_PER_OZ = 4000.00
FALLBACK_SILVER_USD_PER_OZ = 42.00
FALLBACK_USD_TO_TOMAN = 100000.0


# =====================================================================
# PRICE SOURCES
# =====================================================================

def fetch_toman_rate():
    """Free-market USD/Toman (Rials/10). Try several public sources."""
    # 1) AlanChand — USD (Remittance / havaleh) open-market rate
    try:
        html = requests.get("https://alanchand.com/en/currencies-price/usd-hav",
                            headers={"User-Agent": "Mozilla/5.0"}, timeout=10).text
        m = re.search(r'data-price="(\d+)"', html)
        if m:
            rials = float(m.group(1))
            print(f"LIVE (AlanChand): {rials/10:,.0f} Toman / USD")
            return rials / 10.0
    except Exception as e:
        print(f"AlanChand failed: {e}", file=sys.stderr)

    # 2) Wallex — USDT/TMN (free-market proxy)
    try:
        r = requests.get("https://api.wallex.ir/v1/markets",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        r.raise_for_status()
        latest = float(r.json()["result"]["symbols"]["USDTTMN"]["stats"]["lastPrice"])
        print(f"LIVE (Wallex): {latest:,.0f} Toman / USD")
        return latest
    except Exception as e:
        print(f"Wallex failed: {e}", file=sys.stderr)

    # 3) Nobitex — USDT/IRT (Toman)
    try:
        r = requests.get("https://api.nobitex.ir/mkt/actives", timeout=10)
        r.raise_for_status()
        latest = float(r.json()["stats"]["usdt-irt"]["latest"])
        print(f"LIVE (Nobitex): {latest:,.0f} Toman / USD")
        return latest
    except Exception as e:
        print(f"Nobitex failed: {e}", file=sys.stderr)

    raise RuntimeError("All Toman sources failed")


def fetch_prices_direct():
    """Primary source: real public APIs (no key needed)."""
    # Gold spot price in USD per troy ounce
    r = requests.get("https://api.gold-api.com/price/XAU", timeout=10)
    r.raise_for_status()
    gold_per_oz = float(r.json()["price"])

    # Silver spot price in USD per troy ounce
    r = requests.get("https://api.gold-api.com/price/XAG", timeout=10)
    r.raise_for_status()
    silver_per_oz = float(r.json()["price"])

    # Free-market USD/Toman (rate already in Toman)
    toman_rate = fetch_toman_rate()

    return gold_per_oz, silver_per_oz, toman_rate


def fetch_prices_gemini():
    """Fallback source: Gemini Google-Search grounding."""
    query = ("Find the current market price for Gold (XAU/USD), Silver (XAG/USD), and the "
             "live free-market rate for USD to Iranian Toman. Ignore official rates. Return ONLY "
             "the format: 'Gold Price: [NUMBER]|Silver Price: [NUMBER]|Toman Rate: [NUMBER]' "
             "with no symbols or extra text.")
    system = ("You are a financial data retrieval system. Return only the requested string "
              "format using Google Search grounding.")

    payload = {
        "contents": [{"parts": [{"text": query}]}],
        "tools": [{"google_search": {}}],
        "systemInstruction": {"parts": [{"text": system}]},
    }
    headers = {"Content-Type": "application/json"}

    for attempt in range(RETRY_ATTEMPTS):
        try:
            print(f"Fetching via Gemini (attempt {attempt + 1})...")
            r = requests.post(f"{GEMINI_API_URL}?key={GEMINI_API_KEY}",
                              headers=headers, json=payload, timeout=15)
            r.raise_for_status()
            text = r.json().get("candidates", [{}])[0].get(
                "content", {}).get("parts", [{}])[0].get("text", "")
            m = re.search(r"Gold Price:\s*([\d,.]+).*?Silver Price:\s*([\d,.]+).*?Toman Rate:\s*([\d,.]+)",
                          text, re.IGNORECASE | re.DOTALL)
            if not m:
                raise ValueError(f"Unparseable Gemini reply: {text}")
            return (float(m.group(1).replace(",", "")),
                    float(m.group(2).replace(",", "")),
                    float(m.group(3).replace(",", "")))
        except Exception as e:
            print(f"Gemini attempt failed: {e}", file=sys.stderr)
            time.sleep(2 ** attempt)

    raise RuntimeError("Gemini fallback exhausted")


def get_market_data():
    """Try direct APIs first, then Gemini; fall back to constants."""
    try:
        gold, silver, toman = fetch_prices_direct()
        print(f"LIVE (direct APIs): Gold=${gold:,.2f}/oz  Silver=${silver:,.2f}/oz  Toman={toman:,.0f}/USD")
        return gold, silver, toman
    except Exception as e:
        print(f"Direct price fetch failed: {e}", file=sys.stderr)

    try:
        gold, silver, toman = fetch_prices_gemini()
        print(f"LIVE (Gemini fallback): Gold=${gold:,.2f}/oz  Silver=${silver:,.2f}/oz  Toman={toman:,.0f}/USD")
        return gold, silver, toman
    except Exception as e:
        print(f"Gemini fallback failed: {e}", file=sys.stderr)

    print("CRITICAL: all sources failed. Using fallback constants.", file=sys.stderr)
    return FALLBACK_GOLD_USD_PER_OZ, FALLBACK_SILVER_USD_PER_OZ, FALLBACK_USD_TO_TOMAN


# =====================================================================
# TELEGRAM DELIVERY
# =====================================================================

def send_message(message):
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    r = requests.post(TELEGRAM_API_URL_MESSAGE, data=payload, timeout=10)
    r.raise_for_status()


def manual_telegram_url(message):
    enc = urllib.parse.quote_plus(message)
    return f"{TELEGRAM_API_URL_MESSAGE}?chat_id={TELEGRAM_CHAT_ID}&text={enc}&parse_mode=Markdown"


def save_and_send_file_to_telegram(message):
    try:
        send_message(message)
        print("✔ Message sent to Telegram.")
        return
    except Exception as e:
        print(f"❌ Message send failed: {e}")

    try:
        with open(OUTPUT_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(message.replace("*", ""))
        print(f"✔ Saved to {OUTPUT_FILE_PATH}")
    except Exception as e:
        print(f"❌ Save failed: {e}")
        return

    try:
        with open(OUTPUT_FILE_PATH, "rb") as f:
            files = {"document": (os.path.basename(OUTPUT_FILE_PATH), f, "text/plain")}
            requests.post(TELEGRAM_API_URL_DOCUMENT,
                          data={"chat_id": TELEGRAM_CHAT_ID, "caption": "📄 Backup"},
                          files=files, timeout=15).raise_for_status()
        print("✔ File sent to Telegram.")
    except Exception as e:
        print(f"❌ File send failed: {e}")
        print("\nMANUAL SEND URL:\n" + manual_telegram_url(message))


# =====================================================================
# MAIN
# =====================================================================

def run_portfolio_bot():
    print("start...")
    gold_per_oz, silver_per_oz, toman_rate = get_market_data()

    gold_per_g = gold_per_oz / OUNCE_TO_GRAM
    silver_per_g_xau = silver_per_oz / OUNCE_TO_GRAM   # intl XAG/USD-derived
    gold_toman_per_oz = gold_per_oz * toman_rate
    silver_toman_per_oz_xau = silver_per_oz * toman_rate

    # --- Silver value: local price first, XAG/USD second ---
    if USER_SILVER_LOCAL_BAR_TOMAN > 0 and USER_SILVER_LOCAL_BAR_G > 0:
        silver_per_g = USER_SILVER_LOCAL_BAR_TOMAN / USER_SILVER_LOCAL_BAR_G   # Toman/g
        silver_src = "Digikala (local)"
        silver_value_toman = USER_SILVER_G * silver_per_g
        silver_value_usd = silver_value_toman / toman_rate
        silver_per_g_usd = silver_value_usd / USER_SILVER_G
    else:
        silver_per_g = silver_per_g_xau
        silver_src = "XAG/USD"
        silver_value_usd = USER_SILVER_G * silver_per_g_xau
        silver_value_toman = silver_value_usd * toman_rate
        silver_per_g_usd = silver_per_g_xau

    gold_value_usd = USER_GOLD_G * gold_per_g
    total_usd = gold_value_usd + silver_value_usd
    total_toman = total_usd * toman_rate

    msg = (
        "📈 *Live Market & Portfolio Snapshot*\n\n"
        "--- MARKET RATES ---\n"
        f"💰 *Gold (XAU/USD):* ${gold_per_oz:,.2f} / oz\n"
        f"    (≈ ${gold_per_g:,.2f} / gram)\n"
        f"🥈 *Silver (XAG/USD):* ${silver_per_oz:,.2f} / oz\n"
        f"    (≈ ${silver_per_g_xau:,.2f} / gram)\n"
        f"💵 *USD/Toman:* {toman_rate:,.0f} Toman / USD\n"
        f"✨ *Gold in Toman:* {gold_toman_per_oz:,.0f} Toman / oz\n"
        f"✨ *Silver (XAG) in Toman:* {silver_toman_per_oz_xau:,.0f} Toman / oz\n\n"
        "--- YOUR PORTFOLIO ---\n"
        f"🥇 *Gold ({USER_GOLD_G:g}g):* ${gold_value_usd:,.2f} ({gold_value_usd * toman_rate:,.0f} Toman)\n"
        f"🥈 *Silver ({USER_SILVER_G:g}g) [{silver_src}]:* ${silver_value_usd:,.2f} "
        f"({silver_value_toman:,.0f} Toman)\n"
        f"    (≈ {silver_per_g_usd:,.2f} USD / {silver_per_g:,.0f} Toman per gram)\n"
        f"----------------------\n"
        f"📊 *Total:* *{total_usd:,.2f} USD*  /  *({total_toman:,.0f} Toman)*"
    )

    print("\n" + "=" * 50)
    print(msg)
    print("=" * 50)

    save_and_send_file_to_telegram(msg)
    print("end.")


if __name__ == "__main__":
    run_portfolio_bot()

# ---------------------------------------------------------------------
# SECURITY NOTE:
# Don't leave real secrets in the file. Instead, before running:
#   export GEMINI_API_KEY="..."      (Linux/Mac)
#   export TELEGRAM_BOT_TOKEN="..."
# and replace the literals above with os.environ["GEMINI_API_KEY"], etc.
# Then rotate the keys that were pasted into this chat.
# ---------------------------------------------------------------------
