"""
FastAPI backend for m.Stock (Mirae Asset, India)
Features:
- Login & token/session handling via official SDK (Type A)
- Place/modify/cancel orders, order/positions/holdings queries (Type A)
- OHLC / historical candles / LTP (Type B)
- Simple token cache with auto-refresh hook points

Tested on Python 3.11+. Install deps:
  pip install fastapi uvicorn httpx python-dotenv pydantic pydantic-settings mStock-TradingApi-A

Run:
  uvicorn app:app --reload --port 8080

Security notes:
- Never hardcode secrets. Use env vars or a secret manager.
- This server exposes powerful account actions. Restrict access (authN/Z, IP allowlist, TLS).
- m.Stock API keys/tokens are your responsibility.
"""
from __future__ import annotations

import os
import hmac
import hashlib
import time
from datetime import datetime
from typing import Optional, List, Dict, Any

import httpx
from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# --- Settings -----------------------------------------------------------------

class Settings(BaseSettings):
    M_API_KEY: str
    M_API_SECRET: str
    M_USERNAME: str | None = None  # Only if you intend to programmatically login
    M_PASSWORD: str | None = None
    M_ENV: str = "PROD"  # or UAT if you have access

    # FastAPI
    APP_ADMIN_TOKEN: str = Field(default_factory=lambda: os.getenv("APP_ADMIN_TOKEN", "change-me"))

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()  # will read from env/.env

# --- m.Stock endpoints (Type B market data) -----------------------------------
TYPEB_BASE = "https://api.mstock.trade/openapi/typeb"
HEADERS_COMMON = {
    "X-Mirae-Version": "1",
    # Authorization: Bearer <jwtToken>  -> set per request
    # X-PrivateKey: <api_key>           -> set per request
}

# --- m.Stock SDK (Type A trading & session) -----------------------------------
# Official Python client (PyPI: mStock-TradingApi-A) exposes MConnect for login/order APIs
try:
    from tradingapi_a.mconnect import MConnect  # type: ignore
except Exception as e:  # pragma: no cover
    MConnect = None

# --- Simple in-memory token cache (replace with Redis/DB in prod) -------------
class TokenCache(BaseModel):
    access_token: Optional[str] = None
    token_set_at: Optional[float] = None

    def is_valid(self) -> bool:
        # m.Stock tokens typically expire intraday; without exact TTL here, just check presence.
        # You can augment with real expiry once you read it from the SDK/login response.
        return bool(self.access_token)

TOKENS = TokenCache()

# --- FastAPI app ---------------------------------------------------------------
app = FastAPI(title="m.Stock Backend API", version="0.1.0")

# --- Helper: admin protection for sensitive endpoints -------------------------
from fastapi import Header

def require_admin(x_admin_token: str = Header(..., alias="X-Admin-Token")):
    if x_admin_token != settings.APP_ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid admin token")

# --- Auth / Session ------------------------------------------------------------
class StartLoginResponse(BaseModel):
    message: str
    note: str

@app.post("/auth/login", response_model=StartLoginResponse)
async def auth_login(_: None = Depends(require_admin)):
    """Programmatic login using official SDK.

    Two flows exist with brokers:
    1) Redirect-based login that yields a request_token -> exchange for access_token using api_key+checksum (preferred for user auth).
    2) Programmatic login via SDK (requires your userid/password and then generate_session).

    Here we implement flow (2) via SDK for server-side only usage.
    """
    if MConnect is None:
        raise HTTPException(status_code=500, detail="mStock SDK not installed. pip install mStock-TradingApi-A")

    m = MConnect()
    try:
        # Step 1: create a login session with your user credentials
        login_resp = m.login(settings.M_USERNAME, settings.M_PASSWORD)
        if not login_resp or login_resp.get("status") not in (True, "success", "SUCCESS"):
            raise HTTPException(status_code=401, detail=f"Login failed: {login_resp}")

        # Step 2: exchange the request token for an access token using checksum
        # SDK simplifies this: generate_session(api_key, request_token, checksum)
        request_token = login_resp.get("data", {}).get("request_token") or login_resp.get("request_token")
        if not request_token:
            raise HTTPException(status_code=500, detail="No request_token returned by login")

        checksum_raw = f"{settings.M_API_KEY}{request_token}{settings.M_API_SECRET}".encode()
        checksum = hashlib.sha256(checksum_raw).hexdigest()

        gen = m.generate_session(settings.M_API_KEY, request_token, checksum)
        access_token = gen.get("data", {}).get("access_token") or gen.get("access_token")
        if not access_token:
            raise HTTPException(status_code=500, detail=f"Failed to generate access token: {gen}")

        TOKENS.access_token = access_token
        TOKENS.token_set_at = time.time()
        return StartLoginResponse(message="Logged in & token cached", note="Access token stored in memory. Restart/login again to refresh.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Auth error: {e}")

# --- Orders (Type A via SDK) ---------------------------------------------------
class OrderRequest(BaseModel):
    tradingsymbol: str
    exchange: str = Field(example="NSE")
    transaction_type: str = Field(example="BUY")  # BUY/SELL
    order_type: str = Field(example="MARKET")     # MARKET/LIMIT/SL/SL-M etc
    quantity: int
    product: str = Field(example="CNC")
    validity: str = Field(default="DAY")
    price: float | None = 0
    trigger_price: float | None = 0

