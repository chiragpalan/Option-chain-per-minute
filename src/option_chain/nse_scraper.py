"""
nse_scraper.py
Live     : NSE option chain API (per-minute snapshots)
Fallback : nselib fno_bhav_copy (EOD)
Greeks   : py_vollib (Black-Scholes) — only when vollib is installed AND spot > 0
"""
import logging
import math
import requests
from datetime import date, datetime, timedelta
from typing import List, Optional

import pandas as pd
from scipy.stats import norm

logger = logging.getLogger(__name__)

_RISK_FREE = 0.065
_SYM_MAP = {"NIFTY50": "NIFTY"}

_NSE_OC_TYPE = {"NIFTY50": "Indices"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_float(val) -> Optional[float]:
    """Parse a value to float, return None if missing/invalid."""
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    try:
        f = float(val)
        return f if pd.notna(f) else None
    except (TypeError, ValueError):
        return None


def _to_float_nonneg(val) -> Optional[float]:
    """Parse to float, return None if missing or negative."""
    f = _to_float(val)
    return f if (f is not None and f >= 0) else None


def _validate_record(row: dict, source: str) -> bool:
    """
    Validate a parsed option chain record.
    Logs and returns False if invalid.
    """
    strike = row.get("strike")
    if strike is None or not isinstance(strike, (int, float)) or strike <= 0:
        logger.debug("[%s] Skipping record: invalid strike=%s", source, strike)
        return False
    if row.get("option_type") not in ("CE", "PE"):
        logger.debug("[%s] Skipping record: invalid option_type=%s", source, row.get("option_type"))
        return False
    if not row.get("expiry"):
        logger.debug("[%s] Skipping record: missing expiry", source)
        return False
    spot = row.get("spot")
    if spot is None or spot <= 0:
        logger.debug("[%s] Skipping record: invalid spot=%s strike=%s", source, spot, strike)
        return False
    ltp = row.get("ltp")
    if ltp is None or ltp <= 0:
        logger.debug("[%s] Skipping record: zero/invalid ltp=%s strike=%s", source, ltp, strike)
        return False
    oi = row.get("oi")
    if oi is not None and oi < 0:
        logger.debug("[%s] Skipping record: negative oi=%s strike=%s", source, oi, strike)
        return False
    vol = row.get("volume")
    if vol is not None and vol < 0:
        logger.debug("[%s] Skipping record: negative volume=%s strike=%s", source, vol, strike)
        return False
    return True


# ── Greeks (pure BS, no external dependency) ─────────────────────────────────

def _bs_price(flag: str, S: float, K: float, t: float, iv: float) -> float:
    r = _RISK_FREE
    d1 = (math.log(S / K) + (r + 0.5 * iv ** 2) * t) / (iv * math.sqrt(t))
    d2 = d1 - iv * math.sqrt(t)
    if flag == "c":
        return S * norm.cdf(d1) - K * math.exp(-r * t) * norm.cdf(d2)
    return K * math.exp(-r * t) * norm.cdf(-d2) - S * norm.cdf(-d1)


def _greeks(flag: str, S: float, K: float, t: float, iv: float) -> dict:
    null = {"delta": None, "gamma": None, "theta": None, "vega": None, "rho": None}
    if not (S > 0 and K > 0 and t > 0 and iv > 0):
        return null
    try:
        r  = _RISK_FREE
        d1 = (math.log(S / K) + (r + 0.5 * iv ** 2) * t) / (iv * math.sqrt(t))
        d2 = d1 - iv * math.sqrt(t)
        pdf_d1 = norm.pdf(d1)
        gamma  = round(pdf_d1 / (S * iv * math.sqrt(t)), 6)
        vega   = round(S * pdf_d1 * math.sqrt(t) / 100, 4)  # per 1% IV move
        if flag == "c":
            delta = round(norm.cdf(d1), 4)
            theta = round((-S * pdf_d1 * iv / (2 * math.sqrt(t)) - r * K * math.exp(-r * t) * norm.cdf(d2)) / 365, 4)
            rho   = round(K * t * math.exp(-r * t) * norm.cdf(d2) / 100, 4)
        else:
            delta = round(norm.cdf(d1) - 1, 4)
            theta = round((-S * pdf_d1 * iv / (2 * math.sqrt(t)) + r * K * math.exp(-r * t) * norm.cdf(-d2)) / 365, 4)
            rho   = round(-K * t * math.exp(-r * t) * norm.cdf(-d2) / 100, 4)
        return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega, "rho": rho}
    except Exception as e:
        logger.debug("[greeks] failed: %s", e)
        return null


