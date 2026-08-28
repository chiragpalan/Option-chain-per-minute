"""option_fetcher.py — Build option chain with OI, volume, IV and Greeks."""
import logging
from datetime import date
from typing import Callable, Dict, List, Optional

import pandas as pd

from src.option_chain.expiry_handler import current_weekly_expiry, next_expiries
from src.option_chain.strike_selector import strikes_around_atm
from src.option_chain.greeks import compute_greeks

logger = logging.getLogger(__name__)

_RISK_FREE_RATE = 0.065
_STRIKE_GAP: Dict[str, float] = {
    "NIFTY50":     50,
    "BANKNIFTY":   100,
    "SENSEX":      100,
    "MIDCAPNIFTY": 25,
    "FINNIFTY":    50,
}


def _tte(expiry: date) -> float:
    """Time to expiry in years (minimum 1 day)."""
    return max((expiry - date.today()).days, 1) / 365.0


def _build_row(
    symbol: str, expiry: date, strike: float,
    option_type: str, spot: float, tte: float,
    ohlcv: Optional[Dict] = None,
) -> dict:
    ohlcv  = ohlcv or {}
    iv     = ohlcv.get("iv")  # % value e.g. 15.5 — None if unavailable
    iv_dec = iv / 100 if (iv and iv > 0) else None
    greeks = (
        compute_greeks(spot, strike, tte, _RISK_FREE_RATE, iv_dec, option_type)
        if iv_dec
        else {k: None for k in ("delta", "gamma", "theta", "vega", "rho")}
    )
    return {
        "datetime":    pd.Timestamp.now().floor("min").isoformat(),
        "symbol":      symbol,
        "expiry":      expiry.isoformat(),
        "strike":      strike,
        "option_type": option_type,
        "open":        ohlcv.get("open"),
        "high":        ohlcv.get("high"),
        "low":         ohlcv.get("low"),
        "close":       ohlcv.get("close"),
        "volume":      ohlcv.get("volume"),
        "oi":          ohlcv.get("oi"),
        "iv":          iv,   # % or None
        **greeks,
    }


def fetch_option_chain(
    symbol: str,
    spot: float,
    expiry: Optional[date] = None,
    width: int = 10,
    data_provider: Optional[Callable] = None,
) -> pd.DataFrame:
    """
    Build option chain for `symbol` around ATM ± `width` strikes.

    data_provider : callable(symbol, expiry, strike, option_type) -> dict
                    with keys open/high/low/close/volume/oi/iv.
                    Pass None to generate Greeks-only skeleton rows.
    """
    expiry     = expiry or current_weekly_expiry()
    gap        = _STRIKE_GAP.get(symbol, 50)
    tte        = _tte(expiry)
    all_strikes = strikes_around_atm(spot, gap, width)

    rows: List[dict] = []
    for strike in all_strikes:
        for otype in ("CE", "PE"):
            ohlcv: dict = {}
            if data_provider:
                try:
                    ohlcv = data_provider(symbol, expiry, strike, otype) or {}
                except Exception:
                    logger.warning("data_provider error: %s %s %s", symbol, strike, otype)
            rows.append(_build_row(symbol, expiry, strike, otype, spot, tte, ohlcv))

    df = pd.DataFrame(rows)
    logger.info("[%s] Option chain: %d rows (expiry=%s)", symbol, len(df), expiry)
    return df


def fetch_multiple_expiries(
    symbol: str,
    spot: float,
    n_expiries: int = 3,
    width: int = 5,
    data_provider: Optional[Callable] = None,
) -> pd.DataFrame:
    """Fetch option chain across the next `n_expiries` weekly expiries."""
    frames = [
        fetch_option_chain(symbol, spot, exp, width, data_provider)
        for exp in next_expiries(n_expiries)
    ]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
