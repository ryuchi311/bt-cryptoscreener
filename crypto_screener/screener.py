import ccxt
import pandas as pd
import ta
import time
from numbers import Number

# ============================
# EXCHANGE CONFIGURATION: MEXC
# ============================
# This screener exclusively uses MEXC exchange for all trading pairs.
# All symbols discovered and displayed are MEXC USDT spot pairs only.
exchange = ccxt.mexc({
    "enableRateLimit": True,
})
print(f"[Screener] Initialized with exchange: {exchange.name.upper()}")

SYMBOL_DISCOVERY_INTERVAL = 60 * 15  # refresh the symbol list every 15 minutes
DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]
_cached_symbols = []
_last_discovery = 0.0
MANUAL_SYMBOLS_FILE = "manual_symbols.txt"
MAX_SYMBOLS_PER_RUN = 60  # cap processed symbols to avoid timeouts (tuned lower to reduce rate usage)

# Per-timeframe caps to reduce rate usage for short intervals
TIMEFRAME_MAX = {
    # lowered caps to reduce API calls when BACKGROUND_INTERVAL is small
    "30m": 30,
    "1h": 60,
    "4h": 80,
}

TIMEFRAME = "1h"

RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30

STOCH_OVERBOUGHT = 70
STOCH_OVERSOLD = 30


def discover_usdt_symbols():
    """Fetch all active MEXC spot markets quoted in USDT."""
    global _cached_symbols, _last_discovery

    now = time.time()
    cache_expired = not _cached_symbols or (now - _last_discovery) > SYMBOL_DISCOVERY_INTERVAL

    if not cache_expired:
        return _cached_symbols

    try:
        markets = exchange.load_markets()
        # Select perpetual/contract markets quoted in USDT (exclude plain spot markets)
        symbols = [
            symbol for symbol, market in markets.items()
            if market.get("quote") == "USDT" and market.get("active", True)
            and (
                market.get('contract') or market.get('future')
                or (market.get('type') in ("swap", "future"))
                or (not market.get('spot', False))
            )
        ]
        _cached_symbols = sorted(symbols) or DEFAULT_SYMBOLS
        _last_discovery = now
        print(f"[Screener] Discovered {len(_cached_symbols)} MEXC USDT perpetual pairs")
    except Exception as exc:
        # fall back to the last successful list (or defaults) if discovery fails
        print(f"Symbol discovery failed: {exc}")
        if not _cached_symbols:
            _cached_symbols = DEFAULT_SYMBOLS

    return _cached_symbols


# Simple in-memory cache to avoid refetching every run
# cached entry: { 'data': {...}, 'ts': epoch_seconds }
_cache = {}

# How long to reuse cached results per timeframe (seconds)
CACHE_AGE = {
    # Shorter TTLs so RSI/Signals refresh closer to real-time
    "30m": 30,    # was 60
    "1h": 60,     # was 300
    "4h": 300,    # was 900
}

# Persistent cache file
CACHE_FILE = "screener_cache.json"
# Minimum 24h volume in USDT to consider a market (filter low-liquidity)
MIN_VOLUME_USDT = 100.0

import json
import os


def _load_cache_from_disk():
    global _cache
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
                # validate structure
                if isinstance(data, dict):
                    _cache = data
                    # Ensure `prev_price` exists for entries loaded from disk so
                    # change percentages can be calculated immediately after a restart.
                    for sym, entry in list(_cache.items()):
                        try:
                            ed = entry.get('data') if isinstance(entry, dict) else None
                            prev = entry.get('prev_price') if isinstance(entry, dict) else None
                            # if prev_price missing or null, seed it from the last saved price
                            if ed and (prev is None) and isinstance(ed, dict):
                                p = ed.get('price')
                                if p is not None:
                                    _cache[sym]['prev_price'] = float(p)
                        except Exception:
                            # keep going even if one entry is malformed
                            continue
    except Exception as exc:
        print(f"Failed to load cache from disk: {exc}")