def _iv_from_price(flag: str, S: float, K: float, t: float, price: float) -> Optional[float]:
    """Brent's method IV solver — no external dependency."""
    if not (S > 0 and K > 0 and t > 0 and price > 0):
        return None
    try:
        lo, hi = 0.001, 10.0
        for _ in range(100):
            mid = (lo + hi) / 2
            if hi - lo < 1e-5:
                break
            if _bs_price(flag, S, K, t, mid) > price:
                hi = mid
            else:
                lo = mid
        iv = (lo + hi) / 2
        return round(iv, 4) if 0.001 < iv < 10 else None
    except Exception as e:
        logger.debug("[_iv_from_price] failed: %s", e)
        return None


# ── Live NSE option chain ─────────────────────────────────────────────────────

# NIFTY50 uses Indices type
_NSE_OC_V3_URL = "https://www.nseindia.com/api/option-chain-v3?type={typ}&symbol={sym}&expiry={expiry}"
_NSE_OC_ORIGIN = "https://www.nseindia.com/option-chain"


def _nse_session() -> requests.Session:
    """Cookie-warmed session for NSE API calls."""
    from nselib.libutil import default_header, header as nselib_header
    s = requests.Session()
    s.headers.update(nselib_header)
    try:
        resp = s.get(_NSE_OC_ORIGIN, headers=default_header, timeout=10)
        s.cookies.update(resp.cookies)
    except Exception:
        pass
    return s


def _fetch_live_option_chain(symbol: str, spot: float) -> pd.DataFrame:
    """
    Fetch live NSE option chain via option-chain-v3 API (one call per expiry).
    Returns combined DataFrame for all expiries so caller can filter by expiry.
    IV taken from API 'impliedVolatility' field (already in %).
    """
    from nselib.libutil import nse_urlfetch
    nse_sym  = _SYM_MAP.get(symbol, "NIFTY")
    oc_type  = _NSE_OC_TYPE.get(symbol, "Indices")

    # Get expiry list first
    try:
        expiries = get_expiry_dates(symbol)[:4]
    except Exception:
        expiries = []
    if not expiries:
        logger.warning("[%s] No expiries available for live fetch", symbol)
        return pd.DataFrame()

    all_rows: list = []
    skipped = 0

    for expiry in expiries:
        url = _NSE_OC_V3_URL.format(typ=oc_type, sym=nse_sym, expiry=expiry)
        try:
            resp    = nse_urlfetch(url, origin_url=_NSE_OC_ORIGIN)
            data    = resp.json()
            records = data.get("records", {})
            api_spot = _to_float(records.get("underlyingValue"))
            use_spot = api_spot if (api_spot and api_spot > 0) else spot
            raw      = records.get("data", [])
            logger.info("[%s] v3 API expiry=%s status=%d spot=%.2f rows=%d",
                        symbol, expiry, resp.status_code, use_spot, len(raw))
        except Exception as e:
            logger.warning("[%s] v3 API failed for expiry=%s: %s", symbol, expiry, e)
            continue

        try:
            tte = max((datetime.strptime(expiry, "%d-%b-%Y").date() - date.today()).days, 0.5) / 365.0
        except Exception:
            tte = None

        for item in raw:
            strike = _to_float(item.get("strikePrice"))
            if strike is None:
                skipped += 1
                continue
            item_expiry = item.get("expiryDates", [expiry])
            # expiryDates is a list; use the requested expiry
            for otype, key in (("CE", "CE"), ("PE", "PE")):
                d = item.get(key, {})
                if not d:
                    continue
                ltp    = _to_float_nonneg(d.get("lastPrice"))
                oi     = _to_float_nonneg(d.get("openInterest"))
                chg_oi = _to_float(d.get("changeinOpenInterest"))
                vol    = _to_float_nonneg(d.get("totalTradedVolume"))
                iv_api = _to_float(d.get("impliedVolatility"))
                iv_pct = iv_api if (iv_api is not None and iv_api > 0) else None
                # fallback: compute IV from LTP if API didn't provide it
                if iv_pct is None and ltp and ltp > 0 and tte:
                    iv_dec_fb = _iv_from_price("c" if otype == "CE" else "p", use_spot, strike, tte, ltp)
                    iv_pct = round(iv_dec_fb * 100, 2) if iv_dec_fb else None
                iv_dec = iv_pct / 100 if iv_pct else None
                flag   = "c" if otype == "CE" else "p"
                greeks = (
                    _greeks(flag, use_spot, strike, tte, iv_dec)
                    if (iv_dec and tte and use_spot > 0 and strike and strike > 0)
                    else {"delta": None, "gamma": None, "theta": None, "vega": None, "rho": None}
                )
                record = {
                    "expiry": expiry, "strike": strike, "option_type": otype,
                    "spot": use_spot, "ltp": ltp if ltp is not None else 0.0,
                    "open":  _to_float_nonneg(d.get("openPrice")),
                    "high":  _to_float_nonneg(d.get("highPrice")),
                    "low":   _to_float_nonneg(d.get("lowPrice")),
                    "close": _to_float_nonneg(d.get("prevClose")),
                    "volume": vol, "oi": oi, "oi_chg": chg_oi, "iv": iv_pct,
                    **greeks,
                }
                if _validate_record(record, symbol):
                    all_rows.append(record)
                else:
                    skipped += 1

    logger.info("[%s] Live total: %d rows written, %d skipped", symbol, len(all_rows), skipped)
    return pd.DataFrame(all_rows)


