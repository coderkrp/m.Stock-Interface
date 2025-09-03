from fastapi.testclient import TestClient
from main import TOKENS
from models import TokenCache, PersistentTokenCache
import pytest
from datetime import datetime, timezone, timedelta
import time
from unittest.mock import patch
import json


class TestTokenCache:
    def test_is_valid_true(self):
        cache = TokenCache(access_token="test_token", token_set_at=time.time())
        assert cache.is_valid()

    def test_is_valid_false_no_token(self):
        cache = TokenCache(access_token=None, token_set_at=time.time())
        assert not cache.is_valid()

    def test_is_valid_false_no_timestamp(self):
        cache = TokenCache(access_token="test_token", token_set_at=None)
        assert not cache.is_valid()

    def test_is_valid_false_expired_date(self):
        # Set token_set_at to yesterday
        yesterday = datetime.now().date() - timedelta(days=1)
        cache = TokenCache(
            access_token="test_token",
            token_set_at=datetime(
                yesterday.year, yesterday.month, yesterday.day
            ).timestamp(),
        )
        assert not cache.is_valid()

    def test_get_token(self):
        cache = TokenCache(access_token="test_token", token_set_at=time.time())
        assert cache.get_token() == "test_token"


class TestPersistentTokenCache:
    @pytest.fixture(autouse=True)
    def setup(self):
        # Ensure TOKEN_FILE is set to a test-specific path or mocked
        # For simplicity, we'll mock Path methods directly
        pass

    @patch("models.TOKEN_FILE")
    def test_load_existing_valid_token(self, mock_token_file):
        mock_token_file.exists.return_value = True
        mock_token_file.read_text.return_value = json.dumps(
            {"access_token": "valid_token", "token_set_at": time.time()}
        )
        cache = PersistentTokenCache()
        cache.load()
        assert cache.access_token == "valid_token"
        assert cache.is_valid()

    @patch("models.TOKEN_FILE")
    def test_load_existing_expired_token(self, mock_token_file):
        mock_token_file.exists.return_value = True
        # Set token_set_at to yesterday
        yesterday = datetime.now().date() - timedelta(days=1)
        mock_token_file.read_text.return_value = json.dumps(
            {
                "access_token": "expired_token",
                "token_set_at": datetime(
                    yesterday.year, yesterday.month, yesterday.day
                ).timestamp(),
            }
        )
        cache = PersistentTokenCache()
        cache.load()
        assert cache.access_token is None
        assert not cache.is_valid()

    @patch("models.TOKEN_FILE")
    def test_load_no_existing_token(self, mock_token_file):
        mock_token_file.exists.return_value = False
        cache = PersistentTokenCache()
        cache.load()
        assert cache.access_token is None
        assert not cache.is_valid()

    @patch("models.TOKEN_FILE")
    def test_save_token(self, mock_token_file):
        cache = PersistentTokenCache()
        cache.access_token = "new_token"
        cache.token_set_at = time.time()
        cache.save()
        mock_token_file.write_text.assert_called_once()
        written_data = json.loads(mock_token_file.write_text.call_args[0][0])
        assert written_data["access_token"] == "new_token"
        assert "token_set_at" in written_data

    @patch("models.TOKEN_FILE")
    def test_clear_token(self, mock_token_file):
        mock_token_file.exists.return_value = True
        cache = PersistentTokenCache()
        cache.access_token = "some_token"
        cache.token_set_at = time.time()
        cache.clear()
        assert cache.access_token is None
        assert cache.token_set_at is None
        mock_token_file.unlink.assert_called_once()


