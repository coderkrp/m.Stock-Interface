import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import interface
from fastapi import Depends, Header, HTTPException
from datetime import datetime, timezone

# Define a simple override for require_admin
def override_require_admin(x_admin_token: str = Header(...)):
    if x_admin_token == "test-admin-token":
        return None
    raise HTTPException(status_code=401, detail="Invalid admin token")

@pytest.fixture(scope="function")
def client_with_mock_admin_token(monkeypatch):
    # Patch APP_ADMIN_TOKEN before the app is initialized
    monkeypatch.setattr(interface.settings, "APP_ADMIN_TOKEN", "test-admin-token")

    # Override the require_admin dependency
    interface.app.dependency_overrides[interface.require_admin] = override_require_admin
    
    # Mock MConnect and MTicker globally for the test client
    with patch('interface.MConnect') as MockMConnect, \
         patch('interface.MTicker') as MockMTicker:

        mock_mconnect_instance = MockMConnect.return_value
        mock_mconnect_instance.login.return_value = {"status": "success"}
        mock_mconnect_instance.generate_session.return_value = {"data": {"access_token": "mock_access_token"}}
        mock_mconnect_instance.get_order_book.return_value = {"data": []}
        mock_mconnect_instance.place_order.return_value = {"status": "success", "order_id": "mock_order_id"}
        mock_mconnect_instance.modify_order.return_value = {"status": "success"}
        mock_mconnect_instance.cancel_all.return_value = {"status": "success"}
        
        # Mock get_trade_history to expect datetime objects and return a successful response
        def mock_get_trade_history(_fromDate: datetime, _toDate: datetime):
            assert isinstance(_fromDate, datetime)
            assert isinstance(_toDate, datetime)
            return {"data": [{"trade_id": "mock_trade_id", "date": _fromDate.isoformat()}]}
        mock_mconnect_instance.get_trade_history.side_effect = mock_get_trade_history

        mock_mconnect_instance.get_order_details.return_value = {"data": {}}
        mock_mconnect_instance.get_ltp.return_value = {"data": {}}
        mock_mconnect_instance.get_ohlc.return_value = {"data": {}}
        mock_mconnect_instance.get_historical_chart.return_value = {"data": {}}
        mock_mconnect_instance.get_instruments.return_value = MagicMock(decode=lambda x: b"", json=lambda: {"data": {}})
        mock_mconnect_instance.loser_gainer.return_value = {"data": {}}

        interface.mconnect = mock_mconnect_instance

        # Clear tokens before each test
        interface.TOKENS.clear()

        yield TestClient(interface.app)

    # Clean up dependency overrides after the test
    interface.app.dependency_overrides = {}
