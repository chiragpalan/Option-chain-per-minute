"""expiry_handler.py — NSE weekly/monthly expiry date management."""
import logging
from datetime import date, timedelta
from typing import List, Tuple

logger = logging.getLogger(__name__)

_THURSDAY = 3


def _next_weekday(ref: date, weekday: int) -> date:
    days_ahead = (weekday - ref.weekday()) % 7
    return ref + timedelta(days=days_ahead)


def _last_thursday_of_month(year: int, month: int) -> date:
    first_of_next = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last_day = first_of_next - timedelta(days=1)
    days_back = (last_day.weekday() - _THURSDAY) % 7
    return last_day - timedelta(days=days_back)


def current_weekly_expiry(ref: date = None) -> date:
    """Next Thursday on or after ref (defaults to today)."""
    return _next_weekday(ref or date.today(), _THURSDAY)


def current_monthly_expiry(ref: date = None) -> date:
    """Last Thursday of the current month; rolls to next month if past."""
    ref = ref or date.today()
    monthly = _last_thursday_of_month(ref.year, ref.month)
    if ref > monthly:
        nm = ref.month + 1 if ref.month < 12 else 1
        ny = ref.year if ref.month < 12 else ref.year + 1
        monthly = _last_thursday_of_month(ny, nm)
    return monthly


def next_expiries(n: int = 4, ref: date = None) -> List[date]:
    """Return next `n` weekly expiry dates."""
    ref = ref or date.today()
    expiries, cursor = [], _next_weekday(ref, _THURSDAY)
    while len(expiries) < n:
        expiries.append(cursor)
        cursor += timedelta(weeks=1)
    return expiries


def all_expiries(ref: date = None) -> Tuple[date, date, List[date]]:
    """Return (weekly, monthly, next_4_weeklies)."""
    return current_weekly_expiry(ref), current_monthly_expiry(ref), next_expiries(4, ref)
