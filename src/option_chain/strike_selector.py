"""strike_selector.py — ATM / ITM / OTM strike selection."""
import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)


def atm_strike(spot: float, strike_gap: float) -> float:
    """Round spot to nearest strike_gap."""
    return round(spot / strike_gap) * strike_gap


def itm_strikes(spot: float, strike_gap: float, option_type: str, n: int = 5) -> List[float]:
    """
    Return n ITM strikes.
    CE ITM = below spot; PE ITM = above spot.
    """
    atm = atm_strike(spot, strike_gap)
    ot  = option_type.upper()
    if ot == "CE":
        return [atm - strike_gap * i for i in range(1, n + 1)]
    if ot == "PE":
        return [atm + strike_gap * i for i in range(1, n + 1)]
    raise ValueError(f"option_type must be CE or PE, got '{option_type}'")


def otm_strikes(spot: float, strike_gap: float, option_type: str, n: int = 5) -> List[float]:
    """
    Return n OTM strikes.
    CE OTM = above spot; PE OTM = below spot.
    """
    atm = atm_strike(spot, strike_gap)
    ot  = option_type.upper()
    if ot == "CE":
        return [atm + strike_gap * i for i in range(1, n + 1)]
    if ot == "PE":
        return [atm - strike_gap * i for i in range(1, n + 1)]
    raise ValueError(f"option_type must be CE or PE, got '{option_type}'")


def strikes_around_atm(spot: float, strike_gap: float, width: int = 5) -> List[float]:
    """Return ATM ± width strikes sorted ascending."""
    atm = atm_strike(spot, strike_gap)
    return sorted(atm + strike_gap * i for i in range(-width, width + 1))


def select_strikes(
    spot: float,
    strike_gap: float,
    itm_count: int = 5,
    otm_count: int = 5,
) -> Tuple[float, List[float], List[float], List[float], List[float]]:
    """Return (atm, ce_itm, ce_otm, pe_itm, pe_otm)."""
    return (
        atm_strike(spot, strike_gap),
        itm_strikes(spot, strike_gap, "CE", itm_count),
        otm_strikes(spot, strike_gap, "CE", otm_count),
        itm_strikes(spot, strike_gap, "PE", itm_count),
        otm_strikes(spot, strike_gap, "PE", otm_count),
    )
