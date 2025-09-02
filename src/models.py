from datetime import datetime
from typing import Optional, List
from pathlib import Path
import json
import logging

from pydantic import BaseModel, Field

# --- Persistent Token Cache ----------------------------------------------------
TOKEN_FILE = Path(".tokens.json")

logger = logging.getLogger("mstock-backend") # Assuming logger will be configured in main.py and accessible

class TokenCache(BaseModel):
    access_token: Optional[str] = None
    token_set_at: Optional[float] = None

    def is_valid(self) -> bool:
        if not self.access_token or not self.token_set_at:
            return False
        token_date = datetime.fromtimestamp(self.token_set_at).date()
        return token_date == datetime.now().date()
    
    def get_token(self) -> str:
        return self.access_token

class PersistentTokenCache(TokenCache):
    def load(self):
        if TOKEN_FILE.exists():
            try:
                data = json.loads(TOKEN_FILE.read_text())
                self.access_token = data.get("access_token")
                self.token_set_at = data.get("token_set_at")
                if not self.is_valid():
                    self.clear()
            except Exception as e:
                logger.warning("Failed to load token cache", exc_info=e)

    def save(self):
        try:
            TOKEN_FILE.write_text(json.dumps({
                "access_token": self.access_token,
                "token_set_at": self.token_set_at,
            }))
        except Exception as e:
            logger.warning("Failed to persist token cache", exc_info=e)

    def clear(self):
        self.access_token = None
        self.token_set_at = None
        try:
            if TOKEN_FILE.exists():
                TOKEN_FILE.unlink()
        except Exception as e:
            logger.warning("Failed to delete token file", exc_info=e)

class LoginResponse(BaseModel):
    message: str
    note: str

class SessionRequest(BaseModel):
    otp: str

class OrderRequest(BaseModel):
    tradingsymbol: str = Field(examples=["SBIN","RELIANCE"])
    exchange: str = Field(examples=["NSE","BSE"])
    transaction_type: str = Field(examples=["BUY","SELL"])
    order_type: str = Field(examples=["MARKET"])
    quantity: int
    product: str = Field(examples=["CNC"])
    validity: str = Field(default="DAY")
    price: float | None = 0
    trigger_price: float | None = 0

class ModifyOrderRequest(BaseModel):
    order_id: str
    quantity: Optional[str] = None
    price: Optional[str] = None
    trigger_price: Optional[str] = None
    order_type: Optional[str] = None
    validity: Optional[str] = None
    disclosed_quantity: Optional[str] = None

class CancelOrderRequest(BaseModel):
    order_id: str

class OrderStatusRequest(BaseModel):
    order_id: str
    segment: str

class LTPRequest(BaseModel):
    instruments: List[str]  # e.g. ["NSE:RELIANCE", "NSE:TCS"]

class OHLCRequest(BaseModel):
    instruments: List[str]  # e.g. ["NSE:INFY", "NSE:SBIN"]

class HistoricalChartRequest(BaseModel):
    security_token: str
    interval: str           # e.g. "1m", "5m", "1d"
    from_date: datetime
    to_date: datetime

class LoserGainerRequest(BaseModel):
    Exchange: str
    SecurityIdCode: str
    segment: str
