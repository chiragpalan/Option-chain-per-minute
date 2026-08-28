"""database.py — SQLite helpers for option_chain.db."""
import sqlite3
import logging
from datetime import datetime

import pandas as pd
import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

_OC_COLS = [
    "timestamp", "symbol", "expiry", "strike", "option_type",
    "spot", "ltp", "open", "high", "low", "close",
    "volume", "oi", "oi_chg", "iv",
    "delta", "gamma", "theta", "vega", "rho",
]

_OC_DDL = """
CREATE TABLE IF NOT EXISTS nifty50_option_chain (
    timestamp TEXT, symbol TEXT, expiry TEXT, strike REAL, option_type TEXT,
    spot REAL, ltp REAL, open REAL, high REAL, low REAL, close REAL,
    volume REAL, oi REAL, oi_chg REAL, iv REAL,
    delta REAL, gamma REAL, theta REAL, vega REAL, rho REAL,
    PRIMARY KEY (timestamp, strike, option_type, expiry)
)
"""

_MARKET_OPEN  = (9, 0)
_MARKET_CLOSE = (15, 30)


def init_option_db(db: str) -> None:
    with sqlite3.connect(db) as conn:
        conn.execute(_OC_DDL)
        conn.commit()
    logger.info("Option DB initialised: %s", db)


def _is_market_hours() -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    hm = (now.hour, now.minute)
    return _MARKET_OPEN <= hm <= _MARKET_CLOSE


def _last_snapshot_age_mins(db: str) -> float:
    try:
        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT MAX(timestamp) FROM nifty50_option_chain"
            ).fetchone()
        if row and row[0]:
            last = datetime.strptime(row[0], "%Y%m%d%H%M").replace(tzinfo=IST)
            return (datetime.now(IST) - last).total_seconds() / 60
    except Exception:
        pass
    return float("inf")


def insert_option_data(db: str, symbol: str, df: pd.DataFrame, spot: float) -> None:
    if df.empty:
        return
    if not _is_market_hours():
        logger.info("[%s] Outside market hours — skipping insert", symbol)
        return
    init_option_db(db)
    if _last_snapshot_age_mins(db) < 1:
        logger.info("[%s] Skipping insert — last snapshot < 1 min ago", symbol)
        return
    ts = datetime.now(IST).strftime("%Y%m%d%H%M")
    df = df.copy()
    df["timestamp"] = ts
    df["symbol"] = symbol
    df["spot"] = spot

    for col in _OC_COLS:
        if col not in df.columns:
            df[col] = None

    sql = (
        f"INSERT OR REPLACE INTO nifty50_option_chain ({', '.join(_OC_COLS)}) "
        f"VALUES ({', '.join(['?'] * len(_OC_COLS))})"
    )
    with sqlite3.connect(db) as conn:
        conn.executemany(sql, df[_OC_COLS].values.tolist())
        conn.commit()
    logger.info("[%s] Stored %d option rows", symbol, len(df))
