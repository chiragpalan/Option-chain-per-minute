"""
app.py — Flask backend for option chain frontend.
Run: python app.py
"""
import os
import sys
from datetime import date, datetime

import pytz
from flask import Flask, jsonify, render_template, request
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(__file__))

OPTION_DB = os.getenv("OPTION_DB", "data/option_chain.db")
IST       = pytz.timezone("Asia/Kolkata")

app = Flask(__name__, template_folder="frontend/templates", static_folder="frontend/static")

_STRIKE_GAP = {"NIFTY50": 50}
_DISPLAY_NAME = {"NIFTY50": "NIFTY 50"}


def _fmt(v):
    if v is None or (isinstance(v, float) and __import__('math').isnan(v)):
        return None
    return round(float(v), 2)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/expiries")
def api_expiries():
    symbol = request.args.get("symbol", "NIFTY50")
    try:
        from src.option_chain.nse_scraper import get_expiry_dates
        today    = datetime.now(IST).date()
        expiries = [
            e for e in get_expiry_dates(symbol)
            if datetime.strptime(e, "%d-%b-%Y").date() >= today
        ]
        return jsonify(expiries[:4])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/option-chain")
def api_option_chain():
    symbol = request.args.get("symbol", "NIFTY50")
    expiry = request.args.get("expiry", "")

    from src.option_chain.nse_scraper import fetch_option_chain, get_expiry_dates, get_spot
    from src.database import insert_option_data

    spot = get_spot(symbol) or 0.0

    if not expiry:
        expiries = get_expiry_dates(symbol)
        expiry   = expiries[0] if expiries else ""

    try:
        df = fetch_option_chain(symbol, expiry, spot)
    except Exception as e:
        return jsonify({"error": f"Option chain fetch failed: {e}"}), 502

    if df.empty:
        return jsonify({"error": "No option chain data available"}), 404

    try:
        insert_option_data(OPTION_DB, symbol, df, spot)
    except Exception:
        pass

    gap = _STRIKE_GAP.get(symbol, 50)
    atm = round(spot / gap) * gap if spot else 0

    chain = {}
    for _, r in df.iterrows():
        s = float(r["strike"])
        if s not in chain:
            chain[s] = {"strike": s, "CE": {}, "PE": {}}
        otype = str(r["option_type"]).upper()
        chain[s][otype] = {
            "oi":     _fmt(r.get("oi")),
            "oiChg":  _fmt(r.get("oi_chg")),
            "volume": _fmt(r.get("volume")),
            "iv":     _fmt(r.get("iv")),
            "ltp":    _fmt(r.get("ltp")),
            "open":   _fmt(r.get("open")),
            "high":   _fmt(r.get("high")),
            "low":    _fmt(r.get("low")),
            "close":  _fmt(r.get("close")),
            "delta":  _fmt(r.get("delta")),
            "gamma":  _fmt(r.get("gamma")),
            "theta":  _fmt(r.get("theta")),
            "vega":   _fmt(r.get("vega")),
            "rho":    _fmt(r.get("rho")),
        }

    rows = sorted(chain.values(), key=lambda x: x["strike"])
    return jsonify({
        "spot":   spot,
        "atm":    atm,
        "rows":   rows,
        "symbol": _DISPLAY_NAME.get(symbol, symbol),
        "expiry": expiry,
    })


_OC_TABLES = {"NIFTY50": "nifty50_option_chain"}


@app.route("/api/dates")
def api_dates():
    symbol = request.args.get("symbol", "NIFTY50")
    table  = _OC_TABLES.get(symbol)
    if not table:
        return jsonify([])
    try:
        import sqlite3
        with sqlite3.connect(OPTION_DB) as conn:
            rows = conn.execute(
                f"SELECT DISTINCT substr(timestamp,1,8) AS d FROM {table} ORDER BY d DESC LIMIT 30"
            ).fetchall()
        dates = [r[0] for r in rows]
        return jsonify([f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in dates])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/history")
def api_history():
    symbol   = request.args.get("symbol", "NIFTY50")
    date_str = request.args.get("date", "")
    interval = int(request.args.get("interval", 1))
    table    = _OC_TABLES.get(symbol)
    if not table:
        return jsonify([])

    date_filter = date_str.replace("-", "") if date_str else ""

    try:
        import sqlite3
        from collections import defaultdict

        with sqlite3.connect(OPTION_DB) as conn:
            if date_filter:
                rows = conn.execute(
                    f"SELECT timestamp, spot FROM {table} "
                    f"WHERE substr(timestamp,1,8)=? GROUP BY timestamp ORDER BY timestamp",
                    (date_filter,)
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT timestamp, spot FROM {table} GROUP BY timestamp ORDER BY timestamp"
                ).fetchall()

        minute_candles = []
        for ts, spot in rows:
            if not spot or spot <= 0:
                continue
            minute_candles.append({"close": float(spot), "ts": ts})

        if not minute_candles:
            return jsonify([])

        def bucket_key(ts, mins):
            h, m = int(ts[8:10]), int(ts[10:12])
            bm = (m // mins) * mins
            return f"{ts[:8]}{h:02d}{bm:02d}"

        buckets = defaultdict(list)
        for c in minute_candles:
            buckets[bucket_key(c["ts"], interval)].append(c["close"])

        candles = []
        for key in sorted(buckets):
            prices = buckets[key]
            dt = f"{key[:4]}-{key[4:6]}-{key[6:8]}T{key[8:10]}:{key[10:12]}:00"
            candles.append({
                "datetime": dt,
                "open":   round(prices[0], 2),
                "high":   round(max(prices), 2),
                "low":    round(min(prices), 2),
                "close":  round(prices[-1], 2),
                "volume": len(prices),
                "signal": "HOLD",
            })

        return jsonify(candles)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
