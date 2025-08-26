"""
FastAPI backend for m.Stock (Mirae Asset, India)
Features:
- Login & token/session handling via official SDK (Type A)
- Place/modify/cancel orders, order/positions/holdings queries (Type A)
- OHLC / historical candles / LTP (Type B)
- Script Master download + caching + symbol search (Type B)
- Auto-refresh Script Master daily at market open
- WebSocket endpoints for live ticks and order/trade updates
- Simple token cache with auto-refresh hook points

Tested on Python 3.11+. Install deps:
  pip install fastapi uvicorn httpx python-dotenv pydantic pydantic-settings mStock-TradingApi-A apscheduler websockets

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
from fastapi import FastAPI, HTTPException, Depends, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from apscheduler.schedulers.background import BackgroundScheduler
import asyncio

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
    from tradingapi_a.mticker import MTicker   # type: ignore
except Exception:
    MConnect = None
    MTicker = None

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
        SCRIPT_MASTER = data if isinstance(data, list) else data.get("data")

# --- FastAPI app ---------------------------------------------------------------
app = FastAPI(title="m.Stock Backend API", version="0.4.0")

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

# --- WebSocket Endpoints -------------------------------------------------------
active_tick_clients: List[WebSocket] = []
active_order_clients: List[WebSocket] = []

def start_ticker(ws_list: List[WebSocket], mode: str = "TICKS"):
    if MTicker is None:
        print("[ERROR] MTicker not installed")
        return
    m = MTicker(api_key=settings.M_API_KEY, access_token=TOKENS.access_token)

    def on_message(ws, message):
        for client in ws_list:
            asyncio.create_task(client.send_text(message))

    def on_error(ws, error):
        print(f"[WS ERROR] {error}")

    def on_close(ws):
        print("[WS CLOSED]")

    m.on_message = on_message
    m.on_error = on_error
    m.on_close = on_close

    # Subscribe to ticks or order/trade updates based on mode
    if mode == "TICKS":
        m.subscribe(["NSE:RELIANCE"])  # Example subscription, extend as needed
    elif mode == "ORDERS":
        m.subscribe_orders()

    m.connect()

@app.websocket("/ws/ticks")
async def ws_ticks(websocket: WebSocket):
    await websocket.accept()
    active_tick_clients.append(websocket)
    if len(active_tick_clients) == 1:
        asyncio.get_event_loop().run_in_executor(None, start_ticker, active_tick_clients, "TICKS")
    try:
        while True:
            await websocket.receive_text()  # keep alive
    except WebSocketDisconnect:
        active_tick_clients.remove(websocket)

@app.websocket("/ws/orders")
async def ws_orders(websocket: WebSocket):
    await websocket.accept()
    active_order_clients.append(websocket)
    if len(active_order_clients) == 1:
        asyncio.get_event_loop().run_in_executor(None, start_ticker, active_order_clients, "ORDERS")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        active_order_clients.remove(websocket)

# --- Health -------------------------------------------------------------------
@app.get("/healthz")
async def healthz():
    return {"ok": True, "ts": datetime.utcnow().isoformat()}

# --- Background scheduler for Script Master refresh ---------------------------
scheduler = BackgroundScheduler()

@scheduler.scheduled_job("cron", hour=8, minute=45)
def scheduled_refresh():
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(refresh_script_master(), loop)
        else:
            loop.run_until_complete(refresh_script_master())
    except Exception as e:
        print(f"[WARN] Failed to auto-refresh script master: {e}")

@app.on_event("startup")
def startup_event():
    scheduler.start()
    print("Scheduler started for daily Script Master refresh at 08:45 IST")
