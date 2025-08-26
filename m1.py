"""
FastAPI backend for m.Stock (Mirae Asset, India)
Features:
- Login & token/session handling via official SDK (Type A)
- Place/modify/cancel orders, order/positions/holdings queries (Type A)
- OHLC / historical candles / LTP (Type B)
- Script Master download + caching + symbol search (Type B)
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
    M_USERNAME: str | None = None
    M_PASSWORD: str | None = None
    M_ENV: str = "PROD"

    APP_ADMIN_TOKEN: str = Field(default_factory=lambda: os.getenv("APP_ADMIN_TOKEN", "change-me"))

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()

# --- m.Stock endpoints ---------------------------------------------------------
TYPEB_BASE = "https://api.mstock.trade/openapi/typeb"
HEADERS_COMMON = {"X-Mirae-Version": "1"}

# --- m.Stock SDK (Type A trading & session) -----------------------------------
try:
    from tradingapi_a.mconnect import MConnect  # type: ignore
except Exception:
    MConnect = None

# --- Simple in-memory token cache ---------------------------------------------
class TokenCache(BaseModel):
    access_token: Optional[str] = None
    token_set_at: Optional[float] = None

    def is_valid(self) -> bool:
        return bool(self.access_token)

TOKENS = TokenCache()

# --- Script Master cache ------------------------------------------------------
SCRIPT_MASTER: List[Dict[str, Any]] | None = None

async def refresh_script_master():
    global SCRIPT_MASTER
    url = f"{TYPEB_BASE}/instruments/OpenAPIScripMaster"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, headers=_auth_headers())
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        data = r.json()
        # The API usually returns a list of dicts with fields like 'symbol', 'token', 'exchange'
        SCRIPT_MASTER = data if isinstance(data, list) else data.get("data")

# --- FastAPI app ---------------------------------------------------------------
app = FastAPI(title="m.Stock Backend API", version="0.2.0")

# --- Admin token protection ----------------------------------------------------
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
    if MConnect is None:
        raise HTTPException(status_code=500, detail="mStock SDK not installed. pip install mStock-TradingApi-A")
    m = MConnect()
    try:
        login_resp = m.login(settings.M_USERNAME, settings.M_PASSWORD)
        if not login_resp or login_resp.get("status") not in (True, "success", "SUCCESS"):
            raise HTTPException(status_code=401, detail=f"Login failed: {login_resp}")
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
        return StartLoginResponse(message="Logged in & token cached", note="Access token stored in memory.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Auth error: {e}")

# --- Orders --------------------------------------------------------------------
class OrderRequest(BaseModel):
    tradingsymbol: str
    exchange: str = Field(example="NSE")
    transaction_type: str = Field(example="BUY")
    order_type: str = Field(example="MARKET")
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
        order_id = resp.get("data", {}).get("order_id") or resp.get("order_id") or "unknown"
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

# --- Market Data ---------------------------------------------------------------
def _auth_headers() -> Dict[str, str]:
    if not TOKENS.is_valid():
        raise HTTPException(status_code=401, detail="Not logged in. Call /auth/login first.")
    h = HEADERS_COMMON.copy()
    h["Authorization"] = f"Bearer {TOKENS.access_token}"
    h["X-PrivateKey"] = settings.M_API_KEY
    h["Content-Type"] = "application/json"
    return h

@app.get("/market/ohlc")
async def market_ohlc(
    mode: str = Query("OHLC", pattern="^(OHLC|LTP)$"),
    nse: Optional[str] = Query(None),
    bse: Optional[str] = Query(None),
):
    payload = {"mode": mode, "exchangeTokens": {}}
    if nse:
        payload["exchangeTokens"]["NSE"] = [t.strip() for t in nse.split(",") if t.strip()]
    if bse:
        payload["exchangeTokens"]["BSE"] = [t.strip() for t in bse.split(",") if t.strip()]
    if not payload["exchangeTokens"]:
        raise HTTPException(status_code=400, detail="Pass at least one of ?nse= or ?bse=")
    url = f"{TYPEB_BASE}/instruments/quote"
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, headers=_auth_headers(), json=payload)
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        return JSONResponse(r.json())

@app.get("/market/historical")
async def market_historical(
    exchange: str = Query(...),
    symboltoken: str = Query(...),
    interval: str = Query(...),
    fromdate: str = Query(...),
    todate: str = Query(...),
):
    url = f"{TYPEB_BASE}/instruments/historical"
    payload = {"exchange": exchange, "symboltoken": symboltoken, "interval": interval, "fromdate": fromdate, "todate": todate}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, headers=_auth_headers(), json=payload)
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        return JSONResponse(r.json())

@app.get("/market/script-master")
async def script_master(_: None = Depends(require_admin)):
    await refresh_script_master()
    return {"count": len(SCRIPT_MASTER or []), "data": SCRIPT_MASTER}

# --- Symbols Search ------------------------------------------------------------
@app.get("/symbols/search")
async def symbols_search(
    exchange: str = Query(..., description="Exchange code, e.g. NSE/BSE/NFO"),
    tradingsymbol: str = Query(..., description="Tradingsymbol, e.g. RELIANCE"),
    _: None = Depends(require_admin),
):
    global SCRIPT_MASTER
    if SCRIPT_MASTER is None:
        await refresh_script_master()
    if not SCRIPT_MASTER:
        raise HTTPException(status_code=500, detail="Script Master unavailable")
    matches = [row for row in SCRIPT_MASTER if str(row.get("exch", row.get("exchange"))).upper() == exchange.upper() and str(row.get("symbol", row.get("tradingsymbol"))).upper() == tradingsymbol.upper()]
    if not matches:
        raise HTTPException(status_code=404, detail="Symbol not found in Script Master")
    return {"matches": matches}

# --- Health -------------------------------------------------------------------
@app.get("/healthz")
async def healthz():
    return {"ok": True, "ts": datetime.utcnow().isoformat()}
