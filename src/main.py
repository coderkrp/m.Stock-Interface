from __future__ import annotations
import hashlib
import time
import sys
import csv
from datetime import datetime, timezone
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends, WebSocket, Header, Query
from settings import settings
from security import limiter
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from slowapi.middleware import SlowAPIMiddleware
from security import ThrottlingMiddleware

from models import (
    LoginResponse,
    SessionRequest,
    OrderRequest,
    ModifyOrderRequest,
    CancelOrderRequest,
    OrderStatusRequest,
    LTPRequest,
    OHLCRequest,
    HistoricalChartRequest,
    LoserGainerRequest,
    PersistentTokenCache,
)

# --- Logging -------------------------------------------------------------------
import logging
from logging.handlers import TimedRotatingFileHandler
import json

"""
FastAPI backend for m.Stock (Mirae Asset, India)
Type A Only Version
----------------------------------------
Features:
- Two-step login & session handling via Type A SDK (OTP flow)
- Place orders (extendable for modify/cancel)
- WebSocket endpoints for live ticks and order/trade updates (Type A)
- Persistent token store (JSON file) with daily auto-expiry
- Structured JSON logging (stdout + rotating file)

Run:
  uvicorn src.main:app --reload --port 8080

Security notes:
- Never hardcode secrets. Use env vars or a secret manager.
- This server exposes powerful account actions. Restrict access (authN/Z, IP allowlist, TLS).
"""

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "backend.log"


class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "event": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_entry["exc_info"] = self.formatException(record.exc_info)
        if hasattr(record, "method"):
            log_entry["method"] = record.method
        if hasattr(record, "path"):
            log_entry["path"] = record.path
        if hasattr(record, "status_code"):
            log_entry["status_code"] = record.status_code
        if hasattr(record, "duration_ms"):
            log_entry["duration_ms"] = record.duration_ms
        return json.dumps(log_entry)


console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(JsonFormatter())

file_handler = TimedRotatingFileHandler(
    LOG_FILE, when="midnight", interval=1, backupCount=7, encoding="utf-8"
)
file_handler.setFormatter(JsonFormatter())

logger = logging.getLogger("mstock-backend")
logger.setLevel(logging.INFO)
logger.addHandler(console_handler)
logger.addHandler(file_handler)
logger.propagate = False


# --- m.Stock SDK ---------------------------------------------------------------
try:
    from tradingapi_a.mconnect import MConnect  # type: ignore
    from tradingapi_a.mticker import MTicker  # type: ignore
except Exception as e:
    logger.error("Failed to import mStock SDK", exc_info=e)
    MConnect = None
    MTicker = None

# --- Global MConnect handler ---------------------------------------------------
mconnect: MConnect | None = None

# --- Persistent Token Cache ----------------------------------------------------

TOKENS = PersistentTokenCache()

@asynccontextmanager
async def lifespan(app: FastAPI):
    global mconnect
    if MConnect is None:
        logger.error("mStock SDK not installed, cannot init MConnect")
        yield
        return
    try:
        mconnect = MConnect(api_key=settings.M_API_KEY)
        if TOKENS.is_valid():
            mconnect.set_access_token(TOKENS.get_token())
            logger.info("MConnect initialized (with session)")
        else:
            logger.info("MConnect initialized (without session)")
    except Exception as e:
        logger.error("Failed to initialize MConnect", exc_info=e)
    yield


