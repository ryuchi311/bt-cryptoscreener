from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
from screener import run_screener, get_symbols_rsi

# Shared timeframe used by the background task; clients can update via socket event
current_timeframe = "1h"

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Background update interval in seconds (lower for faster auto-updates)
# Reduced for snappier UX — ensure caps are lowered to avoid rate limits
BACKGROUND_INTERVAL = 5

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/ticker")
def api_ticker():
    """GET /api/ticker?symbols=BTC/USDT,ETH/USDT&timeframe=1h

    Returns JSON array of objects with `symbol`, `price`, `rsi`, and `status`.
    """
    symbols = request.args.get("symbols")
    timeframe = request.args.get("timeframe") or None
    if not symbols:
        return jsonify({"error": "Provide symbols query parameter (comma-separated)"}), 400
    syms = [s.strip() for s in symbols.split(",") if s.strip()]
    data = get_symbols_rsi(syms, timeframe=timeframe)
    return jsonify(data)


def background_screener():
    while True:
        data = run_screener(current_timeframe)
        # limit how many rows we push to the client to keep updates responsive
        max_rows = 200
        # Only emit Overbought/Oversold rows to clients to reduce payload
        filtered = [r for r in data if r.get('status') in ('Overbought', 'Oversold')]
        emit_data = filtered[:max_rows]
        print(f"Broadcasting {len(emit_data)} / {len(data)} rows")
        socketio.emit("update", emit_data)
        socketio.sleep(BACKGROUND_INTERVAL)  # let the Socket.IO loop breathe between updates


@socketio.on("connect")
def connect():
    print("Client connected")
    # Send current timeframe to the newly connected client
    socketio.emit("timeframe", current_timeframe)
    try:
        # send an immediate data update so clients see results without waiting
        data = run_screener(current_timeframe)
        max_rows = 200
        filtered = [r for r in data if r.get('status') in ('Overbought', 'Oversold')]
        emit_data = filtered[:max_rows]
        print(f"Sending immediate {len(emit_data)} rows to new client")
        socketio.emit("update", emit_data)
    except Exception as exc:
        print(f"Error sending immediate update: {exc}")


@socketio.on("set_timeframe")
def handle_set_timeframe(tf):
    global current_timeframe
    if not tf:
        return
    print(f"Timeframe set to {tf}")
    current_timeframe = tf
    # Immediately send an update with the new timeframe
    data = run_screener(current_timeframe)
    max_rows = 200
    filtered = [r for r in data if r.get('status') in ('Overbought', 'Oversold')]
    emit_data = filtered[:max_rows]
    socketio.emit("timeframe", current_timeframe)
    socketio.emit("update", emit_data)


if __name__ == "__main__":
    socketio.start_background_task(background_screener)
    # Allow Werkzeug for local/container use. In production use a proper WSGI server.
    socketio.run(app, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)