class OrderResponse(BaseModel):
    order_id: str
    raw: Dict[str, Any]

@app.post("/orders", response_model=OrderResponse)
async def place_order(body: OrderRequest, _: None = Depends(require_admin)):
    if MConnect is None:
        raise HTTPException(status_code=500, detail="mStock SDK not installed")
    if not TOKENS.is_valid():
        raise HTTPException(status_code=401, detail="Not logged in. Call /auth/login first.")

    m = MConnect()
    # SDK keeps internal session; if required, you can set tokens on the object.
    try:
        resp = m.place_order(
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
        order_id = (
            resp.get("data", {}).get("order_id")
            or resp.get("order_id")
            or "unknown"
        )
        return OrderResponse(order_id=order_id, raw=resp)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Order failed: {e}")

@app.get("/orders")
async def get_orders(_: None = Depends(require_admin)):
    if MConnect is None:
        raise HTTPException(status_code=500, detail="mStock SDK not installed")
    m = MConnect()
    try:
        return m.get_order_book()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch order book: {e}")

@app.get("/positions/net")
async def get_net_positions(_: None = Depends(require_admin)):
    if MConnect is None:
        raise HTTPException(status_code=500, detail="mStock SDK not installed")
    m = MConnect()
    try:
        return m.get_net_position()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch net positions: {e}")

@app.get("/portfolio/holdings")
async def get_holdings(_: None = Depends(require_admin)):
    if MConnect is None:
        raise HTTPException(status_code=500, detail="mStock SDK not installed")
    m = MConnect()
    try:
        return m.get_holdings()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch holdings: {e}")

# --- Market Data (Type B via REST) --------------------------------------------

def _auth_headers() -> Dict[str, str]:
    if not TOKENS.is_valid():
        raise HTTPException(status_code=401, detail="Not logged in. Call /auth/login first.")
    h = HEADERS_COMMON.copy()
    h["Authorization"] = f"Bearer {TOKENS.access_token}"
    h["X-PrivateKey"] = settings.M_API_KEY
    h["Content-Type"] = "application/json"
    return h

class OHLCQuery(BaseModel):
    mode: str = Field(default="OHLC", pattern="^(OHLC|LTP)$")
    NSE: Optional[List[str]] = None  # list of symbol tokens as strings
    BSE: Optional[List[str]] = None

@app.get("/market/ohlc")
async def market_ohlc(
    mode: str = Query("OHLC", pattern="^(OHLC|LTP)$"),
    nse: Optional[str] = Query(None, description="Comma-separated tokens for NSE"),
    bse: Optional[str] = Query(None, description="Comma-separated tokens for BSE"),
):
    """Fetch OHLC/LTP for tokens using Type B endpoint.
    Convert tradingsymbol -> token using Script Master first (build and cache offline).
    """
    payload = {
        "mode": mode,
        "exchangeTokens": {}
    }
    if nse:
        payload["exchangeTokens"]["NSE"] = [t.strip() for t in nse.split(",") if t.strip()]
    if bse:
        payload["exchangeTokens"]["BSE"] = [t.strip() for t in bse.split(",") if t.strip()]

    if not payload["exchangeTokens"]:
        raise HTTPException(status_code=400, detail="Pass at least one of ?nse= or ?bse= with tokens")

    url = f"{TYPEB_BASE}/instruments/quote"
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, headers=_auth_headers(), json=payload)
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        return JSONResponse(r.json())

@app.get("/market/historical")
async def market_historical(
    exchange: str = Query(..., examples=["NSE", "BSE", "NFO", "BFO"]),
    symboltoken: str = Query(..., description="Instrument token from script master"),
    interval: str = Query(..., examples=[
        "ONE_MINUTE", "THREE_MINUTE", "FIVE_MINUTE", "TEN_MINUTE", "FIFTEEN_MINUTE",
        "THIRTY_MINUTE", "ONE_HOUR", "ONE_DAY", "ONE_WEEK", "ONE_MONTH"
    ]),
    fromdate: str = Query(..., description="YYYY-MM-DD HH:MM in exchange timezone"),
    todate: str = Query(..., description="YYYY-MM-DD HH:MM in exchange timezone"),
):
    url = f"{TYPEB_BASE}/instruments/historical"
    payload = {
        "exchange": exchange,
        "symboltoken": symboltoken,
        "interval": interval,
        "fromdate": fromdate,
        "todate": todate,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, headers=_auth_headers(), json=payload)
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        return JSONResponse(r.json())

@app.get("/market/script-master")
async def script_master():
    """Fetch the latest instrument master (CSV-like JSON map) and return it.
    In production, download and cache this to map symbols <-> tokens.
    """
    url = f"{TYPEB_BASE}/instruments/OpenAPIScripMaster"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, headers=_auth_headers())
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        return JSONResponse(r.json())

# --- Health -------------------------------------------------------------------
@app.get("/healthz")
async def healthz():
    return {"ok": True, "ts": datetime.utcnow().isoformat()}