# ── Bhav copy parser ──────────────────────────────────────────────────────────

def _parse_bhav(df: pd.DataFrame, nse_sym: str, expiry_str: str, spot: float) -> pd.DataFrame:
    """
    Parse nselib fno_bhav_copy into standardised option chain format.
    - IV computed via Black-Scholes implied_volatility from LTP; NULL if unavailable.
    - OHLC mapped from bhav fields; NULL if field is NaN.
    - Greeks computed only when IV available; NULL otherwise.
    - No hardcoded default values.
    """
    try:
        exp_date = datetime.strptime(expiry_str, "%d-%b-%Y").date()
    except Exception:
        exp_date = _next_thursday(date.today())
    exp_iso = exp_date.strftime("%Y-%m-%d")

    mask = (
        (df["TckrSymb"] == nse_sym) &
        (df["XpryDt"].astype(str).str[:10] == exp_iso) &
        (df["OptnTp"].notna()) &
        (df["StrkPric"].notna())
    )
    sub = df[mask].copy()
    if sub.empty:
        logger.warning("[%s] No bhav rows for expiry %s", nse_sym, exp_iso)
        return pd.DataFrame()

    tte = max((exp_date - date.today()).days, 1) / 365.0
    rows = []
    skipped = 0

    for _, r in sub.iterrows():
        otype = str(r["OptnTp"]).upper()
        flag  = "c" if otype == "CE" else "p"
        K     = _to_float(r["StrkPric"])

        ltp_val  = _to_float(r.get("LastPric"))
        cls_val  = _to_float(r.get("ClsPric"))
        ltp      = ltp_val if (ltp_val is not None and ltp_val > 0) else cls_val

        oi       = _to_float_nonneg(r.get("OpnIntrst"))
        oi_chg   = _to_float(r.get("ChngInOpnIntrst"))
        vol      = _to_float_nonneg(r.get("TtlTradgVol"))

        # OHLC — use pd.notna to correctly handle NaN from pandas
        open_  = _to_float(r.get("OpnPric"))
        high_  = _to_float(r.get("HghPric"))
        low_   = _to_float(r.get("LwPric"))
        close_ = _to_float(r.get("ClsPric"))

        # IV: compute from LTP via Black-Scholes; NULL if unavailable — no hardcoded default
        iv_dec = _iv_from_price(flag, spot, K, tte, ltp) if (ltp and ltp > 0 and spot > 0 and K) else None
        iv_pct = round(iv_dec * 100, 2) if iv_dec else None
        logger.debug("[%s] strike=%s %s ltp=%.2f computed_IV=%s", nse_sym, K, otype, ltp or 0, iv_pct)

        # Greeks: only when IV available
        greeks = (
            _greeks(flag, spot, K, tte, iv_dec)
            if (iv_dec and spot > 0 and K)
            else {"delta": None, "gamma": None, "theta": None, "vega": None, "rho": None}
        )

        record = {
            "expiry":      expiry_str,
            "strike":      K,
            "option_type": otype,
            "spot":        spot,
            "open":        open_,
            "high":        high_,
            "low":         low_,
            "close":       close_,
            "ltp":         ltp if ltp is not None else 0.0,
            "volume":      vol,
            "oi":          oi,
            "oi_chg":      oi_chg,
            "iv":          iv_pct,   # % or None — never hardcoded 18
            **greeks,
        }
        if _validate_record(record, nse_sym):
            rows.append(record)
        else:
            skipped += 1

    result = pd.DataFrame(rows)
    logger.info("[%s] Bhav: %d rows parsed, %d written, %d skipped (expiry=%s spot=%.2f)",
                nse_sym, len(sub), len(rows), skipped, expiry_str, spot)
    return result


# ── Expiry helpers ────────────────────────────────────────────────────────────

def get_expiry_dates(symbol: str = "NIFTY50") -> List[str]:
    nse_sym = _SYM_MAP.get(symbol, "NIFTY")
    try:
        from nselib import derivatives
        data     = derivatives.expiry_dates_option_index()
        expiries = data.get(nse_sym, [])
        if expiries:
            logger.info("[%s] nselib expiries: %s", symbol, expiries[:4])
            return expiries
    except Exception:
        logger.warning("[%s] nselib expiry fetch failed", symbol, exc_info=True)
    result, cursor = [], _next_thursday(date.today())
    for _ in range(6):
        result.append(cursor.strftime("%d-%b-%Y"))
        cursor += timedelta(weeks=1)
    return result


