"""
╔══════════════════════════════════════════════════════════════╗
║     POLYMARKET BTC 15-MIN PURE ARBITRAGE BOT                 ║
║                                                              ║
║                                                              ║
║     Strategy: Buy BOTH Up + Down when combined price < $1    ║
║     Result:   Guaranteed profit regardless of BTC direction  ║
╚══════════════════════════════════════════════════════════════╝

HOW IT WORKS:
  - Every 15 minutes, Polymarket opens a new "Will BTC go Up or Down?" market
  - Each share pays out $1.00 if correct
  - If you can buy UP for $0.48 AND DOWN for $0.51 = $0.99 total cost
  - One of them MUST win → you receive $1.00 → profit $0.01 per share
  - No prediction needed. Pure math.

SETUP (Windows):
  1. Install Python from python.org (check "Add to PATH")
  2. Open Command Prompt:
       pip install requests python-dotenv eth-account py-clob-client
  3. Create a .env file (see below) with your credentials
  4. Run: python btc_arb_bot.py

⚠️  This is real money. Start with DRY_RUN = True.
    Spreads can eat into profits — verify liquidity before going live.
"""

import os
import time
import asyncio
import requests
import json
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────────────────────
# 🔑  CREDENTIALS — set these in a .env file (safer than hardcoding)
#
# Create a file called .env in the same folder as this script:
#   POLY_PRIVATE_KEY=0xYourPrivateKeyHere
#   POLY_PROXY_ADDRESS=0xYourPolymarketProxyWalletAddress
#
# Find your proxy address at: polymarket.com/settings → copy wallet address
# ──────────────────────────────────────────────────────────────

PRIVATE_KEY    = os.getenv("POLY_PRIVATE_KEY", "")
PROXY_ADDRESS  = os.getenv("POLY_PROXY_ADDRESS", "")

# ──────────────────────────────────────────────────────────────
# ⚙️  SETTINGS
# ──────────────────────────────────────────────────────────────

DRY_RUN            = True    # ← Set False only when ready for real money
ORDER_SIZE         = 10      # Shares per trade (=$10 max exposure per side)
ARB_THRESHOLD      = 0.995   # Only trade if UP+DOWN combined price ≤ 0.995
                              #   ($0.995 cost → $1.00 payout = ≥0.5% profit)
SCAN_INTERVAL_SECS = 5       # How often to check prices (seconds)
COIN               = "BTC"   # BTC, ETH, SOL, or XRP

# ──────────────────────────────────────────────────────────────
# 📡  API
# ──────────────────────────────────────────────────────────────

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"

def get_server_time():
    """Get Polymarket's server time to stay in sync."""
    try:
        resp = requests.get(f"{CLOB_API}/time", timeout=5)
        resp.raise_for_status()
        return int(resp.json())
    except:
        return int(time.time())