# --- FastAPI app ---------------------------------------------------------------
app = FastAPI(title="m.Stock Backend API (Type A Only)", version="1.0.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(ThrottlingMiddleware, logger=logger)

# --- Admin Token Protection ----------------------------------------------------
def require_admin(x_admin_token: str = Header(..., alias="X-Admin-Token")):
    if x_admin_token != settings.APP_ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid admin token")

@app.post("/auth/login", response_model=LoginResponse)
async def auth_login(_: None = Depends(require_admin)):
    global mconnect
    try:
        login_resp = mconnect.login(settings.M_USERNAME, settings.M_PASSWORD)
        if hasattr(login_resp, "json"):
            login_resp = login_resp.json()
        if not login_resp or login_resp.get("status") != "success":
            raise HTTPException(status_code=401, detail=f"Login failed: {login_resp}")
        return LoginResponse(
            message="OTP sent",
            note="Check your registered mobile/email, then call /auth/session with your OTP.",
        )
    except Exception as e:
        logger.error("Auth login error", exc_info=e)
        raise HTTPException(status_code=500, detail=f"Auth error: {e}")


@app.post("/auth/session")
async def auth_session(body: SessionRequest, _: None = Depends(require_admin)):
    global mconnect
    try:
        raw = f"{settings.M_API_KEY}{body.otp}{settings.M_API_SECRET}"
        checksum = hashlib.sha256(raw.encode()).hexdigest()
        gen = mconnect.generate_session(settings.M_API_KEY, body.otp, checksum)
        if hasattr(gen, "json"):
            gen = gen.json()

        access_token = gen.get("data", {}).get("access_token") or gen.get(
            "access_token"
        )
        if not access_token:
            raise HTTPException(
                status_code=500, detail=f"Failed to fetch access token: {gen}"
            )

        TOKENS.access_token = access_token
        TOKENS.token_set_at = time.time()
        TOKENS.save()
        # ✅ Set access token into global handler

        logger.info("New session established")

        logger.info(f"The token extracted by me: {access_token}")
        logger.info(f"The token sent by mconnect: {mconnect.access_token}")

        return {"message": "Session established", "data": gen.get("data", gen)}
    except Exception as e:
        logger.error("Auth session error", exc_info=e)
        raise HTTPException(status_code=500, detail=f"Session error: {e}")


# --- Place Orders --------------------------------------------------------------
@app.post("/orders")
async def place_order(body: OrderRequest, _: None = Depends(require_admin)):
    global mconnect
    if not TOKENS.is_valid():
        raise HTTPException(
            status_code=401, detail="Session expired. Please log in again."
        )

    try:
        resp = mconnect.place_order(
            _tradingsymbol=body.tradingsymbol,
            _exchange=body.exchange,
            _transaction_type=body.transaction_type,
            _order_type=body.order_type,
            _quantity=str(body.quantity),
            _product=body.product,
            _validity=body.validity,
            _price=str(body.price or 0),
            _trigger_price=str(body.trigger_price or 0),
        )
        return resp
    except Exception as e:
        logger.error("Order placement failed", exc_info=e)
        raise HTTPException(status_code=400, detail=f"Order failed: {e}")


# --- Modify Pending Order ------------------------------------------------------
@app.post("/orders/modify")
async def modify_order(body: ModifyOrderRequest, _: None = Depends(require_admin)):
    global mconnect
    if not TOKENS.is_valid():
        raise HTTPException(
            status_code=401, detail="Session expired. Please log in again."
        )
    try:
        resp = mconnect.modify_order(
            order_id=body.order_id,
            _quantity=str(body.quantity) if body.quantity else None,
            _price=str(body.price) if body.price else None,
            _trigger_price=str(body.trigger_price) if body.trigger_price else None,
            _order_type=body.order_type,
            _validity=body.validity,
            _disclosed_quantity=(
                body.disclosed_quantity if body.disclosed_quantity else None
            ),
        )
        return resp
    except Exception as e:
        logger.error("Order modification failed", exc_info=e)
        raise HTTPException(status_code=400, detail="Order modification failed")


# --- Cancel Pending Order ------------------------------------------------------
@app.post("/orders/cancel")
async def cancel_order(body: CancelOrderRequest, _: None = Depends(require_admin)):
    global mconnect
    if not TOKENS.is_valid():
        raise HTTPException(
            status_code=401, detail="Session expired. Please log in again."
        )
    try:
        resp = mconnect.cancel_all()
        return resp
    except Exception as e:
        logger.error("Order cancellation failed", exc_info=e)
        raise HTTPException(status_code=400, detail="Order cancellation failed")


# --- Orderbook (all orders) ----------------------------------------------------
@app.get("/orders")
async def get_orders(_: None = Depends(require_admin)):
    if not TOKENS.is_valid():
        raise HTTPException(
            status_code=401, detail="Session expired. Please log in again."
        )
    global mconnect
    try:
        resp = mconnect.get_order_book()
        return resp
    except Exception as e:
        logger.error("Fetching orderbook failed", exc_info=e)
        raise HTTPException(status_code=400, detail="Could not fetch orderbook")


# --- Tradebook (Trades placed between 2 dates) ----------------------------------------------------
@app.get("/trades")
async def get_trades(
    fromDate: datetime = Query(...),
    toDate: datetime = Query(...),
    _: None = Depends(require_admin),
):
    if not TOKENS.is_valid():
        raise HTTPException(
            status_code=401, detail="Session expired. Please log in again."
        )
    global mconnect
    try:
        resp = mconnect.get_trade_history(_fromDate=fromDate, _toDate=toDate)
        return resp
    except Exception as e:
        logger.error("Fetching tradebook failed", exc_info=e)
        raise HTTPException(status_code=400, detail="Could not fetch tradebook")


# --- Order Status (by ID) ------------------------------------------------------
@app.post("/orders/status")
async def order_status(body: OrderStatusRequest, _: None = Depends(require_admin)):
    if not TOKENS.is_valid():
        raise HTTPException(
            status_code=401, detail="Session expired. Please log in again."
        )
    global mconnect
    try:
        resp = mconnect.get_order_details(
            _order_id=body.order_id, _segment=body.segment
        )
        return resp
    except Exception as e:
        logger.error("Order status fetch failed", exc_info=e)
        raise HTTPException(status_code=400, detail="Could not fetch order status")


@app.post("/market/ltp")
async def get_ltp(body: LTPRequest, _: None = Depends(require_admin)):
    if not TOKENS.is_valid():
        raise HTTPException(
            status_code=401, detail="Session expired. Please log in again."
        )
    global mconnect
    try:
        resp = mconnect.get_ltp(body.instruments)
        logger.info(f"Response received : {resp}")
        return resp
    except Exception as e:
        logger.error("Fetching LTP failed", exc_info=e)
        raise HTTPException(status_code=400, detail="Could not fetch LTP")


@app.post("/market/ohlc")
async def get_ohlc(body: OHLCRequest, _: None = Depends(require_admin)):
    if not TOKENS.is_valid():
        raise HTTPException(status_code=401, detail="Session expired")
    global mconnect
    try:
        resp = mconnect.get_ohlc(body.instruments)
        return resp.json() if hasattr(resp, "json") else resp
    except Exception:
        TOKENS.clear()
        raise HTTPException(
            status_code=401, detail="Session expired. Please log in again."
        )
    except Exception as e:
        logger.error("Fetching OHLC failed", exc_info=e)
        raise HTTPException(status_code=400, detail="Could not fetch OHLC")


@app.post("/market/historical")
async def get_historical_chart(
    body: HistoricalChartRequest, _: None = Depends(require_admin)
):
    if not TOKENS.is_valid():
        raise HTTPException(status_code=401, detail="Session expired")
    global mconnect
    try:
        resp = mconnect.get_historical_chart(
            _security_token=body.security_token,
            _interval=body.interval,
            _fromDate=body.from_date.isoformat(),
            _toDate=body.to_date.isoformat(),
        )
        return resp.json() if hasattr(resp, "json") else resp
    except Exception as e:
        logger.error("Fetching historical chart failed", exc_info=e)
        raise HTTPException(status_code=400, detail="Could not fetch historical chart")


@app.get("/market/instruments")
async def get_instruments(_: None = Depends(require_admin)):
    if not TOKENS.is_valid():
        raise HTTPException(status_code=401, detail="Session expired")
    global mconnect
    try:
        resp = mconnect.get_instruments()

        # Decode bytes → str
        text_data = (
            resp.decode("utf-8") if isinstance(resp, (bytes, bytearray)) else str(resp)
        )

        # Split lines and process CSV
        split_data = text_data.split("\n")
        data = [row.strip().split(",") for row in split_data]

        # Write csv file for reference
        # Open the file in write mode
        with open("instrument_scrip_master.csv", mode="w") as file:
            # Create a csv.writer object
            writer = csv.writer(file, delimiter=",")
            # Write data to the CSV file
            for row in data:
                writer.writerow(row)
        # return resp.json() if hasattr(resp, "json") else resp
        return {"message": "Instrument file saved", "rows": len(data)}
    except Exception as e:
        logger.error("Fetching instruments failed", exc_info=e)
        raise HTTPException(status_code=400, detail="Could not fetch instruments")


@app.post("/market/loser_gainer")
async def loser_gainer(body: LoserGainerRequest, _: None = Depends(require_admin)):
    if not TOKENS.is_valid():
        raise HTTPException(status_code=401, detail="Session expired")
    global mconnect
    try:
        resp = mconnect.loser_gainer(
            _Exchange=body.Exchange,
            _SecurityIdCode=body.SecurityIdCode,
            _segment=body.segment,
        )
        return resp.json() if hasattr(resp, "json") else resp
    except Exception as e:
        logger.error("Fetching losers/gainers failed", exc_info=e)
        raise HTTPException(status_code=400, detail="Could not fetch losers/gainers")


# --- WebSocket Endpoints (Ticks / Orders) --------------------------------------
active_tick_clients: list[WebSocket] = []
active_order_clients: list[WebSocket] = []

# TODO: implement MTicker integration here for live streaming


# --- Health --------------------------------------------------------------------
@app.get("/healthz")
async def healthz():
    return {"ok": True, "ts": datetime.now(timezone.utc).isoformat()}