def _expiries_from_bhav(nse_sym: str) -> List[str]:
    try:
        from nselib import derivatives
        import concurrent.futures
        for i in range(5):
            d = date.today() - timedelta(days=i)
            if d.weekday() >= 5:
                continue
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    future = ex.submit(derivatives.fno_bhav_copy, d.strftime("%d-%m-%Y"))
                    bhav = future.result(timeout=20)
            except concurrent.futures.TimeoutError:
                logger.warning("bhav expiry fetch timed out for %s", d)
                continue
            except Exception:
                continue
            if bhav is None or bhav.empty:
                continue
            sub = bhav[(bhav["TckrSymb"] == nse_sym) & (bhav["OptnTp"].notna())]
            if sub.empty:
                continue
            raw = sorted(sub["XpryDt"].astype(str).str[:10].unique())
            result = []
            for r in raw:
                try:
                    result.append(datetime.strptime(r, "%Y-%m-%d").strftime("%d-%b-%Y"))
                except Exception:
                    pass
            if result:
                logger.info("[%s] bhav expiries: %s", nse_sym, result)
                return result
    except Exception:
        logger.warning("bhav expiry fetch failed for %s", nse_sym, exc_info=True)
    return []


def _next_thursday(ref: date) -> date:
    days = (3 - ref.weekday()) % 7
    return ref + timedelta(days=max(days, 1))


def _get_last_trade_date() -> str:
    d = date.today()
    if d.weekday() >= 5:
        d -= timedelta(days=d.weekday() - 4)
    return d.strftime("%d-%m-%Y")


# ── fetch_option_chain ────────────────────────────────────────────────────────

def fetch_option_chain(
    symbol: str,
    expiry: str,
    spot: float,
    trade_date: Optional[str] = None,
) -> pd.DataFrame:
    df_live = _fetch_live_option_chain(symbol, spot)
    if not df_live.empty:
        df_exp = df_live[df_live["expiry"] == expiry].copy() if expiry else df_live.copy()
        if not df_exp.empty:
            return df_exp

    logger.info("[%s] Falling back to bhav copy", symbol)
    nse_sym = _SYM_MAP.get(symbol, "NIFTY")
    if not trade_date:
        d = date.today()
        if d.weekday() >= 5:
            d -= timedelta(days=d.weekday() - 4)
        for i in range(5):
            candidate = d - timedelta(days=i)
            if candidate.weekday() < 5:
                trade_date = candidate.strftime("%d-%m-%Y")
                break

    from nselib import derivatives
    import concurrent.futures
    bhav = None
    for i in range(5):
        try_date = (datetime.strptime(trade_date, "%d-%m-%Y").date() - timedelta(days=i))
        if try_date.weekday() >= 5:
            continue
        ds = try_date.strftime("%d-%m-%Y")
        try:
            logger.info("[%s] Trying bhav copy for %s", symbol, ds)
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(derivatives.fno_bhav_copy, ds)
                b = future.result(timeout=20)
            if b is not None and not b.empty:
                bhav = b
                break
        except concurrent.futures.TimeoutError:
            logger.warning("[%s] bhav fetch timed out for %s — skipping", symbol, ds)
            break
        except Exception:
            logger.warning("[%s] bhav failed for %s", symbol, ds, exc_info=True)

    if bhav is not None:
        df = _parse_bhav(bhav, nse_sym, expiry, spot)
        if not df.empty:
            return df

    logger.error("[%s] All option chain sources failed", symbol)
    return pd.DataFrame()


def get_spot(symbol: str) -> Optional[float]:
    # Try 1: yfinance (works reliably from GitHub Actions)
    try:
        import yfinance as yf
        ticker = yf.Ticker("^NSEI")
        price = ticker.fast_info.get("lastPrice") or ticker.fast_info.get("regularMarketPrice")
        if price and float(price) > 0:
            logger.info("[get_spot] yfinance spot: %.2f", float(price))
            return float(price)
    except Exception as e:
        logger.warning("[get_spot] yfinance failed: %s", e)

    # Try 2: nselib capital_market
    try:
        from nselib import capital_market
        data = capital_market.index_data()
        if data is not None and not data.empty:
            row = data[data["indexSymbol"] == "NIFTY 50"] if "indexSymbol" in data.columns else pd.DataFrame()
            if not row.empty:
                return float(row.iloc[0]["last"])
    except Exception as e:
        logger.warning("[get_spot] nselib failed: %s", e)

    return None