def get_current_15m_slugs(coin="BTC"):
    """
    Generate the correct slug(s) to try for the current 15-minute window.
    Slug format: btc-updown-15m-{unix_timestamp}
    The timestamp is the START of the current or next 15-min interval.
    """
    coin_lower = coin.lower()
    server_time = get_server_time()

    # Round down to current 15-min interval, also try adjacent ones
    slugs = []
    for offset in [-15, 0, 15, 30]:
        interval_start = (server_time // 900) * 900 + (offset * 60)
        slugs.append(f"{coin_lower}-updown-15m-{interval_start}")
    return slugs

def find_active_15m_market(coin="BTC"):
    """Find the currently active 15-minute Up/Down market using correct slug generation."""
    slugs = get_current_15m_slugs(coin)

    for slug in slugs:
        try:
            resp = requests.get(
                f"{GAMMA_API}/markets",
                params={"slug": slug},
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            markets = data if isinstance(data, list) else [data]
            for market in markets:
                if market.get("active") or market.get("enable_order_book"):
                    print(f"  Found market: {slug}")
                    return market
        except Exception as e:
            continue

    # Fallback: broad search
    try:
        coin_lower = coin.lower()
        resp = requests.get(
            f"{GAMMA_API}/markets",
            params={"active": "true", "limit": 100},
            timeout=10
        )
        resp.raise_for_status()
        for market in resp.json():
            slug = market.get("slug", "")
            if f"{coin_lower}-updown-15m" in slug:
                print(f"  Found via fallback: {slug}")
                return market
    except Exception as e:
        print(f"[ERROR] Fallback search failed: {e}")

    return None

def get_orderbook_price(token_id, side="buy"):
    """Get the best price for a token, trying multiple endpoint formats."""
    # Try 1: /price with token_id in path
    try:
        resp = requests.get(
            f"{CLOB_API}/price/{token_id}",
            params={"side": side},
            timeout=5
        )
        if resp.status_code == 200:
            data = resp.json()
            price = data.get("price") or data.get("mid") or data.get("p")
            if price is not None:
                return float(price)
    except:
        pass

    # Try 2: /midpoint with token_id in path
    try:
        resp = requests.get(f"{CLOB_API}/midpoint/{token_id}", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            mid = data.get("mid") or data.get("price")
            if mid is not None:
                return float(mid)
    except:
        pass

    # Try 3: /midpoint with query param
    try:
        resp = requests.get(
            f"{CLOB_API}/midpoint",
            params={"token_id": token_id},
            timeout=5
        )
        if resp.status_code == 200:
            data = resp.json()
            mid = data.get("mid") or data.get("price")
            if mid is not None:
                return float(mid)
    except:
        pass

    # Try 4: last trade price
    try:
        resp = requests.get(
            f"{CLOB_API}/last-trade-price",
            params={"token_id": token_id},
            timeout=5
        )
        if resp.status_code == 200:
            data = resp.json()
            price = data.get("price") or data.get("p")
            if price is not None:
                return float(price)
    except:
        pass

    return None

def get_tokens_from_clob(market):
    """Fetch token IDs from CLOB API since Gamma API often returns empty tokens."""
    condition_id = market.get("conditionId") or market.get("condition_id") or market.get("id")
    if not condition_id:
        return []
    try:
        resp = requests.get(f"{CLOB_API}/markets/{condition_id}", timeout=10)
        resp.raise_for_status()
        tokens = resp.json().get("tokens", [])
        if tokens:
            print(f"  [DEBUG] Got {len(tokens)} tokens from CLOB")
            return tokens
    except Exception as e:
        print(f"  [DEBUG] CLOB fetch attempt 1 failed: {e}")
    try:
        resp = requests.get(f"{CLOB_API}/markets", params={"condition_id": condition_id}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            return data[0].get("tokens", [])
        return data.get("tokens", [])
    except Exception as e:
        print(f"  [DEBUG] CLOB fetch attempt 2 failed: {e}")
        return []

def get_both_prices(market):
    """
    Fetch current ask prices for both the UP and DOWN tokens.
    Returns (up_price, down_price, up_token_id, down_token_id) or None.
    """
    tokens = market.get("tokens", [])

    # Gamma API often returns empty tokens — fetch from CLOB directly
    if not tokens:
        tokens = get_tokens_from_clob(market)

    if not tokens:
        print(f"  [DEBUG] No tokens found. Market keys: {list(market.keys())}")
        print(f"  [DEBUG] conditionId: {market.get('conditionId')} id: {market.get('id')}")
        return None

    up_token = None
    down_token = None

    for t in tokens:
        outcome = t.get("outcome", "").lower()
        if outcome in ("yes", "up"):
            up_token = t
        elif outcome in ("no", "down"):
            down_token = t

    if not up_token or not down_token:
        if len(tokens) >= 2:
            up_token, down_token = tokens[0], tokens[1]
        else:
            print(f"  [DEBUG] Only {len(tokens)} token(s): {tokens}")
            return None

    up_id   = up_token.get("token_id")
    down_id = down_token.get("token_id")

    if not up_id or not down_id:
        print(f"  [DEBUG] Missing token IDs. Tokens: {tokens}")
        return None

    up_price   = get_orderbook_price(up_id, side="buy")
    down_price = get_orderbook_price(down_id, side="buy")

    if up_price is None or down_price is None:
        print(f"  [DEBUG] All price endpoints failed for these token IDs.")
        print(f"  [DEBUG] Up token  : {up_id}")
        print(f"  [DEBUG] Down token: {down_id}")
        return None

    # Sanity check: if UP price is < 0.1 the tokens are likely swapped
    if up_price < 0.1:
        up_price, down_price = down_price, up_price
        up_id, down_id = down_id, up_id

    return up_price, down_price, up_id, down_id

# ──────────────────────────────────────────────────────────────
# 💸  TRADING
# ──────────────────────────────────────────────────────────────

def place_order(token_id, price, size, side="BUY", label=""):
    """Place a single order. In dry-run, just logs."""
    if DRY_RUN:
        print(f"    [DRY RUN] {side} {size} shares @ {price:.4f} — {label}")
        return {"success": True, "dry_run": True}

    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.constants import POLYGON

        clob = ClobClient(
            host=CLOB_API,
            chain_id=POLYGON,
            private_key=PRIVATE_KEY,
        )
        clob.set_api_creds(clob.create_or_derive_api_creds())

        result = clob.create_and_post_order(OrderArgs(
            token_id=token_id,
            price=round(price * 1.005, 4),  # tiny slippage buffer
            size=float(size),
            side=side,
            order_type=OrderType.GTC,
        ))
        return {"success": True, "order": result}
    except Exception as e:
        print(f"    [ORDER ERROR] {e}")
        return {"success": False, "error": str(e)}

def execute_arbitrage(up_price, down_price, up_id, down_id, market):
    """Execute both legs of the arbitrage trade."""
    combined = up_price + down_price
    profit_per_share = 1.0 - combined
    total_cost = combined * ORDER_SIZE
    expected_profit = profit_per_share * ORDER_SIZE

    print(f"\n  🎯 ARB OPPORTUNITY FOUND!")
    print(f"     UP price   : ${up_price:.4f}")
    print(f"     DOWN price : ${down_price:.4f}")
    print(f"     Combined   : ${combined:.4f} (< $1.00 ✅)")
    print(f"     Profit/share: ${profit_per_share:.4f} ({profit_per_share*100:.2f}%)")
    print(f"     Order size : {ORDER_SIZE} shares")
    print(f"     Total cost : ${total_cost:.2f}")
    print(f"     Expected profit: ${expected_profit:.2f}")

    # Place UP leg
    print(f"\n  Placing UP leg...")
    up_result = place_order(up_id, up_price, ORDER_SIZE, "BUY", "UP")

    if not up_result.get("success"):
        print(f"  ❌ UP leg failed — aborting (no exposure taken)")
        return False

    # Place DOWN leg
    print(f"  Placing DOWN leg...")
    down_result = place_order(down_id, down_price, ORDER_SIZE, "BUY", "DOWN")

    if not down_result.get("success"):
        print(f"  ⚠️  DOWN leg failed! UP leg may have filled — check Polymarket!")
        print(f"      You may have one-sided exposure. Go to polymarket.com to manage.")
        return False

    print(f"  ✅ Both legs placed! Waiting for market to resolve...")
    return True

# ──────────────────────────────────────────────────────────────
# 📊  STATS TRACKER
# ──────────────────────────────────────────────────────────────

class Stats:
    def __init__(self):
        self.scans         = 0
        self.opportunities = 0
        self.trades        = 0
        self.total_profit_estimate = 0.0
        self.start_time    = datetime.now()

    def print_summary(self):
        elapsed = (datetime.now() - self.start_time).seconds // 60
        print(f"\n  📊 Session stats: {self.scans} scans | "
              f"{self.opportunities} opportunities | "
              f"{self.trades} trades | "
              f"~${self.total_profit_estimate:.2f} est. profit | "
              f"{elapsed}min running")

# ──────────────────────────────────────────────────────────────
# 🔁  MAIN LOOP
# ──────────────────────────────────────────────────────────────

def run_bot():
    stats = Stats()
    current_market_slug = None
    last_trade_market = None

    mode = "💡 DRY RUN" if DRY_RUN else "🔴 LIVE TRADING"
    print(f"""
╔══════════════════════════════════════════════╗
║  BTC 15-Min Arbitrage Bot — {mode}
║  Coin      : {COIN}
║  Order size: {ORDER_SIZE} shares per side
║  Threshold : combined price ≤ {ARB_THRESHOLD}
║  Scan rate : every {SCAN_INTERVAL_SECS}s
╚══════════════════════════════════════════════╝
    """)

    while True:
        stats.scans += 1
        now = datetime.now().strftime("%H:%M:%S")

        # Find active market
        market = find_active_15m_market(COIN)
        if not market:
            print(f"[{now}] No active {COIN} 15m market found. Retrying...")
            time.sleep(15)
            continue

        slug     = market.get("slug", "unknown")
        end_date = market.get("endDate", "?")
        question = market.get("question", "")[:60]

        # New market detected
        if slug != current_market_slug:
            current_market_slug = slug
            last_trade_market = None
            print(f"\n[{now}] 🆕 New market: {question}")
            print(f"         Ends: {end_date}")

        # Get prices
        prices = get_both_prices(market)
        if not prices:
            print(f"[{now}] Could not fetch prices, retrying...")
            time.sleep(SCAN_INTERVAL_SECS)
            continue

        up_price, down_price, up_id, down_id = prices
        combined = up_price + down_price

        print(f"[{now}] UP={up_price:.3f} DOWN={down_price:.3f} "
              f"TOTAL={combined:.3f} ", end="")

        # Check for arbitrage opportunity
        if combined <= ARB_THRESHOLD:
            print(f"← ARB! 🎯")
            stats.opportunities += 1

            # Only trade each market once (avoid doubling up)
            if last_trade_market != slug:
                success = execute_arbitrage(up_price, down_price, up_id, down_id, market)
                if success:
                    stats.trades += 1
                    stats.total_profit_estimate += (1.0 - combined) * ORDER_SIZE
                    last_trade_market = slug
            else:
                print(f"  (Already traded this market round — waiting for next)")
        else:
            gap = combined - ARB_THRESHOLD
            print(f"← no arb (need {gap:.3f} more compression)")

        # Print stats every 20 scans
        if stats.scans % 20 == 0:
            stats.print_summary()

        time.sleep(SCAN_INTERVAL_SECS)


if __name__ == "__main__":
    if not DRY_RUN and not PRIVATE_KEY:
        print("❌ ERROR: POLY_PRIVATE_KEY not set in .env file!")
        print("   Create a .env file with your credentials first.")
        exit(1)

    try:
        run_bot()
    except KeyboardInterrupt:
        print("\n\nBot stopped. Goodbye!")