def _save_cache_to_disk():
    try:
        # make a JSON-serializable copy (ensure numbers are native types)
        safe = {}
        for k, v in _cache.items():
            safe[k] = {
                'data': v.get('data'),
                'ts': float(v.get('ts', 0)),
                'prev_price': float(v.get('prev_price')) if v.get('prev_price') is not None else None
            }
        with open(CACHE_FILE, 'w', encoding='utf-8') as fh:
            json.dump(safe, fh)
    except Exception as exc:
        print(f"Failed to save cache to disk: {exc}")


# load cache at import time if present
_load_cache_from_disk()


def read_manual_symbols():
    """Read user-specified symbols from MANUAL_SYMBOLS_FILE. Comments and blank lines ignored."""
    try:
        path = f"{__package__}/manual_symbols.txt" if __package__ else "manual_symbols.txt"
        symbols = []
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                symbols.append(line)
        return [s for s in symbols]
    except FileNotFoundError:
        return []
    except Exception as exc:
        print(f"Failed to read manual symbols: {exc}")
        return []


def fetch_symbol_data(symbol, timeframe=TIMEFRAME):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=100)
        df = pd.DataFrame(ohlcv, columns=["time","open","high","low","close","volume"])

        df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=14).rsi()


        latest = df.iloc[-1]

        # Try to get a live last price from ticker (more accurate for current price)
        last_price = None
        try:
            ticker = exchange.fetch_ticker(symbol)
            last_price = ticker.get('last')
        except Exception:
            # fallback to latest close from OHLCV
            last_price = latest.get('close')

        # Determine signal strictly from RSI thresholds (short labels)
        status = "Neutral"
        try:
            rsi_val = float(latest["rsi"])
            if rsi_val >= RSI_OVERBOUGHT:
                status = "Overbought"
            elif rsi_val <= RSI_OVERSOLD:
                status = "Oversold"
        except Exception:
            status = "Neutral"

        # Compute volume in USDT when possible. Prefer ticker.quoteVolume, else
        # estimate from base volume (OHLCV) * last price.
        volume_usdt = None
        try:
            # prefer ticker info if available
            if 'ticker' in locals() and isinstance(ticker, dict):
                qv = ticker.get('quoteVolume')
                if qv is not None:
                    volume_usdt = float(qv)
                else:
                    base_vol = ticker.get('baseVolume')
                    if base_vol is not None and last_price is not None:
                        volume_usdt = float(base_vol) * float(last_price)
            # fallback to OHLCV's last-volume (usually base asset volume)
            if (volume_usdt is None) and (latest.get('volume') is not None) and (last_price is not None):
                try:
                    volume_usdt = float(latest.get('volume')) * float(last_price)
                except Exception:
                    volume_usdt = None
        except Exception:
            volume_usdt = None

        return {
            "symbol": symbol,
            "price": float(round(float(last_price), 8)) if last_price is not None else None,
            "rsi": float(round(latest["rsi"], 2)),
            "status": status,
            "volume_usdt": float(round(volume_usdt, 2)) if volume_usdt is not None else None,
        }

    except Exception as e:
        return {"symbol": symbol, "error": str(e)}


