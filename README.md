# mstock_interface

A Python interface for m.Stock (Mirae Asset, India) Type A Only Version.

## Features

- Two-step login & session handling via Type A SDK (OTP flow)
- Place, modify, and cancel orders
- Retrieve orderbook, tradebook, and order status
- Fetch market data: Last Traded Price (LTP), Open-High-Low-Close (OHLC), Historical Charts, Instruments, Loser/Gainer data
- WebSocket endpoints for live ticks and order/trade updates (Type A)
- Persistent token store (JSON file) with daily auto-expiry
- Structured JSON logging (stdout + rotating file)
- Request throttling and timing middleware

## Installation

Requires Python 3.11 or higher.

You can install the package using pip:

```bash
pip install mstock_interface
```

### Environment Variables

This application requires the following environment variables to be set for connecting to the m.Stock API:

- `M_API_KEY`
- `M_USERNAME`
- `M_PASSWORD`
- `M_API_SECRET`
- `APP_ADMIN_TOKEN` (for internal API access control)

It is recommended to use a `.env` file for managing these variables. A `.env.example` file might be provided in the project root.

## Usage

This package provides a FastAPI backend. To run the server:

1.  **Set up environment variables:** Create a `.env` file in the project root with your m.Stock API credentials and an `APP_ADMIN_TOKEN`.

    ```
    M_API_KEY=your_api_key
    M_USERNAME=your_username
    M_PASSWORD=your_password
    M_API_SECRET=your_api_secret
    APP_ADMIN_TOKEN=your_admin_token
    ```

2.  **Run the FastAPI application:**

    ```bash
uvicorn src.main:app --reload --port 8080
```

    The API will be accessible at `http://127.0.0.1:8080`.

### API Endpoints Example (Login)

You can interact with the API using tools like `curl` or `httpx`.

**1. Initiate Login (Request OTP):**

```bash
curl -X POST "http://127.0.0.1:8080/auth/login" \
     -H "X-Admin-Token: your_admin_token"
```

**2. Establish Session (with OTP):**

```bash
curl -X POST "http://127.0.0.1:8080/auth/session" \
     -H "Content-Type: application/json" \
     -H "X-Admin-Token: your_admin_token" \
     -d '{"otp": "YOUR_OTP_HERE"}'
```

For other endpoints and their request/response structures, please refer to the API documentation (accessible via `/docs` endpoint when the server is running, e.g., `http://127.0.0.1:8080/docs`).

## Contributing

Contributions are welcome! Please refer to the [GitHub repository](https://github.com/coderkrp/m.Stock-Interface.git) for guidelines.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Project Status

[![PyPI - Version](https://img.shields.io/pypi/v/mstock_interface)](https://pypi.org/project/mstock_interface/)
[![GitHub last commit](https://img.shields.io/github/last-commit/coderkrp/m.Stock-Interface)](https://github.com/coderkrp/m.Stock-Interface)
[![GitHub issues](https://img.shields.io/github/issues/coderkrp/m.Stock-Interface)](https://github.com/coderkrp/m.Stock-Interface/issues)
