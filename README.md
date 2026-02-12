# Crypto Screener (MEXC Perp)

Lightweight Flask + Socket.IO web app that screens MEXC USDT perpetual pairs and computes indicators (RSI).

This repository contains a small live UI and a minimal JSON API you can call from other tools.

Project structure

- `crypto_screener/`
	- `app.py` — Flask + Socket.IO server and background broadcaster
	- `screener.py` — core logic: symbol discovery, OHLCV/ticker fetching, RSI calculation, caching
	- `templates/index.html` — single-page UI (Tailwind + DaisyUI)
	- `static/` — images and assets (logos)
	- `requirements.txt` — Python dependencies

Key features

- Live socket updates of signals (Overbought / Oversold) to the UI
- Lightweight REST endpoint: `GET /api/ticker?symbols=...&timeframe=...`
- Client-side controls: timeframe tabs, filter, manual fetch prompt, theme selector
- Preset symbol macros accessible from the navbar (desktop/mobile)

Quick start

1. Create and activate a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Run the app (development):

```bash
python crypto_screener/app.py
```

3. Open the UI at `http://localhost:5000/`.

API: `/api/ticker`

Returns an array of objects for the requested symbols. Query parameters:

- `symbols` (required) — comma-separated list, e.g. `BTC/USDT,ETH/USDT`
- `timeframe` (optional) — `1h`, `30m`, `4h` (defaults to server timeframe)

Example:

```bash
curl 'http://localhost:5000/api/ticker?symbols=BTC/USDT,ETH/USDT&timeframe=1h'
```

Returned JSON fields (per symbol):

- `symbol`, `price`, `rsi`, `status` (`Overbought` / `Oversold` / `Neutral`), `volume_usdt` (when available)

About volume and accuracy

- The screener prefers `ticker.quoteVolume` when available. If missing it will estimate USDT volume using base volume * last price or OHLCV volume * last price. This reduces zero-volume responses for many markets.
- RSI is computed from OHLCV `close` values using a 14-period window via the `ta` library.

Frontend notes

- The UI uses Tailwind + DaisyUI components; theme selection is available via the navbar (persisted in `localStorage`).
- There is a manual "Fetch RSI" prompt and navbar presets for quick symbol groups.

Customization

- To add persistent presets you can modify `templates/index.html` (client JS) or add a small server endpoint to store preferences.
- To change discovery (exchange, spot vs perpetual), edit `screener.discover_usdt_symbols()`.

Troubleshooting

- If you don't see frontend changes, hard-refresh your browser (Ctrl/Cmd+Shift+R) or open an Incognito window — the template is served by Flask and may be cached by the browser.
- If volume is still missing for a symbol, the exchange may not provide recent ticker or OHLCV volume fields for that market.

License & safety

This is a small demo. Use responsibly and respect exchange rate limits when running at scale.
