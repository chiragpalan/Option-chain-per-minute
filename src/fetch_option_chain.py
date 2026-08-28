"""
fetch_option_chain.py — Fetch NIFTY50 option chain snapshot and store to DB.
Run: python -m src.fetch_option_chain
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

import pytz
from dotenv import load_dotenv

load_dotenv()

OPTION_DB = os.getenv("OPTION_DB", "data/option_chain.db")
LOG_DIR   = os.getenv("LOG_DIR",   "data/logs")

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs("data", exist_ok=True)

_fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
_fh  = RotatingFileHandler(os.path.join(LOG_DIR, "app.log"), maxBytes=5*1024*1024, backupCount=3)
_fh.setFormatter(_fmt)
_ch  = logging.StreamHandler(sys.stdout)
_ch.setFormatter(_fmt)
logging.basicConfig(level=logging.INFO, handlers=[_fh, _ch])
logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")


def main() -> None:
    from src.option_chain.nse_scraper import fetch_option_chain, get_expiry_dates, get_spot
    from src.database import insert_option_data

    logger.info("=== NIFTY50 option chain fetch starting ===")

    spot = get_spot("NIFTY50") or 0.0
    if spot <= 0:
        logger.error("Could not fetch spot price — aborting")
        sys.exit(1)

    expiries = get_expiry_dates("NIFTY50")
    if not expiries:
        logger.error("No expiries found — aborting")
        sys.exit(1)

    expiries = expiries[:4]
    logger.info("Spot: %.2f  Expiries: %s", spot, expiries)

    df = fetch_option_chain("NIFTY50", None, spot)
    if df.empty:
        logger.error("Empty option chain — aborting")
        sys.exit(1)

    insert_option_data(OPTION_DB, "NIFTY50", df, spot)
    logger.info("=== Done ===")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("Fatal error")
        sys.exit(1)