class TestAPIEndpoints:

    def test_healthz(self, client_with_mock_admin_token: TestClient):
        response = client_with_mock_admin_token.get("/healthz")
        assert response.status_code == 200
        assert response.json()["ok"] is True

    def test_auth_login(self, client_with_mock_admin_token: TestClient):
        response = client_with_mock_admin_token.post(
            "/auth/login", headers={"X-Admin-Token": "test-admin-token"}
        )
        assert response.status_code == 200
        assert response.json()["message"] == "OTP sent"
        assert "note" in response.json()

    def test_auth_session(self, client_with_mock_admin_token: TestClient):
        # First, call auth/login to set up the session context (though not strictly necessary for this test)
        client_with_mock_admin_token.post(
            "/auth/login", headers={"X-Admin-Token": "test-admin-token"}
        )

        # Now, test auth/session
        response = client_with_mock_admin_token.post(
            "/auth/session",
            json={"otp": "123456"},
            headers={"X-Admin-Token": "test-admin-token"},
        )
        assert response.status_code == 200
        assert response.json()["message"] == "Session established"
        assert "data" in response.json()
        assert TOKENS.is_valid()
        assert TOKENS.access_token == "mock_access_token"

    def test_place_order(self, client_with_mock_admin_token: TestClient):
        # Ensure a valid token is set for this test
        TOKENS.access_token = "mock_access_token"
        TOKENS.token_set_at = time.time()  # Use current timestamp

        order_data = {
            "tradingsymbol": "SBIN",
            "exchange": "NSE",
            "transaction_type": "BUY",
            "order_type": "MARKET",
            "quantity": 1,
            "product": "CNC",
        }
        response = client_with_mock_admin_token.post(
            "/orders", json=order_data, headers={"X-Admin-Token": "test-admin-token"}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["order_id"] == "mock_order_id"

    def test_get_orders(self, client_with_mock_admin_token: TestClient):
        TOKENS.access_token = "mock_access_token"
        TOKENS.token_set_at = time.time()  # Use current timestamp

        response = client_with_mock_admin_token.get(
            "/orders", headers={"X-Admin-Token": "test-admin-token"}
        )
        assert response.status_code == 200
        assert "data" in response.json()

    def test_get_trades(self, client_with_mock_admin_token: TestClient):
        TOKENS.access_token = "mock_access_token"
        TOKENS.token_set_at = time.time()  # Use current timestamp

        trade_history_data = {
            "fromDate": datetime(
                2024, 1, 1, 0, 0, 0, 0, tzinfo=timezone.utc
            ),  # Pass datetime object directly
            "toDate": datetime(
                2024, 1, 2, 0, 0, 0, 0, tzinfo=timezone.utc
            ),  # Pass datetime object directly
        }
        response = client_with_mock_admin_token.get(
            "/trades",
            params=trade_history_data,
            headers={"X-Admin-Token": "test-admin-token"},
        )
        assert response.status_code == 200
        assert "data" in response.json()

    def test_order_status(self, client_with_mock_admin_token: TestClient):
        TOKENS.access_token = "mock_access_token"
        TOKENS.token_set_at = time.time()  # Use current timestamp

        order_status_data = {"order_id": "mock_order_id", "segment": "NSE"}
        response = client_with_mock_admin_token.post(
            "/orders/status",
            json=order_status_data,
            headers={"X-Admin-Token": "test-admin-token"},
        )
        assert response.status_code == 200
        assert "data" in response.json()

    def test_get_ltp(self, client_with_mock_admin_token: TestClient):
        TOKENS.access_token = "mock_access_token"
        TOKENS.token_set_at = time.time()  # Use current timestamp

        ltp_data = {"instruments": ["NSE:RELIANCE"]}
        response = client_with_mock_admin_token.post(
            "/market/ltp", json=ltp_data, headers={"X-Admin-Token": "test-admin-token"}
        )
        assert response.status_code == 200
        assert "data" in response.json()

    def test_get_ohlc(self, client_with_mock_admin_token: TestClient):
        TOKENS.access_token = "mock_access_token"
        TOKENS.token_set_at = time.time()  # Use current timestamp

        ohlc_data = {"instruments": ["NSE:INFY"]}
        response = client_with_mock_admin_token.post(
            "/market/ohlc",
            json=ohlc_data,
            headers={"X-Admin-Token": "test-admin-token"},
        )
        assert response.status_code == 200
        assert "data" in response.json()

    def test_get_historical_chart(self, client_with_mock_admin_token: TestClient):
        TOKENS.access_token = "mock_access_token"
        TOKENS.token_set_at = time.time()  # Use current timestamp

        historical_data = {
            "security_token": "12345",
            "interval": "1d",
            "from_date": datetime(
                2024, 1, 1, 0, 0, 0, 0, tzinfo=timezone.utc
            ).isoformat(
                timespec="microseconds"
            ),  # Use full ISO format with microseconds and Z
            "to_date": datetime(2024, 1, 2, 0, 0, 0, 0, tzinfo=timezone.utc).isoformat(
                timespec="microseconds"
            ),  # Use full ISO format with microseconds and Z
        }
        response = client_with_mock_admin_token.post(
            "/market/historical",
            json=historical_data,
            headers={"X-Admin-Token": "test-admin-token"},
        )
        assert response.status_code == 200
        assert "data" in response.json()

    def test_get_instruments(self, client_with_mock_admin_token: TestClient):
        TOKENS.access_token = "mock_access_token"
        TOKENS.token_set_at = time.time()  # Use current timestamp

        response = client_with_mock_admin_token.get(
            "/market/instruments", headers={"X-Admin-Token": "test-admin-token"}
        )
        assert response.status_code == 200
        assert response.json()["message"] == "Instrument file saved"

    def test_loser_gainer(self, client_with_mock_admin_token: TestClient):
        TOKENS.access_token = "mock_access_token"
        TOKENS.token_set_at = time.time()  # Use current timestamp

        loser_gainer_data = {
            "Exchange": "NSE",
            "SecurityIdCode": "NIFTY",
            "segment": "EQ",
        }
        response = client_with_mock_admin_token.post(
            "/market/loser_gainer",
            json=loser_gainer_data,
            headers={"X-Admin-Token": "test-admin-token"},
        )
        assert response.status_code == 200
        assert "data" in response.json()