def run_screener(timeframe=None):
    results = []
    discovered = discover_usdt_symbols()
    manual = read_manual_symbols()

    # If the user provided a manual list, prefer displaying/processing only those symbols.
    # Otherwise fall back to discovered perpetual symbols from the exchange.
    combined = []
    seen = set()
    if manual:
        for s in manual:
            if s and s not in seen:
                seen.add(s)
                combined.append(s)
    else:
        for s in discovered:
            if s and s not in seen:
                seen.add(s)
                combined.append(s)

    if not combined:
        return [{"symbol": "Discovery", "error": "No USDT symbols found or manual list is empty"}]

    # Limit work per invocation (choose cap based on timeframe)
    tf = timeframe or TIMEFRAME
    cap = TIMEFRAME_MAX.get(tf, MAX_SYMBOLS_PER_RUN)

    now = time.time()
    cache_ttl = CACHE_AGE.get(tf, 300)

    # Decide which symbols need fetching now and which can reuse cache
    to_fetch = []
    final_order = []
    for symbol in combined:
        entry = _cache.get(symbol)
        if entry and (now - entry.get('ts', 0)) < cache_ttl:
            # cached and fresh
            final_order.append(symbol)
            continue
        # else schedule for fetch
        to_fetch.append(symbol)

    # Only fetch up to cap items this run (prioritize manual symbols since they are first in `combined`)
    to_fetch_now = to_fetch[:cap]

    # Fetch and update cache
    for symbol in to_fetch_now:
        # try to get ticker first (gives recent price and volume)
        prev = _cache.get(symbol)
        prev_price = None
        if prev and isinstance(prev.get('data', {}).get('price'), (int, float)):
            prev_price = float(prev['data']['price'])

        ticker = None
        last_price = None
        volume_usdt = None
        try:
            ticker = exchange.fetch_ticker(symbol)
            last_price = ticker.get('last')
            # try quoteVolume, else approximate by baseVolume * last
            volume_usdt = ticker.get('quoteVolume')
            if volume_usdt is None:
                base_vol = ticker.get('baseVolume')
                if base_vol is not None and last_price is not None:
                    volume_usdt = float(base_vol) * float(last_price)
        except Exception:
            ticker = None

        # if volume is present and below threshold, mark as low volume and cache minimal data
        if volume_usdt is not None and float(volume_usdt) < MIN_VOLUME_USDT:
            data = { 'symbol': symbol, 'price': float(last_price) if last_price is not None else None, 'volume_usdt': float(volume_usdt) if volume_usdt is not None else None, 'error': 'Low volume' }
            _cache[symbol] = {'data': data, 'ts': now, 'prev_price': prev_price}
            continue

        # otherwise fetch full OHLCV-based indicators
        data = fetch_symbol_data(symbol, timeframe=tf)
        # ensure price is set (fallback to ticker.last if available)
        if (data.get('price') is None) and last_price is not None:
            try:
                data['price'] = float(last_price)
            except Exception:
                pass
        # add volume_usdt to the data
        if volume_usdt is not None:
            try:
                data['volume_usdt'] = float(volume_usdt)
            except Exception:
                pass

        _cache[symbol] = { 'data': data, 'ts': now, 'prev_price': prev_price }

    # persist cache after fetching
    _save_cache_to_disk()

    # Build results using cached data where available; for items not fetched and no cache, return an error row
    for symbol in combined:
        entry = _cache.get(symbol)
        if entry and isinstance(entry.get('data'), dict):
            row = dict(entry['data'])
            # compute percent change if previous price available
            prev_price = entry.get('prev_price')
            cur_price = row.get('price')
            change = None
            try:
                if prev_price is not None and cur_price is not None and prev_price != 0:
                    change = round((float(cur_price) - float(prev_price)) / float(prev_price) * 100, 2)
            except Exception:
                change = None
            if change is not None:
                row['change_pct'] = change
            # Normalize the status based solely on current RSI value to override any stale cached status
            try:
                rsi_val = row.get('rsi')
                if rsi_val is not None:
                    rsi_num = float(rsi_val)
                    if rsi_num >= RSI_OVERBOUGHT:
                        row['status'] = 'Overbought'
                    elif rsi_num <= RSI_OVERSOLD:
                        row['status'] = 'Oversold'
                    else:
                        row['status'] = 'Neutral'
            except Exception:
                row['status'] = row.get('status', 'Neutral')
            results.append(row)
        else:
            results.append({ 'symbol': symbol, 'error': 'Not fetched yet' })
    # Remove any entries that do not have a valid price (None).
    # These typically indicate delisted/unsupported markets or failed data fetches from MEXC.
    results = [r for r in results if r.get('price') is not None]

    # Sort successful results by RSI descending, keep error rows at the end
    successes = [r for r in results if isinstance(r.get("rsi"), Number)]
    errors = [r for r in results if not isinstance(r.get("rsi"), Number)]

    successes.sort(key=lambda x: x.get("rsi", float("-inf")), reverse=True)
    return successes + errors


def get_symbols_rsi(symbols, timeframe=None):
    """Return RSI and price info for a list of symbol strings.

    This is a lightweight helper for APIs/CLI tools: it calls
    `fetch_symbol_data` for each symbol and returns the collected results.
    """
    tf = timeframe or TIMEFRAME
    out = []
    for s in symbols:
        if not s:
            continue
        try:
            data = fetch_symbol_data(s, timeframe=tf)
            out.append(data)
        except Exception as exc:
            out.append({"symbol": s, "error": str(exc)})
    return out
