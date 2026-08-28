"""greeks.py — Black-Scholes option Greeks."""
import logging
import math
from typing import Literal

from scipy.stats import norm

logger = logging.getLogger(__name__)


def _d1_d2(spot: float, strike: float, tte: float, r: float, iv: float):
    if tte <= 0 or iv <= 0:
        raise ValueError("tte and iv must be positive")
    d1 = (math.log(spot / strike) + (r + 0.5 * iv ** 2) * tte) / (iv * math.sqrt(tte))
    return d1, d1 - iv * math.sqrt(tte)


def delta(spot: float, strike: float, tte: float, r: float, iv: float,
          option_type: Literal["CE", "PE"] = "CE") -> float:
    """Delta: CE ∈ (0,1), PE ∈ (-1,0)."""
    d1, _ = _d1_d2(spot, strike, tte, r, iv)
    return norm.cdf(d1) if option_type == "CE" else norm.cdf(d1) - 1


def gamma(spot: float, strike: float, tte: float, r: float, iv: float) -> float:
    """Gamma (identical for CE and PE)."""
    d1, _ = _d1_d2(spot, strike, tte, r, iv)
    return norm.pdf(d1) / (spot * iv * math.sqrt(tte))


def theta(spot: float, strike: float, tte: float, r: float, iv: float,
          option_type: Literal["CE", "PE"] = "CE", trading_days: int = 252) -> float:
    """Theta per calendar day."""
    d1, d2 = _d1_d2(spot, strike, tte, r, iv)
    t1 = -(spot * norm.pdf(d1) * iv) / (2 * math.sqrt(tte))
    disc = r * strike * math.exp(-r * tte)
    t2 = -disc * norm.cdf(d2) if option_type == "CE" else disc * norm.cdf(-d2)
    return (t1 + t2) / trading_days


def vega(spot: float, strike: float, tte: float, r: float, iv: float) -> float:
    """Vega per 1% change in IV."""
    d1, _ = _d1_d2(spot, strike, tte, r, iv)
    return spot * norm.pdf(d1) * math.sqrt(tte) * 0.01


def rho(spot: float, strike: float, tte: float, r: float, iv: float,
        option_type: Literal["CE", "PE"] = "CE") -> float:
    """Rho per 1% change in interest rate."""
    _, d2 = _d1_d2(spot, strike, tte, r, iv)
    factor = strike * tte * math.exp(-r * tte) * 0.01
    return factor * norm.cdf(d2) if option_type == "CE" else -factor * norm.cdf(-d2)


def compute_greeks(
    spot: float,
    strike: float,
    tte: float,
    r: float,
    iv: float,
    option_type: Literal["CE", "PE"] = "CE",
) -> dict:
    """
    Return all five Greeks.

    Parameters
    ----------
    spot   : underlying price
    strike : option strike
    tte    : time to expiry in years (e.g. 7/365)
    r      : annualised risk-free rate as decimal (e.g. 0.065)
    iv     : annualised implied volatility as decimal (e.g. 0.18)
    """
    try:
        return {
            "delta": delta(spot, strike, tte, r, iv, option_type),
            "gamma": gamma(spot, strike, tte, r, iv),
            "theta": theta(spot, strike, tte, r, iv, option_type),
            "vega":  vega(spot, strike, tte, r, iv),
            "rho":   rho(spot, strike, tte, r, iv, option_type),
        }
    except Exception:
        logger.exception("compute_greeks failed: strike=%s type=%s", strike, option_type)
        return {k: None for k in ("delta", "gamma", "theta", "vega", "rho")}
