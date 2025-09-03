import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from main import app, TOKENS, require_admin
from settings import settings
import main
from fastapi import Header, HTTPException
from datetime import datetime


# Define a simple override for require_admin
def override_require_admin(x_admin_token: str = Header(...)):
    if x_admin_token == "test-admin-token":
        return None
    raise HTTPException(status_code=401, detail="Invalid admin token")


@pytest.fixture(scope="function")
def client_with_mock_admin_token(monkeypatch):
    # Patch APP_ADMIN_TOKEN before the app is initialized
    monkeypatch.setattr(settings, "APP_ADMIN_TOKEN", "test-admin-token")

    # Override the require_admin dependency
    app.dependency_overrides[require_admin] = override_require_admin

    # Mock MConnect and MTicker globally for the test client
    with patch("main.MConnect") as MockMConnect:

        mock_mconnect_instance = MockMConnect.return_value
        mock_mconnect_instance.login.return_value = {"status": "success"}
        mock_mconnect_instance.generate_session.return_value = {
            "data": {"access_token": "mock_access_token"}
        }
        mock_mconnect_instance.get_order_book.return_value = {"data": []}
        mock_mconnect_instance.place_order.return_value = {
            "status": "success",
            "order_id": "mock_order_id",
        }
        mock_mconnect_instance.modify_order.return_value = {"status": "success"}
        mock_mconnect_instance.cancel_all.return_value = {"status": "success"}

        # Mock get_trade_history to expect datetime objects and return a successful response
        def mock_get_trade_history(_fromDate: datetime, _toDate: datetime):
            assert isinstance(_fromDate, datetime)
            assert isinstance(_toDate, datetime)
            return {
                "data": [{"trade_id": "mock_trade_id", "date": _fromDate.isoformat()}]
            }

        mock_mconnect_instance.get_trade_history.side_effect = mock_get_trade_history

        mock_mconnect_instance.get_order_details.return_value = {"data": {}}
        mock_mconnect_instance.get_ltp.return_value = {"data": {}}
        mock_mconnect_instance.get_ohlc.return_value = {"data": {}}
        mock_mconnect_instance.get_historical_chart.return_value = {"data": {}}
        mock_mconnect_instance.get_instruments.return_value = MagicMock(
            decode=lambda x: b"", json=lambda: {"data": {}}
        )
        mock_mconnect_instance.loser_gainer.return_value = {"data": {}}

        monkeypatch.setattr(main, "mconnect", mock_mconnect_instance)

        # Clear tokens before each test
        TOKENS.clear()

        yield TestClient(app)

    # Clean up dependency overrides after the test
    app.dependency_overrides = {}
